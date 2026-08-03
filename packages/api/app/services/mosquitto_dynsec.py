"""Client/administration wrapper around Mosquitto's built-in
`dynamic-security` plugin (mosquitto_dynamic_security.so).

============================================================================
READ THIS BEFORE MODIFYING -- non-trivial, please double check against a
live broker (`mosquitto_ctrl dynsec ...` is a good way to cross-check
command/response shapes) before relying on this in production.
============================================================================

How it works:
  * The plugin listens for administrative commands published (by an admin
    client, i.e. us) to the topic `$CONTROL/dynamic-security/v1`, as a JSON
    body shaped `{"commands": [{"command": "<name>", ...fields}]}`.
  * It replies on the FIXED topic `$CONTROL/dynamic-security/v1/response`
    with `{"responses": [{"command": "<name>", "error": "..."}]}` (no
    "error" key on success).
  * CORRELATION -- READ THIS, IT'S NOT WHAT THE MQTTv5 SPEC WOULD SUGGEST:
    the natural design is to attach a per-command MQTTv5 `CorrelationData`
    property and match it on the response, and this module still sets
    CorrelationData + ResponseTopic on every outbound publish for protocol
    correctness / forward compatibility. HOWEVER, this was tested against a
    live Mosquitto 2.0.18 broker while building this module, and confirmed
    empirically that mosquitto_dynamic_security.so's responses carry NO
    MQTTv5 properties at all (CorrelationData comes back empty on every
    reply) -- so responses cannot actually be matched to requests that way
    on this broker version. Instead we serialize: an `asyncio.Lock` around
    the publish+await-reply pair guarantees at most one dynsec command is
    ever in flight at a time on this connection, so "the next message that
    arrives on the response topic" is unambiguously the reply to the
    command we just sent (dynsec responses arrive in the same order the
    requests were made, single connection, QoS 1). If a future Mosquitto
    version starts populating CorrelationData correctly, the extra property
    is harmless and this FIFO assumption remains correct regardless.
  * We keep exactly one long-lived MQTTv5 connection open (subscribed to
    the response topic), created at FastAPI startup, serializing every
    dynsec admin call (device claim/unclaim) over it with a ~5s timeout.

Device provisioning model:
  * On CLAIM: create a dynsec "client" named after the device_key (dynsec
    clients are keyed by *username*; we set both username AND clientid to
    device_key -- this assumes the field agent's MQTT client also connects
    with client_id == device_key == username, which is the convention
    devices are provisioned with. If a future agent version diverges from
    that convention, swap the `%c` substitutions below for `%u`.), with
    password = the freshly generated device_bearer_token, then ensure it
    holds the shared `genmon-device` role (created once, idempotently).
  * The `genmon-device` role uses Mosquitto's `%c` per-client topic
    substitution so ONE role's ACLs work for every device:
      - publish (client -> broker): genmon/%c/data, genmon/%c/io,
        genmon/%c/cmd/ack
      - subscribe + receive (broker -> client): genmon/%c/cmd
    Note dynsec distinguishes being allowed to *subscribe* to a topic
    (`subscribePattern`/`subscribeLiteral`) from being allowed to actually
    *receive* messages that match it (`publishClientReceive`) -- both ACL
    entries are required for the retained `cmd` topic to actually reach
    the device.
  * On UNCLAIM: delete the dynsec client. The shared role is left in place
    (other devices still use it).
"""
import asyncio
import json
import logging
import uuid
from contextlib import AsyncExitStack
from typing import Any, Optional

import aiomqtt
from paho.mqtt.packettypes import PacketTypes
from paho.mqtt.properties import Properties

from app.config import settings

logger = logging.getLogger("genmon.dynsec")

DYNSEC_COMMAND_TOPIC = "$CONTROL/dynamic-security/v1"
DYNSEC_RESPONSE_TOPIC = "$CONTROL/dynamic-security/v1/response"
DEVICE_ROLE_NAME = "genmon-device"

_DEVICE_ROLE_ACLS = [
    {"acltype": "publishClientSend", "topic": "genmon/%c/data", "priority": 0, "allow": True},
    {"acltype": "publishClientSend", "topic": "genmon/%c/io", "priority": 0, "allow": True},
    {"acltype": "publishClientSend", "topic": "genmon/%c/cmd/ack", "priority": 0, "allow": True},
    {"acltype": "subscribePattern", "topic": "genmon/%c/cmd", "priority": 0, "allow": True},
    {"acltype": "publishClientReceive", "topic": "genmon/%c/cmd", "priority": 0, "allow": True},
]


class DynsecError(Exception):
    pass


class DynsecTimeout(DynsecError):
    pass


def _build_tls_params() -> Optional["aiomqtt.TLSParameters"]:
    if not settings.MQTT_TLS:
        return None
    return aiomqtt.TLSParameters()


def _error_is_already_exists(message: str) -> bool:
    m = message.lower()
    return "already exists" in m


class MosquittoDynsecClient:
    def __init__(self) -> None:
        self._client: Optional[aiomqtt.Client] = None
        self._stack: Optional[AsyncExitStack] = None
        self._listen_task: Optional[asyncio.Task] = None
        # Connection lifecycle (connect/disconnect) is guarded separately
        # from command execution so a call arriving mid-(re)connect doesn't
        # deadlock against itself.
        self._conn_lock = asyncio.Lock()
        # At most one dynsec command may be in flight at a time -- see the
        # big module docstring for why this single-slot design replaced a
        # CorrelationData-keyed map.
        self._call_lock = asyncio.Lock()
        self._pending_response: Optional["asyncio.Future[dict]"] = None
        self._role_ready = False

    # -- connection lifecycle -------------------------------------------------
    async def connect(self) -> None:
        async with self._conn_lock:
            await self._connect_locked()

    async def _connect_locked(self) -> None:
        if self._client is not None:
            return
        stack = AsyncExitStack()
        client = aiomqtt.Client(
            hostname=settings.MQTT_HOST,
            port=settings.MQTT_PORT,
            username=settings.MQTT_USERNAME,
            password=settings.MQTT_PASSWORD,
            identifier="genmon-api-dynsec",
            protocol=aiomqtt.ProtocolVersion.V5,
            tls_params=_build_tls_params(),
        )
        try:
            await stack.enter_async_context(client)
            await client.subscribe(DYNSEC_RESPONSE_TOPIC, qos=1)
        except Exception:
            await stack.aclose()
            raise
        self._stack = stack
        self._client = client
        self._listen_task = asyncio.create_task(self._listen_loop(), name="dynsec-listener")
        logger.info("dynsec client connected and subscribed to %s", DYNSEC_RESPONSE_TOPIC)

    async def disconnect(self) -> None:
        async with self._conn_lock:
            if self._listen_task is not None:
                self._listen_task.cancel()
                self._listen_task = None
            if self._stack is not None:
                try:
                    await self._stack.aclose()
                except Exception:  # pragma: no cover
                    logger.exception("error closing dynsec connection")
            self._stack = None
            self._client = None
            if self._pending_response is not None and not self._pending_response.done():
                self._pending_response.set_exception(DynsecError("dynsec client disconnected"))
            self._pending_response = None

    async def _listen_loop(self) -> None:
        assert self._client is not None
        try:
            async for message in self._client.messages:
                self._dispatch_response(message)
        except asyncio.CancelledError:
            raise
        except Exception:  # pragma: no cover
            logger.exception("dynsec response listener crashed")

    def _dispatch_response(self, message: aiomqtt.Message) -> None:
        fut = self._pending_response
        if fut is None or fut.done():
            # No call is currently waiting -- an unsolicited/late message,
            # or a message that arrived after we already timed out. Nothing
            # sane to do with it.
            logger.debug("dynsec response received with nothing awaiting it, ignoring")
            return
        try:
            payload = json.loads(message.payload)
        except (json.JSONDecodeError, TypeError):
            fut.set_exception(DynsecError("malformed dynsec response payload"))
            return
        fut.set_result(payload)

    # -- low level request/response -------------------------------------------
    async def _send_command(self, command: dict[str, Any], timeout: float = 5.0) -> dict:
        async with self._call_lock:
            if self._client is None:
                await self.connect()
            assert self._client is not None

            # CorrelationData/ResponseTopic are set for protocol correctness
            # and forward compatibility, but are NOT relied upon for
            # matching -- see the module docstring for why.
            props = Properties(PacketTypes.PUBLISH)
            props.CorrelationData = uuid.uuid4().hex.encode("utf-8")
            props.ResponseTopic = DYNSEC_RESPONSE_TOPIC

            loop = asyncio.get_event_loop()
            fut: "asyncio.Future[dict]" = loop.create_future()
            self._pending_response = fut

            body = json.dumps({"commands": [command]})
            try:
                await self._client.publish(
                    DYNSEC_COMMAND_TOPIC, payload=body, qos=1, properties=props
                )
                try:
                    response = await asyncio.wait_for(fut, timeout=timeout)
                except asyncio.TimeoutError as exc:
                    raise DynsecTimeout(
                        f"Timed out waiting for dynsec response to {command.get('command')}"
                    ) from exc
            finally:
                self._pending_response = None

        for r in response.get("responses", []):
            error = r.get("error")
            if error:
                raise DynsecError(f"{command.get('command')} failed: {error}")
        return response

    # -- idempotent role setup -------------------------------------------------
    async def ensure_device_role(self) -> None:
        if self._role_ready:
            return
        try:
            await self._send_command(
                {
                    "command": "createRole",
                    "rolename": DEVICE_ROLE_NAME,
                    "acls": _DEVICE_ROLE_ACLS,
                }
            )
            logger.info("dynsec role %s created", DEVICE_ROLE_NAME)
        except DynsecError as exc:
            if not _error_is_already_exists(str(exc)):
                raise
            logger.info("dynsec role %s already exists, leaving as-is", DEVICE_ROLE_NAME)
        self._role_ready = True

    # -- device client management -----------------------------------------------
    async def create_device_client(self, device_key: str, password: str) -> None:
        """Create (or reset) the MQTT credentials for a freshly-claimed
        device, and ensure it holds the shared genmon-device role."""
        await self.ensure_device_role()
        try:
            await self._send_command(
                {
                    "command": "createClient",
                    "username": device_key,
                    "clientid": device_key,
                    "password": password,
                    "roles": [{"rolename": DEVICE_ROLE_NAME}],
                }
            )
            logger.info("dynsec client %s created", device_key)
            return
        except DynsecError as exc:
            if not _error_is_already_exists(str(exc)):
                raise
        # Client already existed (e.g. re-claim after an unclean unclaim) --
        # reset its password and make sure the role is attached.
        await self._send_command(
            {"command": "setClientPassword", "username": device_key, "password": password}
        )
        try:
            await self._send_command(
                {
                    "command": "addClientRole",
                    "username": device_key,
                    "rolename": DEVICE_ROLE_NAME,
                }
            )
        except DynsecError as exc:
            # Empirically verified against a live Mosquitto 2.0.18 broker:
            # addClientRole on a client that already holds the role does NOT
            # return a descriptive message -- it returns the generic
            # "Internal error". There is no more specific string to match
            # here; treat it as the (harmless) already-has-role case.
            if "internal error" not in str(exc).lower():
                raise
        logger.info("dynsec client %s re-provisioned (already existed)", device_key)

    async def delete_device_client(self, device_key: str) -> None:
        try:
            await self._send_command({"command": "deleteClient", "username": device_key})
            logger.info("dynsec client %s deleted", device_key)
        except DynsecError as exc:
            if "not found" in str(exc).lower() or "does not exist" in str(exc).lower():
                logger.info("dynsec client %s already absent", device_key)
                return
            raise


dynsec_client = MosquittoDynsecClient()
