from datetime import datetime

from pydantic import BaseModel, EmailStr

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


class TokenPayload(BaseModel):
    sub: str
    type: str
    exp: datetime
