"""modbus_profile_templates + seed builtin templates

Revision ID: 0004
Revises: 0003
Create Date: 2026-08-03

NOTE: this data migration seeds the three built-in templates as they exist
at this point in time. The api ALSO idempotently re-seeds/updates these
same three templates (by slug) on every startup, via
app.services.template_seed -- that's the source of truth for keeping them
current going forward. This migration exists so a fresh database has them
immediately after `alembic upgrade head`, before the api process has even
started once.
"""
import uuid

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None

HOLDING = 4
INPUT = 3
DISCRETE = 1

modbus_profile_templates = sa.table(
    "modbus_profile_templates",
    sa.column("id", postgresql.UUID(as_uuid=True)),
    sa.column("slug", sa.String),
    sa.column("name", sa.String),
    sa.column("description", sa.Text),
    sa.column("category", sa.String),
    sa.column("registers", postgresql.JSONB),
    sa.column("is_builtin", sa.Boolean),
)


def upgrade() -> None:
    op.create_table(
        "modbus_profile_templates",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("slug", sa.String(128), nullable=False, unique=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("category", sa.String(64), nullable=False, server_default="general"),
        sa.Column("registers", postgresql.JSONB(), nullable=False),
        sa.Column("is_builtin", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_modbus_profile_templates_slug", "modbus_profile_templates", ["slug"], unique=True)

    op.bulk_insert(
        modbus_profile_templates,
        [
            {
                "id": uuid.uuid4(),
                "slug": "generic-generator",
                "name": "Generic Generator",
                "description": "Generic 10-register holding-register profile suitable as a "
                "starting point for most single-phase/three-phase gensets with a basic Modbus "
                "gateway.",
                "category": "general",
                "registers": [
                    {"address": 0, "label": "RPM", "unit": "rpm", "register_type": HOLDING, "register_count": 1, "read_interval_seconds": 10},
                    {"address": 1, "label": "Battery Voltage", "unit": "V", "register_type": HOLDING, "register_count": 1, "read_interval_seconds": 10},
                    {"address": 2, "label": "Coolant Temp", "unit": "C", "register_type": HOLDING, "register_count": 1, "read_interval_seconds": 10},
                    {"address": 3, "label": "Oil Pressure", "unit": "psi", "register_type": HOLDING, "register_count": 1, "read_interval_seconds": 10},
                    {"address": 4, "label": "Gen Voltage L1", "unit": "V", "register_type": HOLDING, "register_count": 1, "read_interval_seconds": 10},
                    {"address": 5, "label": "Gen Voltage L2", "unit": "V", "register_type": HOLDING, "register_count": 1, "read_interval_seconds": 10},
                    {"address": 6, "label": "Gen Voltage L3", "unit": "V", "register_type": HOLDING, "register_count": 1, "read_interval_seconds": 10},
                    {"address": 7, "label": "Frequency", "unit": "Hz", "register_type": HOLDING, "register_count": 1, "read_interval_seconds": 10},
                    {"address": 8, "label": "Active Power", "unit": "kW", "register_type": HOLDING, "register_count": 1, "read_interval_seconds": 10},
                    {"address": 9, "label": "Fuel Level", "unit": "%", "register_type": HOLDING, "register_count": 1, "read_interval_seconds": 10},
                ],
                "is_builtin": True,
            },
            {
                "id": uuid.uuid4(),
                "slug": "cat-emcp42",
                "name": "Caterpillar EMCP 4.2",
                "description": "20-register input-register profile modeled on the Caterpillar "
                "EMCP 4.2 genset controller's common Modbus map.",
                "category": "caterpillar",
                "registers": [
                    {"address": 1000, "label": "RPM", "unit": "rpm", "register_type": INPUT, "register_count": 1, "read_interval_seconds": 10},
                    {"address": 1001, "label": "Coolant Temp", "unit": "C", "register_type": INPUT, "register_count": 1, "read_interval_seconds": 10},
                    {"address": 1002, "label": "Oil Pressure", "unit": "psi", "register_type": INPUT, "register_count": 1, "read_interval_seconds": 10},
                    {"address": 1003, "label": "Battery Voltage", "unit": "V", "register_type": INPUT, "register_count": 1, "read_interval_seconds": 10},
                    {"address": 1004, "label": "Gen Voltage L1-N", "unit": "V", "register_type": INPUT, "register_count": 1, "read_interval_seconds": 10},
                    {"address": 1005, "label": "Gen Voltage L2-N", "unit": "V", "register_type": INPUT, "register_count": 1, "read_interval_seconds": 10},
                    {"address": 1006, "label": "Gen Voltage L3-N", "unit": "V", "register_type": INPUT, "register_count": 1, "read_interval_seconds": 10},
                    {"address": 1007, "label": "Grid Frequency", "unit": "Hz", "register_type": INPUT, "register_count": 1, "read_interval_seconds": 10},
                    {"address": 1008, "label": "Active Power", "unit": "kW", "register_type": INPUT, "register_count": 1, "read_interval_seconds": 10},
                    {"address": 1009, "label": "Run Hours (Hi)", "unit": None, "register_type": INPUT, "register_count": 1, "read_interval_seconds": 30},
                    {"address": 1010, "label": "Run Hours (Lo)", "unit": None, "register_type": INPUT, "register_count": 1, "read_interval_seconds": 30},
                    {"address": 1011, "label": "Fuel Level", "unit": "%", "register_type": INPUT, "register_count": 1, "read_interval_seconds": 10},
                    {"address": 1012, "label": "Fuel Rate", "unit": "L/h", "register_type": INPUT, "register_count": 1, "read_interval_seconds": 10},
                    {"address": 1013, "label": "Intake Manifold Temp", "unit": "C", "register_type": INPUT, "register_count": 1, "read_interval_seconds": 10},
                    {"address": 1014, "label": "% Load", "unit": "%", "register_type": INPUT, "register_count": 1, "read_interval_seconds": 10},
                    {"address": 1015, "label": "Current L1", "unit": "A", "register_type": INPUT, "register_count": 1, "read_interval_seconds": 10},
                    {"address": 1016, "label": "Current L2", "unit": "A", "register_type": INPUT, "register_count": 1, "read_interval_seconds": 10},
                    {"address": 1017, "label": "Current L3", "unit": "A", "register_type": INPUT, "register_count": 1, "read_interval_seconds": 10},
                    {"address": 1018, "label": "kW Total", "unit": "kW", "register_type": INPUT, "register_count": 1, "read_interval_seconds": 10},
                    {"address": 1019, "label": "kVAR Total", "unit": "kVAR", "register_type": INPUT, "register_count": 1, "read_interval_seconds": 10},
                ],
                "is_builtin": True,
            },
            {
                "id": uuid.uuid4(),
                "slug": "generator-standard",
                "name": "Generator Standard (with Run Status & Alarms)",
                "description": "Baseline analog telemetry plus a running-status discrete input "
                "and a handful of common alarm bits, for gensets whose gateway exposes "
                "status/alarms as discrete inputs.",
                "category": "generator",
                "registers": [
                    {"address": 0, "label": "RPM", "unit": "rpm", "register_type": INPUT, "register_count": 1, "read_interval_seconds": 10},
                    {"address": 1, "label": "Coolant Temp", "unit": "C", "register_type": INPUT, "register_count": 1, "read_interval_seconds": 10},
                    {"address": 2, "label": "Oil Pressure", "unit": "psi", "register_type": INPUT, "register_count": 1, "read_interval_seconds": 10},
                    {"address": 3, "label": "Battery Voltage", "unit": "V", "register_type": INPUT, "register_count": 1, "read_interval_seconds": 10},
                    {"address": 4, "label": "Fuel Level", "unit": "%", "register_type": INPUT, "register_count": 1, "read_interval_seconds": 10},
                    {"address": 5, "label": "Running Status", "unit": None, "register_type": DISCRETE, "register_count": 1, "read_interval_seconds": 5, "role": "running_status"},
                    {"address": 6, "label": "Low Oil Pressure", "unit": None, "register_type": DISCRETE, "register_count": 1, "read_interval_seconds": 5, "role": "alarm"},
                    {"address": 7, "label": "High Coolant Temp", "unit": None, "register_type": DISCRETE, "register_count": 1, "read_interval_seconds": 5, "role": "alarm"},
                    {"address": 8, "label": "Overspeed", "unit": None, "register_type": DISCRETE, "register_count": 1, "read_interval_seconds": 5, "role": "alarm"},
                    {"address": 9, "label": "Low Fuel", "unit": None, "register_type": DISCRETE, "register_count": 1, "read_interval_seconds": 5, "role": "alarm"},
                    {"address": 10, "label": "Common Shutdown", "unit": None, "register_type": DISCRETE, "register_count": 1, "read_interval_seconds": 5, "role": "alarm"},
                ],
                "is_builtin": True,
            },
        ],
    )


def downgrade() -> None:
    op.drop_index("ix_modbus_profile_templates_slug", table_name="modbus_profile_templates")
    op.drop_table("modbus_profile_templates")
