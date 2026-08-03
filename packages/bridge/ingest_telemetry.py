"""Handles genmon/{device_key}/data -- telemetry readings."""
import logging

import asyncpg
from pydantic import ValidationError

from schemas import TelemetryPayload

logger = logging.getLogger("bridge.ingest_telemetry")


async def handle(pool: asyncpg.Pool, payload_bytes: bytes) -> None:
    try:
        payload = TelemetryPayload.model_validate_json(payload_bytes)
    except ValidationError as exc:
        logger.warning("Invalid telemetry payload, skipping: %s", exc)
        return

    try:
        async with pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute(
                    """
                    INSERT INTO readings (
                        time, device_key, register_address, register_type,
                        register_friendly_name, value, unit
                    )
                    VALUES ($1, $2, $3, $4, $5, $6, $7)
                    """,
                    payload.timestamp_utc,
                    payload.device_key,
                    payload.register_address,
                    payload.register_type,
                    payload.register_friendly_name,
                    payload.value,
                    payload.unit,
                )
                await conn.execute(
                    "UPDATE devices SET last_seen_at = now() WHERE device_key = $1",
                    payload.device_key,
                )
    except Exception:  # noqa: BLE001 - never crash the worker on one bad message
        logger.exception(
            "Failed to write telemetry reading for device_key=%s register=%s",
            payload.device_key, payload.register_address,
        )
