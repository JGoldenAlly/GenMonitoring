import logging
from datetime import datetime, timedelta, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.dependencies import get_current_user, require_role
from app.models import Device, Generator, GeneratorCommand, User
from app.schemas.commands import (
    CommandCreate,
    CommandListOut,
    CommandOut,
    CurrentCommandOut,
    InhibitRequest,
    IOStateOut,
)
from app.services.mqtt_publisher import build_command_payload, mqtt_publisher

logger = logging.getLogger("genmon.commands")

router = APIRouter(tags=["commands"])

# Commands still "in flight" from the api's point of view -- a new command
# supersedes any of these.
ACTIVE_COMMAND_STATUSES = ("pending", "delivered", "acknowledged")

_COMMAND_TYPE_TO_MQTT_TYPE = {
    "run": "start_session",
    "stop": "stop_session",
    "cancel": "stop_session",
}


async def _get_generator_or_404(generator_id: UUID, db: AsyncSession) -> Generator:
    generator = await db.get(Generator, generator_id)
    if generator is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Generator not found")
    return generator


async def _get_device_key(generator: Generator, db: AsyncSession) -> str:
    device = await db.get(Device, generator.device_id)
    if device is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Device not found")
    return device.device_key


async def _publish_command(device_key: str, command_id: UUID, mqtt_type: str) -> None:
    ttl = min(settings.DEFAULT_COMMAND_TTL_SECONDS, settings.MAX_COMMAND_TTL_SECONDS)
    payload = build_command_payload(command_id=str(command_id), command_type=mqtt_type, ttl_seconds=ttl)
    try:
        await mqtt_publisher.publish_command(device_key, payload)
    except Exception:  # noqa: BLE001 - never fail the HTTP request over a transient MQTT hiccup
        logger.exception(
            "failed to publish %s command %s to device %s -- the session_renewal "
            "loop and /config fallback will reconcile this",
            mqtt_type,
            command_id,
            device_key,
        )


@router.post(
    "/generators/{generator_id}/commands",
    response_model=CommandOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_role("admin", "operator"))],
)
async def create_command(
    generator_id: UUID,
    body: CommandCreate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> GeneratorCommand:
    generator = await _get_generator_or_404(generator_id, db)

    if not generator.start_stop_enabled:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Start/stop control is not enabled for this generator",
        )

    if body.command_type == "run":
        if not body.reason or not body.reason.strip():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="reason is required to start a generator",
            )
        if generator.control_inhibited:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Control is inhibited for this generator: "
                f"{generator.control_inhibited_reason or 'no reason given'}",
            )

    device_key = await _get_device_key(generator, db)

    expires_at: datetime | None = None
    if body.command_type == "run":
        duration = min(
            body.duration_minutes or settings.DEFAULT_RUN_SESSION_MINUTES,
            generator.max_run_session_minutes,
        )
        expires_at = datetime.now(timezone.utc) + timedelta(minutes=duration)

    # Supersede any command still "in flight" for this generator.
    result = await db.execute(
        select(GeneratorCommand).where(
            GeneratorCommand.generator_id == generator.id,
            GeneratorCommand.status.in_(ACTIVE_COMMAND_STATUSES),
        )
    )
    previous_commands = list(result.scalars().all())

    new_command = GeneratorCommand(
        generator_id=generator.id,
        requested_by_user_id=user.id,
        command_type=body.command_type,
        reason=body.reason,
        status="pending",
        expires_at=expires_at,
    )
    db.add(new_command)
    await db.flush()  # assign new_command.id

    for prev in previous_commands:
        prev.status = "superseded"
        prev.superseded_by_command_id = new_command.id

    generator.current_command_id = new_command.id
    if body.command_type == "run":
        generator.current_desired_state = "run"
        generator.current_command_expires_at = expires_at
    else:
        generator.current_desired_state = "stop"
        generator.current_command_expires_at = None

    await db.commit()
    await db.refresh(new_command)

    mqtt_type = _COMMAND_TYPE_TO_MQTT_TYPE[body.command_type]
    await _publish_command(device_key, new_command.id, mqtt_type)

    return new_command


@router.get("/generators/{generator_id}/commands", response_model=CommandListOut)
async def list_commands(
    generator_id: UUID,
    limit: int = Query(default=50, le=200, gt=0),
    offset: int = Query(default=0, ge=0),
    _user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> CommandListOut:
    await _get_generator_or_404(generator_id, db)

    total_result = await db.execute(
        select(func.count()).select_from(GeneratorCommand).where(
            GeneratorCommand.generator_id == generator_id
        )
    )
    total = total_result.scalar_one()

    result = await db.execute(
        select(GeneratorCommand)
        .where(GeneratorCommand.generator_id == generator_id)
        .order_by(GeneratorCommand.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    items = list(result.scalars().all())

    return CommandListOut(items=items, total=total, limit=limit, offset=offset)


@router.get("/generators/{generator_id}/commands/current", response_model=CurrentCommandOut)
async def get_current_command(
    generator_id: UUID,
    _user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> CurrentCommandOut:
    generator = await _get_generator_or_404(generator_id, db)

    last_command = None
    if generator.current_command_id is not None:
        last_command = await db.get(GeneratorCommand, generator.current_command_id)
    if last_command is None:
        result = await db.execute(
            select(GeneratorCommand)
            .where(GeneratorCommand.generator_id == generator_id)
            .order_by(GeneratorCommand.created_at.desc())
            .limit(1)
        )
        last_command = result.scalar_one_or_none()

    io_result = await db.execute(
        text(
            """
            SELECT DISTINCT ON (channel)
                channel, state, time, matches_commanded, mismatch_type
            FROM generator_io_events
            WHERE generator_id = :generator_id::uuid
            ORDER BY channel, time DESC
            """
        ),
        {"generator_id": str(generator_id)},
    )
    io_states = [IOStateOut(**row._mapping) for row in io_result.all()]

    return CurrentCommandOut(
        generator_id=generator.id,
        current_command_id=generator.current_command_id,
        current_desired_state=generator.current_desired_state,
        current_command_expires_at=generator.current_command_expires_at,
        control_inhibited=generator.control_inhibited,
        control_inhibited_reason=generator.control_inhibited_reason,
        last_command=last_command,
        io_states=io_states,
    )


@router.post(
    "/generators/{generator_id}/commands/{command_id}/cancel",
    response_model=CommandOut,
    dependencies=[Depends(require_role("admin", "operator"))],
)
async def cancel_command(
    generator_id: UUID,
    command_id: UUID,
    db: AsyncSession = Depends(get_db),
) -> GeneratorCommand:
    generator = await _get_generator_or_404(generator_id, db)
    command = await db.get(GeneratorCommand, command_id)
    if command is None or command.generator_id != generator.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Command not found")

    if command.status not in ACTIVE_COMMAND_STATUSES:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Command is already {command.status} and cannot be cancelled",
        )

    device_key = await _get_device_key(generator, db)

    command.status = "cancelled"
    if generator.current_command_id == command.id:
        generator.current_desired_state = None
        generator.current_command_id = None
        generator.current_command_expires_at = None

    await db.commit()
    await db.refresh(command)

    await _publish_command(device_key, command.id, "stop_session")

    return command


@router.post(
    "/generators/{generator_id}/inhibit",
    dependencies=[Depends(require_role("admin", "operator"))],
)
async def inhibit_generator(
    generator_id: UUID,
    body: InhibitRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    generator = await _get_generator_or_404(generator_id, db)
    generator.control_inhibited = True
    generator.control_inhibited_reason = body.reason
    generator.control_inhibited_by_user_id = user.id
    generator.control_inhibited_at = datetime.now(timezone.utc)
    await db.commit()
    return {
        "generator_id": str(generator.id),
        "control_inhibited": True,
        "control_inhibited_reason": generator.control_inhibited_reason,
        "control_inhibited_by_user_id": str(generator.control_inhibited_by_user_id),
        "control_inhibited_at": generator.control_inhibited_at.isoformat(),
    }


@router.delete(
    "/generators/{generator_id}/inhibit",
    dependencies=[Depends(require_role("admin"))],
)
async def uninhibit_generator(
    generator_id: UUID,
    db: AsyncSession = Depends(get_db),
) -> dict:
    generator = await _get_generator_or_404(generator_id, db)
    generator.control_inhibited = False
    generator.control_inhibited_reason = None
    generator.control_inhibited_by_user_id = None
    generator.control_inhibited_at = None
    await db.commit()
    return {"generator_id": str(generator.id), "control_inhibited": False}
