"""Client/administration wrapper around EMQX's HTTP Management API.

Replaces packages/api/app/services/mosquitto_dynsec.py (see git history),
which drove Mosquitto's dynamic-security plugin over an MQTT connection.
This module talks to EMQX instead, which manages authentication/
authorization through a normal REST API -- no MQTT connection involved for
admin operations at all.

============================================================================
READ THIS BEFORE MODIFYING -- NOT verified against a live broker
============================================================================
The old Mosquitto module was built, then empirically tested against a real
broker, which caught several real bugs (see its module docstring). This
module could NOT be tested the same way: this sandbox's network policy
blocks both pulling emqx/emqx and fetching docs.emqx.com/emqx.io directly.
It's built from EMQX 5.x's documented REST API via web search of
docs.emqx.com pages and community discussions/PRs, cross-checked for
internal consistency, but not exercised against a running instance.

Before depending on this in production: stand up a real EMQX container and
exercise create_device_client / delete_device_client / ensure_own_mqtt_user
against it with `curl -u $EMQX_API_KEY:$EMQX_API_SECRET $EMQX_API_URL/api/v5/...`
matching the calls below, the same way the Mosquitto module was verified.
Specifically worth confirming:
  - The exact success/conflict status codes for POST .../users (assumed
    201 success / 409 conflict; the _looks_like_conflict() fallback below
    also checks response text as a hedge against getting the status code
    wrong).
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
        await self.connect()
        assert self._client is not None
        resp = await self._client.post(AUTHZ_USER_RULES_PATH, json=[{"username": username, "rules": rules}])
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
