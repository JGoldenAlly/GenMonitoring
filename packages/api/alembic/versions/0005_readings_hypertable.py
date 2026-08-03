"""readings hypertable (raw telemetry, not an ORM model)

Revision ID: 0005
Revises: 0004
Create Date: 2026-08-03

"""
from alembic import op
import sqlalchemy as sa

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def _timescaledb_available() -> bool:
    bind = op.get_bind()
    result = bind.execute(
        sa.text("SELECT 1 FROM pg_available_extensions WHERE name = 'timescaledb'")
    )
    return result.first() is not None


def upgrade() -> None:
    op.create_table(
        "readings",
        sa.Column("time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("device_key", sa.String(32), nullable=False),
        sa.Column("register_address", sa.Integer(), nullable=False),
        sa.Column("register_type", sa.Integer(), nullable=False, server_default="4"),
        sa.Column("register_friendly_name", sa.String(255), nullable=True),
        sa.Column("value", sa.Numeric(), nullable=True),
        sa.Column("unit", sa.String(32), nullable=True),
    )
    op.create_index("ix_readings_device_key_time", "readings", ["device_key", sa.text("time DESC")])
    op.create_index(
        "ix_readings_device_key_register_time",
        "readings",
        ["device_key", "register_address", sa.text("time DESC")],
    )

    if _timescaledb_available():
        op.execute("CREATE EXTENSION IF NOT EXISTS timescaledb")
        op.execute(
            "SELECT create_hypertable('readings', 'time', "
            "if_not_exists => TRUE, migrate_data => TRUE)"
        )
        op.execute(
            "SELECT add_retention_policy('readings', INTERVAL '1 year', if_not_exists => TRUE)"
        )


def downgrade() -> None:
    op.drop_index("ix_readings_device_key_register_time", table_name="readings")
    op.drop_index("ix_readings_device_key_time", table_name="readings")
    op.drop_table("readings")
