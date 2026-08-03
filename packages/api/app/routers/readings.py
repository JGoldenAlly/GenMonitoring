from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import get_current_user_or_apikey
from app.models import Device, Generator, ModbusRegister, User
from app.schemas.readings import LatestReadingOut, ReadingOut

router = APIRouter(tags=["readings"])

MAX_LIMIT = 1000


async def _get_generator_and_addresses(
    generator_id: UUID, db: AsyncSession
) -> tuple[Generator, str, list[int]]:
    generator = await db.get(Generator, generator_id)
    if generator is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Generator not found")

    device = await db.get(Device, generator.device_id)
    if device is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Device not found")

    result = await db.execute(
        select(ModbusRegister.register_address).where(ModbusRegister.generator_id == generator.id)
    )
    addresses = [row[0] for row in result.all()]
    return generator, device.device_key, addresses


@router.get("/generators/{generator_id}/readings", response_model=list[ReadingOut])
async def get_readings(
    generator_id: UUID,
    register_address: int | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    limit: int = Query(default=200, le=MAX_LIMIT, gt=0),
    _user: User = Depends(get_current_user_or_apikey),
    db: AsyncSession = Depends(get_db),
) -> list[ReadingOut]:
    _generator, device_key, addresses = await _get_generator_and_addresses(generator_id, db)
    if not addresses:
        return []

    if register_address is not None:
        if register_address not in addresses:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="register_address does not belong to this generator",
            )
        addresses = [register_address]

    query = """
        SELECT time, device_key, register_address, register_type, register_friendly_name, value, unit
        FROM readings
        WHERE device_key = :device_key AND register_address = ANY(:addresses::integer[])
    """
    params: dict = {"device_key": device_key, "addresses": addresses}
    if since is not None:
        query += " AND time >= :since"
        params["since"] = since
    if until is not None:
        query += " AND time <= :until"
        params["until"] = until
    query += " ORDER BY time DESC LIMIT :limit"
    params["limit"] = min(limit, MAX_LIMIT)

    result = await db.execute(text(query), params)
    return [ReadingOut(**row._mapping) for row in result.all()]


@router.get("/generators/{generator_id}/readings/latest", response_model=list[LatestReadingOut])
async def get_latest_readings(
    generator_id: UUID,
    _user: User = Depends(get_current_user_or_apikey),
    db: AsyncSession = Depends(get_db),
) -> list[LatestReadingOut]:
    _generator, device_key, addresses = await _get_generator_and_addresses(generator_id, db)
    if not addresses:
        return []

    query = """
        SELECT DISTINCT ON (register_address)
            register_address, register_type, register_friendly_name, value, unit, time
        FROM readings
        WHERE device_key = :device_key AND register_address = ANY(:addresses::integer[])
        ORDER BY register_address, time DESC
    """
    result = await db.execute(text(query), {"device_key": device_key, "addresses": addresses})
    return [LatestReadingOut(**row._mapping) for row in result.all()]
