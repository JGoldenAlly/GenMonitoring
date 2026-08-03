"""devices

Revision ID: 0002
Revises: 0001
Create Date: 2026-08-03

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "devices",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("device_key", sa.String(32), nullable=False, unique=True),
        sa.Column("cpu_serial", sa.String(128), nullable=False, unique=True),
        sa.Column(
            "owner_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("claimed", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("device_bearer_token", sa.String(64), nullable=True),
        sa.Column("mqtt_host", sa.String(255), nullable=True),
        sa.Column("mqtt_port", sa.Integer(), nullable=False, server_default="8883"),
        sa.Column("auto_update_enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("reporting_interval_seconds", sa.Integer(), nullable=False, server_default="60"),
        sa.Column(
            "config_refresh_interval_seconds", sa.Integer(), nullable=False, server_default="60"
        ),
        sa.Column("scan_requested", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("modbus_scan_results", postgresql.JSONB(), nullable=True),
        sa.Column("logs_requested", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("sim_notes", sa.Text(), nullable=True),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_devices_device_key", "devices", ["device_key"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_devices_device_key", table_name="devices")
    op.drop_table("devices")
