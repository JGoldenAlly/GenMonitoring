"""generator_io_events hypertable

Revision ID: 0008
Revises: 0007
Create Date: 2026-08-03

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0008"
down_revision = "0007"
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
        "generator_io_events",
        sa.Column("time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("device_key", sa.String(32), nullable=False),
        sa.Column("generator_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "channel",
            sa.Enum("IN1", "OUT1", name="io_channel", native_enum=False),
            nullable=False,
        ),
        sa.Column("state", sa.Boolean(), nullable=False),
        sa.Column("correlated_command_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("matches_commanded", sa.Boolean(), nullable=True),
        sa.Column("mismatch_type", sa.String(32), nullable=True),
        # Composite primary key including `time` so this can become a
        # hypertable (Timescale requires the partitioning column in any
        # unique/primary key constraint).
        sa.PrimaryKeyConstraint("time", "device_key", "channel", name="pk_generator_io_events"),
    )
    op.create_index(
        "ix_generator_io_events_device_key_time",
        "generator_io_events",
        ["device_key", sa.text("time DESC")],
    )
    op.create_index(
        "ix_generator_io_events_generator_channel_time",
        "generator_io_events",
        ["generator_id", "channel", sa.text("time DESC")],
    )

    if _timescaledb_available():
        op.execute("CREATE EXTENSION IF NOT EXISTS timescaledb")
        op.execute(
            "SELECT create_hypertable('generator_io_events', 'time', "
            "if_not_exists => TRUE, migrate_data => TRUE)"
        )
        op.execute(
            "SELECT add_retention_policy('generator_io_events', INTERVAL '2 years', "
            "if_not_exists => TRUE)"
        )


def downgrade() -> None:
    op.drop_index("ix_generator_io_events_generator_channel_time", table_name="generator_io_events")
    op.drop_index("ix_generator_io_events_device_key_time", table_name="generator_io_events")
    op.drop_table("generator_io_events")
