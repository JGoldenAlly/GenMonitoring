"""FastAPI application entrypoint for GenMonitoring's api package."""
import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.database import AsyncSessionLocal, engine
from app.routers import (
    apikeys,
    auth,
    commands,
    devices,
    generators,
    readings,
    templates,
    users,
)
from app.services.mosquitto_dynsec import dynsec_client
from app.services.mqtt_publisher import mqtt_publisher
from app.services.session_renewal import session_renewal_loop
from app.services.template_seed import seed_builtin_templates

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("genmon.main")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # --- startup ---
    logger.info("GenMonitoring api starting up")

    async with AsyncSessionLocal() as db:
        await seed_builtin_templates(db)
    logger.info("builtin modbus profile templates seeded")

    try:
        await mqtt_publisher.connect()
    except Exception:  # noqa: BLE001
        logger.exception(
            "could not connect mqtt_publisher at startup -- will retry lazily on first publish"
        )

    try:
        await dynsec_client.connect()
        await dynsec_client.ensure_device_role()
        await dynsec_client.ensure_admin_publish_role()
    except Exception:  # noqa: BLE001
        logger.exception(
            "could not connect to mosquitto dynamic-security plugin at startup -- device "
            "claim/unclaim will retry the connection lazily"
        )

    renewal_task = asyncio.create_task(session_renewal_loop(), name="session-renewal-loop")

    yield

    # --- shutdown ---
    logger.info("GenMonitoring api shutting down")
    renewal_task.cancel()
    try:
        await renewal_task
    except asyncio.CancelledError:
        pass

    await mqtt_publisher.disconnect()
    await dynsec_client.disconnect()
    await engine.dispose()


app = FastAPI(
    title="GenMonitoring API",
    description="Ally Energy's generator monitoring platform -- device provisioning, "
    "Modbus register configuration, telemetry access, and remote start/stop control.",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(users.router)
app.include_router(apikeys.router)
app.include_router(devices.router)
app.include_router(generators.router)
app.include_router(templates.router)
app.include_router(readings.router)
app.include_router(commands.router)


@app.get("/health", tags=["health"])
async def health() -> dict:
    return {"status": "ok", "service": "genmonitoring-api"}
