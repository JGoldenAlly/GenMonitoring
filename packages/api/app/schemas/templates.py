from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel

__all__ = ["TemplateRegisterSpec", "TemplateCreate", "TemplateUpdate", "TemplateOut"]


class TemplateRegisterSpec(BaseModel):
    address: int
    label: str
    unit: str | None = None
    register_type: int = 4
    register_count: int = 1
    read_interval_seconds: int = 10
    role: Literal["running_status", "alarm"] | None = None


class TemplateCreate(BaseModel):
    name: str
    description: str | None = None
    category: str = "general"
    registers: list[TemplateRegisterSpec]


class TemplateUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    category: str | None = None
    registers: list[TemplateRegisterSpec] | None = None


class TemplateOut(BaseModel):
    id: UUID
    slug: str
    name: str
    description: str | None
    category: str
    registers: list[TemplateRegisterSpec]
    is_builtin: bool
    created_at: datetime

    model_config = {"from_attributes": True}
