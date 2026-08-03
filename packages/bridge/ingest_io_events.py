"""Handles genmon/{device_key}/io -- GPIO IN1/OUT1 state transitions.

Resolves the reporting device_key to the (at most one, per the DB's
unique-partial-index constraint) start/stop-enabled generator, correlates the
event against the most recent live `run` command, computes
`matches_commanded`/`mismatch_type`, and inserts the audit row.
"""
import logging

import asyncpg
from pydantic import ValidationError

from schemas import IoEventPayload

logger = logging.getLogger("bridge.ingest_io_events")

_ACTIVE_RUN_COMMAND_QUERY = """
    SELECT id
    FROM generator_commands
    WHERE generator_id = $1
      AND command_type = 'run'
      AND status IN ('acknowledged', 'delivered')
      AND created_at <= $2
      AND (expires_at IS NULL OR expires_at >= $2)
    ORDER BY created_at DESC
    LIMIT 1
"""


async def handle(pool: asyncpg.Pool, payload_bytes: bytes) -> None:
    try:
        payload = IoEventPayload.model_validate_json(payload_bytes)
    except ValidationError as exc:
        logger.warning("Invalid IO event payload, skipping: %s", exc)
        return

    try:
        async with pool.acquire() as conn:
            generator_id = await conn.fetchval(
                """
                SELECT g.id
                FROM generators g
                JOIN devices d ON d.id = g.device_id
                WHERE d.device_key = $1
                  AND g.start_stop_enabled = true
                """,
                payload.device_key,
            )
            if generator_id is None:
                logger.warning(
                    "No start/stop-enabled generator found for device_key=%s; "
                    "skipping IO event (channel=%s, state=%s)",
                    payload.device_key, payload.channel, payload.state,
                )
                return

            correlated_command_id = await conn.fetchval(
                _ACTIVE_RUN_COMMAND_QUERY, generator_id, payload.observed_at_utc
            )
            has_active_run_command = correlated_command_id is not None

            matches_commanded, mismatch_type = _evaluate_match(
                payload.channel, payload.state, has_active_run_command
            )

            await conn.execute(
                """
                INSERT INTO generator_io_events (
                    time, device_key, generator_id, channel, state,
                    correlated_command_id, matches_commanded, mismatch_type
                )
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
                """,
                payload.observed_at_utc,
                payload.device_key,
                generator_id,
                payload.channel,
                payload.state,
                correlated_command_id,
                matches_commanded,
                mismatch_type,
            )

            if mismatch_type is not None:
                logger.warning(
                    "IO event mismatch detected: device_key=%s generator_id=%s "
                    "channel=%s state=%s mismatch_type=%s",
                    payload.device_key, generator_id,
                    payload.channel, payload.state, mismatch_type,
                )
    except Exception:  # noqa: BLE001 - never crash the worker on one bad message
        logger.exception(
            "Failed to process IO event for device_key=%s channel=%s",
            payload.device_key, payload.channel,
        )


def _evaluate_match(channel: str, state: bool, has_active_run_command: bool):
    """Returns (matches_commanded, mismatch_type) per the design contract.

    Only IN1 transitions can disagree with what GenMonitoring commanded --
    OUT1 is the output *we* drive, so it always matches by construction.
    """
    if channel == "IN1":
        if state is True and not has_active_run_command:
            # Closed/commanded-to-run with no GenMonitoring run command active.
            return False, "external_start"
        if state is False and has_active_run_command:
            # Opened/stopped while we believe a run command should still hold.
            return False, "unexpected_stop"

    return True, None
