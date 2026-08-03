"""Pydantic payload models for the MQTT messages the bridge consumes.

These mirror the wire contract shared with `packages/agent` (publisher) and
`packages/api` (which reads/writes the same rows via the ORM). Keep field
names and types in lockstep with the design doc -- other packages are built
against this same contract in parallel.
"""
from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel


class TelemetryPayload(BaseModel):
    """genmon/{device_key}/data -- one Modbus register reading."""

    device_key: str
    register_address: int
    register_type: int = 4
    register_friendly_name: str
    value: Optional[float] = None
    unit: str
    timestamp_utc: datetime


class IoEventPayload(BaseModel):
    """genmon/{device_key}/io -- an IN1/OUT1 state transition."""

    device_key: str
    channel: Literal["IN1", "OUT1"]
    state: bool
    observed_at_utc: datetime


class CommandAckPayload(BaseModel):
    """genmon/{device_key}/cmd/ack -- agent's result for a dispatched command."""

    command_id: str
    session_id: str
    result: Literal["applied", "rejected", "expired_no_renewal"]
    out1_state: bool
    in1_state: bool
    applied_at: datetime
