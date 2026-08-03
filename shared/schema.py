"""
Canonical, cross-service reference for GenMonitoring's on-the-wire contracts.

This module is NOT imported by the api/bridge/agent containers at runtime (they
each ship their own copy of the payload models appropriate to their language/
framework). It exists as the single source of truth a human (or a future
service) can read to see every topic name, payload shape, and identifier
format in one place, so the three independently-deployed packages don't drift
out of sync with each other.
"""

import re

# ---------------------------------------------------------------------------
# Device identity
# ---------------------------------------------------------------------------

# Field agents derive this from the CM4's /proc/cpuinfo serial (last 8 hex
# chars, split 4+4). "GM" distinguishes this platform's device keys from the
# reference AetherLynk platform's "AL-XXXX-XXXX" format.
DEVICE_KEY_REGEX = re.compile(r"^GM-[A-F0-9]{4}-[A-F0-9]{4}$")

# ---------------------------------------------------------------------------
# MQTT topic layout (prefix distinct from AetherLynk's "aetherlynk/")
# ---------------------------------------------------------------------------

TOPIC_PREFIX = "genmon"


def topic_data(device_key: str) -> str:
    """Telemetry readings. Publisher: agent. Subscriber: bridge. QoS 1, no retain."""
    return f"{TOPIC_PREFIX}/{device_key}/data"


def topic_io(device_key: str) -> str:
    """GPIO IN1/OUT1 state transitions. Publisher: agent. Subscriber: bridge. QoS 1, no retain."""
    return f"{TOPIC_PREFIX}/{device_key}/io"


def topic_cmd(device_key: str) -> str:
    """Start/stop/renew commands. Publisher: api. Subscriber: agent. QoS 1, RETAINED."""
    return f"{TOPIC_PREFIX}/{device_key}/cmd"


def topic_cmd_ack(device_key: str) -> str:
    """Command acknowledgement/result. Publisher: agent. Subscriber: bridge. QoS 1, no retain."""
    return f"{TOPIC_PREFIX}/{device_key}/cmd/ack"


# ---------------------------------------------------------------------------
# Payload shapes (documented as plain dict schemas; each service implements
# its own pydantic/TypeScript equivalent of these)
# ---------------------------------------------------------------------------

# genmon/{device_key}/data
TELEMETRY_PAYLOAD_EXAMPLE = {
    "device_key": "GM-1A2B-3C4D",
    "register_address": 40010,
    "register_type": 4,  # 0=coil, 1=discrete_input, 3=input_register, 4=holding_register
    "register_friendly_name": "Fuel Level",
    "value": 82.5,
    "unit": "%",
    "timestamp_utc": "2026-08-03T14:02:11.340Z",
}

# genmon/{device_key}/io
IO_EVENT_PAYLOAD_EXAMPLE = {
    "device_key": "GM-1A2B-3C4D",
    "channel": "IN1",  # "IN1" | "OUT1"
    "state": True,  # True = closed/energized (2-wire "run"), False = open ("stop")
    "observed_at_utc": "2026-08-03T14:02:11.340Z",
}

# genmon/{device_key}/cmd  (api -> agent, retained)
COMMAND_PAYLOAD_EXAMPLE = {
    "command_id": "4a6e3e2a-9c2e-4b1a-9b0e-2f2a6e3e2a9c",
    "type": "start_session",  # "start_session" | "stop_session" | "renew_session"
    "session_id": "4a6e3e2a-9c2e-4b1a-9b0e-2f2a6e3e2a9c",  # == command_id
    "ttl_seconds": 300,  # local deadman lease; agent hard-caps this at 1800s
    "issued_at": "2026-08-03T14:02:10.000Z",
}

# genmon/{device_key}/cmd/ack  (agent -> bridge)
COMMAND_ACK_PAYLOAD_EXAMPLE = {
    "command_id": "4a6e3e2a-9c2e-4b1a-9b0e-2f2a6e3e2a9c",
    "session_id": "4a6e3e2a-9c2e-4b1a-9b0e-2f2a6e3e2a9c",
    "result": "applied",  # "applied" | "rejected" | "expired_no_renewal"
    "out1_state": True,
    "in1_state": True,
    "applied_at": "2026-08-03T14:02:11.340Z",
}

# ---------------------------------------------------------------------------
# Modbus register types (unchanged encoding from the AetherLynk reference)
# ---------------------------------------------------------------------------

REGISTER_TYPE_COIL = 0
REGISTER_TYPE_DISCRETE_INPUT = 1
REGISTER_TYPE_INPUT_REGISTER = 3
REGISTER_TYPE_HOLDING_REGISTER = 4

# Per-register "transport" shape returned by GET /devices/{device_key}/config,
# discriminated by "kind":
RTU_TRANSPORT_EXAMPLE = {
    "kind": "rtu",
    "serial_port": "/dev/ttyAMA5",  # the CM4 board's only onboard RS-485 port
    "baudrate": 9600,
    "parity": "N",
    "stopbits": 1,
    "slave_id": 3,
}
TCP_TRANSPORT_EXAMPLE = {
    "kind": "tcp",
    "host": "192.168.1.50",
    "port": 502,
    "slave_id": 1,
}
