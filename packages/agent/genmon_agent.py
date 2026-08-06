#!/usr/bin/env python3
"""
genmon_agent.py -- GenMonitoring Raspberry Pi CM4 field agent.

Ally Energy generator-monitoring platform. This single-file agent runs on a
Raspberry Pi CM4 mounted on a Waveshare "Compute Module 4 PoE 4G Board" and:

  * Derives a stable device identity (GM-XXXX-XXXX) from the CPU serial.
  * Pre-registers with the GenMonitoring API and polls it for configuration.
  * Polls one or more Modbus (RTU and/or TCP) registers on a schedule and
    publishes telemetry over MQTT.
  * Drives an isolated digital output (OUT1 / BCM GPIO6) that commands a
    generator start/stop relay, gated by a local "deadman" session timer
    that is completely independent of network connectivity.
  * Watches an isolated digital input (IN1 / BCM GPIO23) that mirrors the
    generator's actual run/stop state and reports transitions immediately.
  * Manages an optional M.2 cellular modem (SIMCom SIM7600G-H-M.2) via
    ModemManager/NetworkManager, and prioritizes Ethernet over cellular
    using measured reachability.
  * Self-updates in place when the backend publishes a new agent version.

Hardware safety notes (read before touching wiring):
  - IN1 (BCM GPIO23) is wired in PARALLEL with the generator controller's own
    2-wire remote-start contact loop, so it reflects the actual run/stop
    state of the generator regardless of who/what commanded it (this agent,
    a local switch, or the controller's own logic). It is informational
    only in this agent and NEVER drives OUT1 automatically.
  - OUT1 (BCM GPIO6) is the ONLY actuation path in this agent. Driving it
    closed/energized commands "run"; open commands "stop". It always boots
    OPEN (stopped) and only GpioController.start_session()/stop_session()
    (here: handle_start/handle_renew/handle_stop) are permitted to touch it.
  - GPIO23 (IN1) can collide with SPI0's chip-select-1 function on this
    board per Waveshare's documentation. This agent and its installer must
    NEVER enable SPI0's dual-chip-select overlay. See install.sh.
  - Because OUT1 drives a physical generator-start relay, the pin mapping
    MUST be verified against real hardware (see install.sh's commissioning
    step) before OUT1 is ever wired to a live start circuit.
"""
from __future__ import annotations

import argparse
import collections
import configparser
import hashlib
import json
import logging
import os
import py_compile
import queue
import random
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path

import paho.mqtt.client as mqtt
import requests
from gpiozero import Button, DigitalOutputDevice
from pymodbus.client import ModbusSerialClient, ModbusTcpClient

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

AGENT_VERSION = "1.0.0"

DEFAULT_API_BASE = "https://api.allyoperations.com"
DEFAULT_CONFIG_PATH = "/etc/genmon/device.conf"
DEFAULT_LOG_DIR = "/var/log/genmon"

# How often (seconds) we re-probe interface reachability and re-prioritize
# Ethernet vs. cellular routes. Not part of the shared platform contract --
# purely a local tuning knob -- so it is a module constant, not a config key.
ROUTE_PRIORITY_INTERVAL_SECONDS = 300

PRE_REGISTER_MAX_ATTEMPTS = 10
PRE_REGISTER_BACKOFF_CAP_SECONDS = 60

# Modbus register_type -> pymodbus client method, per the shared platform
# contract: 0=coils, 1=discrete inputs, 3=input registers, 4=holding
# registers. This agent is READ-ONLY: no write_coil/write_register call
# exists anywhere in this file. The only physical actuation is the GPIO
# OUT1 path (GpioController), which is architecturally separate from
# generic Modbus polling.
REGISTER_TYPE_METHOD_MAP = {
    0: "read_coils",
    1: "read_discrete_inputs",
    3: "read_input_registers",
    4: "read_holding_registers",
}

logger = logging.getLogger("genmon_agent")


# ---------------------------------------------------------------------------
# Logging setup
# ---------------------------------------------------------------------------

def setup_logging(log_dir: str = DEFAULT_LOG_DIR) -> logging.Logger:
    """Configure the module logger: stdout (captured by journald under
    systemd) plus a rotating file under /var/log/genmon for submit-logs."""
    logger.setLevel(logging.INFO)
    fmt = logging.Formatter(
        "%(asctime)s %(levelname)-8s %(name)s: %(message)s", "%Y-%m-%dT%H:%M:%S%z"
    )

    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.setFormatter(fmt)
    logger.addHandler(stream_handler)

    try:
        Path(log_dir).mkdir(parents=True, exist_ok=True)
        file_handler = RotatingFileHandler(
            str(Path(log_dir) / "genmon-agent.log"), maxBytes=5 * 1024 * 1024, backupCount=5
        )
        file_handler.setFormatter(fmt)
        logger.addHandler(file_handler)
    except OSError as exc:
        logger.warning("Could not attach file log handler at %s: %s", log_dir, exc)

    return logger


def iso_utc_now() -> str:
    """RFC3339/ISO8601 UTC timestamp with a trailing 'Z', e.g. 2026-08-03T12:00:00Z."""
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_iso8601(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        logger.warning("Could not parse ISO8601 timestamp: %r", value)
        return None


def get_local_ip() -> str:
    """Best-effort local outbound IP, used for heartbeat bodies. Uses a UDP
    'connect' which does not actually transmit any packet -- it just asks
    the kernel to pick a source address/route for that destination."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.settimeout(1.0)
            sock.connect(("8.8.8.8", 80))
            return sock.getsockname()[0]
    except OSError:
        return "0.0.0.0"


# ---------------------------------------------------------------------------
# DeviceIdentity
# ---------------------------------------------------------------------------

class DeviceIdentity:
    """Derives the GM-XXXX-XXXX device key from the CPU serial in
    /proc/cpuinfo, using the same algorithm AetherLynk used for its AL-
    prefixed keys (last 8 hex chars of the serial, split 4+4). Falls back to
    a sha256-of-hostname derivation on non-Pi dev/test hosts where
    /proc/cpuinfo has no usable Serial line."""

    CPUINFO_PATH = "/proc/cpuinfo"
    PREFIX = "GM"

    @classmethod
    def get_cpu_serial(cls) -> str:
        try:
            with open(cls.CPUINFO_PATH, "r") as fh:
                for line in fh:
                    if line.lower().startswith("serial"):
                        parts = line.split(":", 1)
                        if len(parts) == 2:
                            serial = parts[1].strip()
                            # A real Pi always has a serial; an all-zero
                            # value indicates a QEMU/non-Pi kernel that
                            # still exposes a fake cpuinfo Serial field.
                            if serial and set(serial) != {"0"}:
                                return serial
        except OSError:
            pass
        return ""

    @classmethod
    def derive_device_key(cls) -> tuple[str, str]:
        """Returns (device_key, cpu_serial_or_fallback_identifier)."""
        cpu_serial = cls.get_cpu_serial()
        if cpu_serial:
            hex_tail = cpu_serial[-8:].rjust(8, "0").upper()
            device_key = f"{cls.PREFIX}-{hex_tail[:4]}-{hex_tail[4:]}"
            return device_key, cpu_serial

        # Fallback for non-Pi dev/test environments: sha256 of hostname.
        hostname = socket.gethostname()
        digest = hashlib.sha256(hostname.encode("utf-8")).hexdigest()
        hex_tail = digest[-8:].upper()
        device_key = f"{cls.PREFIX}-{hex_tail[:4]}-{hex_tail[4:]}"
        fallback_serial = f"hostname:{hostname}"
        logger.warning(
            "No usable CPU serial found in %s; deriving a dev/test device "
            "identity from hostname (%s) instead. This is expected off "
            "real Pi hardware and NOT expected in production.",
            cls.CPUINFO_PATH, hostname,
        )
        return device_key, fallback_serial


# ---------------------------------------------------------------------------
# ConfigStore
# ---------------------------------------------------------------------------

class ConfigStore:
    """Wraps /etc/genmon/device.conf (INI via configparser).

    Sections: [device] [auth] [mqtt] [gpio] [network] [cellular].

    The [gpio] section holds hardware-safety-critical values that are
    LOCAL ONLY -- session_ttl_max_seconds in particular is the hard ceiling
    the server can never exceed. Nothing derived from a server response may
    ever be written into [gpio]; set_value() enforces this unconditionally.
    """

    SECTION_DEFAULTS = {
        "device": {
            "device_key": "",
            "cpu_serial": "",
            "api_base_url": DEFAULT_API_BASE,
            "agent_version": AGENT_VERSION,
        },
        "auth": {
            "device_bearer_token": "",
        },
        "mqtt": {
            "host": "",
            "port": "1883",
            "tls": "false",
        },
        "gpio": {
            "in1_pin": "23",
            "in1_debounce_ms": "50",
            "out1_pin": "6",
            "session_ttl_seconds": "300",
            "session_ttl_max_seconds": "1800",
        },
        "network": {
            "config_refresh_interval_seconds": "60",
            "heartbeat_interval_seconds": "60",
        },
        "cellular": {
            "apn": "",
        },
    }

    # Sections a server response is never allowed to modify.
    PROTECTED_SECTIONS = {"gpio"}

    def __init__(self, path: str):
        self.path = Path(path)
        self._lock = threading.RLock()
        self._parser = configparser.ConfigParser()
        self._apply_defaults()
        self._load()

    def _apply_defaults(self) -> None:
        for section, values in self.SECTION_DEFAULTS.items():
            if not self._parser.has_section(section):
                self._parser.add_section(section)
            for key, value in values.items():
                if not self._parser.has_option(section, key):
                    self._parser.set(section, key, value)

    def _load(self) -> None:
        with self._lock:
            if self.path.exists():
                self._parser.read(self.path)
                # Re-fill anything missing from an older/partial file on disk.
                self._apply_defaults()
            else:
                logger.warning(
                    "Config file %s not found; using in-memory defaults. "
                    "install.sh normally creates this from device.conf.example.",
                    self.path,
                )

    def save(self) -> None:
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(self.path.suffix + ".tmp")
            with open(tmp, "w") as fh:
                self._parser.write(fh)
            os.replace(tmp, self.path)

    def get(self, section: str, key: str, fallback: str = "") -> str:
        with self._lock:
            return self._parser.get(section, key, fallback=fallback)

    def getint(self, section: str, key: str, fallback: int = 0) -> int:
        with self._lock:
            return self._parser.getint(section, key, fallback=fallback)

    def getboolean(self, section: str, key: str, fallback: bool = False) -> bool:
        with self._lock:
            return self._parser.getboolean(section, key, fallback=fallback)

    def set_value(self, section: str, key: str, value: str) -> bool:
        """Set a single key. Returns True if the value actually changed.
        Refuses (and logs) any write targeting PROTECTED_SECTIONS -- GPIO
        safety values must only ever be edited locally by an operator via
        device.conf, never by code processing a server response."""
        with self._lock:
            if section in self.PROTECTED_SECTIONS:
                logger.error(
                    "Refusing to modify protected config section [%s] (key=%s): "
                    "GPIO/safety values are local-only and must never be "
                    "overwritten by server-supplied configuration.",
                    section, key,
                )
                return False
            if not self._parser.has_section(section):
                self._parser.add_section(section)
            current = self._parser.get(section, key, fallback=None)
            if current == value:
                return False
            self._parser.set(section, key, value)
            return True


# ---------------------------------------------------------------------------
# ProtocolDriver / ModbusTcpDriver / ModbusRtuDriver
# ---------------------------------------------------------------------------

class ProtocolReadError(RuntimeError):
    """Raised for any failed Modbus read: timeout, CRC error, exception
    response, or a closed/unopenable port."""


class ProtocolDriver(ABC):
    """Read-only Modbus transport abstraction. There is intentionally no
    write_coil/write_register method anywhere in this hierarchy -- physical
    actuation happens exclusively via GpioController's OUT1 path."""

    @abstractmethod
    def read(self, register_type: int, address: int, count: int, slave_id: int) -> list[int]:
        """Return a list of raw integer values (0/1 for coil/discrete-input
        reads, native register values for input/holding-register reads)."""
        raise NotImplementedError

    @staticmethod
    def _decode_response(response, register_type: int, count: int) -> list[int]:
        if response is None or response.isError():
            raise ProtocolReadError(f"Modbus exception/error response: {response!r}")
        if hasattr(response, "bits"):
            return [1 if bit else 0 for bit in response.bits[:count]]
        return list(response.registers)


class ModbusTcpDriver(ProtocolDriver):
    """pymodbus ModbusTcpClient, pooled per (host, port)."""

    _instances: dict[tuple[str, int], "ModbusTcpDriver"] = {}
    _instances_lock = threading.Lock()

    def __init__(self, host: str, port: int):
        self.host = host
        self.port = port
        # Even over TCP, a single pymodbus client instance is not safe for
        # concurrent transactions from multiple threads -- one connection,
        # one in-flight request/response at a time.
        self._lock = threading.Lock()
        self._client = ModbusTcpClient(host=host, port=port, timeout=3)

    @classmethod
    def get(cls, host: str, port: int) -> "ModbusTcpDriver":
        key = (host, port)
        with cls._instances_lock:
            inst = cls._instances.get(key)
            if inst is None:
                inst = cls(host, port)
                cls._instances[key] = inst
            return inst

    def read(self, register_type: int, address: int, count: int, slave_id: int) -> list[int]:
        method_name = REGISTER_TYPE_METHOD_MAP.get(register_type)
        if method_name is None:
            raise ProtocolReadError(f"Unsupported register_type={register_type!r}")
        with self._lock:
            if not self._client.connected:
                if not self._client.connect():
                    raise ProtocolReadError(f"Unable to connect to Modbus TCP {self.host}:{self.port}")
            method = getattr(self._client, method_name)
            try:
                response = method(address, count=count, slave=slave_id)
            except Exception as exc:  # pymodbus can raise ModbusIOException et al.
                raise ProtocolReadError(
                    f"Modbus TCP read failed ({self.host}:{self.port}, slave={slave_id}, "
                    f"addr={address}): {exc}"
                ) from exc
            return self._decode_response(response, register_type, count)


class ModbusRtuDriver(ProtocolDriver):
    """pymodbus ModbusSerialClient, pooled per serial_port path.

    RS-485 is a shared half-duplex bus: two pollers targeting different
    slave IDs on the SAME serial port must never key frames onto the wire
    simultaneously, or their requests/responses will collide. Every read on
    a given port therefore takes a threading.Lock scoped to that port
    (self._port_lock below), for the lifetime of the pooled driver.
    """

    _instances: dict[str, "ModbusRtuDriver"] = {}
    _instances_lock = threading.Lock()

    def __init__(self, serial_port: str, baudrate: int, parity: str, stopbits: int):
        self.serial_port = serial_port
        self._port_lock = threading.Lock()
        self._client = ModbusSerialClient(
            port=serial_port,
            baudrate=baudrate,
            parity=parity,
            stopbits=stopbits,
            bytesize=8,
            timeout=2,
        )

    @classmethod
    def get(cls, serial_port: str, baudrate: int, parity: str, stopbits: int) -> "ModbusRtuDriver":
        with cls._instances_lock:
            inst = cls._instances.get(serial_port)
            if inst is None:
                inst = cls(serial_port, baudrate, parity, stopbits)
                cls._instances[serial_port] = inst
            return inst

    def read(self, register_type: int, address: int, count: int, slave_id: int) -> list[int]:
        method_name = REGISTER_TYPE_METHOD_MAP.get(register_type)
        if method_name is None:
            raise ProtocolReadError(f"Unsupported register_type={register_type!r}")
        with self._port_lock:
            if not self._client.connected:
                if not self._client.connect():
                    raise ProtocolReadError(f"Unable to open serial port {self.serial_port}")
            method = getattr(self._client, method_name)
            try:
                response = method(address, count=count, slave=slave_id)
            except Exception as exc:
                raise ProtocolReadError(
                    f"Modbus RTU read failed ({self.serial_port}, slave={slave_id}, "
                    f"addr={address}): {exc}"
                ) from exc
            return self._decode_response(response, register_type, count)


# ---------------------------------------------------------------------------
# RegisterPoller
# ---------------------------------------------------------------------------

class RegisterPoller:
    """One instance per configured Modbus register. Self-reschedules a
    threading.Timer at its own read_interval_seconds and pushes each
    reading into a shared queue.Queue, which a separate reporting timer
    (GenMonAgent._flush_telemetry_buffer) drains by publishing each reading
    individually to genmon/{device_key}/data."""

    def __init__(
        self,
        register_cfg: dict,
        driver_factory,
        buffer: "queue.Queue[dict]",
        stop_event: threading.Event,
        device_key: str,
    ):
        self.cfg = register_cfg
        self.driver_factory = driver_factory
        self.buffer = buffer
        self.stop_event = stop_event
        self.device_key = device_key
        self._timer: threading.Timer | None = None
        self._lock = threading.Lock()

    def start(self) -> None:
        # Small random jitter on the very first read so a fleet of freshly
        # (re)configured pollers on the same bus don't all fire at once.
        self._schedule(random.uniform(0.0, 2.0))

    def stop(self) -> None:
        with self._lock:
            if self._timer is not None:
                self._timer.cancel()
                self._timer = None

    def _schedule(self, delay: float) -> None:
        if self.stop_event.is_set():
            return
        with self._lock:
            self._timer = threading.Timer(delay, self._poll_once)
            self._timer.daemon = True
            self._timer.start()

    def _poll_once(self) -> None:
        if self.stop_event.is_set():
            return
        value = None
        try:
            driver = self.driver_factory()
            raw = driver.read(
                self.cfg["register_type"],
                self.cfg["register_address"],
                self.cfg["register_count"],
                self.cfg["slave_id"],
            )
            value = self._decode_value(raw)
        except ProtocolReadError as exc:
            logger.warning(
                "Register read failed (%s, addr=%s): %s",
                self.cfg.get("register_friendly_name"), self.cfg.get("register_address"), exc,
            )
        except Exception:
            logger.exception(
                "Unexpected error polling register %s (addr=%s)",
                self.cfg.get("register_friendly_name"), self.cfg.get("register_address"),
            )

        reading = {
            "device_key": self.device_key,
            "register_address": self.cfg["register_address"],
            "register_type": self.cfg["register_type"],
            "register_friendly_name": self.cfg["register_friendly_name"],
            "value": value,
            "unit": self.cfg.get("unit"),
            "timestamp_utc": iso_utc_now(),
        }
        self.buffer.put(reading)
        self._schedule(max(1, int(self.cfg["read_interval_seconds"])))

    @staticmethod
    def _decode_value(raw: list[int]) -> float | None:
        if not raw:
            return None
        if len(raw) == 1:
            return float(raw[0])
        if len(raw) == 2:
            # Two 16-bit words combined big-endian into an unsigned 32-bit
            # value. NOTE: this assumes high-word-first ordering and no
            # implied decimal scale -- a common convention for genset/ATS
            # controllers, but controllers using a different word order or
            # a scale factor will need a per-register transform added here
            # once that convention is known for the specific hardware being
            # integrated. Documented rather than silently guessed further.
            return float((raw[0] << 16) | raw[1])
        # Wider-than-32-bit register blocks: return the first word rather
        # than silently dropping the reading.
        return float(raw[0])


# ---------------------------------------------------------------------------
# GpioController -- the deadman failsafe. Read the module docstring's safety
# notes before changing anything here.
# ---------------------------------------------------------------------------

def _configure_lgpio_pin_factory() -> None:
    """Force gpiozero onto the lgpio pin factory (not RPi.GPIO/native/
    pigpio). lgpio talks to the kernel's /dev/gpiochip* character devices,
    which is what Raspberry Pi OS Bookworm expects and what this design
    requires on CM4. We force this explicitly at GpioController construction
    time rather than trusting gpiozero's auto-detection order, because a
    different backend could silently change pull-resistor/debounce/edge
    behavior on this safety-critical I/O. Deferred to first use (not module
    import time) so the file remains import/syntax-checkable off real
    GPIO hardware (e.g. in CI)."""
    from gpiozero import Device
    from gpiozero.pins.lgpio import LGPIOFactory

    Device.pin_factory = LGPIOFactory()


class GpioController:
    """Owns IN1 (BCM GPIO23, informational run/stop sense input) and OUT1
    (BCM GPIO6, the sole generator start/stop actuation output), plus the
    local session "deadman" timer that is the most safety-critical logic in
    this agent.

    Session lifecycle:
      * start_session (handle_start): drive OUT1 closed, record session_id,
        (re)arm a threading.Timer for min(server ttl_seconds, local
        session_ttl_max_seconds ceiling).
      * renew_session (handle_renew): same session_id only -- cancel and
        re-arm the timer with a fresh (clamped) ttl_seconds.
      * stop_session (handle_stop): cancel the timer immediately and drive
        OUT1 open, regardless of TTL state or session_id match -- a stop
        must always be able to stop.
      * Deadman expiry (_expire_session): if the timer fires with no
        renewal, OUT1 is forced open immediately and entirely locally, with
        NO dependency on MQTT/HTTP connectivity whatsoever -- this happens
        purely in-process via gpiozero. Only *after* that local action do we
        attempt to report it (ack_callback/io_event_callback), which may be
        queued by the caller (CommandChannel) if currently disconnected.

    On process (re)start for ANY reason, OUT1 always initializes open via
    DigitalOutputDevice(..., initial_value=False) -- no attempt is ever made
    to restore a prior in-flight session's closed state.

    IN1 is informational only and NEVER auto-drives OUT1 -- there is no
    local echo/mirroring logic here, by design, to keep the failsafe simple
    and singular.
    """

    def __init__(self, config: ConfigStore):
        _configure_lgpio_pin_factory()

        self.config = config
        in1_pin = config.getint("gpio", "in1_pin", fallback=23)
        out1_pin = config.getint("gpio", "out1_pin", fallback=6)
        debounce_s = config.getint("gpio", "in1_debounce_ms", fallback=50) / 1000.0

        self._lock = threading.RLock()
        self._session_id: str | None = None
        self._timer: threading.Timer | None = None

        # Callbacks wired up by the orchestrator after both GpioController
        # and CommandChannel exist (avoids a constructor cycle). Left as
        # None-safe no-ops until then.
        self.io_event_callback = None  # fn(channel: str, state: bool) -> None
        self.ack_callback = None  # fn(command_id, session_id, result, out1_state, in1_state, applied_at) -> None

        # IN1: pulled to ground = closed/active. Button(pull_up=True) means
        # gpiozero drives an internal pull-up and treats a LOW (grounded)
        # reading as "active" -- i.e. in1.is_active == True exactly when the
        # dry contact is closed, matching the hardware fact sheet directly.
        self.in1 = Button(in1_pin, pull_up=True, bounce_time=debounce_s)
        self.in1.when_activated = self._on_in1_activated
        self.in1.when_deactivated = self._on_in1_deactivated

        # OUT1: ALWAYS boots open/de-energized. Per this board's opto-
        # isolated output design, driving GPIO6 HIGH (DigitalOutputDevice
        # .value = True) asserts the output "closed/energized" (commands
        # "run"); LOW/False asserts "open" (commands "stop"). This polarity
        # assumption MUST be verified against real hardware during
        # commissioning (see install.sh) before OUT1 is ever wired to a
        # live generator start circuit.
        self.out1 = DigitalOutputDevice(out1_pin, initial_value=False)

    # -- state accessors -----------------------------------------------

    @property
    def in1_state(self) -> bool:
        return bool(self.in1.is_active)

    @property
    def out1_state(self) -> bool:
        return bool(self.out1.value)

    @property
    def session_id(self) -> str | None:
        with self._lock:
            return self._session_id

    # -- IN1 sensing (informational only, sub-second latency) -----------

    def _on_in1_activated(self) -> None:
        logger.info("IN1 transitioned to ACTIVE (generator running per remote-start loop).")
        if self.io_event_callback:
            self.io_event_callback("IN1", True)

    def _on_in1_deactivated(self) -> None:
        logger.info("IN1 transitioned to INACTIVE (generator stopped per remote-start loop).")
        if self.io_event_callback:
            self.io_event_callback("IN1", False)

    # -- OUT1 command handlers: the ONLY code paths permitted to touch out1.value --

    def handle_start(self, session_id: str, ttl_seconds) -> str:
        with self._lock:
            if not session_id or ttl_seconds is None:
                logger.warning("Rejecting start_session: missing session_id/ttl_seconds.")
                return "rejected"
            try:
                ttl_seconds = int(ttl_seconds)
            except (TypeError, ValueError):
                logger.warning("Rejecting start_session: non-numeric ttl_seconds=%r.", ttl_seconds)
                return "rejected"
            if ttl_seconds <= 0:
                logger.warning("Rejecting start_session: non-positive ttl_seconds=%r.", ttl_seconds)
                return "rejected"

            effective_ttl = self._clamp_ttl(ttl_seconds)
            was_active = self.out1.value
            self._cancel_timer_locked()
            self.out1.on()
            self._session_id = session_id
            self._arm_timer_locked(effective_ttl)
            logger.info(
                "start_session applied: session_id=%s ttl_seconds=%s (requested=%s) OUT1=CLOSED",
                session_id, effective_ttl, ttl_seconds,
            )
            if not was_active and self.io_event_callback:
                self.io_event_callback("OUT1", True)
            return "applied"

    def handle_renew(self, session_id: str, ttl_seconds) -> str:
        with self._lock:
            if self._session_id != session_id or not self.out1.value:
                logger.warning(
                    "Rejecting renew_session for session_id=%s: no matching active session "
                    "(current=%s, out1=%s).",
                    session_id, self._session_id, self.out1.value,
                )
                return "rejected"
            if ttl_seconds is None:
                logger.warning("Rejecting renew_session %s: missing ttl_seconds.", session_id)
                return "rejected"
            try:
                ttl_seconds = int(ttl_seconds)
            except (TypeError, ValueError):
                logger.warning("Rejecting renew_session %s: non-numeric ttl_seconds=%r.", session_id, ttl_seconds)
                return "rejected"
            if ttl_seconds <= 0:
                logger.warning("Rejecting renew_session %s: non-positive ttl_seconds=%r.", session_id, ttl_seconds)
                return "rejected"

            effective_ttl = self._clamp_ttl(ttl_seconds)
            self._cancel_timer_locked()
            self._arm_timer_locked(effective_ttl)
            logger.info("renew_session applied: session_id=%s ttl_seconds=%s (requested=%s)",
                        session_id, effective_ttl, ttl_seconds)
            return "applied"

    def handle_stop(self, session_id: str | None) -> str:
        # A stop must always be able to stop: we intentionally do NOT
        # require session_id to match here (unlike renew) -- if the backend
        # or an operator wants OUT1 open, it goes open, full stop.
        with self._lock:
            self._cancel_timer_locked()
            was_active = self.out1.value
            self.out1.off()
            self._session_id = None
            logger.info("stop_session applied (requested session_id=%s): OUT1=OPEN", session_id)
            if was_active and self.io_event_callback:
                self.io_event_callback("OUT1", False)
            return "applied"

    def close(self) -> None:
        """Called during graceful shutdown. Forces OUT1 open (defensive --
        no one will be able to renew a session while the process is down)
        and releases the gpiozero device handles."""
        with self._lock:
            self._cancel_timer_locked()
            try:
                self.out1.off()
            except Exception:
                logger.exception("Error forcing OUT1 open during close()")
            self._session_id = None
        try:
            self.out1.close()
        except Exception:
            logger.exception("Error closing OUT1 gpiozero device")
        try:
            self.in1.close()
        except Exception:
            logger.exception("Error closing IN1 gpiozero device")

    # -- internal timer plumbing -----------------------------------------

    def _clamp_ttl(self, requested_ttl_seconds: int) -> int:
        ceiling = self.config.getint("gpio", "session_ttl_max_seconds", fallback=1800)
        return max(1, min(requested_ttl_seconds, ceiling))

    def _arm_timer_locked(self, ttl_seconds: int) -> None:
        self._timer = threading.Timer(ttl_seconds, self._expire_session)
        self._timer.daemon = True
        self._timer.start()

    def _cancel_timer_locked(self) -> None:
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None

    def _expire_session(self) -> None:
        """Fires with NO dependency on MQTT/HTTP connectivity: driving OUT1
        open is a purely local, in-process gpiozero call. Reporting the
        expiry (ack/IO event) happens afterward and may be queued by the
        CommandChannel if currently disconnected."""
        with self._lock:
            expired_session_id = self._session_id
            was_active = self.out1.value
            self.out1.off()
            self._session_id = None
            self._timer = None
        logger.warning(
            "Session %s EXPIRED with no renewal -- OUT1 forced OPEN locally.", expired_session_id
        )
        if was_active and self.io_event_callback:
            self.io_event_callback("OUT1", False)
        if expired_session_id is not None and self.ack_callback:
            self.ack_callback(
                command_id=None,
                session_id=expired_session_id,
                result="expired_no_renewal",
                out1_state=False,
                in1_state=self.in1_state,
                applied_at=iso_utc_now(),
            )


# ---------------------------------------------------------------------------
# CommandChannel -- MQTT publish/subscribe
# ---------------------------------------------------------------------------

class CommandChannel:
    """Owns the paho-mqtt client and the topic contract:

      publish  genmon/{device_key}/data       (telemetry, QoS1, no retain)
      publish  genmon/{device_key}/io         (IO transitions, QoS1, no retain)
      publish  genmon/{device_key}/cmd/ack    (command results, QoS1, no retain)
      subscribe genmon/{device_key}/cmd       (retained commands from backend)

    Publishes made while disconnected are held in a small bounded in-memory
    pending list and flushed on the next successful connect, rather than
    relying solely on paho's own internal retry semantics -- this keeps the
    safety-critical expired_no_renewal ack/IO event delivery guarantee
    explicit and easy to reason about.
    """

    MAX_PENDING = 500

    def __init__(self, config: ConfigStore, device_key: str, gpio: GpioController):
        self.config = config
        self.device_key = device_key
        self.gpio = gpio

        self.topic_data = f"genmon/{device_key}/data"
        self.topic_io = f"genmon/{device_key}/io"
        self.topic_ack = f"genmon/{device_key}/cmd/ack"
        self.topic_cmd = f"genmon/{device_key}/cmd"

        self.on_command_processed = None  # fn(command_id: str) -> None, set by GenMonAgent

        self._lock = threading.Lock()
        self._pending: collections.deque = collections.deque()
        self._pending_lock = threading.Lock()
        self._recent_io_events: collections.deque = collections.deque(maxlen=100)
        self._last_ack: dict | None = None
        self._events_lock = threading.Lock()

        self.client: mqtt.Client | None = None
        self._current_key: tuple | None = None

    # -- connection lifecycle --------------------------------------------

    def ensure_connected(self) -> None:
        """(Re)build/(re)connect the MQTT client if host/port/tls have
        changed since the last call, or kick paho's own reconnect loop if
        the client exists but is currently down. Safe to call repeatedly
        (e.g. once per config-refresh cycle)."""
        host = self.config.get("mqtt", "host", fallback="")
        if not host:
            return
        port = self.config.getint("mqtt", "port", fallback=1883)
        tls = self.config.getboolean("mqtt", "tls", fallback=False)
        key = (host, port, tls)

        with self._lock:
            if self.client is not None and self._current_key == key:
                return  # already configured for these params; paho's own
                        # loop_start() thread handles reconnection on drops.
            self._rebuild_client_locked(host, port, tls)
            self._current_key = key

    def _rebuild_client_locked(self, host: str, port: int, tls: bool) -> None:
        if self.client is not None:
            try:
                self.client.loop_stop()
                self.client.disconnect()
            except Exception:
                pass

        client = mqtt.Client(client_id=f"genmon-{self.device_key}", clean_session=False, protocol=mqtt.MQTTv311)
        client.on_connect = self._on_connect
        client.on_disconnect = self._on_disconnect
        client.on_message = self._on_message
        client.reconnect_delay_set(min_delay=1, max_delay=60)
        if tls:
            # System default CA trust store. The broker must present a
            # certificate signed by a publicly trusted (or system-installed)
            # CA -- MQTT_TLS/[mqtt] tls exists precisely so local/dev
            # deployments can flip this off against a plaintext broker.
            client.tls_set()
        self.client = client
        try:
            client.connect_async(host, port, keepalive=60)
            client.loop_start()
            logger.info("MQTT connecting to %s:%s (tls=%s)...", host, port, tls)
        except Exception as exc:
            logger.error("MQTT connect_async(%s:%s) failed: %s", host, port, exc)

    def _on_connect(self, client, userdata, flags, rc):
        if rc == 0:
            logger.info("MQTT connected; subscribing to %s", self.topic_cmd)
            client.subscribe(self.topic_cmd, qos=1)
            self._flush_pending()
        else:
            logger.warning("MQTT connect failed with rc=%s", rc)

    def _on_disconnect(self, client, userdata, rc):
        if rc != 0:
            logger.warning("MQTT unexpectedly disconnected (rc=%s); paho will auto-reconnect.", rc)
        else:
            logger.info("MQTT disconnected.")

    def _on_message(self, client, userdata, msg):
        try:
            payload = json.loads(msg.payload.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            logger.warning("Discarding malformed command payload on %s", msg.topic)
            return

        command_id = payload.get("command_id")
        session_id = payload.get("session_id")
        cmd_type = payload.get("type")
        ttl_seconds = payload.get("ttl_seconds")

        if cmd_type == "start_session":
            result = self.gpio.handle_start(session_id, ttl_seconds)
        elif cmd_type == "renew_session":
            result = self.gpio.handle_renew(session_id, ttl_seconds)
        elif cmd_type == "stop_session":
            result = self.gpio.handle_stop(session_id)
        else:
            logger.warning("Unknown command type %r (command_id=%s)", cmd_type, command_id)
            result = "rejected"

        self.publish_ack(
            command_id=command_id,
            session_id=session_id,
            result=result,
            out1_state=self.gpio.out1_state,
            in1_state=self.gpio.in1_state,
            applied_at=iso_utc_now(),
        )

        if command_id and self.on_command_processed:
            try:
                self.on_command_processed(command_id)
            except Exception:
                logger.exception("on_command_processed hook failed for command_id=%s", command_id)

    # -- publish helpers --------------------------------------------------

    def publish_telemetry(self, reading: dict) -> None:
        self._publish(self.topic_data, reading)

    def publish_io_event(self, channel: str, state: bool, observed_at_utc: str | None = None) -> None:
        payload = {
            "device_key": self.device_key,
            "channel": channel,
            "state": bool(state),
            "observed_at_utc": observed_at_utc or iso_utc_now(),
        }
        self._publish(self.topic_io, payload)
        with self._events_lock:
            self._recent_io_events.append(payload)

    def publish_ack(
        self,
        command_id: str | None,
        session_id: str | None,
        result: str,
        out1_state: bool,
        in1_state: bool,
        applied_at: str | None = None,
    ) -> None:
        payload = {
            "command_id": command_id,
            "session_id": session_id,
            "result": result,
            "out1_state": bool(out1_state),
            "in1_state": bool(in1_state),
            "applied_at": applied_at or iso_utc_now(),
        }
        self._publish(self.topic_ack, payload)
        with self._events_lock:
            self._last_ack = payload

    def drain_recent_io_events(self) -> list[dict]:
        """Used by the heartbeat's low-frequency HTTP backup path."""
        with self._events_lock:
            items = list(self._recent_io_events)
            self._recent_io_events.clear()
            return items

    def pop_last_ack(self) -> dict | None:
        with self._events_lock:
            ack, self._last_ack = self._last_ack, None
            return ack

    def _publish(self, topic: str, payload: dict) -> None:
        data = json.dumps(payload)
        sent = False
        if self.client is not None and self.client.is_connected():
            info = self.client.publish(topic, data, qos=1, retain=False)
            sent = info.rc == mqtt.MQTT_ERR_SUCCESS
        if not sent:
            with self._pending_lock:
                self._pending.append((topic, data))
                while len(self._pending) > self.MAX_PENDING:
                    self._pending.popleft()

    def _flush_pending(self) -> None:
        with self._pending_lock:
            items = list(self._pending)
            self._pending.clear()
        for topic, data in items:
            info = self.client.publish(topic, data, qos=1, retain=False)
            if info.rc != mqtt.MQTT_ERR_SUCCESS:
                with self._pending_lock:
                    self._pending.append((topic, data))
        if items:
            logger.info("Flushed %d queued MQTT publish(es) after reconnect.", len(items))


# ---------------------------------------------------------------------------
# CellularManager
# ---------------------------------------------------------------------------

class CellularManager:
    """Manages the optional M.2 cellular modem (SIMCom SIM7600G-H-M.2) via
    ModemManager + NetworkManager. If no modem is present (plenty of units
    are Ethernet-only on this board), this logs once at info level and gets
    out of the way -- it must never error or block agent startup."""

    MMCLI_PATH = "/usr/bin/mmcli"
    NMCLI_PATH = "/usr/bin/nmcli"
    GSM_CON_NAME = "genmon-wwan"

    def __init__(self, config: ConfigStore):
        self.config = config
        self._no_modem_logged = False

    def detect_modem(self) -> bool:
        try:
            result = subprocess.run(
                [self.MMCLI_PATH, "-L"], capture_output=True, text=True, timeout=10
            )
        except FileNotFoundError:
            if not self._no_modem_logged:
                logger.info("ModemManager (mmcli) is not installed; skipping cellular bring-up.")
                self._no_modem_logged = True
            return False
        except subprocess.TimeoutExpired:
            logger.warning("mmcli -L timed out; treating as 'no modem' for this cycle.")
            return False

        output = (result.stdout or "") + (result.stderr or "")
        present = result.returncode == 0 and "No modems were found" not in output and bool(result.stdout.strip())
        if not present and not self._no_modem_logged:
            logger.info(
                "No cellular modem detected (mmcli -L reports none); this unit appears "
                "Ethernet-only. Skipping cellular bring-up."
            )
            self._no_modem_logged = True
        return present

    def ensure_connection(self) -> None:
        """Idempotently create the 'genmon-wwan' NetworkManager GSM profile
        if a modem is present and an APN is configured. Never raises -- all
        failure modes are logged and swallowed so a modem/config problem
        can't block the rest of the agent."""
        if not self.detect_modem():
            return

        apn = self.config.get("cellular", "apn", fallback="").strip()
        if not apn:
            logger.warning(
                "Cellular modem detected but no APN configured in [cellular] apn= "
                "(device.conf); skipping GSM profile creation. Verizon-provisions "
                "the APN manually per SIM -- see config/device.conf.example."
            )
            return

        if self._connection_exists(self.GSM_CON_NAME):
            return

        try:
            subprocess.run(
                [
                    "sudo", "-n", self.NMCLI_PATH,
                    "connection", "add", "type", "gsm", "ifname", "*",
                    "con-name", self.GSM_CON_NAME, "apn", apn,
                    "connection.autoconnect", "yes",
                ],
                check=True, capture_output=True, text=True, timeout=30,
            )
            logger.info("Created NetworkManager GSM connection '%s' (APN=%s).", self.GSM_CON_NAME, apn)
        except subprocess.CalledProcessError as exc:
            logger.error("Failed to create GSM connection: %s", (exc.stderr or exc.stdout or exc).strip())
        except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
            logger.error("nmcli unavailable/timed out creating GSM connection: %s", exc)

    def _connection_exists(self, con_name: str) -> bool:
        try:
            result = subprocess.run(
                [self.NMCLI_PATH, "-t", "-f", "NAME", "connection", "show"],
                capture_output=True, text=True, timeout=10,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return False
        return con_name in result.stdout.splitlines()


# ---------------------------------------------------------------------------
# NetworkManagerHelper -- nmcli helper + route-metric prioritization
# ---------------------------------------------------------------------------

class NetworkManagerHelper:
    """Probes real internet reachability per network interface (TCP connect
    to well-known IPs with SO_BINDTODEVICE) and sets NetworkManager route
    metrics so Ethernet wins over cellular whenever both are reachable --
    the same interface-prioritization idea AetherLynk used. PoE Ethernet is
    the primary uplink on this board, so Ethernet defaults to a lower
    (higher-priority) metric than cellular."""

    NMCLI_PATH = "/usr/bin/nmcli"
    PROBE_TARGETS = (("1.1.1.1", 443), ("8.8.8.8", 443))
    PROBE_TIMEOUT_SECONDS = 3

    ETHERNET_METRIC_REACHABLE = 100
    ETHERNET_METRIC_UNREACHABLE = 600
    CELLULAR_METRIC_REACHABLE = 700
    CELLULAR_METRIC_UNREACHABLE = 1200

    # SO_BINDTODEVICE is Linux-only and was only exposed in Python's socket
    # module (as socket.SO_BINDTODEVICE) starting in 3.8; fall back to its
    # well-known numeric value (25) for defensiveness on older interpreters.
    _SO_BINDTODEVICE = getattr(socket, "SO_BINDTODEVICE", 25)

    def list_active_interfaces(self) -> list[tuple[str, str]]:
        """Returns [(ifname, nm_type), ...] for currently-connected devices."""
        try:
            result = subprocess.run(
                [self.NMCLI_PATH, "-t", "-f", "DEVICE,TYPE,STATE", "device"],
                capture_output=True, text=True, timeout=10,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return []
        interfaces = []
        for line in result.stdout.splitlines():
            parts = line.split(":")
            if len(parts) >= 3 and parts[2] == "connected":
                interfaces.append((parts[0], parts[1]))
        return interfaces

    def active_connection_for_device(self, ifname: str) -> str | None:
        try:
            result = subprocess.run(
                [self.NMCLI_PATH, "-t", "-f", "GENERAL.CONNECTION", "device", "show", ifname],
                capture_output=True, text=True, timeout=10,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return None
        for line in result.stdout.splitlines():
            if line.startswith("GENERAL.CONNECTION:"):
                name = line.split(":", 1)[1].strip()
                return name if name and name != "--" else None
        return None

    def probe_reachable(self, ifname: str) -> bool:
        for host, port in self.PROBE_TARGETS:
            sock = None
            try:
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.setsockopt(socket.SOL_SOCKET, self._SO_BINDTODEVICE, (ifname + "\0").encode("utf-8"))
                sock.settimeout(self.PROBE_TIMEOUT_SECONDS)
                sock.connect((host, port))
                return True
            except OSError:
                continue
            finally:
                if sock is not None:
                    sock.close()
        return False

    def set_route_metric(self, con_name: str, metric: int) -> None:
        try:
            subprocess.run(
                ["sudo", "-n", self.NMCLI_PATH, "connection", "modify", con_name, "ipv4.route-metric", str(metric)],
                check=True, capture_output=True, text=True, timeout=15,
            )
            subprocess.run(
                ["sudo", "-n", self.NMCLI_PATH, "connection", "up", con_name],
                check=False, capture_output=True, text=True, timeout=30,
            )
            logger.info("Set route-metric=%s on connection '%s'.", metric, con_name)
        except subprocess.CalledProcessError as exc:
            logger.warning("Failed to set route-metric on '%s': %s", con_name, (exc.stderr or exc.stdout or exc).strip())
        except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
            logger.warning("nmcli unavailable/timed out setting route-metric on '%s': %s", con_name, exc)

    def prioritize_interfaces(self) -> None:
        for ifname, iftype in self.list_active_interfaces():
            con_name = self.active_connection_for_device(ifname)
            if not con_name:
                continue
            reachable = self.probe_reachable(ifname)
            if iftype == "ethernet":
                metric = self.ETHERNET_METRIC_REACHABLE if reachable else self.ETHERNET_METRIC_UNREACHABLE
            elif iftype in ("gsm", "cdma"):
                metric = self.CELLULAR_METRIC_REACHABLE if reachable else self.CELLULAR_METRIC_UNREACHABLE
            else:
                continue
            self.set_route_metric(con_name, metric)


# ---------------------------------------------------------------------------
# SelfUpdater
# ---------------------------------------------------------------------------

class SelfUpdater:
    """Compares AGENT_VERSION to the server's target_agent_version and, if
    different and auto_update_enabled, downloads the new script, sanity-
    checks it with py_compile, backs up the running file, atomically
    replaces it, and os.execv()s to restart in-place (same PID, so systemd
    doesn't need to intervene)."""

    def __init__(self, config: ConfigStore, script_path: Path | None = None):
        self.config = config
        self.script_path = script_path or Path(__file__).resolve()

    def maybe_update(self, target_version: str, auto_update_enabled: bool, download_url: str, timeout: int = 30) -> bool:
        if not auto_update_enabled:
            return False
        if not target_version or target_version == AGENT_VERSION:
            return False

        logger.info("Self-update available: local=%s target=%s (source=%s)", AGENT_VERSION, target_version, download_url)
        try:
            response = requests.get(download_url, timeout=timeout)
            response.raise_for_status()
            new_source = response.text
        except requests.RequestException as exc:
            logger.error("Self-update download failed: %s", exc)
            return False

        if not new_source.strip():
            logger.error("Self-update aborted: downloaded script was empty.")
            return False

        tmp_path = self.script_path.with_suffix(".py.new")
        backup_path = self.script_path.with_suffix(".py.bak")
        try:
            tmp_path.write_text(new_source, encoding="utf-8")
            py_compile.compile(str(tmp_path), doraise=True)
        except py_compile.PyCompileError as exc:
            logger.error("Self-update aborted: downloaded script failed to compile: %s", exc)
            tmp_path.unlink(missing_ok=True)
            return False
        except OSError as exc:
            logger.error("Self-update aborted: could not write/compile candidate script: %s", exc)
            return False

        try:
            shutil.copy2(self.script_path, backup_path)
            os.replace(tmp_path, self.script_path)
        except OSError as exc:
            logger.error("Self-update aborted: could not install new script: %s", exc)
            return False

        logger.info("Self-update installed (previous version backed up at %s). Restarting in-place.", backup_path)
        os.execv(sys.executable, [sys.executable, str(self.script_path)] + sys.argv[1:])
        return True  # unreachable on success; os.execv() replaces this process image


# ---------------------------------------------------------------------------
# GenMonAgent -- main orchestrator
# ---------------------------------------------------------------------------

class GenMonAgent:
    """Startup/pre-register/claim-wait, MQTT client lifecycle (including
    the cmd subscription), the config-refresh timer, heartbeat, register
    poller sync, and the deadman-timer wiring all come together here."""

    def __init__(self, config_path: str | None = None):
        self._config_path = config_path or os.environ.get("GENMON_CONFIG_PATH", DEFAULT_CONFIG_PATH)

        self.config: ConfigStore | None = None
        self.device_key: str | None = None
        self.cpu_serial: str | None = None

        self.gpio: GpioController | None = None
        self.cellular: CellularManager | None = None
        self.netmgr: NetworkManagerHelper | None = None
        self.command_channel: CommandChannel | None = None
        self.self_updater: SelfUpdater | None = None

        self.http = requests.Session()
        self.telemetry_buffer: "queue.Queue[dict]" = queue.Queue()
        self._pollers: dict = {}
        self._stop_event = threading.Event()

        self._reporting_interval_seconds = 60  # overwritten by /config's reporting_interval_seconds once claimed
        self._claimed = False
        self._claim_wait_logged = False
        self._mqtt_tls_env_override = False
        self._last_mqtt_command_id: str | None = None
        self._last_reconciled_command_id: str | None = None
        self._scan_in_progress = False

    # -- lifecycle ----------------------------------------------------------

    def run(self) -> None:
        self._install_signal_handlers()
        self._load_identity_and_config()
        self._apply_env_overrides()

        self.gpio = GpioController(self.config)
        self.cellular = CellularManager(self.config)
        self.netmgr = NetworkManagerHelper()
        self.command_channel = CommandChannel(self.config, self.device_key, self.gpio)
        # Wire the deadman failsafe's callbacks now that both objects exist.
        self.gpio.io_event_callback = self.command_channel.publish_io_event
        self.gpio.ack_callback = self.command_channel.publish_ack
        self.command_channel.on_command_processed = self._on_command_processed
        self.self_updater = SelfUpdater(self.config)

        self.pre_register()
        self.cellular.ensure_connection()

        # First call of each loop runs synchronously here (small, bounded
        # startup delay) so config/bearer-token/mqtt are in place before the
        # heartbeat loop's first run needs them.
        self._repeat(self._refresh_config_once,
                     lambda: self.config.getint("network", "config_refresh_interval_seconds", fallback=60))
        self._repeat(self._send_heartbeat_once,
                     lambda: self.config.getint("network", "heartbeat_interval_seconds", fallback=60))
        self._repeat(self._flush_telemetry_buffer, lambda: self._reporting_interval_seconds)
        self._repeat(self._prioritize_interfaces_once, lambda: ROUTE_PRIORITY_INTERVAL_SECONDS)

        logger.info("GenMon agent fully initialized (device_key=%s); entering steady state.", self.device_key)
        self._stop_event.wait()

    def _install_signal_handlers(self) -> None:
        signal.signal(signal.SIGTERM, self._handle_shutdown_signal)
        signal.signal(signal.SIGINT, self._handle_shutdown_signal)

    def _handle_shutdown_signal(self, signum, frame) -> None:
        logger.info("Received signal %s; shutting down.", signum)
        self._stop_event.set()
        try:
            if self.gpio is not None:
                # Force OUT1 open defensively -- nothing will be able to
                # renew a session while this process is stopped.
                self.gpio.handle_stop(self.gpio.session_id)
        except Exception:
            logger.exception("Error forcing OUT1 open during shutdown")
        for poller in list(self._pollers.values()):
            try:
                poller.stop()
            except Exception:
                pass
        try:
            if self.command_channel is not None and self.command_channel.client is not None:
                self.command_channel.client.loop_stop()
                self.command_channel.client.disconnect()
        except Exception:
            pass
        try:
            if self.gpio is not None:
                self.gpio.close()
        except Exception:
            pass
        sys.exit(0)

    # -- generic periodic-task scheduler ------------------------------------

    def _repeat(self, fn, interval_seconds_fn) -> None:
        """Runs fn() immediately, then reschedules itself via
        threading.Timer at interval_seconds_fn() seconds, forever, until
        _stop_event is set. Exceptions in fn are caught and logged so one
        bad cycle never kills the recurring schedule."""
        if self._stop_event.is_set():
            return
        try:
            fn()
        except Exception:
            logger.exception("Periodic task %s raised an exception", getattr(fn, "__name__", fn))
        finally:
            if not self._stop_event.is_set():
                interval = max(1, int(interval_seconds_fn()))
                timer = threading.Timer(interval, self._repeat, args=(fn, interval_seconds_fn))
                timer.daemon = True
                timer.start()

    # -- identity / config bootstrap -----------------------------------------

    def _load_identity_and_config(self) -> None:
        self.config = ConfigStore(self._config_path)
        device_key = self.config.get("device", "device_key")
        cpu_serial = self.config.get("device", "cpu_serial")

        if not device_key:
            device_key, cpu_serial = DeviceIdentity.derive_device_key()
            self.config.set_value("device", "device_key", device_key)
            self.config.set_value("device", "cpu_serial", cpu_serial)
            self.config.save()
            self._maybe_set_hostname_once(device_key)

        self.device_key = device_key
        self.cpu_serial = cpu_serial or DeviceIdentity.get_cpu_serial()

        if self.config.set_value("device", "agent_version", AGENT_VERSION):
            self.config.save()

        logger.info("GenMon agent starting: device_key=%s agent_version=%s", self.device_key, AGENT_VERSION)

    def _maybe_set_hostname_once(self, device_key: str) -> None:
        """Best-effort, one-time-only hostname set on first-ever bootstrap
        (when a fresh device_key was just derived), to make fleets easier to
        identify (e.g. `ssh genmon-ab12cd34`). Uses the sudoers grant for
        hostnamectl set-hostname genmon-* installed by install.sh. Never
        repeated after the first run, so it will not clobber a hostname an
        operator later customizes further."""
        try:
            suffix = device_key.replace("GM-", "").replace("-", "").lower()
            hostname = f"genmon-{suffix}"
            subprocess.run(
                ["sudo", "-n", "/usr/bin/hostnamectl", "set-hostname", hostname],
                check=True, capture_output=True, text=True, timeout=10,
            )
            logger.info("Set system hostname to '%s' on first bootstrap.", hostname)
        except Exception as exc:
            logger.info("Could not set hostname on first bootstrap (non-fatal): %s", exc)

    def _apply_env_overrides(self) -> None:
        api_base = os.environ.get("GENMON_API_BASE")
        if api_base:
            if self.config.set_value("device", "api_base_url", api_base.rstrip("/")):
                self.config.save()

        mqtt_tls_env = os.environ.get("MQTT_TLS")
        self._mqtt_tls_env_override = mqtt_tls_env is not None
        if mqtt_tls_env is not None:
            value = mqtt_tls_env.strip().lower() in ("1", "true", "yes", "on")
            if self.config.set_value("mqtt", "tls", "true" if value else "false"):
                self.config.save()

    # -- HTTP helpers ---------------------------------------------------

    def _api_url(self, path: str) -> str:
        base = self.config.get("device", "api_base_url", fallback=DEFAULT_API_BASE).rstrip("/")
        return f"{base}{path}"

    def _auth_headers(self) -> dict:
        token = self.config.get("auth", "device_bearer_token")
        return {"Authorization": f"Bearer {token}"} if token else {}

    def pre_register(self) -> bool:
        """POST /devices/pre-register with exponential backoff (2^n capped
        at 60s) up to 10 attempts. If all attempts fail we log and continue
        anyway -- the device may already be known to the API from a
        previous run, and /config polling will keep trying regardless."""
        url = self._api_url("/devices/pre-register")
        body = {"cpu_serial": self.cpu_serial, "device_key": self.device_key}
        for attempt in range(PRE_REGISTER_MAX_ATTEMPTS):
            try:
                resp = self.http.post(url, json=body, timeout=15)
                if resp.status_code < 300:
                    logger.info("Pre-registration succeeded (HTTP %s).", resp.status_code)
                    return True
                logger.warning(
                    "Pre-registration attempt %d/%d got HTTP %s: %s",
                    attempt + 1, PRE_REGISTER_MAX_ATTEMPTS, resp.status_code, resp.text[:300],
                )
            except requests.RequestException as exc:
                logger.warning(
                    "Pre-registration attempt %d/%d failed: %s", attempt + 1, PRE_REGISTER_MAX_ATTEMPTS, exc
                )
            if attempt < PRE_REGISTER_MAX_ATTEMPTS - 1:
                time.sleep(min(2 ** attempt, PRE_REGISTER_BACKOFF_CAP_SECONDS))

        logger.error(
            "Pre-registration did not succeed after %d attempts; continuing -- the device may "
            "already be registered from a previous run, and /config polling will retry the "
            "backend relationship regardless.", PRE_REGISTER_MAX_ATTEMPTS,
        )
        return False

    def fetch_config(self) -> dict | None:
        url = self._api_url(f"/devices/{self.device_key}/config")
        try:
            resp = self.http.get(url, timeout=15)
            resp.raise_for_status()
            return resp.json()
        except requests.RequestException as exc:
            logger.warning("Config refresh failed: %s", exc)
            return None
        except ValueError as exc:
            logger.warning("Config refresh returned non-JSON body: %s", exc)
            return None

    # -- config refresh ---------------------------------------------------

    def _refresh_config_once(self) -> None:
        data = self.fetch_config()
        if data is None:
            return

        claimed = bool(data.get("claimed"))
        self._claimed = claimed
        if not claimed:
            if not self._claim_wait_logged:
                logger.info("Device not yet claimed by an operator; waiting (device_key=%s).", self.device_key)
                self._claim_wait_logged = True
            return
        self._claim_wait_logged = False

        changed = False
        token = data.get("device_bearer_token")
        if token:
            changed |= self.config.set_value("auth", "device_bearer_token", token)

        mqtt_host = data.get("mqtt_host")
        mqtt_port = data.get("mqtt_port")
        mqtt_tls = data.get("mqtt_tls")
        if mqtt_host:
            changed |= self.config.set_value("mqtt", "host", str(mqtt_host))
        if mqtt_port:
            changed |= self.config.set_value("mqtt", "port", str(int(mqtt_port)))
        if mqtt_tls is not None and not self._mqtt_tls_env_override:
            changed |= self.config.set_value("mqtt", "tls", "true" if mqtt_tls else "false")

        refresh_interval = data.get("config_refresh_interval_seconds")
        if refresh_interval:
            changed |= self.config.set_value("network", "config_refresh_interval_seconds", str(int(refresh_interval)))

        if changed:
            self.config.save()

        reporting_interval = data.get("reporting_interval_seconds")
        if reporting_interval:
            self._reporting_interval_seconds = max(1, int(reporting_interval))

        self.command_channel.ensure_connected()

        registers = data.get("modbus_registers") or []
        self._sync_pollers(registers)

        if data.get("scan_requested"):
            self._perform_scan_async()

        self._reconcile_generator_command(data.get("generator_command"))

        target_version = data.get("target_agent_version")
        auto_update = bool(data.get("auto_update_enabled"))
        if target_version:
            self.self_updater.maybe_update(
                target_version=target_version,
                auto_update_enabled=auto_update,
                download_url=self._api_url("/devices/agent/download"),
            )

    # -- Modbus register poller sync ---------------------------------------

    @staticmethod
    def _transport_key(transport: dict) -> tuple:
        kind = transport.get("kind")
        if kind == "tcp":
            return ("tcp", transport.get("host"), int(transport.get("port", 502)))
        return ("rtu", transport.get("serial_port"))

    def _make_driver_factory(self, transport: dict):
        kind = transport.get("kind")
        if kind == "tcp":
            host = transport["host"]
            port = int(transport.get("port", 502))
            return lambda: ModbusTcpDriver.get(host, port)
        elif kind == "rtu":
            serial_port = transport["serial_port"]
            baudrate = int(transport.get("baudrate", 9600))
            parity = transport.get("parity", "N")
            stopbits = int(transport.get("stopbits", 1))
            return lambda: ModbusRtuDriver.get(serial_port, baudrate, parity, stopbits)
        raise ValueError(f"Unknown transport kind: {kind!r}")

    def _sync_pollers(self, registers: list) -> None:
        seen_keys = set()
        for reg in registers:
            if not reg.get("enabled", True):
                continue
            transport = reg.get("transport") or {}
            slave_id = transport.get("slave_id")
            key = (*self._transport_key(transport), slave_id, reg["register_address"], reg["register_type"])
            seen_keys.add(key)
            if key in self._pollers:
                continue  # static for the register's lifetime in v1; a real
                          # change (e.g. new read_interval) requires the
                          # backend to remove/re-add the register entry.
            try:
                driver_factory = self._make_driver_factory(transport)
            except (KeyError, ValueError) as exc:
                logger.error("Skipping register %s: invalid transport %r: %s",
                             reg.get("register_friendly_name"), transport, exc)
                continue

            poller = RegisterPoller(
                register_cfg={
                    "register_address": reg["register_address"],
                    "register_type": reg["register_type"],
                    "register_count": reg.get("register_count", 1),
                    "register_friendly_name": reg.get("register_friendly_name", ""),
                    "unit": reg.get("unit"),
                    "read_interval_seconds": reg.get("read_interval_seconds", 30),
                    "slave_id": slave_id,
                    "_transport": transport,
                },
                driver_factory=driver_factory,
                buffer=self.telemetry_buffer,
                stop_event=self._stop_event,
                device_key=self.device_key,
            )
            self._pollers[key] = poller
            poller.start()
            logger.info("Started poller for register '%s' (addr=%s, every %ss).",
                        reg.get("register_friendly_name"), reg["register_address"],
                        reg.get("read_interval_seconds", 30))

        for key in list(self._pollers.keys()):
            if key not in seen_keys:
                self._pollers.pop(key).stop()
                logger.info("Stopped poller for removed/disabled register key=%s", key)

    def _flush_telemetry_buffer(self) -> None:
        drained = 0
        while True:
            try:
                reading = self.telemetry_buffer.get_nowait()
            except queue.Empty:
                break
            self.command_channel.publish_telemetry(reading)
            drained += 1
        if drained:
            logger.debug("Flushed %d telemetry reading(s) to MQTT.", drained)

    def _prioritize_interfaces_once(self) -> None:
        self.netmgr.prioritize_interfaces()

    # -- generator_command reconciliation (fallback path) -------------------

    def _on_command_processed(self, command_id: str) -> None:
        """Called by CommandChannel whenever a command actually arrives
        live over MQTT -- lets _reconcile_generator_command know MQTT is
        the authoritative channel for this command_id, so the /config
        fallback path leaves it alone."""
        self._last_mqtt_command_id = command_id

    def _reconcile_generator_command(self, gc: dict | None) -> None:
        """The generator_command object in /config is a fallback/
        reconciliation path used ONLY if the corresponding retained MQTT
        command was never received. We treat a command_id as "already
        handled" if it matches either the last command actually delivered
        over MQTT or the last one this method already reconciled -- so a
        given fallback command_id is only ever applied once, and MQTT
        (being lower latency and the primary path) always takes precedence
        when both arrive."""
        if not gc:
            return
        command_id = gc.get("command_id")
        if not command_id:
            return
        if command_id in (self._last_mqtt_command_id, self._last_reconciled_command_id):
            return

        desired_state = gc.get("desired_state")
        applied_at = iso_utc_now()

        if desired_state == "run":
            expires_at = parse_iso8601(gc.get("expires_at"))
            if expires_at is None:
                logger.warning(
                    "Ignoring fallback generator_command %s: desired_state=run but expires_at "
                    "is missing/unparseable.", command_id,
                )
                return
            ttl_seconds = (expires_at - datetime.now(timezone.utc)).total_seconds()
            if ttl_seconds <= 0:
                logger.info("Fallback generator_command %s already expired (expires_at=%s); ignoring.",
                            command_id, gc.get("expires_at"))
                self._last_reconciled_command_id = command_id
                return
            if self.gpio.session_id == command_id and self.gpio.out1_state:
                result = self.gpio.handle_renew(command_id, ttl_seconds)
            else:
                result = self.gpio.handle_start(command_id, ttl_seconds)
        elif desired_state == "stop" or desired_state is None:
            result = self.gpio.handle_stop(command_id)
        else:
            logger.warning("Ignoring fallback generator_command %s: unknown desired_state=%r",
                           command_id, desired_state)
            return

        logger.info("Reconciled fallback generator_command %s (desired_state=%s) -> %s",
                    command_id, desired_state, result)
        self._last_reconciled_command_id = command_id
        self.command_channel.publish_ack(
            command_id=command_id,
            session_id=command_id,
            result=result,
            out1_state=self.gpio.out1_state,
            in1_state=self.gpio.in1_state,
            applied_at=applied_at,
        )

    # -- Modbus scan (best-effort discovery) --------------------------------

    def _perform_scan_async(self) -> None:
        if self._scan_in_progress:
            return
        self._scan_in_progress = True
        threading.Thread(target=self._perform_scan, daemon=True, name="genmon-scan").start()

    def _perform_scan(self) -> None:
        try:
            results = self._scan_default_rtu_bus()
            self.http.post(
                self._api_url(f"/devices/{self.device_key}/scan-results"),
                json={"results": results, "scanned_at": iso_utc_now()},
                headers=self._auth_headers(),
                timeout=60,
            )
        except Exception:
            logger.exception("Modbus scan failed")
        finally:
            self._scan_in_progress = False

    def _scan_default_rtu_bus(self) -> list:
        """Best-effort slave-ID discovery on the onboard RS-485 bus
        (/dev/ttyAMA5), which is the only Modbus transport this hardware
        exposes natively for local generator-controller wiring. Reuses
        serial parameters from an already-configured RTU register on this
        port if one exists, else falls back to common defaults (9600 8N1).
        Read-only: a single holding-register probe at address 0, count 1,
        per candidate slave ID 1..32 (a practical range for a single RS-485
        generator-monitoring segment). Never touches OUT1."""
        serial_port = "/dev/ttyAMA5"
        baudrate, parity, stopbits = 9600, "N", 1
        for poller in self._pollers.values():
            transport = poller.cfg.get("_transport") or {}
            if transport.get("kind") == "rtu" and transport.get("serial_port") == serial_port:
                baudrate = int(transport.get("baudrate", baudrate))
                parity = transport.get("parity", parity)
                stopbits = int(transport.get("stopbits", stopbits))
                break

        driver = ModbusRtuDriver.get(serial_port, baudrate, parity, stopbits)
        results = []
        for slave_id in range(1, 33):
            try:
                driver.read(register_type=4, address=0, count=1, slave_id=slave_id)
                results.append({"slave_id": slave_id, "responded": True})
            except ProtocolReadError:
                continue
            except Exception:
                logger.exception("Unexpected error scanning slave_id=%s", slave_id)
                continue
        return results

    # -- heartbeat --------------------------------------------------------

    def _send_heartbeat_once(self) -> None:
        token = self.config.get("auth", "device_bearer_token")
        if not token:
            return  # not claimed yet -- nothing to authenticate the heartbeat with

        # Redundant per-heartbeat-cycle IO snapshot over MQTT, independent
        # of any transition, per the shared platform contract.
        if self.gpio is not None:
            self.command_channel.publish_io_event("IN1", self.gpio.in1_state)
            self.command_channel.publish_io_event("OUT1", self.gpio.out1_state)

        body = {"local_ip": get_local_ip()}
        events = self.command_channel.drain_recent_io_events()
        if events:
            body["io_events"] = events
        ack = self.command_channel.pop_last_ack()
        if ack:
            body["command_ack"] = ack

        try:
            resp = self.http.post(
                self._api_url(f"/devices/{self.device_key}/heartbeat"),
                json=body,
                headers={"Authorization": f"Bearer {token}"},
                timeout=15,
            )
            resp.raise_for_status()
        except requests.RequestException as exc:
            logger.warning("Heartbeat POST failed: %s", exc)

    # -- log submission -----------------------------------------------------

    @staticmethod
    def _tail_file(path: Path, max_bytes: int) -> str:
        size = path.stat().st_size
        with open(path, "rb") as fh:
            if size > max_bytes:
                fh.seek(size - max_bytes)
            data = fh.read()
        return data.decode("utf-8", errors="replace")

    @staticmethod
    def _capture_network_diagnostics() -> dict:
        diagnostics = {}
        for name, cmd in (("addr", ["/usr/sbin/ip", "-j", "addr", "show"]),
                          ("route", ["/usr/sbin/ip", "-j", "route", "show"])):
            try:
                result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
                diagnostics[name] = result.stdout[:20_000]
            except Exception as exc:
                diagnostics[name] = f"<unavailable: {exc}>"
        return diagnostics

    def submit_logs(self, reason: str = "manual") -> None:
        """POSTs a truncated tail of the local log file (plus a small
        network diagnostics snapshot) to /devices/{device_key}/submit-logs.
        Called automatically from main() if an unhandled exception escapes
        GenMonAgent.run(), and safe to call manually at any other time."""
        log_file = Path(DEFAULT_LOG_DIR) / "genmon-agent.log"
        if not log_file.exists():
            logger.warning("submit_logs: no log file at %s to submit.", log_file)
            return
        try:
            tail = self._tail_file(log_file, max_bytes=200_000)
        except OSError as exc:
            logger.error("submit_logs: could not read log file: %s", exc)
            return

        payload = {
            "reason": reason,
            "agent_version": AGENT_VERSION,
            "log_tail": tail,
            "network_diagnostics": self._capture_network_diagnostics(),
            "submitted_at": iso_utc_now(),
        }
        try:
            resp = self.http.post(
                self._api_url(f"/devices/{self.device_key}/submit-logs"),
                json=payload,
                headers=self._auth_headers(),
                timeout=20,
            )
            resp.raise_for_status()
            logger.info("Submitted logs to API (reason=%s).", reason)
        except requests.RequestException as exc:
            logger.error("Failed to submit logs to API: %s", exc)


# ---------------------------------------------------------------------------
# main entrypoint
# ---------------------------------------------------------------------------

def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="GenMonitoring Raspberry Pi CM4 field agent")
    parser.add_argument(
        "--config",
        default=os.environ.get("GENMON_CONFIG_PATH", DEFAULT_CONFIG_PATH),
        help="Path to the device.conf INI file (default: %(default)s)",
    )
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    setup_logging()
    agent = GenMonAgent(config_path=args.config)
    try:
        agent.run()
    except SystemExit:
        raise
    except Exception:
        logger.exception("Fatal error in GenMonAgent; attempting to submit crash logs before exit.")
        try:
            agent.submit_logs(reason="unhandled_exception")
        except Exception:
            logger.exception("Could not submit crash logs to API.")
        raise


if __name__ == "__main__":
    main()
