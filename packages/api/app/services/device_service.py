"""Shared ingest logic for device I/O transitions and command acks.

Both the primary MQTT ingest path (owned by the `bridge` package, which
writes directly into this same database) and the HTTP heartbeat backup
path (`POST /devices/{device_key}/heartbeat`, used when MQTT connectivity
is degraded) need to record `generator_io_events` and apply command acks
identically. Everything reachable from the api process funnels through the
functions below so the logic exists in exactly one place.
"""
import logging
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Device, Generator, GeneratorCommand
from app.schemas.devices import CommandAckIn, IOEventIn

logger = logging.getLogger("genmon.device_service")

_TERMINAL_STATUSES = {"expired", "superseded", "cancelled", "acknowledged"}


def _find_generator_for_channel(generators: list[Generator], channel: str) -> Generator | None:
    for g in generators:
        if g.gpio_in_channel == channel or g.gpio_out_channel == channel:
            return g
    return None


def _classify(generator: Generator | None, state: bool) -> tuple[bool | None, str | None]:
    """Given the generator's currently-desired state, decide whether this
    IN1/OUT1 transition matches what we commanded, and if not, what kind of
    mismatch it looks like.

    - state True ("on"/"running") matches iff desired_state == "run"
    - state False ("off"/"stopped") matches iff desired_state in (None, "stop")

    external_start: it came on when we did not command it to run.
    unexpected_stop: it went off while we still expect it to be running.
    """
    if generator is None:
        return None, None
    desired = generator.current_desired_state
    if state:
        matches = desired == "run"
        mismatch = None if matches else "external_start"
    else:
        matches = desired in (None, "stop")
        mismatch = None if matches else "unexpected_stop"
    return matches, mismatch


async def record_io_events(
    db: AsyncSession,
    device: Device,
    events: list[IOEventIn],
) -> None:
    if not events:
        return

    generators = list(device.generators) if device.generators is not None else []

    rows = []
    for evt in events:
        generator = _find_generator_for_channel(generators, evt.channel)
        matches, mismatch = _classify(generator, evt.state)
        rows.append(
            {
                "time": evt.observed_at_utc,
                "device_key": device.device_key,
                "generator_id": str(generator.id) if generator else None,
                "channel": evt.channel,
                "state": evt.state,
                "correlated_command_id": str(generator.current_command_id)
                if generator and generator.current_command_id
                else None,
                "matches_commanded": matches,
                "mismatch_type": mismatch,
            }
        )

    for row in rows:
        await db.execute(
            text(
                """
                INSERT INTO generator_io_events
                    (time, device_key, generator_id, channel, state,
                     correlated_command_id, matches_commanded, mismatch_type)
                VALUES
                    (:time, :device_key, :generator_id::uuid, :channel, :state,
                     :correlated_command_id::uuid, :matches_commanded, :mismatch_type)
                """
            ),
            row,
        )
    await db.commit()


async def apply_command_ack(
    db: AsyncSession,
    device: Device,
    ack: CommandAckIn,
) -> None:
    try:
        command_uuid = UUID(ack.command_id)
    except ValueError:
        logger.warning("received command_ack with non-uuid command_id=%s", ack.command_id)
        return

    command = await db.get(GeneratorCommand, command_uuid)
    if command is None:
        logger.warning("command_ack for unknown command_id=%s", ack.command_id)
        return

    generator = await db.get(Generator, command.generator_id)
    if generator is None or generator.device_id != device.id:
        logger.warning(
            "command_ack for command %s does not belong to device %s",
            ack.command_id,
            device.device_key,
        )
        return

    now = datetime.now(timezone.utc)

    if ack.status in ("acknowledged", "completed"):
        if command.status not in _TERMINAL_STATUSES:
            command.status = "acknowledged"
        if command.acknowledged_at is None:
            command.acknowledged_at = now
        if ack.status == "completed" and command.command_type in ("stop", "cancel"):
            if generator.current_command_id == command.id:
                generator.current_desired_state = None
                generator.current_command_expires_at = None
                generator.current_command_id = None
    elif ack.status in ("failed", "expired"):
        if command.status not in _TERMINAL_STATUSES:
            command.status = "expired"
        if generator.current_command_id == command.id:
            generator.current_desired_state = None
            generator.current_command_expires_at = None
            generator.current_command_id = None

    await db.commit()
