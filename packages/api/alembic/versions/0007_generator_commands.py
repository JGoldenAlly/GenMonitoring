"""generator_commands, then wire generators.current_command_id/etc

Revision ID: 0007
Revises: 0006
Create Date: 2026-08-03

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "generator_commands",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "generator_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("generators.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "requested_by_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id"),
            nullable=False,
        ),
        sa.Column(
            "command_type",
            sa.Enum("run", "stop", "cancel", name="command_type", native_enum=False),
            nullable=False,
        ),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column(
            "status",
            sa.Enum(
                "pending",
                "delivered",
                "acknowledged",
                "expired",
                "superseded",
                "cancelled",
                name="command_status",
                native_enum=False,
            ),
            nullable=False,
            server_default="pending",
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "superseded_by_command_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("generator_commands.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("acknowledged_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index(
        "ix_generator_commands_generator_created",
        "generator_commands",
        ["generator_id", sa.text("created_at DESC")],
    )

    op.add_column(
        "generators",
        sa.Column(
            "current_command_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("generator_commands.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.add_column(
        "generators",
        sa.Column(
            "current_desired_state",
            sa.Enum("run", "stop", name="desired_state", native_enum=False),
            nullable=True,
        ),
    )
    op.add_column(
        "generators",
        sa.Column("current_command_expires_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("generators", "current_command_expires_at")
    op.drop_column("generators", "current_desired_state")
    op.drop_column("generators", "current_command_id")
    op.drop_index("ix_generator_commands_generator_created", table_name="generator_commands")
    op.drop_table("generator_commands")
