"""GenMonitoring bridge -- pure asyncio MQTT -> Postgres worker.

No HTTP surface at all. Connects to the MQTT broker (EMQX) over TLS,
subscribes to three explicit topic filters, validates each message with the
matching pydantic schema, and writes into Postgres via asyncpg. Modeled on
AetherLynk's bridge: an outer reconnect loop with exponential backoff around
the MQTT session, and per-message try/except so a single bad payload or a
transient DB hiccup never takes the whole worker down.
"""
import asyncio
import logging
import ssl
from typing import Optional

import aiomqtt

import config
import db
import ingest_command_ack
import ingest_io_events
import ingest_telemetry

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("bridge.main")

_RECONNECT_INITIAL_BACKOFF_SECONDS = 1
_RECONNECT_MAX_BACKOFF_SECONDS = 60


def _build_tls_context() -> Optional[ssl.SSLContext]:
    if not config.settings.MQTT_TLS:
        return None
    return ssl.create_default_context()


def _build_topics(prefix: str) -> tuple[str, str, str]:
    """Three explicit subscriptions -- deliberately not one broad wildcard,
    so the bridge's own broker ACL stays legible.

    Note the ack topic is genmon/{device_key}/cmd/ack, a single fixed
    "cmd/ack" segment (the agent does not publish acks per-channel).
    """
    return (
        f"{prefix}/+/data",
        f"{prefix}/+/io",
        f"{prefix}/+/cmd/ack",
    )


async def _dispatch(pool, prefix: str, message: "aiomqtt.Message") -> None:
    topic = str(message.topic)
    parts = topic.split("/")

    try:
        if len(parts) == 3 and parts[0] == prefix and parts[2] == "data":
            await ingest_telemetry.handle(pool, message.payload)
        elif len(parts) == 3 and parts[0] == prefix and parts[2] == "io":
            await ingest_io_events.handle(pool, message.payload)
        elif (
            len(parts) == 4
            and parts[0] == prefix
            and parts[2] == "cmd"
            and parts[3] == "ack"
        ):
            await ingest_command_ack.handle(pool, message.payload)
        else:
            logger.warning("Received message on unrecognized topic: %s", topic)
    except Exception:  # noqa: BLE001 - never let one message kill the loop
        logger.exception("Unhandled error dispatching message on topic %s", topic)


async def _run_mqtt_loop(pool) -> None:
    prefix = config.settings.MQTT_TOPIC_PREFIX
    data_topic, io_topic, ack_topic = _build_topics(prefix)
    tls_context = _build_tls_context()
    backoff = _RECONNECT_INITIAL_BACKOFF_SECONDS

    while True:
        try:
            async with aiomqtt.Client(
                hostname=config.settings.MQTT_HOST,
                port=config.settings.MQTT_PORT,
                username=config.settings.MQTT_USERNAME,
                password=config.settings.MQTT_PASSWORD,
                tls_context=tls_context,
            ) as client:
                logger.info(
                    "Connected to MQTT broker %s:%s (tls=%s)",
                    config.settings.MQTT_HOST,
                    config.settings.MQTT_PORT,
                    config.settings.MQTT_TLS,
                )

                await client.subscribe(data_topic, qos=1)
                await client.subscribe(io_topic, qos=1)
                await client.subscribe(ack_topic, qos=1)
                logger.info(
                    "Subscribed to topics: %s | %s | %s",
                    data_topic, io_topic, ack_topic,
                )

                # Reset backoff once we have a healthy session.
                backoff = _RECONNECT_INITIAL_BACKOFF_SECONDS

                async for message in client.messages:
                    await _dispatch(pool, prefix, message)

        except aiomqtt.MqttError as exc:
            logger.warning(
                "MQTT connection error: %s (reconnecting in %ds)", exc, backoff
            )
        except Exception:  # noqa: BLE001 - keep the worker alive no matter what
            logger.exception(
                "Unexpected error in MQTT loop (reconnecting in %ds)", backoff
            )

        await asyncio.sleep(backoff)
        backoff = min(backoff * 2, _RECONNECT_MAX_BACKOFF_SECONDS)


async def main() -> None:
    logger.info("Starting GenMonitoring bridge worker")

    try:
        pool = await db.create_pool_with_retry(config.settings.DATABASE_URL)
    except RuntimeError:
        logger.error(
            "Fatal: could not establish a database connection pool at startup. "
            "Exiting non-zero so the container orchestrator restarts us."
        )
        raise SystemExit(1)

    try:
        await _run_mqtt_loop(pool)
    finally:
        await pool.close()


if __name__ == "__main__":
    asyncio.run(main())
