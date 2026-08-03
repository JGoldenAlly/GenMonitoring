from datetime import datetime
from uuid import UUID

from pydantic import BaseModel

__all__ = ["ApiKeyCreate", "ApiKeyCreated", "ApiKeyOut"]


class ApiKeyCreate(BaseModel):
    label: str | None = None


class ApiKeyCreated(BaseModel):
    id: UUID
    label: str | None
    api_key: str  # raw key, shown once
    created_at: datetime


class ApiKeyOut(BaseModel):
    id: UUID
    label: str | None
    created_at: datetime
    last_used_at: datetime | None

    model_config = {"from_attributes": True}
