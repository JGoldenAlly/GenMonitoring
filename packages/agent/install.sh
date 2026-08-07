#!/usr/bin/env bash
#
# install.sh -- installs the GenMonitoring field agent on a Raspberry Pi CM4
# (Waveshare "Compute Module 4 PoE 4G Board", Raspberry Pi OS Trixie 64-bit --
# Bookworm is also supported as an older base). Must be run as root, e.g.:
#
#   sudo ./install.sh
#   sudo GENMON_API_BASE=https://api.allyoperations.com ./install.sh
#   sudo GENMON_AGENT_SOURCE=github GENMON_AGENT_VERSION=1.0.0 ./install.sh
#
# Environment variables recognized:
#   GENMON_API_BASE      Base URL of the GenMonitoring API. If set (and
#                         GENMON_AGENT_SOURCE is unset), the agent script is
#                         fetched from this API's own unauthenticated
#                         GET /devices/agent/download endpoint. Also written
#                         into the agent's own environment so it uses the
#                         same API at runtime.
#   GENMON_AGENT_SOURCE   "api" or "github" -- explicitly picks the download
#                         source, overriding the GENMON_API_BASE-implied
#                         default.
#   GENMON_AGENT_VERSION  A specific agent version tag (e.g. "1.0.0") to
#                         fetch from GitHub Releases, or "latest" (default).
#                         Only relevant when the source is "github".
#
# IMPORTANT: neither download path ever embeds a token/PAT/credential of any
# kind -- both are unauthenticated public endpoints. This is a deliberate
# departure from AetherLynk's known anti-pattern of baking a GitHub PAT into
# its installer.
#
# Safety notes (see genmon_agent.py's module docstring for the full detail):
#   - IN1 = BCM GPIO23 can collide with SPI0's chip-select-1 function on this
#     board. This script NEVER enables an SPI0 dual-chip-select overlay, and
#     actively refuses to proceed if one is already present in config.txt.
#   - OUT1 = BCM GPIO6 drives a physical generator-start relay. This script
#     includes an explicit commissioning/verification step -- it does NOT
#     just trust the pin mapping documentation blindly.
#
set -euo pipefail

# ---------------------------------------------------------------------------
# Logging helpers
# ---------------------------------------------------------------------------

_C_INFO="\033[0;36m"; _C_WARN="\033[0;33m"; _C_ERROR="\033[0;31m"; _C_STEP="\033[1;32m"; _C_RESET="\033[0m"

log_step()  { printf "\n${_C_STEP}==> %s${_C_RESET}\n" "$1"; }
log_info()  { printf "${_C_INFO}    [info]  %s${_C_RESET}\n" "$1"; }
log_warn()  { printf "${_C_WARN}    [warn]  %s${_C_RESET}\n" "$1" >&2; }
log_error() { printf "${_C_ERROR}    [error] %s${_C_RESET}\n" "$1" >&2; }

# BASH_SOURCE[0] is unset when this script is piped into bash (e.g.
# `curl ... | bash`) rather than run from a saved file -- there is no
# script file to locate in that case, so SCRIPT_DIR is deliberately left
# empty rather than letting `set -u` kill the script on an unbound-variable
# reference. write_device_conf/install_systemd_service already fall back
# to embedded templates when SCRIPT_DIR-relative files aren't found, which
# covers this case correctly; only the --commission-only re-run message
# below needs to know PIPED_INSTALL to give the right instructions.
PIPED_INSTALL=0
if [[ -n "${BASH_SOURCE[0]:-}" ]] && [[ -f "${BASH_SOURCE[0]}" ]]; then
  SCRIPT_DIR="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")" && pwd)"
else
  SCRIPT_DIR=""
  PIPED_INSTALL=1
fi
CONFIG_TXT="/boot/firmware/config.txt"
REBOOT_REQUIRED=0
PYTHON_BIN=""

# ---------------------------------------------------------------------------
# Step 1: OS / hardware precondition checks
# ---------------------------------------------------------------------------

check_preconditions() {
  log_step "Step 1: OS / hardware precondition checks"

  if [[ "${EUID}" -ne 0 ]]; then
    log_error "This installer must be run as root (e.g. 'sudo ./install.sh')."
    exit 1
  fi

  if [[ "$(uname -m)" != "aarch64" ]]; then
    log_error "Expected a 64-bit (aarch64) Raspberry Pi OS userland, found: $(uname -m)."
    exit 1
  fi

  if [[ -r /etc/os-release ]]; then
    # shellcheck disable=SC1091
    . /etc/os-release
    # Trixie is the primary target going forward (confirmed live: current
    # field devices image as Trixie, e.g. python3-lgpio 0.2.2-1~rpt1+trixie,
    # Python 3.13 by default, no 'raspi-gpio' package -- see
    # install_gpio_pinmux_tool). Bookworm is kept as a tolerated older base
    # since it's still a valid/supported Raspberry Pi OS release.
    case "${VERSION_CODENAME:-}" in
      trixie|bookworm)
        log_info "Detected OS: ${PRETTY_NAME:-Debian ${VERSION_CODENAME}}"
        ;;
      *)
        log_warn "Expected Raspberry Pi OS Trixie (VERSION_CODENAME=trixie) or Bookworm, found '${VERSION_CODENAME:-unknown}'. Continuing, but this installer is only validated against those two."
        ;;
    esac
  else
    log_warn "/etc/os-release not found/readable; cannot verify OS version. Continuing."
  fi

  local model=""
  if [[ -r /proc/device-tree/model ]]; then
    model="$(tr -d '\0' < /proc/device-tree/model || true)"
  fi
  if [[ "$model" == *"Compute Module 4"* ]]; then
    log_info "Detected hardware: ${model}"
  else
    log_warn "Could not confirm CM4 hardware from /proc/device-tree/model (got: '${model:-unknown}')."
    log_warn "Continuing, but the IN1=GPIO23 / OUT1=GPIO6 / RS-485=GPIO12,13 pin assumptions are specific to the Waveshare CM4 PoE 4G board."
  fi

  if [[ ! -d /boot/firmware ]]; then
    log_error "/boot/firmware not found -- expected on Raspberry Pi OS (Bookworm and newer)'s boot partition layout. Aborting."
    exit 1
  fi
}

# ---------------------------------------------------------------------------
# Step 2: apt dependencies
# ---------------------------------------------------------------------------

install_apt_dependencies() {
  log_step "Step 2: apt dependencies"
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -qq
  # Deliberately minimal set for a headless field agent: no xvfb/vnc/
  # dnsmasq/iptables/chromium. These must all succeed -- gpiod provides
  # gpioset/gpioget, used by the commissioning step below.
  #
  # python3-lgpio: gpiozero's LGPIOFactory pin factory backend, installed
  # from Raspberry Pi OS's own apt repo (precompiled, with a working
  # liblgpio.so) rather than pip installing the 'lgpio' PyPI package.
  # Confirmed live that the PyPI package has no prebuilt wheel for this
  # platform and its source build either fails outright (missing swig) or
  # succeeds but then fails to link (missing liblgpio.so as a system
  # library -- apt has no separate liblgpio-dev/liblgpio1 package to
  # provide it standalone). gpiozero's other alternative, its built-in
  # "native" pin factory, is ALSO not viable here: it depends on the
  # legacy /sys/class/gpio sysfs interface, which Raspberry Pi OS Bookworm
  # kernels have disabled (confirmed live: "OSError: [Errno 22] Invalid
  # argument" from gpiozero/pins/native.py's export() call). apt's
  # python3-lgpio is therefore the only backend this OS actually supports
  # out of the box.
  #
  # No compiler toolchain (gcc/python3-dev/swig) is needed as a result --
  # this is a precompiled package, not something pip builds from source.
  apt-get install -y --no-install-recommends \
    network-manager \
    modemmanager \
    python3-venv \
    python3-pip \
    python3-lgpio \
    gpiod \
    curl \
    ca-certificates
  log_info "Core apt dependencies installed."
  install_gpio_pinmux_tool
}

# GPIO pinmux inspection tool ('raspi-gpio' or its newer replacement
# 'pinctrl') -- used ONLY by the Step 15 commissioning check to confirm
# IN1/GPIO23 isn't SPI0-claimed. Handled as its own best-effort step,
# separate from the core dependencies above: confirmed live that
# 'raspi-gpio' isn't installable via apt on a "trixie"-based Raspberry Pi
# OS release ("E: Unable to locate package raspi-gpio") -- it's been
# superseded there by 'pinctrl', which recent Raspberry Pi OS images often
# ship preinstalled already. A failure here must NEVER abort the rest of
# the install; commissioning_checks() below adapts to whichever tool (if
# any) actually ends up available.
install_gpio_pinmux_tool() {
  if command -v pinctrl >/dev/null 2>&1 || command -v raspi-gpio >/dev/null 2>&1; then
    log_info "GPIO pinmux tool already present ($(command -v pinctrl || command -v raspi-gpio))."
    return
  fi
  if apt-get install -y --no-install-recommends raspi-gpio >/dev/null 2>&1; then
    log_info "Installed 'raspi-gpio'."
    return
  fi
  if apt-get install -y --no-install-recommends pinctrl >/dev/null 2>&1; then
    log_info "Installed 'pinctrl'."
    return
  fi
  log_warn "Neither 'raspi-gpio' nor 'pinctrl' could be installed (not packaged for this OS release,"
  log_warn "or already provided by the base image under a different mechanism). The commissioning"
  log_warn "check in Step 15 will not be able to automatically verify IN1/GPIO23 isn't SPI0-claimed --"
  log_warn "you'll need to check that manually (e.g. 'cat /sys/kernel/debug/pinctrl/*/pinmux-pins' if"
  log_warn "debugfs is mounted) before wiring OUT1 to a live generator."
}

# ---------------------------------------------------------------------------
# Step 3: Python 3.11+ selection
# ---------------------------------------------------------------------------

select_python() {
  log_step "Step 3: Python 3.11+ selection"
  local candidate ver major minor
  for candidate in python3.13 python3.12 python3.11 python3; do
    if command -v "$candidate" >/dev/null 2>&1; then
      ver="$("$candidate" -c 'import sys; print("%d.%d" % sys.version_info[:2])' 2>/dev/null || echo "0.0")"
      major="${ver%%.*}"
      minor="${ver##*.}"
      if (( major > 3 || (major == 3 && minor >= 11) )); then
        PYTHON_BIN="$(command -v "$candidate")"
        log_info "Using ${PYTHON_BIN} (Python ${ver})"
        break
      fi
    fi
  done
  if [[ -z "$PYTHON_BIN" ]]; then
    log_error "No Python 3.11+ interpreter found. Raspberry Pi OS ships a new-enough Python as 'python3' by default (3.11 on Bookworm, 3.13 on Trixie) -- check for a broken/older install."
    exit 1
  fi
}

# ---------------------------------------------------------------------------
# Step 4: /boot/firmware/config.txt overlays (RS-485 + USB host-mode)
# ---------------------------------------------------------------------------

add_config_line() {
  local line="$1"
  if grep -qxF "$line" "$CONFIG_TXT" 2>/dev/null; then
    log_info "config.txt already has: ${line}"
  else
    echo "$line" >> "$CONFIG_TXT"
    log_info "Appended to config.txt: ${line}"
    REBOOT_REQUIRED=1
  fi
}

configure_boot_overlays() {
  log_step "Step 4: RS-485 + USB host-mode overlays in ${CONFIG_TXT}"

  if grep -qE '^\s*dtoverlay=spi0-2cs\b' "$CONFIG_TXT" 2>/dev/null; then
    log_error "config.txt already enables 'dtoverlay=spi0-2cs' (SPI0 dual chip-select)."
    log_error "This conflicts with IN1 (BCM GPIO23) per Waveshare's board documentation -- SPI0 CE1"
    log_error "can silently take over that same pin. Remove that overlay line from ${CONFIG_TXT}"
    log_error "before continuing, then re-run this script."
    exit 1
  fi

  # RS-485 on the onboard header: BCM GPIO12/13 -> /dev/ttyAMA5. Disabled by
  # default; this overlay is what turns it on.
  add_config_line "dtoverlay=uart5"

  # USB host-mode enablement so the SoC's USB subsystem enumerates the M.2
  # SIM7600G-H-M.2 modem's internal USB interface correctly.
  add_config_line "dtoverlay=dwc2,dr_mode=host"

  # Explicitly NEVER added by this installer, under any circumstance:
  #   dtoverlay=spi0-2cs   (or any other SPI0 dual-chip-select overlay)
  # See the check above and the module docstring in genmon_agent.py for why.

  if [[ "$REBOOT_REQUIRED" -eq 1 ]]; then
    log_warn "config.txt was modified -- A REBOOT IS REQUIRED for /dev/ttyAMA5 (RS-485) and USB host-mode to take effect."
  else
    log_info "config.txt already had both required overlays; no reboot needed for this step."
  fi
}

# ---------------------------------------------------------------------------
# Step 5: dedicated system user
# ---------------------------------------------------------------------------

create_system_user() {
  log_step "Step 5: dedicated 'genmon' system user"
  if id -u genmon >/dev/null 2>&1; then
    log_info "User 'genmon' already exists."
    usermod -aG dialout,gpio genmon
  else
    useradd --system --no-create-home --shell /usr/sbin/nologin --groups dialout,gpio genmon
    log_info "Created system user 'genmon' (groups: dialout, gpio)."
  fi
}

# ---------------------------------------------------------------------------
# Step 6: desktop device-key file permissions (Raspberry Pi OS Desktop only)
# ---------------------------------------------------------------------------

grant_desktop_key_permissions() {
  log_step "Step 6: desktop device-key file permissions"
  # genmon_agent.py writes the device key to every local user's ~/Desktop
  # (genmon_device_key.txt) on every startup, as a convenience for
  # technicians working at the machine with a monitor/keyboard attached
  # instead of SSHing in. The unprivileged 'genmon' user has no write access
  # to another user's home directory by default -- grant it here, once, by
  # joining that user's primary group and making their Desktop folder
  # group-writable. Low-sensitivity data (just a device identifier), so a
  # group-write grant is an acceptable trade for not needing broader
  # permissions or an extra ACL package.
  local found=0 home user group
  for home in /home/*/; do
    [[ -d "$home" ]] || continue
    user="$(basename "$home")"
    # Skip anything that isn't a real login account (defensive; genmon
    # itself has no /home entry since it's --no-create-home).
    id -u "$user" >/dev/null 2>&1 || continue
    found=1
    group="$(id -gn "$user")"
    install -d -o "$user" -g "$group" -m 0770 "${home}Desktop"
    usermod -aG "$group" genmon
    log_info "Granted 'genmon' write access to ${home}Desktop (via group '${group}')."
  done
  if [[ "$found" -eq 0 ]]; then
    log_info "No /home/* user directories found (headless/Lite install) -- nothing to grant. The"
    log_info "agent's desktop-key-file write is a no-op here, which is expected and harmless."
  fi
}

# ---------------------------------------------------------------------------
# Step 7: directories
# ---------------------------------------------------------------------------

create_directories() {
  log_step "Step 7: directories"
  install -d -o genmon -g genmon -m 0750 /opt/genmon
  # 0770, not 0750: the agent rewrites device.conf in place via a
  # write-tmp-then-rename (os.replace) pattern for atomicity, which needs
  # write+execute on the *directory* (to create device.conf.tmp and rename
  # it over device.conf), not just on the file. Confirmed live: 0750 left
  # the genmon group with read+execute only, so every save() crashed with
  # PermissionError on device.conf.tmp. Still root:genmon-only (0770 grants
  # nothing to "other"), so this stays as protected as before against any
  # user outside that group.
  install -d -o root -g genmon -m 0770 /etc/genmon
  install -d -o genmon -g genmon -m 0750 /var/log/genmon
  log_info "Created /opt/genmon, /etc/genmon, /var/log/genmon."
}

# ---------------------------------------------------------------------------
# Step 8: Python virtual environment
# ---------------------------------------------------------------------------

create_venv() {
  log_step "Step 8: Python virtual environment"
  if [[ ! -x /opt/genmon/venv/bin/python3 ]]; then
    # --system-site-packages: lets the venv see the apt-installed
    # python3-lgpio package (see install_apt_dependencies) -- that package
    # ships a working precompiled liblgpio.so, unlike the PyPI 'lgpio'
    # wheel, which has no prebuilt binary for this platform and fails at
    # the link step when pip tries to build it from source. Everything
    # this venv pip-installs (requirements.txt) still shadows any
    # same-named system package, so this only adds visibility, not
    # version conflicts.
    "$PYTHON_BIN" -m venv --system-site-packages /opt/genmon/venv
    log_info "Created venv at /opt/genmon/venv"
  else
    log_info "venv already exists at /opt/genmon/venv"
  fi

  # Force system-site-packages visibility even on a venv that already
  # existed before this setting was introduced (or was otherwise created
  # without it) -- confirmed live that a pre-existing venv from an earlier
  # install run kept include-system-site-packages=false untouched by the
  # block above, silently hiding the apt-installed python3-lgpio module
  # from it despite "Step 8" reporting success. pyvenv.cfg is read live by
  # the venv's Python on every interpreter start, not just baked in at
  # creation time, so editing it in place here (no venv recreation) is
  # sufficient to fix an existing install.
  local pyvenv_cfg="/opt/genmon/venv/pyvenv.cfg"
  if [[ -f "$pyvenv_cfg" ]]; then
    if grep -q '^include-system-site-packages' "$pyvenv_cfg"; then
      sed -i 's/^include-system-site-packages.*/include-system-site-packages = true/' "$pyvenv_cfg"
    else
      echo "include-system-site-packages = true" >> "$pyvenv_cfg"
    fi
    log_info "Confirmed venv has include-system-site-packages = true."
  fi
}

# ---------------------------------------------------------------------------
# Step 9: fetch genmon_agent.py + requirements.txt from a PUBLIC source
# ---------------------------------------------------------------------------

fetch_agent_files() {
  log_step "Step 9: fetch genmon_agent.py + requirements.txt"

  local tmp_dir
  tmp_dir="$(mktemp -d)"
  # shellcheck disable=SC2064
  trap "rm -rf '${tmp_dir}'" RETURN

  local mode="${GENMON_AGENT_SOURCE:-}"
  if [[ -z "$mode" ]]; then
    if [[ -n "${GENMON_API_BASE:-}" ]]; then mode="api"; else mode="github"; fi
  fi
  local version="${GENMON_AGENT_VERSION:-latest}"
  local agent_url req_url

  case "$mode" in
    api)
      local api_base="${GENMON_API_BASE:-https://api.allyoperations.com}"
      agent_url="${api_base%/}/devices/agent/download"
      ;;
    github)
      if [[ "$version" == "latest" ]]; then
        agent_url="https://github.com/jgoldenally/genmonitoring/releases/latest/download/genmon_agent.py"
      else
        agent_url="https://github.com/jgoldenally/genmonitoring/releases/download/agent-v${version}/genmon_agent.py"
      fi
      ;;
    *)
      log_error "Unknown GENMON_AGENT_SOURCE='${mode}' (expected 'github' or 'api')."
      exit 1
      ;;
  esac

  # requirements.txt has no API endpoint of its own -- it is always fetched
  # from the GitHub release asset, regardless of which source served the
  # agent script itself.
  if [[ "$version" == "latest" ]]; then
    req_url="https://github.com/jgoldenally/genmonitoring/releases/latest/download/requirements.txt"
  else
    req_url="https://github.com/jgoldenally/genmonitoring/releases/download/agent-v${version}/requirements.txt"
  fi

  log_info "Downloading agent script (unauthenticated, no token embedded) from: ${agent_url}"
  curl -fsSL "$agent_url" -o "${tmp_dir}/genmon_agent.py"

  log_info "Downloading requirements.txt from: ${req_url}"
  curl -fsSL "$req_url" -o "${tmp_dir}/requirements.txt"

  # Sanity-check the downloaded script compiles before installing it.
  "$PYTHON_BIN" -m py_compile "${tmp_dir}/genmon_agent.py"

  install -o genmon -g genmon -m 0640 "${tmp_dir}/genmon_agent.py" /opt/genmon/genmon_agent.py
  install -o genmon -g genmon -m 0640 "${tmp_dir}/requirements.txt" /opt/genmon/requirements.txt
  log_info "Installed genmon_agent.py + requirements.txt to /opt/genmon/"
}

# ---------------------------------------------------------------------------
# Step 10: pip install
# ---------------------------------------------------------------------------

pip_install_dependencies() {
  log_step "Step 10: pip install (into /opt/genmon/venv)"
  /opt/genmon/venv/bin/pip install --upgrade pip --quiet
  /opt/genmon/venv/bin/pip install -r /opt/genmon/requirements.txt --quiet
  chown -R genmon:genmon /opt/genmon
  log_info "Dependencies installed."
}

# ---------------------------------------------------------------------------
# Step 11: /etc/genmon/device.conf (never overwritten if already present)
# ---------------------------------------------------------------------------

write_embedded_device_conf_template() {
  cat > "$1" <<'CONF_EOF'
[device]
device_key =
cpu_serial =
api_base_url = https://api.allyoperations.com
agent_version = 0.0.0

[auth]
device_bearer_token =

[mqtt]
host =
port = 1883
tls = false

[gpio]
in1_pin = 23
in1_debounce_ms = 50
out1_pin = 6
session_ttl_seconds = 300
session_ttl_max_seconds = 1800

[network]
config_refresh_interval_seconds = 60
heartbeat_interval_seconds = 60

[cellular]
apn =
CONF_EOF
}

write_device_conf() {
  log_step "Step 11: /etc/genmon/device.conf"
  if [[ -f /etc/genmon/device.conf ]]; then
    log_info "/etc/genmon/device.conf already exists -- leaving it untouched (never overwritten)."
    return
  fi

  local template="${SCRIPT_DIR}/config/device.conf.example"
  if [[ -f "$template" ]]; then
    install -o root -g genmon -m 0640 "$template" /etc/genmon/device.conf
    log_info "Wrote /etc/genmon/device.conf from local template: ${template}"
  else
    # Running install.sh standalone (e.g. fetched without the rest of the
    # repo checkout) -- fall back to an embedded copy of the same template.
    write_embedded_device_conf_template /etc/genmon/device.conf
    chown root:genmon /etc/genmon/device.conf
    chmod 0640 /etc/genmon/device.conf
    log_info "Wrote /etc/genmon/device.conf from embedded fallback template."
  fi

  if [[ -n "${GENMON_API_BASE:-}" ]]; then
    sed -i "s#^api_base_url = .*#api_base_url = ${GENMON_API_BASE%/}#" /etc/genmon/device.conf
    log_info "Set api_base_url = ${GENMON_API_BASE%/} in device.conf."
  fi
}

# ---------------------------------------------------------------------------
# Step 12: sudoers drop-in (validated with visudo -cf before activating)
# ---------------------------------------------------------------------------

install_sudoers() {
  log_step "Step 12: sudoers drop-in for 'genmon'"

  local tmp_sudoers
  tmp_sudoers="$(mktemp)"
  cat > "$tmp_sudoers" <<'SUDOERS_EOF'
# GenMonitoring field agent -- narrowly scoped passwordless sudo for the
# unprivileged 'genmon' user. Installed/validated by install.sh; if you
# hand-edit this file, re-validate with `visudo -cf /etc/sudoers.d/genmon`.

Cmnd_Alias GENMON_NMCLI = \
    /usr/bin/nmcli connection add type gsm ifname \* con-name genmon-wwan apn * connection.autoconnect yes, \
    /usr/bin/nmcli connection modify * ipv4.route-metric *, \
    /usr/bin/nmcli connection up *

Cmnd_Alias GENMON_IP = \
    /usr/sbin/ip -j addr show, \
    /usr/sbin/ip -j route show

Cmnd_Alias GENMON_HOSTNAME = \
    /usr/bin/hostnamectl set-hostname genmon-*

genmon ALL=(root) NOPASSWD: GENMON_NMCLI, GENMON_IP, GENMON_HOSTNAME
SUDOERS_EOF

  if visudo -cf "$tmp_sudoers"; then
    install -o root -g root -m 0440 "$tmp_sudoers" /etc/sudoers.d/genmon
    log_info "Installed validated sudoers drop-in at /etc/sudoers.d/genmon"
  else
    log_error "Generated sudoers content failed 'visudo -cf' validation -- NOT installing it."
    rm -f "$tmp_sudoers"
    exit 1
  fi
  rm -f "$tmp_sudoers"
}

# ---------------------------------------------------------------------------
# Step 13: systemd service
# ---------------------------------------------------------------------------

write_embedded_systemd_unit() {
  cat > "$1" <<'UNIT_EOF'
[Unit]
Description=GenMonitoring field agent (generator monitoring/control)
After=network-online.target ModemManager.service
Wants=network-online.target

[Service]
Type=simple
User=genmon
SupplementaryGroups=gpio dialout
AmbientCapabilities=CAP_NET_ADMIN CAP_NET_RAW
CapabilityBoundingSet=CAP_NET_ADMIN CAP_NET_RAW
WorkingDirectory=/opt/genmon
ExecStart=/opt/genmon/venv/bin/python3 /opt/genmon/genmon_agent.py --config /etc/genmon/device.conf
EnvironmentFile=-/etc/genmon/agent.env
Environment=PYTHONUNBUFFERED=1
Environment=GPIOZERO_PIN_FACTORY=lgpio
Restart=always
RestartSec=10
StandardOutput=journal
StandardError=journal
SyslogIdentifier=genmon-agent
TimeoutStopSec=15

[Install]
WantedBy=multi-user.target
UNIT_EOF
}

install_systemd_service() {
  log_step "Step 13: systemd service"
  local src="${SCRIPT_DIR}/systemd/genmon-agent.service"
  if [[ -f "$src" ]]; then
    install -o root -g root -m 0644 "$src" /etc/systemd/system/genmon-agent.service
  else
    write_embedded_systemd_unit /etc/systemd/system/genmon-agent.service
  fi
  systemctl daemon-reload
  systemctl enable genmon-agent.service
  if ! systemctl restart genmon-agent.service; then
    log_warn "genmon-agent.service did not start cleanly -- check 'journalctl -u genmon-agent -e'."
    log_warn "This can be expected before the pending reboot finishes enabling the RS-485/USB overlays."
  fi
  log_info "genmon-agent.service installed and enabled."
}

# ---------------------------------------------------------------------------
# Step 14: cellular bring-up if a modem is detected, clean skip if not
# ---------------------------------------------------------------------------

cellular_bringup_check() {
  log_step "Step 14: cellular modem detection"
  if command -v mmcli >/dev/null 2>&1 && mmcli -L 2>/dev/null | grep -q '/Modem/'; then
    log_info "Cellular modem detected via 'mmcli -L'."
    log_info "The agent creates/manages the 'genmon-wwan' NetworkManager GSM profile automatically"
    log_info "at runtime, using the APN configured in /etc/genmon/device.conf's [cellular] section."
    local apn
    apn="$(python3 - <<'PYEOF' 2>/dev/null || true
import configparser
c = configparser.ConfigParser()
c.read("/etc/genmon/device.conf")
print(c.get("cellular", "apn", fallback=""))
PYEOF
)"
    if [[ -z "${apn// }" ]]; then
      log_info "[cellular] apn= is blank -- the agent will still bring up the GSM connection, just"
      log_info "without an explicit APN, letting the network/carrier database auto-assign one (the"
      log_info "same 'auto APN' trick most commercial IoT gateways use). Try this first; only set an"
      log_info "explicit APN in device.conf if that doesn't result in a working data session."
    fi
  else
    log_info "No cellular modem detected -- this unit is Ethernet-only. No action needed; the agent"
    log_info "detects modem absence at runtime too and skips cellular bring-up without error."
  fi
}

# ---------------------------------------------------------------------------
# Step 15: commissioning -- verify the IN1/OUT1 pin mapping against real
# hardware before ANYONE wires OUT1 to a live generator start circuit.
# ---------------------------------------------------------------------------

commissioning_checks() {
  log_step "Step 15: GPIO commissioning checks"

  if [[ -e /dev/ttyAMA5 ]]; then
    log_info "/dev/ttyAMA5 exists -- the RS-485 UART overlay is active."
  else
    log_warn "/dev/ttyAMA5 does not exist yet."
    if [[ "$REBOOT_REQUIRED" -eq 1 ]]; then
      log_warn "This is expected until the pending reboot completes. After rebooting, re-run:"
      if [[ "$PIPED_INSTALL" -eq 1 ]]; then
        log_warn "    curl -fsSL <the same URL you used before> | sudo bash -s -- --commission-only"
      else
        log_warn "    sudo ${SCRIPT_DIR}/install.sh --commission-only"
      fi
    else
      log_warn "This is unexpected since no reboot was pending -- check 'dmesg | grep tty' and"
      log_warn "${CONFIG_TXT} for 'dtoverlay=uart5'."
    fi
  fi

  local pinmux_check_line
  if command -v pinctrl >/dev/null 2>&1; then
    pinmux_check_line="       pinctrl get 23"
  elif command -v raspi-gpio >/dev/null 2>&1; then
    pinmux_check_line="       raspi-gpio get 23"
  else
    pinmux_check_line="       (neither 'pinctrl' nor 'raspi-gpio' is available on this system --\n       check /sys/kernel/debug/pinctrl/*/pinmux-pins manually instead, or\n       install one of those tools yourself before proceeding)"
  fi

  cat <<EOF

============================================================
 GPIO COMMISSIONING -- REQUIRED BEFORE CONNECTING A REAL GENERATOR
============================================================
This board's isolated I/O maps to:
  IN1  (generator run/stop sense)   -> BCM GPIO23
  OUT1 (generator start/stop cmd)   -> BCM GPIO6

Verify BOTH pins against the physical hardware before wiring OUT1 to a
live generator start relay:

  1. Verify IN1 (GPIO23) is a plain input and NOT claimed by SPI0:
$(echo -e "$pinmux_check_line")
     Expect an INPUT function, not an SPI0 ALT function (e.g. "CE1").
     If you see an SPI ALT function here, STOP. Do not proceed. This
     almost always means an SPI0 dual-chip-select overlay is enabled
     somewhere -- this installer does not add one and never should on
     this board.

  2. Verify OUT1 (GPIO6) drives the physical relay/terminal you expect,
     with NOTHING connected to the generator's start circuit yet:
       sudo gpioset gpiochip0 6=1     # energize OUT1 -- watch/listen for
                                      #   the relay/terminal to respond
       sudo gpioset gpiochip0 6=0     # de-energize OUT1
     Confirm with a multimeter (continuity/voltage across the OUT1
     terminal block) that this toggles as expected BEFORE connecting the
     generator's 2-wire remote-start loop.

  3. Only once both are confirmed against real hardware: wire IN1 in
     parallel with the generator controller's own remote-start contact
     loop, and wire OUT1 into that same loop as this agent's command path.

This installer intentionally does NOT toggle a live relay unattended --
blindly trusting a pin-mapping document for a physical start-circuit
actuator is exactly what this commissioning step exists to prevent.
============================================================

EOF

  local confirm=""
  read -r -p "Have you verified IN1/OUT1 above against real hardware? [y/N] " confirm || true
  if [[ "${confirm:-}" =~ ^[Yy]$ ]]; then
    log_info "Commissioning confirmed by operator."
  else
    log_warn "Commissioning NOT confirmed. genmon-agent.service is installed and enabled, but do"
    log_warn "NOT connect OUT1 to a live generator start circuit until the steps above are done."
  fi
}

# ---------------------------------------------------------------------------
# Final summary
# ---------------------------------------------------------------------------

print_summary() {
  log_step "Summary"
  local device_key="(not yet assigned -- appears after the agent's first successful start)"
  if [[ -f /etc/genmon/device.conf ]]; then
    local dk
    dk="$(python3 - <<'PYEOF' 2>/dev/null || true
import configparser
c = configparser.ConfigParser()
c.read("/etc/genmon/device.conf")
print(c.get("device", "device_key", fallback=""))
PYEOF
)"
    [[ -n "${dk// }" ]] && device_key="$dk"
  fi

  local reboot_line="no"
  if [[ "$REBOOT_REQUIRED" -eq 1 ]]; then
    reboot_line="YES -- run 'sudo reboot' now to activate RS-485 (/dev/ttyAMA5) and USB host-mode."
  fi

  cat <<SUMMARY_EOF

============================================================
 GenMonitoring agent install complete
============================================================
 Device key       : ${device_key}
 Config file      : /etc/genmon/device.conf
 Agent script     : /opt/genmon/genmon_agent.py
 Python venv      : /opt/genmon/venv
 Logs             : /var/log/genmon/genmon-agent.log  (also: journalctl -u genmon-agent -f)
 Service          : systemctl status genmon-agent

 Reboot required  : ${reboot_line}

 IMPORTANT: do not wire OUT1 to a live generator start circuit until you
 have completed the GPIO commissioning checks printed above against real
 hardware (re-run with '--commission-only' after rebooting if needed).
============================================================
SUMMARY_EOF
}

# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

main() {
  if [[ "${1:-}" == "--commission-only" ]]; then
    check_preconditions
    commissioning_checks
    exit 0
  fi

  check_preconditions
  install_apt_dependencies
  select_python
  configure_boot_overlays
  create_system_user
  grant_desktop_key_permissions
  create_directories
  create_venv
  fetch_agent_files
  pip_install_dependencies
  write_device_conf
  install_sudoers
  install_systemd_service
  cellular_bringup_check
  commissioning_checks
  print_summary
}

main "$@"
