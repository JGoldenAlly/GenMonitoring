"""Postgres connection pool creation with startup retry.

The bridge talks straight asyncpg SQL (no ORM) -- it is a thin,
dependency-light worker, deliberately separate from the api's SQLAlchemy
models even though it reads/writes the same tables.
"""
import asyncio
import logging
from typing import Optional

import asyncpg

logger = logging.getLogger("bridge.db")

_INITIAL_BACKOFF_SECONDS = 2
_MAX_BACKOFF_SECONDS = 60
_MAX_ATTEMPTS = 10


def normalize_dsn(dsn: str) -> str:
    """asyncpg's DSN parser does not accept the `+asyncpg` driver suffix
    that SQLAlchemy-style DATABASE_URLs use, so strip it if present."""
    prefix = "postgresql+asyncpg://"
    if dsn.startswith(prefix):
        return "postgresql://" + dsn[len(prefix):]
    return dsn


async def create_pool_with_retry(dsn: str) -> asyncpg.Pool:
    """Create the asyncpg pool, retrying with exponential backoff
    (2s -> 60s cap, ~10 attempts) before giving up.

    Raises RuntimeError if the pool cannot be established after all
    attempts are exhausted -- callers should treat that as fatal and exit
    non-zero so a container orchestrator restarts the process.
    """
    dsn = normalize_dsn(dsn)
    backoff = _INITIAL_BACKOFF_SECONDS
    last_error: Optional[Exception] = None

    for attempt in range(1, _MAX_ATTEMPTS + 1):
        try:
            pool = await asyncpg.create_pool(dsn, min_size=1, max_size=5)
            logger.info(
                "Connected to Postgres (attempt %d/%d)", attempt, _MAX_ATTEMPTS
            )
            await _log_timescaledb_status(pool)
            return pool
        except Exception as exc:  # noqa: BLE001 - want to retry on anything
            last_error = exc
            if attempt >= _MAX_ATTEMPTS:
                break
            logger.warning(
                "Postgres connection attempt %d/%d failed (%s); retrying in %ds",
                attempt, _MAX_ATTEMPTS, exc, backoff,
            )
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, _MAX_BACKOFF_SECONDS)

    logger.error(
        "Exhausted %d attempts to connect to Postgres; giving up", _MAX_ATTEMPTS
    )
    raise RuntimeError(
        f"Could not connect to Postgres after {_MAX_ATTEMPTS} attempts"
    ) from last_error


async def _log_timescaledb_status(pool: asyncpg.Pool) -> None:
    """Detect whether TimescaleDB is installed/active so we can log it.

    The bridge does not require TimescaleDB -- `readings` and
    `generator_io_events` are expected to already exist (as plain tables or
    hypertables) via the api's Alembic migrations. This is purely an
    informational check, mirroring the AetherLynk bridge's
    pg_available_extensions/pg_extension probe, so operators can see at a
    glance whether they're running degraded (plain Postgres) or with
    hypertables.
    """
    try:
        async with pool.acquire() as conn:
            installed = await conn.fetchval(
                "SELECT extname FROM pg_extension WHERE extname = 'timescaledb'"
            )
            if installed:
                logger.info("TimescaleDB extension is installed and active.")
                return

            available = await conn.fetchval(
                "SELECT name FROM pg_available_extensions WHERE name = 'timescaledb'"
            )
            if available:
                logger.warning(
                    "TimescaleDB extension is available but not installed in "
                    "this database; continuing with plain Postgres "
                    "(hypertable features degraded)."
                )
            else:
                logger.warning(
                    "TimescaleDB extension is not available on this Postgres "
                    "instance; continuing with plain Postgres."
                )
    except Exception:  # noqa: BLE001 - purely informational, never fatal
        logger.exception("Could not determine TimescaleDB availability (non-fatal)")
