from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel

__all__ = [
    "PreRegisterRequest",
    "DeviceOut",
    "DeviceUpdate",
    "ClaimResponse",
    "RtuTransport",
    "TcpTransport",
    "RegisterConfigOut",
    "GeneratorCommandFallback",
    "DeviceConfigOut",
    "IOEventIn",
    "CommandAckIn",
    "HeartbeatRequest",
    "HeartbeatResponse",
    "ScanResultsRequest",
    "SubmitLogsRequest",
]


class PreRegisterRequest(BaseModel):
    cpu_serial: str
    device_key: str


class DeviceOut(BaseModel):
    id: UUID
    device_key: str
    cpu_serial: str
    owner_id: UUID | None
    claimed: bool
    mqtt_host: str | None
    mqtt_port: int
    auto_update_enabled: bool
    reporting_interval_seconds: int
    config_refresh_interval_seconds: int
    scan_requested: bool
    modbus_scan_results: dict | None
    logs_requested: bool
    sim_notes: str | None
    last_seen_at: datetime | None
    created_at: datetime

    model_config = {"from_attributes": True}


class DeviceUpdate(BaseModel):
    auto_update_enabled: bool | None = None
    reporting_interval_seconds: int | None = None
    config_refresh_interval_seconds: int | None = None
    scan_requested: bool | None = None
    logs_requested: bool | None = None
    sim_notes: str | None = None


class ClaimResponse(BaseModel):
    device_key: str
    claimed: bool
    device_bearer_token: str | None = None


class RtuTransport(BaseModel):
    kind: Literal["rtu"] = "rtu"
    serial_port: str
    baudrate: int
    parity: str
    stopbits: int
    slave_id: int


class TcpTransport(BaseModel):
    kind: Literal["tcp"] = "tcp"
    host: str
    port: int
    slave_id: int


class RegisterConfigOut(BaseModel):
    id: UUID
    generator_id: UUID
    register_address: int
    register_type: int
    register_count: int
    register_friendly_name: str
    unit: str | None
    role: str | None
    read_interval_seconds: int
    enabled: bool
    transport: RtuTransport | TcpTransport


class GeneratorCommandFallback(BaseModel):
    generator_id: UUID | None = None
    desired_state: Literal["run", "stop"] | None = None
    expires_at: datetime | None = None
    command_id: UUID | None = None


class DeviceConfigOut(BaseModel):
    claimed: bool
    device_bearer_token: str | None = None
    mqtt_host: str
    mqtt_port: int
    mqtt_tls: bool
    auto_update_enabled: bool
    reporting_interval_seconds: int
    config_refresh_interval_seconds: int
    target_agent_version: str
    scan_requested: bool
    modbus_registers: list[RegisterConfigOut]
    generator_command: GeneratorCommandFallback


class IOEventIn(BaseModel):
    channel: Literal["IN1", "OUT1"]
    state: bool
    observed_at_utc: datetime


class CommandAckIn(BaseModel):
    command_id: str
    status: Literal["acknowledged", "completed", "failed", "expired"]
    detail: str | None = None


class HeartbeatRequest(BaseModel):
    io_events: list[IOEventIn] | None = None
    command_ack: CommandAckIn | None = None
    agent_version: str | None = None
    extra: dict[str, Any] | None = None


class HeartbeatResponse(BaseModel):
    ok: bool = True
    server_time: datetime


class ScanResultsRequest(BaseModel):
    results: dict[str, Any]


class SubmitLogsRequest(BaseModel):
    content: str
