"""Idempotent seeding of the built-in Modbus profile templates.

Runs on every FastAPI startup (see app.main lifespan) and upserts by
`slug`, so re-deploys keep these three built-ins current without ever
duplicating rows or clobbering user-authored custom templates.
"""
import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import ModbusProfileTemplate

logger = logging.getLogger("genmon.template_seed")

# register_type: 0=coil, 1=discrete_input, 3=input_register, 4=holding_register
HOLDING = 4
INPUT = 3
DISCRETE = 1

BUILTIN_TEMPLATES = [
    {
        "slug": "generic-generator",
        "name": "Generic Generator",
        "description": "Generic 10-register holding-register profile suitable as a starting "
        "point for most single-phase/three-phase gensets with a basic Modbus gateway.",
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
    },
    {
        "slug": "cat-emcp42",
        "name": "Caterpillar EMCP 4.2",
        "description": "20-register input-register profile modeled on the Caterpillar EMCP 4.2 "
        "genset controller's common Modbus map.",
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
    },
    {
        "slug": "generator-standard",
        "name": "Generator Standard (with Run Status & Alarms)",
        "description": "Baseline analog telemetry plus a running-status discrete input and a "
        "handful of common alarm bits, for gensets whose gateway exposes status/alarms as "
        "discrete inputs.",
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
    },
]


async def seed_builtin_templates(db: AsyncSession) -> None:
    for tpl in BUILTIN_TEMPLATES:
        result = await db.execute(
            select(ModbusProfileTemplate).where(ModbusProfileTemplate.slug == tpl["slug"])
        )
        existing = result.scalar_one_or_none()
        if existing is None:
            db.add(
                ModbusProfileTemplate(
                    slug=tpl["slug"],
                    name=tpl["name"],
                    description=tpl["description"],
                    category=tpl["category"],
                    registers=tpl["registers"],
                    is_builtin=True,
                )
            )
            logger.info("seeded builtin template %s", tpl["slug"])
        else:
            existing.name = tpl["name"]
            existing.description = tpl["description"]
            existing.category = tpl["category"]
            existing.registers = tpl["registers"]
    await db.commit()
