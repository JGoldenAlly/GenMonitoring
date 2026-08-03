from datetime import datetime

from pydantic import BaseModel, EmailStr

from app.schemas.users import UserOut

__all__ = ["LoginRequest", "RefreshRequest", "TokenPair", "TokenPayload"]


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class RefreshRequest(BaseModel):
    refresh_token: str


class TokenPair(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    # The portal needs the caller's role for client-side UI gating (it has
    # no separate /auth/me call) -- embed it directly in both /auth/login
    # and /auth/refresh responses rather than requiring a second round trip.
    user: UserOut


class TokenPayload(BaseModel):
    sub: str
    type: str
    exp: datetime
