from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import require_role
from app.models import User
from app.schemas import PasswordReset, UserCreate, UserOut, UserUpdate
from app.security import hash_password

router = APIRouter(prefix="/users", tags=["users"], dependencies=[Depends(require_role("admin"))])


@router.get("", response_model=list[UserOut])
async def list_users(db: AsyncSession = Depends(get_db)) -> list[User]:
    result = await db.execute(select(User).order_by(User.created_at))
    return list(result.scalars().all())


@router.post("", response_model=UserOut, status_code=status.HTTP_201_CREATED)
async def create_user(body: UserCreate, db: AsyncSession = Depends(get_db)) -> User:
    user = User(
        email=body.email,
        password_hash=hash_password(body.password),
        role=body.role,
        is_active=body.is_active,
    )
    db.add(user)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Email already in use")
    await db.refresh(user)
    return user


async def _get_user_or_404(user_id: UUID, db: AsyncSession) -> User:
    user = await db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    return user


@router.get("/{user_id}", response_model=UserOut)
async def get_user(user_id: UUID, db: AsyncSession = Depends(get_db)) -> User:
    return await _get_user_or_404(user_id, db)


@router.put("/{user_id}", response_model=UserOut)
async def update_user(user_id: UUID, body: UserUpdate, db: AsyncSession = Depends(get_db)) -> User:
    user = await _get_user_or_404(user_id, db)
    data = body.model_dump(exclude_unset=True)
    for field, value in data.items():
        setattr(user, field, value)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Email already in use")
    await db.refresh(user)
    return user


@router.delete("/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_user(user_id: UUID, db: AsyncSession = Depends(get_db)) -> None:
    user = await _get_user_or_404(user_id, db)
    await db.delete(user)
    await db.commit()


@router.put("/{user_id}/password", status_code=status.HTTP_204_NO_CONTENT)
async def reset_password(user_id: UUID, body: PasswordReset, db: AsyncSession = Depends(get_db)) -> None:
    user = await _get_user_or_404(user_id, db)
    user.password_hash = hash_password(body.new_password)
    await db.commit()
