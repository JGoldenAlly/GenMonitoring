"""Async MQTT publisher used exclusively to publish command messages to
`genmon/{device_key}/cmd`. The api NEVER subscribes to device telemetry --
that is the bridge's job. This module owns exactly one long-lived
connection, opened once at FastAPI startup (see app.main lifespan) and
reused for every publish, with best-effort reconnect on failure.
"""
import asyncio
import json
import logging
import os
from contextlib import AsyncExitStack
from datetime import datetime, timezone
from typing import Any, Optional

import aiomqtt

from app.config import settings

logger = logging.getLogger("genmon.mqtt_publisher")


def _build_tls_params() -> Optional["aiomqtt.TLSParameters"]:
    if not settings.MQTT_TLS:
        return None
    # Default TLSParameters() uses the system CA trust store and standard
    # TLS verification. If the broker uses a private CA, point
    # `ca_certs` at it via a future env var -- left as the simplest
    # correct default for now.
    return aiomqtt.TLSParameters()


class MqttPublisher:
    def __init__(self) -> None:
        self._client: Optional[aiomqtt.Client] = None
        self._stack: Optional[AsyncExitStack] = None
        self._lock = asyncio.Lock()
        self._connected = False

    async def connect(self) -> None:
        async with self._lock:
            await self._connect_locked()

    async def _connect_locked(self) -> None:
        if self._connected and self._client is not None:
            return
        stack = AsyncExitStack()
        client = aiomqtt.Client(
            hostname=settings.MQTT_HOST,
            port=settings.MQTT_PORT,
            username=settings.MQTT_USERNAME,
            password=settings.MQTT_PASSWORD,
            identifier=f"genmon-api-publisher-{os.getpid()}",
            tls_params=_build_tls_params(),
        )
        try:
            await stack.enter_async_context(client)
        except Exception:
            await stack.aclose()
            raise
        self._stack = stack
        self._client = client
        self._connected = True
        logger.info(
            "mqtt_publisher connected to %s:%s (tls=%s)",
            settings.MQTT_HOST,
            settings.MQTT_PORT,
            settings.MQTT_TLS,
        )

    async def disconnect(self) -> None:
        async with self._lock:
            if self._stack is not None:
                try:
                    await self._stack.aclose()
                except Exception:  # pragma: no cover - best effort teardown
                    logger.exception("error closing mqtt_publisher connection")
            self._stack = None
            self._client = None
            self._connected = False

    async def _reset_locked(self) -> None:
        if self._stack is not None:
            try:
                await self._stack.aclose()
            except Exception:  # pragma: no cover
                pass
        self._stack = None
        self._client = None
        self._connected = False

    async def publish_command(
        self,
        device_key: str,
        payload: dict[str, Any],
        qos: int = 1,
        retain: bool = True,
    ) -> None:
        """Publish a retained command message to genmon/{device_key}/cmd."""
        topic = f"genmon/{device_key}/cmd"
        data = json.dumps(payload)

        async with self._lock:
            last_exc: Exception | None = None
            for attempt in range(2):
                try:
                    await self._connect_locked()
                    assert self._client is not None
                    await self._client.publish(topic, payload=data, qos=qos, retain=retain)
                    return
                except (aiomqtt.MqttError, OSError) as exc:
                    last_exc = exc
                    logger.warning(
                        "mqtt publish attempt %s failed for %s: %s -- reconnecting",
                        attempt,
                        topic,
                        exc,
                    )
                    await self._reset_locked()
            if last_exc is not None:
                raise last_exc


def build_command_payload(
    command_id: str,
    command_type: str,
    ttl_seconds: int,
) -> dict[str, Any]:
    """Builds the exact payload shape the field agent expects on
    genmon/{device_key}/cmd, per the shared platform contract."""
    return {
        "command_id": command_id,
        "type": command_type,
        "session_id": command_id,
        "ttl_seconds": ttl_seconds,
        "issued_at": datetime.now(timezone.utc).isoformat(),
    }


mqtt_publisher = MqttPublisher()
