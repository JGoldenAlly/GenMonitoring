# GenMonitoring

Ally Generator Monitoring Platform.

GenMonitoring polls Modbus data (RTU over RS-485 or TCP) from diesel/gas
generators via a field agent running on a Raspberry Pi CM4, publishes
telemetry over MQTT to a self-hosted stack, and lets an authorized operator
remotely **start and stop a generator** through a GPIO relay output, while
continuously watching a GPIO input so a start commanded by anyone/anything
else outside the platform is still detected and logged.

It is a sibling platform to Ally's existing AetherLynk system, focused
specifically on generators, with two capabilities AetherLynk doesn't have:
Modbus RTU support and remote start/stop control.

## Architecture

```
CM4 field agent  --MQTT/TLS:8883-->  Mosquitto  -->  bridge  -->  Postgres + TimescaleDB
  (Modbus RTU/TCP,                                      ^                  ^
   GPIO IN1/OUT1)                                        |                  |
        ^ subscribes genmon/{key}/cmd  <---------------  api  <---------  portal
                 (retained, QoS1)         publishes genmon/{key}/cmd   (Next.js)
```

- **`packages/api`** -- FastAPI backend. Device claiming, Modbus templates,
  telemetry queries, and the generator start/stop command channel.
- **`packages/bridge`** -- pure MQTT-to-database worker, no web UI.
- **`packages/portal`** -- Next.js web UI.
- **`packages/agent`** -- the field-agent software that runs on the CM4.
- **`unraid/`** -- Unraid Community Applications templates for the three
  containers above.
- **`mosquitto/`** -- local/dev Mosquitto config + dynamic-security bootstrap
  script (credential management, no `docker.sock` mount needed anywhere).

## MQTT topics

| Topic | Direction | QoS/Retain | Purpose |
|---|---|---|---|
| `genmon/{device_key}/data` | agent -> bridge | 1, no retain | Telemetry readings |
| `genmon/{device_key}/io` | agent -> bridge | 1, no retain | GPIO IN1/OUT1 transitions |
| `genmon/{device_key}/cmd` | api -> agent | 1, **retained** | Start/stop/renew commands |
| `genmon/{device_key}/cmd/ack` | agent -> bridge | 1, no retain | Command result |

See `shared/schema.py` for the full payload shapes and device-key format.

## Generator start/stop safety model

Generators here use standard 2-wire start/stop logic: closing the remote
contact commands **run**, opening it commands **stop**. The field agent
drives that contact via GPIO OUT1 and independently monitors it via GPIO IN1,
so a start commanded by a local switch or another system is still detected.

Two layers of safety bound every remote "start" session:

1. **Operator session cap** -- a `run` command has an overall expiry
   (`max_run_session_minutes` per generator, default 60 min).
2. **Local deadman lease** -- the field agent tracks a much shorter lease
   (default 300s) with its own local timer, independent of connectivity. The
   API renews this lease every ~150s as long as the operator's session is
   still active. **If renewal ever stops -- API crash, broker outage,
   cellular drop -- the agent forces the relay open on its own,
   with no dependency on connectivity.** On any agent restart (crash,
   self-update, reboot), the relay always initializes open/stopped.

Do not wire OUT1 to a real generator's start circuit until you've completed
the GPIO commissioning check in the agent install steps below.

---

## Part 1 -- Deploying the containers on Unraid

### Prerequisites

- A Postgres 16 instance with the TimescaleDB extension available (a plain
  Postgres 16 also works -- the schema falls back gracefully, just without
  hypertable compression/retention). The included `docker-compose.yml` uses
  `timescale/timescaledb:latest-pg16` if you'd rather run this as its own
  container/VM instead of on Unraid's own Postgres, if you have one.
- A Mosquitto 2.x broker with the dynamic-security plugin (stock in
  `eclipse-mosquitto:2`).

### 1. Stand up Postgres and Mosquitto

If you don't already have a Postgres+TimescaleDB instance, add one via the
Unraid Community Applications store (search `timescaledb`), or run the
`postgres` service from this repo's `docker-compose.yml` standalone.

For Mosquitto: add the `eclipse-mosquitto:2` container (Community
Applications has a template, or add it manually), mounting a config
directory to `/mosquitto/config` and a data directory to `/mosquitto/data`.
Copy `mosquitto/config/mosquitto.conf` from this repo into that config
directory.

Then, from a machine with Docker (does not need to be Unraid itself, just
network-reachable to the broker), run the bootstrap script **once**:

```bash
./mosquitto/bootstrap-dynsec.sh <api-admin-password> <bridge-password>
```

This generates `dynamic-security.json` (copy it alongside your
`mosquitto.conf` on the Unraid share) with:
- a `genmon-api` admin account (used by the API to provision/revoke
  per-device MQTT credentials and to publish start/stop commands), and
- a restricted `genmon-bridge` account (subscribe-only, telemetry/IO/ack
  topics only).

Restart the Mosquitto container after copying the generated file in.

For a **production** deployment, enable the commented-out TLS listener block
in `mosquitto.conf` (port 8883) with real certificates, and set `MQTT_TLS=true`
on both the `api` and `bridge` containers, and on every field agent's
`device.conf`.

### 2. Import the Unraid templates

In Unraid's Docker tab, click **Add Container**, switch the template
dropdown to **"Template: (select one)"**, and instead paste each XML's raw
URL (or use "Add Container" > paste the repo URL for each template file
under `unraid/`):

- `unraid/genmonitoring-api.xml`
- `unraid/genmonitoring-bridge.xml`
- `unraid/genmonitoring-portal.xml`

Fill in the required fields for each (all marked `Required="true"` in the
template): database URL, `JWT_SECRET` (generate with `openssl rand -hex 32`),
Mosquitto host/port/credentials, `CORS_ORIGINS` (your portal's URL), and for
the portal, `NEXT_PUBLIC_API_URL` (your api's browser-reachable URL).

Start `genmonitoring-api` and `genmonitoring-bridge` first, then
`genmonitoring-portal`. The API runs its Alembic migrations and seeds the
built-in Modbus templates (`generic-generator`, `cat-emcp42`,
`generator-standard`) automatically on first startup.

### 3. Create your first admin user

The API ships with no default users. Create the first admin account directly
against the database (or via a one-off script/endpoint -- see
`packages/api/README` if present, or use `docker exec` into the api
container and run whatever admin-bootstrap helper it provides) before logging
into the portal.

### Local development

`docker compose up --build` from the repo root brings up Postgres, Mosquitto,
api, bridge, and portal together, using the plaintext (`MQTT_TLS=false`)
1883 listener for convenience. Run `mosquitto/bootstrap-dynsec.sh` once
first (`docker compose up postgres mosquitto -d` to get the broker running,
then bootstrap against it).

---

## Part 2 -- Installing the field agent on a CM4

### Hardware

- Raspberry Pi CM4 (any RAM/eMMC variant) on a Waveshare **Compute Module 4
  PoE 4G Board**.
- An M.2 B-key 4G modem in the board's M.2 socket if cellular backup is
  needed (Verizon SIM, manually provisioned -- PoE Ethernet is the primary
  link; cellular is automatic failover). The board's M.2 socket is USB2.0
  only, not PCIe.
- The generator's 2-wire remote start/stop circuit wired to the board's
  isolated screw terminals: **OUT1** (drives the circuit: closed = run,
  open = stop) and **IN1** (monitors the same circuit's actual state).
  ⚠️ Confirm the OUT1/IN1 terminal-to-GPIO mapping with the commissioning
  check in step 8 below **before** connecting to a live generator -- do not
  trust silkscreen/documentation alone for this connection.
- If polling over RS-485/Modbus RTU, wire the generator controller's A/B
  (or D+/D-) lines to the board's onboard RS-485 terminal.

### 1. Flash Raspberry Pi OS (Bookworm, 64-bit) to the CM4's eMMC/SD

Use Raspberry Pi Imager as usual, enabling SSH and setting a hostname/user
during imaging if you like (the install script will rename the host to the
device key later anyway).

### 2. Copy the agent package to the Pi

```bash
scp -r packages/agent pi@<cm4-ip>:/tmp/genmon-agent
```

(Or clone this repo directly on the Pi.)

### 3. Run the installer

```bash
ssh pi@<cm4-ip>
sudo bash /tmp/genmon-agent/install.sh
```

This installs dependencies (NetworkManager, ModemManager, no VNC/router-mode
packages), creates a dedicated unprivileged `genmon` system user, sets up a
Python venv, fetches `genmon_agent.py`, writes `/etc/genmon/device.conf`,
installs the systemd service, and edits `/boot/firmware/config.txt` to
enable the onboard RS-485 port and USB host mode for the M.2 modem.

### 4. Reboot

The `config.txt` changes (RS-485, USB) only take effect after a reboot --
the installer will tell you if one is pending.

```bash
sudo reboot
```

### 5. Set the API endpoint and (if used) cellular APN

Edit `/etc/genmon/device.conf`:

```ini
[device]
api_base_url = https://<your-unraid-ip-or-domain>:8000

[cellular]
apn = <your Verizon-provisioned APN>
```

Restart the agent: `sudo systemctl restart genmon-agent`.

### 6. Claim the device in the portal

The agent prints its device key (`GM-XXXX-XXXX`) to the console/journal on
first boot (`journalctl -u genmon-agent -f`). In the portal, go to
**Devices**, find the unclaimed device by that key, and claim it.

### 7. Add the generator

In the portal, open the claimed device and **Add Generator**: pick a Modbus
template (`generic-generator`, `cat-emcp42`, or `generator-standard` as
starting points -- real register addresses are controller-specific, adjust
as needed for your generator's actual Modbus map), choose RTU or TCP
transport, and if this generator should be start/stop-controllable, set the
GPIO channels (`OUT1`/`IN1`) and enable start/stop control.

### 8. GPIO commissioning check -- do this before wiring to a live generator

```bash
raspi-gpio get 23   # should show a plain input function, not SPI0_CE1
```

Then toggle GPIO6 (e.g. via a short `gpiozero` one-liner run as the `genmon`
user) and confirm the OUT1 screw terminal actually energizes/de-energizes --
confirm continuity or a relay click, not just that the command ran without
error. Only after this passes should you wire OUT1 into a real generator's
2-wire start circuit.

### 9. Verify RS-485 (if used)

```bash
ls -l /dev/ttyAMA5
```

should exist post-reboot. If a register configured as RTU transport isn't
reading, check baud/parity/slave-ID against the generator controller's
Modbus documentation, and confirm wiring polarity (A/B may need to be
swapped).

---

## Repository layout

```
packages/api/         FastAPI backend
packages/bridge/       MQTT -> Postgres worker
packages/portal/       Next.js web UI
packages/agent/        CM4 field-agent software + install script
shared/schema.py       Canonical MQTT topic/payload/device-key reference
unraid/                Unraid Community Applications templates
mosquitto/             Local/dev broker config + dynamic-security bootstrap
docker-compose.yml     Local development stack
```
