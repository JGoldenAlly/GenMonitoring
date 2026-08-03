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
    (the plugin does not honor a custom ResponseTopic override, but we set
    one anyway since that's part of the documented MQTTv5 request/response
    pattern) with `{"responses": [{"command": "<name>", "error": "..."}]}`
    (no "error" key on success).
  * Because MQTT has no built-in request id, correlation is done via the
    MQTTv5 `CorrelationData` publish property: we generate a random token
    per outbound command, attach it as CorrelationData, and match it
    against the CorrelationData property on the incoming response message.
    This requires an MQTTv5 connection (dynsec administration itself is not
    v5-specific, but the request/response correlation pattern we use here
    is only clean with v5 properties).
  * We keep exactly one long-lived MQTTv5 connection open (subscribed to
    the response topic), created at FastAPI startup, and multiplex all
    dynsec admin calls (device claim/unclaim) over it via a
    correlation-id -> asyncio.Future map, with a ~5s timeout per call.

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
        self._pending: dict[str, "asyncio.Future[dict]"] = {}
        self._lock = asyncio.Lock()
        self._role_ready = False

    # -- connection lifecycle -------------------------------------------------
    async def connect(self) -> None:
        async with self._lock:
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
        async with self._lock:
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
            for fut in self._pending.values():
                if not fut.done():
                    fut.set_exception(DynsecError("dynsec client disconnected"))
            self._pending.clear()

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
        props = getattr(message, "properties", None)
        corr = getattr(props, "CorrelationData", None) if props else None
        if corr is None:
            return
        corr_id = corr.decode("utf-8") if isinstance(corr, (bytes, bytearray)) else str(corr)
        fut = self._pending.pop(corr_id, None)
        if fut is None or fut.done():
            return
        try:
            payload = json.loads(message.payload)
        except (json.JSONDecodeError, TypeError):
            fut.set_exception(DynsecError("malformed dynsec response payload"))
            return
        fut.set_result(payload)

    # -- low level request/response -------------------------------------------
    async def _send_command(self, command: dict[str, Any], timeout: float = 5.0) -> dict:
        if self._client is None:
            await self.connect()
        assert self._client is not None

        corr_id = uuid.uuid4().hex
        props = Properties(PacketTypes.PUBLISH)
        props.CorrelationData = corr_id.encode("utf-8")
        props.ResponseTopic = DYNSEC_RESPONSE_TOPIC

        loop = asyncio.get_event_loop()
        fut: "asyncio.Future[dict]" = loop.create_future()
        self._pending[corr_id] = fut

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
            self._pending.pop(corr_id, None)

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
            if "already has role" not in str(exc).lower():
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
