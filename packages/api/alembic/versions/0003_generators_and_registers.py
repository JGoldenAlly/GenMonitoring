"""generators and modbus_registers

Revision ID: 0003
Revises: 0002
Create Date: 2026-08-03

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "generators",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "device_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("devices.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("friendly_name", sa.String(255), nullable=False),
        sa.Column(
            "modbus_transport",
            sa.Enum("rtu", "tcp", name="modbus_transport", native_enum=False),
            nullable=False,
        ),
        sa.Column("modbus_host", sa.String(255), nullable=True),
        sa.Column("modbus_port", sa.Integer(), nullable=True, server_default="502"),
        sa.Column("modbus_baud", sa.Integer(), nullable=True),
        sa.Column("modbus_parity", sa.String(1), nullable=True, server_default="N"),
        sa.Column("modbus_stop_bits", sa.Integer(), nullable=True, server_default="1"),
        sa.Column("modbus_slave_id", sa.Integer(), nullable=False),
        sa.Column("gpio_out_channel", sa.String(16), nullable=True),
        sa.Column("gpio_in_channel", sa.String(16), nullable=True),
        sa.Column("start_stop_enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("max_run_session_minutes", sa.Integer(), nullable=False, server_default="60"),
        sa.Column("control_inhibited", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("control_inhibited_reason", sa.Text(), nullable=True),
        sa.Column(
            "control_inhibited_by_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("control_inhibited_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            onupdate=sa.func.now(),
        ),
        sa.CheckConstraint(
            "(modbus_transport = 'tcp' AND modbus_host IS NOT NULL AND modbus_port IS NOT NULL) OR "
            "(modbus_transport = 'rtu' AND modbus_baud IS NOT NULL)",
            name="ck_generators_transport_fields",
        ),
    )
    op.create_index("ix_generators_device_id", "generators", ["device_id"])

    # Only one start/stop-enabled generator allowed per device.
    op.execute(
        "CREATE UNIQUE INDEX ux_generators_device_start_stop "
        "ON generators (device_id) WHERE start_stop_enabled"
    )

    op.create_table(
        "modbus_registers",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "generator_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("generators.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("register_address", sa.Integer(), nullable=False),
        sa.Column("register_type", sa.Integer(), nullable=False, server_default="4"),
        sa.Column("register_count", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("register_friendly_name", sa.String(255), nullable=False),
        sa.Column("unit", sa.String(32), nullable=True),
        sa.Column("role", sa.String(32), nullable=True),
        sa.Column("read_interval_seconds", sa.Integer(), nullable=False, server_default="10"),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
    )
    op.create_index("ix_modbus_registers_generator_id", "modbus_registers", ["generator_id"])


def downgrade() -> None:
    op.drop_index("ix_modbus_registers_generator_id", table_name="modbus_registers")
    op.drop_table("modbus_registers")
    op.execute("DROP INDEX IF EXISTS ux_generators_device_start_stop")
    op.drop_index("ix_generators_device_id", table_name="generators")
    op.drop_table("generators")
