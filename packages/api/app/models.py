"""SQLAlchemy 2.0 ORM models for GenMonitoring.

Notes:
- All enum-like columns use SQLAlchemy's `Enum(..., native_enum=False)` which
  renders as VARCHAR + CHECK constraint rather than a Postgres native ENUM
  type. This keeps Alembic migrations simple (no ALTER TYPE dances when a
  value is added later).
- `readings` (raw telemetry) is intentionally NOT an ORM model -- it is a
  high-volume hypertable written mostly via raw SQL/bulk inserts from the
  bridge/ingest path. It is created directly in a migration. A thin
  read-only helper lives in app/services/readings_service.py-equivalent
  logic inside routers/readings.py using `sa.table(...)` reflection-free
  raw queries.
"""
import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

# ---------------------------------------------------------------------------
# Enum value sets (kept as plain tuples so they can be reused by Pydantic
# schemas without importing SQLAlchemy there).
# ---------------------------------------------------------------------------
USER_ROLES = ("admin", "operator", "viewer")
MODBUS_TRANSPORTS = ("rtu", "tcp")
COMMAND_TYPES = ("run", "stop", "cancel")
COMMAND_STATUSES = (
    "pending",
    "delivered",
    "acknowledged",
    "expired",
    "superseded",
    "cancelled",
)
DESIRED_STATES = ("run", "stop")
IO_CHANNELS = ("IN1", "OUT1")
MISMATCH_TYPES = ("external_start", "unexpected_stop")


def _enum(values, name):
    return Enum(*values, name=name, native_enum=False, validate_strings=True)


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    email: Mapped[str] = mapped_column(String(320), unique=True, nullable=False, index=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str] = mapped_column(_enum(USER_ROLES, "user_role"), nullable=False, default="operator")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    api_keys: Mapped[list["ApiKey"]] = relationship(back_populates="user", cascade="all, delete-orphan")


class ApiKey(Base):
    __tablename__ = "api_keys"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    key_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    label: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    user: Mapped["User"] = relationship(back_populates="api_keys")


class Device(Base):
    __tablename__ = "devices"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    device_key: Mapped[str] = mapped_column(String(32), unique=True, nullable=False, index=True)
    cpu_serial: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    owner_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    claimed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    device_bearer_token: Mapped[str | None] = mapped_column(String(64), nullable=True)

    mqtt_host: Mapped[str | None] = mapped_column(String(255), nullable=True)
    mqtt_port: Mapped[int] = mapped_column(Integer, nullable=False, default=8883)

    auto_update_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    reporting_interval_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=60)
    config_refresh_interval_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=60)

    scan_requested: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    modbus_scan_results: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    logs_requested: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    sim_notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    generators: Mapped[list["Generator"]] = relationship(back_populates="device", cascade="all, delete-orphan")
    logs: Mapped[list["DeviceLog"]] = relationship(back_populates="device", cascade="all, delete-orphan")


class Generator(Base):
    __tablename__ = "generators"
    __table_args__ = (
        CheckConstraint(
            "(modbus_transport = 'tcp' AND modbus_host IS NOT NULL AND modbus_port IS NOT NULL) OR "
            "(modbus_transport = 'rtu' AND modbus_baud IS NOT NULL)",
            name="ck_generators_transport_fields",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    device_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("devices.id", ondelete="CASCADE"), nullable=False, index=True
    )
    friendly_name: Mapped[str] = mapped_column(String(255), nullable=False)

    modbus_transport: Mapped[str] = mapped_column(_enum(MODBUS_TRANSPORTS, "modbus_transport"), nullable=False)
    modbus_host: Mapped[str | None] = mapped_column(String(255), nullable=True)
    modbus_port: Mapped[int | None] = mapped_column(Integer, nullable=True, default=502)
    modbus_baud: Mapped[int | None] = mapped_column(Integer, nullable=True)
    modbus_parity: Mapped[str | None] = mapped_column(String(1), nullable=True, default="N")
    modbus_stop_bits: Mapped[int | None] = mapped_column(Integer, nullable=True, default=1)
    modbus_slave_id: Mapped[int] = mapped_column(Integer, nullable=False)

    gpio_out_channel: Mapped[str | None] = mapped_column(String(16), nullable=True)
    gpio_in_channel: Mapped[str | None] = mapped_column(String(16), nullable=True)
    start_stop_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    max_run_session_minutes: Mapped[int] = mapped_column(Integer, nullable=False, default=60)

    control_inhibited: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    control_inhibited_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    control_inhibited_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    control_inhibited_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Added by migration 0007, after generator_commands exists.
    current_command_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("generator_commands.id", ondelete="SET NULL"), nullable=True
    )
    current_desired_state: Mapped[str | None] = mapped_column(_enum(DESIRED_STATES, "desired_state"), nullable=True)
    current_command_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    device: Mapped["Device"] = relationship(back_populates="generators")
    registers: Mapped[list["ModbusRegister"]] = relationship(
        back_populates="generator", cascade="all, delete-orphan"
    )
    commands: Mapped[list["GeneratorCommand"]] = relationship(
        back_populates="generator",
        cascade="all, delete-orphan",
        foreign_keys="GeneratorCommand.generator_id",
    )


class ModbusRegister(Base):
    __tablename__ = "modbus_registers"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    generator_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("generators.id", ondelete="CASCADE"), nullable=False, index=True
    )
    register_address: Mapped[int] = mapped_column(Integer, nullable=False)
    register_type: Mapped[int] = mapped_column(Integer, nullable=False, default=4)
    register_count: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    register_friendly_name: Mapped[str] = mapped_column(String(255), nullable=False)
    unit: Mapped[str | None] = mapped_column(String(32), nullable=True)
    role: Mapped[str | None] = mapped_column(String(32), nullable=True)
    read_interval_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=10)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    generator: Mapped["Generator"] = relationship(back_populates="registers")


class ModbusProfileTemplate(Base):
    __tablename__ = "modbus_profile_templates"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    slug: Mapped[str] = mapped_column(String(128), unique=True, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    category: Mapped[str] = mapped_column(String(64), nullable=False, default="general")
    registers: Mapped[list] = mapped_column(JSONB, nullable=False)
    is_builtin: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class GeneratorCommand(Base):
    __tablename__ = "generator_commands"
    __table_args__ = (
        UniqueConstraint("id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    generator_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("generators.id", ondelete="CASCADE"), nullable=False, index=True
    )
    requested_by_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=False
    )
    command_type: Mapped[str] = mapped_column(_enum(COMMAND_TYPES, "command_type"), nullable=False)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(
        _enum(COMMAND_STATUSES, "command_status"), nullable=False, default="pending"
    )
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    superseded_by_command_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("generator_commands.id", ondelete="SET NULL"), nullable=True
    )
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)

    generator: Mapped["Generator"] = relationship(
        back_populates="commands", foreign_keys=[generator_id]
    )


class GeneratorIOEvent(Base):
    """Maps to the `generator_io_events` hypertable (raw SQL DDL in migration
    0008 -- this ORM model matches its shape for convenience of querying via
    the ORM; it is not used to CREATE the table)."""

    __tablename__ = "generator_io_events"

    time: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    device_key: Mapped[str] = mapped_column(String(32), primary_key=True)
    generator_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    channel: Mapped[str] = mapped_column(_enum(IO_CHANNELS, "io_channel"), nullable=False, primary_key=True)
    state: Mapped[bool] = mapped_column(Boolean, nullable=False)
    correlated_command_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    matches_commanded: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    mismatch_type: Mapped[str | None] = mapped_column(String(32), nullable=True)


class DeviceLog(Base):
    __tablename__ = "device_logs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    device_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("devices.id", ondelete="CASCADE"), nullable=False, index=True
    )
    content: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    device: Mapped["Device"] = relationship(back_populates="logs")
