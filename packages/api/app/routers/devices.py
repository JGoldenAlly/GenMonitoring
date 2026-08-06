import os
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import settings
from app.database import get_db
from app.dependencies import get_current_user, get_device_by_bearer_token, require_role
from app.models import Device, DeviceLog, Generator
from app.schemas.devices import (
    ClaimResponse,
    DeviceConfigOut,
    DeviceOut,
    DeviceUpdate,
    GeneratorCommandFallback,
    HeartbeatRequest,
    HeartbeatResponse,
    PreRegisterRequest,
    RegisterConfigOut,
    RtuTransport,
    ScanResultsRequest,
    SubmitLogsRequest,
    TcpTransport,
)
from app.security import generate_device_bearer_token
from app.services import device_service
from app.services.emqx_admin import EmqxAdminError, emqx_admin_client

router = APIRouter(prefix="/devices", tags=["devices"])

RTU_SERIAL_PORT = "/dev/ttyAMA5"  # the board's only onboard RS-485 port


async def _get_device_by_key_or_404(device_key: str, db: AsyncSession, *, with_generators: bool = False) -> Device:
    stmt = select(Device).where(Device.device_key == device_key)
    if with_generators:
        stmt = stmt.options(selectinload(Device.generators).selectinload(Generator.registers))
    result = await db.execute(stmt)
    device = result.scalar_one_or_none()
    if device is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Device not found")
    return device


@router.post("/pre-register", response_model=DeviceOut, status_code=status.HTTP_201_CREATED)
async def pre_register_device(body: PreRegisterRequest, db: AsyncSession = Depends(get_db)) -> Device:
    """Called by the field agent on first boot -- no auth. Upserts by
    cpu_serial so re-running this (e.g. after a reflash) is safe and never
    clobbers an existing claim."""
    result = await db.execute(select(Device).where(Device.cpu_serial == body.cpu_serial))
    device = result.scalar_one_or_none()

    if device is not None:
        device.device_key = body.device_key
        await db.commit()
        await db.refresh(device)
        return device

    device = Device(
        device_key=body.device_key,
        cpu_serial=body.cpu_serial,
        claimed=False,
        mqtt_host=settings.MQTT_HOST,
        mqtt_port=settings.MQTT_PORT,
    )
    db.add(device)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="device_key already registered to a different cpu_serial",
        )
    await db.refresh(device)
    return device


@router.get("/agent/download")
async def download_agent() -> FileResponse:
    path = settings.AGENT_SCRIPT_PATH
    if not path or not os.path.isfile(path):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Agent script not available at '{path}'. Set AGENT_SCRIPT_PATH to a valid file.",
        )
    return FileResponse(path, media_type="text/x-python", filename="genmon_agent.py")


@router.get("/{device_key}/config", response_model=DeviceConfigOut)
async def get_device_config(device_key: str, db: AsyncSession = Depends(get_db)) -> DeviceConfigOut:
    """Polled by the field agent (no auth) to get its live configuration."""
    device = await _get_device_by_key_or_404(device_key, db, with_generators=True)

    registers: list[RegisterConfigOut] = []
    generator_command = GeneratorCommandFallback()

    for generator in device.generators:
        if generator.modbus_transport == "rtu":
            transport = RtuTransport(
                serial_port=RTU_SERIAL_PORT,
                baudrate=generator.modbus_baud,
                parity=generator.modbus_parity or "N",
                stopbits=generator.modbus_stop_bits or 1,
                slave_id=generator.modbus_slave_id,
            )
        else:
            transport = TcpTransport(
                host=generator.modbus_host,
                port=generator.modbus_port or 502,
                slave_id=generator.modbus_slave_id,
            )

        for reg in generator.registers:
            registers.append(
                RegisterConfigOut(
                    id=reg.id,
                    generator_id=generator.id,
                    register_address=reg.register_address,
                    register_type=reg.register_type,
                    register_count=reg.register_count,
                    register_friendly_name=reg.register_friendly_name,
                    unit=reg.unit,
                    role=reg.role,
                    read_interval_seconds=reg.read_interval_seconds,
                    enabled=reg.enabled,
                    transport=transport,
                )
            )

        if generator.start_stop_enabled:
            generator_command = GeneratorCommandFallback(
                generator_id=generator.id,
                desired_state=generator.current_desired_state,
                expires_at=generator.current_command_expires_at,
                command_id=generator.current_command_id,
            )

    return DeviceConfigOut(
        claimed=device.claimed,
        device_bearer_token=device.device_bearer_token if device.claimed else None,
        mqtt_host=device.mqtt_host or settings.MQTT_HOST,
        mqtt_port=device.mqtt_port or settings.MQTT_PORT,
        mqtt_tls=settings.MQTT_TLS,
        auto_update_enabled=device.auto_update_enabled,
        reporting_interval_seconds=device.reporting_interval_seconds,
        config_refresh_interval_seconds=device.config_refresh_interval_seconds,
        target_agent_version=settings.TARGET_AGENT_VERSION,
        scan_requested=device.scan_requested,
        modbus_registers=registers,
        generator_command=generator_command,
    )


@router.post(
    "/{device_key}/claim",
    response_model=ClaimResponse,
    dependencies=[Depends(require_role("admin", "operator"))],
)
async def claim_device(
    device_key: str,
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ClaimResponse:
    device = await _get_device_by_key_or_404(device_key, db)
    if device.claimed:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Device is already claimed")

    token = generate_device_bearer_token()
    try:
        await emqx_admin_client.create_device_client(device_key, token)
    except EmqxAdminError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Failed to provision MQTT credentials for device: {exc}",
        )

    device.claimed = True
    device.device_bearer_token = token
    device.owner_id = user.id
    await db.commit()

    return ClaimResponse(device_key=device_key, claimed=True, device_bearer_token=token)


@router.delete(
    "/{device_key}/claim",
    response_model=ClaimResponse,
    dependencies=[Depends(require_role("admin", "operator"))],
)
async def unclaim_device(device_key: str, db: AsyncSession = Depends(get_db)) -> ClaimResponse:
    device = await _get_device_by_key_or_404(device_key, db)
    if not device.claimed:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Device is not claimed")

    try:
        await emqx_admin_client.delete_device_client(device_key)
    except EmqxAdminError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Failed to remove MQTT credentials for device: {exc}",
        )

    device.claimed = False
    device.device_bearer_token = None
    device.owner_id = None
    await db.commit()

    return ClaimResponse(device_key=device_key, claimed=False, device_bearer_token=None)


@router.get("", response_model=list[DeviceOut])
async def list_devices(
    _user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[Device]:
    result = await db.execute(select(Device).order_by(Device.created_at.desc()))
    return list(result.scalars().all())


@router.put(
    "/{device_key}",
    response_model=DeviceOut,
    dependencies=[Depends(require_role("admin", "operator"))],
)
async def update_device(device_key: str, body: DeviceUpdate, db: AsyncSession = Depends(get_db)) -> Device:
    device = await _get_device_by_key_or_404(device_key, db)
    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(device, field, value)
    await db.commit()
    await db.refresh(device)
    return device


@router.post("/{device_key}/scan-results", status_code=status.HTTP_204_NO_CONTENT)
async def submit_scan_results(
    device_key: str,
    body: ScanResultsRequest,
    device: Device = Depends(get_device_by_bearer_token),
    db: AsyncSession = Depends(get_db),
) -> None:
    if device.device_key != device_key:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Token does not match device_key")
    device.modbus_scan_results = body.results
    device.scan_requested = False
    device.last_seen_at = datetime.now(timezone.utc)
    await db.commit()


@router.post("/{device_key}/heartbeat", response_model=HeartbeatResponse)
async def heartbeat(
    device_key: str,
    body: HeartbeatRequest,
    device: Device = Depends(get_device_by_bearer_token),
    db: AsyncSession = Depends(get_db),
) -> HeartbeatResponse:
    if device.device_key != device_key:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Token does not match device_key")

    device.last_seen_at = datetime.now(timezone.utc)
    await db.commit()

    if body.io_events:
        await device_service.record_io_events(db, device, body.io_events)
    if body.command_ack:
        await device_service.apply_command_ack(db, device, body.command_ack)

    return HeartbeatResponse(ok=True, server_time=datetime.now(timezone.utc))


@router.post("/{device_key}/submit-logs", status_code=status.HTTP_204_NO_CONTENT)
async def submit_logs(
    device_key: str,
    body: SubmitLogsRequest,
    device: Device = Depends(get_device_by_bearer_token),
    db: AsyncSession = Depends(get_db),
) -> None:
    if device.device_key != device_key:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Token does not match device_key")
    db.add(DeviceLog(device_id=device.id, content=body.content))
    device.logs_requested = False
    await db.commit()
