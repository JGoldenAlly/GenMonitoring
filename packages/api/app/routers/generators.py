from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.dependencies import get_current_user, require_role
from app.models import Device, Generator, ModbusProfileTemplate, ModbusRegister
from app.schemas.generators import (
    ApplyTemplateRequest,
    GeneratorCreate,
    GeneratorOut,
    GeneratorUpdate,
)

router = APIRouter(tags=["generators"])

DUPLICATE_START_STOP_DETAIL = (
    "This device already has another generator configured with start/stop control enabled "
    "(only one start/stop-enabled generator is allowed per device)."
)


async def _get_generator_or_404(generator_id: UUID, db: AsyncSession) -> Generator:
    generator = await db.get(Generator, generator_id)
    if generator is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Generator not found")
    return generator


@router.get("/generators", response_model=list[GeneratorOut])
async def list_generators(
    device_id: UUID | None = None,
    _user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[Generator]:
    stmt = select(Generator).order_by(Generator.created_at.desc())
    if device_id is not None:
        stmt = stmt.where(Generator.device_id == device_id)
    result = await db.execute(stmt)
    return list(result.scalars().all())


@router.post(
    "/generators",
    response_model=GeneratorOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_role("admin", "operator"))],
)
async def create_generator(body: GeneratorCreate, db: AsyncSession = Depends(get_db)) -> Generator:
    device = await db.get(Device, body.device_id)
    if device is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Device not found")

    generator = Generator(**body.model_dump())
    db.add(generator)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=DUPLICATE_START_STOP_DETAIL)
    await db.refresh(generator)
    return generator


@router.get("/generators/{generator_id}", response_model=GeneratorOut)
async def get_generator(
    generator_id: UUID,
    _user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Generator:
    return await _get_generator_or_404(generator_id, db)


@router.put(
    "/generators/{generator_id}",
    response_model=GeneratorOut,
    dependencies=[Depends(require_role("admin", "operator"))],
)
async def update_generator(
    generator_id: UUID, body: GeneratorUpdate, db: AsyncSession = Depends(get_db)
) -> Generator:
    generator = await _get_generator_or_404(generator_id, db)
    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(generator, field, value)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=DUPLICATE_START_STOP_DETAIL)
    await db.refresh(generator)
    return generator


@router.delete(
    "/generators/{generator_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_role("admin", "operator"))],
)
async def delete_generator(generator_id: UUID, db: AsyncSession = Depends(get_db)) -> None:
    generator = await _get_generator_or_404(generator_id, db)
    await db.delete(generator)
    await db.commit()


@router.post(
    "/devices/{device_key}/apply-template",
    response_model=GeneratorOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_role("admin", "operator"))],
)
async def apply_template(
    device_key: str, body: ApplyTemplateRequest, db: AsyncSession = Depends(get_db)
) -> Generator:
    result = await db.execute(select(Device).where(Device.device_key == device_key))
    device = result.scalar_one_or_none()
    if device is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Device not found")

    template = await db.get(ModbusProfileTemplate, body.template_id)
    if template is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Template not found")

    generator = Generator(
        device_id=device.id,
        friendly_name=body.friendly_name or template.name,
        modbus_transport=body.modbus_transport,
        modbus_host=body.modbus_host,
        modbus_port=body.modbus_port,
        modbus_baud=body.modbus_baud,
        modbus_parity=body.modbus_parity,
        modbus_stop_bits=body.modbus_stop_bits,
        modbus_slave_id=body.modbus_slave_id,
        gpio_out_channel=body.gpio_out_channel,
        gpio_in_channel=body.gpio_in_channel,
        start_stop_enabled=body.start_stop_enabled,
    )
    db.add(generator)
    try:
        await db.flush()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=DUPLICATE_START_STOP_DETAIL)

    for reg in template.registers:
        db.add(
            ModbusRegister(
                generator_id=generator.id,
                register_address=reg["address"],
                register_type=reg.get("register_type", 4),
                register_count=reg.get("register_count", 1),
                register_friendly_name=reg["label"],
                unit=reg.get("unit"),
                role=reg.get("role"),
                read_interval_seconds=reg.get("read_interval_seconds", 10),
                enabled=True,
            )
        )

    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=DUPLICATE_START_STOP_DETAIL)

    result = await db.execute(
        select(Generator)
        .options(selectinload(Generator.registers))
        .where(Generator.id == generator.id)
    )
    return result.scalar_one()
