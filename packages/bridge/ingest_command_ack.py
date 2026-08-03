"""Handles genmon/{device_key}/cmd/ack -- command ack/result from the agent.

The ack is authoritative for `generator_commands.status` only. It is
deliberately NOT turned into a second `generator_io_events` insert: the agent
separately publishes IN1/OUT1 transitions to genmon/{device_key}/io, and that
is the single source of truth for the IO-event audit trail. Treating the ack
as a second IO-event source would risk double-inserting the same physical
transition. We log the ack's reported out1_state/in1_state for observability
instead.
"""
import logging

import asyncpg
from pydantic import ValidationError

from schemas import CommandAckPayload

logger = logging.getLogger("bridge.ingest_command_ack")

# Maps the agent-reported ack result to the generator_commands.status value.
_RESULT_TO_STATUS = {
    "applied": "acknowledged",
    "expired_no_renewal": "expired",
    "rejected": "cancelled",
}


async def handle(pool: asyncpg.Pool, payload_bytes: bytes) -> None:
    try:
        payload = CommandAckPayload.model_validate_json(payload_bytes)
    except ValidationError as exc:
        logger.warning("Invalid command ack payload, skipping: %s", exc)
        return

    new_status = _RESULT_TO_STATUS[payload.result]

    try:
        async with pool.acquire() as conn:
            result = await conn.execute(
                """
                UPDATE generator_commands
                SET status = $1, acknowledged_at = $2
                WHERE id = $3
                """,
                new_status,
                payload.applied_at,
                payload.command_id,
            )

            # asyncpg's execute() returns a tag like "UPDATE 1" / "UPDATE 0".
            if result == "UPDATE 0":
                logger.warning(
                    "Command ack for unknown command_id=%s (session_id=%s); "
                    "no matching generator_commands row to update",
                    payload.command_id, payload.session_id,
                )
            else:
                logger.info(
                    "Command %s ack processed: result=%s -> status=%s "
                    "(session_id=%s, out1_state=%s, in1_state=%s)",
                    payload.command_id, payload.result, new_status,
                    payload.session_id, payload.out1_state, payload.in1_state,
                )
    except Exception:  # noqa: BLE001 - never crash the worker on one bad message
        logger.exception(
            "Failed to update generator_commands for command_id=%s",
            payload.command_id,
        )
