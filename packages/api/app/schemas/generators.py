from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, model_validator

__all__ = [
    "ModbusRegisterCreate",
    "ModbusRegisterUpdate",
    "ModbusRegisterOut",
    "GeneratorCreate",
    "GeneratorUpdate",
    "GeneratorOut",
    "ApplyTemplateRequest",
]

Transport = Literal["rtu", "tcp"]


class ModbusRegisterCreate(BaseModel):
    register_address: int
    register_type: int = 4
    register_count: int = 1
    register_friendly_name: str
    unit: str | None = None
    role: Literal["running_status", "alarm"] | None = None
    read_interval_seconds: int = 10
    enabled: bool = True


class ModbusRegisterUpdate(BaseModel):
    register_address: int | None = None
    register_type: int | None = None
    register_count: int | None = None
    register_friendly_name: str | None = None
    unit: str | None = None
    role: Literal["running_status", "alarm"] | None = None
    read_interval_seconds: int | None = None
    enabled: bool | None = None


class ModbusRegisterOut(BaseModel):
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

    model_config = {"from_attributes": True}


class GeneratorCreate(BaseModel):
    device_id: UUID
    friendly_name: str
    modbus_transport: Transport
    modbus_host: str | None = None
    modbus_port: int | None = 502
    modbus_baud: int | None = None
    modbus_parity: str | None = "N"
    modbus_stop_bits: int | None = 1
    modbus_slave_id: int
    gpio_out_channel: str | None = None
    gpio_in_channel: str | None = None
    start_stop_enabled: bool = False
    max_run_session_minutes: int = 60
    notes: str | None = None

    @model_validator(mode="after")
    def _check_transport_fields(self):
        if self.modbus_transport == "tcp":
            if not self.modbus_host or not self.modbus_port:
                raise ValueError("modbus_host and modbus_port are required for tcp transport")
        else:
            if not self.modbus_baud:
                raise ValueError("modbus_baud is required for rtu transport")
        return self


class GeneratorUpdate(BaseModel):
    friendly_name: str | None = None
    modbus_transport: Transport | None = None
    modbus_host: str | None = None
    modbus_port: int | None = None
    modbus_baud: int | None = None
    modbus_parity: str | None = None
    modbus_stop_bits: int | None = None
    modbus_slave_id: int | None = None
    gpio_out_channel: str | None = None
    gpio_in_channel: str | None = None
    start_stop_enabled: bool | None = None
    max_run_session_minutes: int | None = None
    notes: str | None = None


class GeneratorOut(BaseModel):
    id: UUID
    device_id: UUID
    friendly_name: str
    modbus_transport: Transport
    modbus_host: str | None
    modbus_port: int | None
    modbus_baud: int | None
    modbus_parity: str | None
    modbus_stop_bits: int | None
    modbus_slave_id: int
    gpio_out_channel: str | None
    gpio_in_channel: str | None
    start_stop_enabled: bool
    max_run_session_minutes: int
    control_inhibited: bool
    control_inhibited_reason: str | None
    control_inhibited_by_user_id: UUID | None
    control_inhibited_at: datetime | None
    current_command_id: UUID | None
    current_desired_state: str | None
    current_command_expires_at: datetime | None
    notes: str | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class ApplyTemplateRequest(BaseModel):
    template_id: UUID
    modbus_transport: Transport
    modbus_host: str | None = None
    modbus_port: int | None = 502
    modbus_baud: int | None = None
    modbus_parity: str | None = "N"
    modbus_stop_bits: int | None = 1
    modbus_slave_id: int
    friendly_name: str | None = None
    gpio_out_channel: str | None = None
    gpio_in_channel: str | None = None
    start_stop_enabled: bool = False

    @model_validator(mode="after")
    def _check_transport_fields(self):
        if self.modbus_transport == "tcp":
            if not self.modbus_host or not self.modbus_port:
                raise ValueError("modbus_host and modbus_port are required for tcp transport")
        else:
            if not self.modbus_baud:
                raise ValueError("modbus_baud is required for rtu transport")
        return self
