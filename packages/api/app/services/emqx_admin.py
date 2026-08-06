"""Client/administration wrapper around EMQX's HTTP Management API.

Replaces packages/api/app/services/mosquitto_dynsec.py (see git history),
which drove Mosquitto's dynamic-security plugin over an MQTT connection.
This module talks to EMQX instead, which manages authentication/
authorization through a normal REST API -- no MQTT connection involved for
admin operations at all.

============================================================================
READ THIS BEFORE MODIFYING -- partially verified against a live broker
============================================================================
This module was originally built with NO live verification (built from
EMQX 5.x's documented REST API via web search, cross-checked for internal
consistency only) because this project's build environment couldn't reach
emqx/emqx or docs.emqx.com/emqx.io to test against. It has since been
spot-checked by hand against a real production EMQX 5.6.2 instance
(mqtt.allyoperations.com), which CONFIRMED:
  - POST /api/v5/authentication/password_based:built_in_database/users
    with {"user_id", "password"} succeeds (2xx, returns the created user
    object) and 409s with an "ALREADY_EXISTS"-shaped body on a repeat --
    exactly what _looks_like_conflict()/_upsert_mqtt_user() assume.
  - POST /api/v5/authorization/sources/built_in_database/rules/users
    exists and works for a first create, but is a STRICT CREATE, not an
    upsert -- confirmed empirically: a second POST for the same username
    returns 409 `{"code":"ALREADY_EXISTS","message":"User '<name>' already
    exist"}` rather than replacing the rule set. _set_user_rules() now
    handles this by falling back to PUT .../rules/users/{username} on
    conflict, mirroring _upsert_mqtt_user()'s pattern -- but that PUT path
    is still an INFERENCE (by symmetry with the authn side), not itself
    independently confirmed. If a device re-claim ever fails here, this is
    the first thing to check with a live curl call.
  - Authentication and Authorization are two independent things you must
    each explicitly add via the EMQX dashboard (or equivalent config) --
    adding one does NOT imply the other exists. A fresh EMQX instance with
    only Authentication configured will 404 with "Not found:
    built_in_database" on every authorization call until an Authorization
    source of type Built-in Database is separately added (Dashboard:
    Access Control -> Authorization -> Create -> Built-in Database). Watch
    out for other authorization sources (e.g. a default "File" backend)
    ranked ahead of it -- EMQX evaluates sources in order and the first
    one to match (allow or deny) wins, so a broad rule in an
    earlier-ranked source can silently shadow the rules this module sets.

Still NOT independently verified, carried over from the original
build-from-docs pass:
  - Whether /api/v5/authorization/sources/built_in_database/rules/users
    (per-username rules, used here) behaves as documented for
    rules/clients (per-clientid rules), which is the one confirmed-via-
    search example -- users and clients are documented as parallel/
    symmetric scopes, but only the clients form was seen quoted directly.
  - That authentication and authorization must both be independently
    configured in emqx/emqx.conf (see that file) for a `built_in_database`
    backend to exist as `password_based:built_in_database` /
    `built_in_database` for these calls to have anything to operate on.

How it works:
  * Admin/management calls use HTTP Basic auth with an API key/secret pair
    as username/password (NOT an MQTT connection):
        curl -u <api_key>:<api_secret> http://emqx:18083/api/v5/nodes
  * The API key/secret is bootstrapped via EMQX's own "bootstrap file"
    mechanism (`api_key.bootstrap_file` in emqx/emqx.conf) -- see
    emqx/bootstrap.sh, which just writes/confirms that file. There is no
    "init" dance to run here the way Mosquitto's dynsec plugin needed.
  * Authentication (can this username/password log in at all) and
    Authorization (what topics can they pub/sub once connected) are two
    separate EMQX subsystems with separate REST endpoints:
      - authn: /api/v5/authentication/password_based:built_in_database/users
      - authz: /api/v5/authorization/sources/built_in_database/rules/users
    Both identifiers are fixed strings tied to the single authenticator/
    authorizer declared in emqx/emqx.conf -- this module assumes exactly
    one of each is configured, matching that file.
  * Unlike Mosquitto's dynamic-security plugin, EMQX has no shared "role" +
    %c-substitution construct standing between a user and its rules -- each
    device's ACL rules are created directly, with device_key already
    substituted into the topic strings, at claim time. There is no
    equivalent of the old module's ensure_device_role() step.
  * A rule-set POST replaces whatever was previously set for that
    username, so re-provisioning an existing device_key (e.g. re-claim
    after an unclean unclaim) is naturally idempotent.

Device provisioning model:
  * On CLAIM: create a built_in_database user (user_id=device_key,
    password=device_bearer_token); if that conflicts (already exists --
    e.g. re-claim), reset its password via PUT instead. Then set the
    device's 4 ACL rules, keyed by username=device_key:
      - publish   genmon/{device_key}/data
      - publish   genmon/{device_key}/io
      - publish   genmon/{device_key}/cmd/ack
      - subscribe genmon/{device_key}/cmd
    (EMQX authz doesn't split "may subscribe" from "may receive" the way
    Mosquitto's dynsec plugin does -- one subscribe-allow rule covers both
    concerns here, which is simpler than the old module needed to be.)
  * On UNCLAIM: delete the user's ACL rules, then delete the user itself.
    Both deletes treat 404 as success (already gone is the desired state).
"""
import logging
from typing import Optional

import httpx

from app.config import settings

logger = logging.getLogger("genmon.emqx_admin")

AUTHENTICATOR_ID = "password_based:built_in_database"
AUTHN_USERS_PATH = f"/api/v5/authentication/{AUTHENTICATOR_ID}/users"
AUTHZ_USER_RULES_PATH = "/api/v5/authorization/sources/built_in_database/rules/users"


class EmqxAdminError(Exception):
    pass


def _looks_like_conflict(resp: httpx.Response) -> bool:
    if resp.status_code == 409:
        return True
    if resp.status_code == 400:
        text = resp.text.lower()
        return "already exist" in text or "alreadyexist" in text
    return False


def _device_acl_rules(device_key: str) -> list[dict]:
    return [
        {"topic": f"genmon/{device_key}/data", "permission": "allow", "action": "publish"},
        {"topic": f"genmon/{device_key}/io", "permission": "allow", "action": "publish"},
        {"topic": f"genmon/{device_key}/cmd/ack", "permission": "allow", "action": "publish"},
        {"topic": f"genmon/{device_key}/cmd", "permission": "allow", "action": "subscribe"},
    ]


class EmqxAdminClient:
    def __init__(self) -> None:
        self._client: Optional[httpx.AsyncClient] = None

    # -- connection lifecycle -------------------------------------------------
    async def connect(self) -> None:
        if self._client is not None:
            return
        client = httpx.AsyncClient(
            base_url=settings.EMQX_API_URL.rstrip("/"),
            auth=(settings.EMQX_API_KEY, settings.EMQX_API_SECRET),
            timeout=10.0,
        )
        try:
            # /api/v5/nodes requires authentication (unlike /api/v5/status,
            # which is a plain liveness probe) -- use it here so a bad
            # API key/secret surfaces at startup, not on the first claim.
            resp = await client.get("/api/v5/nodes")
            if resp.status_code >= 400:
                raise EmqxAdminError(
                    f"EMQX API not reachable/authorized at {settings.EMQX_API_URL}: "
                    f"{resp.status_code} {resp.text}"
                )
        except Exception:
            await client.aclose()
            raise
        self._client = client
        logger.info("emqx_admin connected to %s", settings.EMQX_API_URL)

    async def disconnect(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    def _raise_for_status(self, resp: httpx.Response, action: str) -> None:
        if resp.status_code >= 400:
            raise EmqxAdminError(f"EMQX API call failed ({action}): {resp.status_code} {resp.text}")

    # -- shared helpers ---------------------------------------------------------
    async def _upsert_mqtt_user(self, username: str, password: str) -> None:
        # Lazily (re)connect on every call rather than requiring connect() to
        # have already succeeded -- if EMQX wasn't up yet at api startup (see
        # app.main's lifespan, which logs and continues rather than crashing
        # the api), the first real claim/unclaim/etc. call gets a fresh
        # attempt instead of permanently failing with "not connected".
        await self.connect()
        assert self._client is not None
        resp = await self._client.post(AUTHN_USERS_PATH, json={"user_id": username, "password": password})
        if _looks_like_conflict(resp):
            resp = await self._client.put(f"{AUTHN_USERS_PATH}/{username}", json={"password": password})
        self._raise_for_status(resp, f"create/reset mqtt user '{username}'")

    async def _set_user_rules(self, username: str, rules: list[dict]) -> None:
        # CONFIRMED against a live EMQX 5.6.2 instance: this endpoint is a
        # strict create, NOT an upsert -- POSTing rules for a username that
        # already has a rule set returns 409 ALREADY_EXISTS rather than
        # replacing them (unlike the authn side, which really does behave
        # like the module docstring originally assumed for both). Fall back
        # to PUT-by-username to update an existing rule set, mirroring
        # _upsert_mqtt_user's pattern. The PUT path/body shape below is
        # inferred from that same symmetry, not independently confirmed --
        # verify it if a re-claim ever fails here.
        await self.connect()
        assert self._client is not None
        resp = await self._client.post(AUTHZ_USER_RULES_PATH, json=[{"username": username, "rules": rules}])
        if _looks_like_conflict(resp):
            resp = await self._client.put(f"{AUTHZ_USER_RULES_PATH}/{username}", json={"rules": rules})
        self._raise_for_status(resp, f"set ACL rules for '{username}'")

    # -- api's own mqtt account (publishes genmon/{device_key}/cmd) -------------
    async def ensure_admin_publish_role(self) -> None:
        """Idempotently ensure the api's OWN mqtt pub/sub account
        (settings.MQTT_USERNAME) exists and can publish genmon/+/cmd.
        Call once at startup, right after connect()."""
        await self._upsert_mqtt_user(settings.MQTT_USERNAME, settings.MQTT_PASSWORD)
        await self._set_user_rules(
            settings.MQTT_USERNAME,
            [{"topic": "genmon/+/cmd", "permission": "allow", "action": "publish"}],
        )
        logger.info("emqx_admin: ensured own mqtt user '%s' can publish genmon/+/cmd", settings.MQTT_USERNAME)

    # -- device client management -----------------------------------------------
    async def create_device_client(self, device_key: str, password: str) -> None:
        """Create (or reset) the MQTT credentials for a freshly-claimed
        device, and set its scoped ACL rules."""
        await self._upsert_mqtt_user(device_key, password)
        await self._set_user_rules(device_key, _device_acl_rules(device_key))
        logger.info("emqx_admin: provisioned device client '%s'", device_key)

    async def delete_device_client(self, device_key: str) -> None:
        await self.connect()
        assert self._client is not None

        resp = await self._client.delete(f"{AUTHZ_USER_RULES_PATH}/{device_key}")
        if resp.status_code not in (200, 204, 404):
            self._raise_for_status(resp, f"delete ACL rules for '{device_key}'")

        resp = await self._client.delete(f"{AUTHN_USERS_PATH}/{device_key}")
        if resp.status_code not in (200, 204, 404):
            self._raise_for_status(resp, f"delete mqtt user '{device_key}'")

        logger.info("emqx_admin: removed device client '%s'", device_key)


emqx_admin_client = EmqxAdminClient()
