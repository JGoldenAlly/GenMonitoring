"""Background task that keeps run-session commands alive on the wire.

The field agent enforces its own local deadman timer using the `ttl_seconds`
of the last command/renewal it received -- if it stops hearing from us it
will fail safe and stop the generator. This loop is what makes an
operator's overall run session (bounded by `current_command_expires_at`)
actually last that long: every 30s it republishes a `renew_session` for
every generator that's still supposed to be running, and once the
session's real expiry passes it publishes a final `stop_session` and clears
the generator's desired state.
"""
import asyncio
import logging
from datetime import datetime, timezone

from sqlalchemy import select

from app.config import settings
from app.database import AsyncSessionLocal
from app.models import Device, Generator, GeneratorCommand
from app.services.mqtt_publisher import build_command_payload, mqtt_publisher

logger = logging.getLogger("genmon.session_renewal")

POLL_INTERVAL_SECONDS = 30
_TERMINAL_STATUSES = {"expired", "superseded", "cancelled", "acknowledged"}


async def _renew_active_sessions() -> None:
    now = datetime.now(timezone.utc)
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(Generator).where(
                Generator.current_desired_state == "run",
                Generator.current_command_expires_at.is_not(None),
            )
        )
        generators = list(result.scalars().all())

        for generator in generators:
            device = await db.get(Device, generator.device_id)
            device_key = device.device_key if device else None
            if device_key is None:
                continue

            if generator.current_command_expires_at and generator.current_command_expires_at > now:
                if generator.current_command_id is None:
                    continue
                payload = build_command_payload(
                    command_id=str(generator.current_command_id),
                    command_type="renew_session",
                    ttl_seconds=settings.DEFAULT_COMMAND_TTL_SECONDS,
                )
                try:
                    await mqtt_publisher.publish_command(device_key, payload)
                    logger.debug(
                        "renewed session for generator=%s device=%s", generator.id, device_key
                    )
                except Exception:  # noqa: BLE001 - keep the loop alive regardless
                    logger.exception(
                        "failed to renew session for generator=%s device=%s",
                        generator.id,
                        device_key,
                    )
            else:
                # Session window has elapsed -- tell the agent to stop and
                # clear our own bookkeeping.
                payload = build_command_payload(
                    command_id=str(generator.current_command_id) if generator.current_command_id else "expired",
                    command_type="stop_session",
                    ttl_seconds=settings.DEFAULT_COMMAND_TTL_SECONDS,
                )
                try:
                    await mqtt_publisher.publish_command(device_key, payload)
                except Exception:  # noqa: BLE001
                    logger.exception(
                        "failed to publish expiry stop_session for generator=%s device=%s",
                        generator.id,
                        device_key,
                    )

                if generator.current_command_id is not None:
                    command = await db.get(GeneratorCommand, generator.current_command_id)
                    if command is not None and command.status not in _TERMINAL_STATUSES:
                        command.status = "expired"

                generator.current_desired_state = None
                generator.current_command_id = None
                generator.current_command_expires_at = None
                logger.info(
                    "run session expired for generator=%s device=%s -- stop published",
                    generator.id,
                    device_key,
                )

        await db.commit()


async def session_renewal_loop() -> None:
    logger.info("session_renewal_loop started (interval=%ss)", POLL_INTERVAL_SECONDS)
    while True:
        try:
            await _renew_active_sessions()
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - never let one bad tick kill the loop
            logger.exception("session_renewal tick failed")
        await asyncio.sleep(POLL_INTERVAL_SECONDS)
