from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, model_validator

__all__ = [
    "CommandCreate",
    "CommandOut",
    "CommandListOut",
    "IOStateOut",
    "CurrentCommandOut",
    "InhibitRequest",
]


class CommandCreate(BaseModel):
    command_type: Literal["run", "stop", "cancel"]
    reason: str | None = None
    duration_minutes: int | None = None

    @model_validator(mode="after")
    def _reason_required_for_run(self):
        if self.command_type == "run" and not (self.reason and self.reason.strip()):
            raise ValueError("reason is required for run commands")
        return self


class CommandOut(BaseModel):
    id: UUID
    generator_id: UUID
    requested_by_user_id: UUID
    command_type: str
    reason: str | None
    status: str
    expires_at: datetime | None
    superseded_by_command_id: UUID | None
    acknowledged_at: datetime | None
    created_at: datetime

    model_config = {"from_attributes": True}


class CommandListOut(BaseModel):
    items: list[CommandOut]
    total: int
    limit: int
    offset: int


class IOStateOut(BaseModel):
    channel: str
    state: bool
    time: datetime
    matches_commanded: bool | None = None
    mismatch_type: str | None = None


class CurrentCommandOut(BaseModel):
    generator_id: UUID
    current_command_id: UUID | None
    current_desired_state: str | None
    current_command_expires_at: datetime | None
    control_inhibited: bool
    control_inhibited_reason: str | None
    last_command: CommandOut | None
    io_states: list[IOStateOut]


class InhibitRequest(BaseModel):
    reason: str
