"""Shared FastAPI dependencies: auth (JWT + API key) and device bearer auth."""
from datetime import datetime, timezone
from uuid import UUID

from fastapi import Depends, Header, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.models import ApiKey, Device, User
from app.security import decode_token, hash_api_key

bearer_scheme = HTTPBearer(auto_error=False)


async def _load_active_user(db: AsyncSession, user_id: str) -> User:
    try:
        uid = UUID(user_id)
    except ValueError:
        raise HTTPException(status_code=401, detail="Invalid token subject")
    result = await db.execute(select(User).where(User.id == uid))
    user = result.scalar_one_or_none()
    if user is None or not user.is_active:
        raise HTTPException(status_code=401, detail="User not found or inactive")
    return user


async def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    db: AsyncSession = Depends(get_db),
) -> User:
    """Requires a valid JWT *access* token. Used on all user-facing write
    endpoints and most read endpoints (never on device endpoints)."""
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )
    try:
        payload = decode_token(credentials.credentials)
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid or expired token")

    if payload.get("type") != "access":
        raise HTTPException(status_code=401, detail="Expected an access token")

    return await _load_active_user(db, payload.get("sub"))


def require_role(*roles: str):
    """Dependency factory: require the current user to have one of `roles`."""

    async def _dep(user: User = Depends(get_current_user)) -> User:
        if user.role not in roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Requires one of roles: {', '.join(roles)}",
            )
        return user

    return _dep


async def get_current_user_or_apikey(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    db: AsyncSession = Depends(get_db),
) -> User:
    """Accepts EITHER a JWT access token OR a raw API key on the same
    `Authorization: Bearer <token-or-key>` header. Intended ONLY for
    read-only telemetry endpoints -- never wire this into command/write
    endpoints.
    """
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )
    token = credentials.credentials

    # First try as a JWT access token.
    try:
        payload = decode_token(token)
        if payload.get("type") == "access":
            return await _load_active_user(db, payload.get("sub"))
    except JWTError:
        pass

    # Fall back to API key lookup.
    key_hash = hash_api_key(token)
    result = await db.execute(select(ApiKey).where(ApiKey.key_hash == key_hash))
    api_key = result.scalar_one_or_none()
    if api_key is None:
        raise HTTPException(status_code=401, detail="Invalid credentials")

    api_key.last_used_at = datetime.now(timezone.utc)
    await db.commit()

    user = await _load_active_user(db, str(api_key.user_id))
    return user


async def get_device_by_bearer_token(
    authorization: str | None = Header(default=None),
    db: AsyncSession = Depends(get_db),
) -> Device:
    """Auth dependency for device-facing endpoints: checks the
    `Authorization: Bearer <device_bearer_token>` header via direct string
    equality against `devices.device_bearer_token` (no JWT/API-key
    involved)."""
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing device bearer token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    token = authorization.split(" ", 1)[1].strip()
    if not token:
        raise HTTPException(status_code=401, detail="Missing device bearer token")

    result = await db.execute(
        select(Device).where(Device.device_bearer_token == token, Device.claimed.is_(True))
    )
    device = result.scalar_one_or_none()
    if device is None:
        raise HTTPException(status_code=401, detail="Invalid device credentials")
    return device
