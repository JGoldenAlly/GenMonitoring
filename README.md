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
CM4 field agent  --MQTT:1883-->  EMQX  -->  bridge  -->  Postgres + TimescaleDB
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
- **`emqx/`** -- EMQX bootstrap script (credential management via EMQX's
  HTTP Management API, no `docker.sock` mount needed anywhere).

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

### Ally Operations production domains

This deployment uses:

| Service | Domain | Notes |
|---|---|---|
| `genmonitoring-api` | `https://api.allyoperations.com` | Browser + field-agent facing, HTTPS |
| `genmonitoring-portal` | `https://portal.allyoperations.com` | Browser facing, HTTPS |
| EMQX | `mqtt.allyoperations.com:1883` | Field-agent facing, **plaintext MQTT, no TLS**; dashboard/management API on `:18083` |

All three Unraid templates and the agent's `install.sh`/`device.conf.example`
already default to these values. DNS (`A`/`CNAME` records for all three
subdomains) needs to be in place before field agents can reach
`api.allyoperations.com`/`mqtt.allyoperations.com` from wherever they connect
from.

For `api`/`portal` (plain HTTPS), any standard Unraid reverse proxy setup
(SWAG, Nginx Proxy Manager, Cloudflare Tunnel, etc.) terminating TLS and
forwarding to the container's port works as usual.

**EMQX runs plaintext on 1883 by deliberate choice for this deployment** --
`MQTT_TLS=false` is the default in both Unraid templates and in the
agent's config. This means device MQTT credentials, telemetry, and the
start/stop command channel all travel unencrypted between a field agent
and `mqtt.allyoperations.com:1883`. That's an acceptable trade for the
simplicity of not managing broker certificates **only if** the network
path field agents actually use to reach that port isn't the open internet
-- e.g. it's firewalled to known agent egress IPs/ranges, or routed over a
private APN/VPN backhaul rather than raw public internet. If you can't
guarantee that, enable a TLS listener on EMQX (`EMQX_LISTENERS__SSL__DEFAULT__BIND`
and the corresponding cert/key env vars -- see EMQX's listener
documentation) and set `MQTT_TLS=true`/`MQTT_PORT=8883` on the
`api`/`bridge` containers and in every field agent's `device.conf` instead
(a plain HTTP reverse proxy can't front raw MQTT -- you'd need certs
directly in EMQX or a TCP/SNI passthrough proxy in front of it). Note this
is separate from EMQX's dashboard/management API port (`18083`), which is
plain HTTP either way in this setup -- keep that port itself firewalled to
trusted operator IPs only, since it's the same admin credential the api
uses to provision device MQTT accounts.

### Prerequisites

- A Postgres 16 instance with the TimescaleDB extension available (a plain
  Postgres 16 also works -- the schema falls back gracefully, just without
  hypertable compression/retention). The included `docker-compose.yml` uses
  `timescale/timescaledb:latest-pg16` if you'd rather run this as its own
  container/VM instead of on Unraid's own Postgres, if you have one.
- An **EMQX** broker (`emqx/emqx:5.8.0` or similar 5.x). The api manages
  per-device MQTT credentials and ACL rules entirely through EMQX's HTTP
  Management API (`packages/api/app/services/emqx_admin.py`) -- no
  `docker.sock` mount, no broker CLI tool, just REST calls authenticated
  with an API key/secret pair (see `emqx/bootstrap.sh`). ⚠️ This
  integration was built from EMQX's documented REST API but has **not**
  been exercised against a live broker (this project's build environment
  couldn't reach EMQX's registry/docs to verify it empirically, unlike the
  Mosquitto integration it replaced, which was live-tested). Budget time to
  verify `emqx_admin.py`'s exact API calls against your actual EMQX version
  before trusting device claim/unclaim in production -- the module's
  docstring lists exactly what to check.

### 1. Stand up Postgres and EMQX

If you don't already have a Postgres+TimescaleDB instance, add one via the
Unraid Community Applications store (search `timescaledb`), or run the
`postgres` service from this repo's `docker-compose.yml` standalone.

For EMQX: add the `emqx/emqx:5.8.0` container (Community Applications
has an official EMQX template, or add it manually), with authentication
and authorization configured for its built-in database backend and a
bootstrap API key file mounted in. The env vars/volumes this needs are the
same ones set on the `emqx` service in this repo's `docker-compose.yml` --
mirror those on the Unraid container:

```
EMQX_AUTHENTICATION__1__MECHANISM=password_based
EMQX_AUTHENTICATION__1__BACKEND=built_in_database
EMQX_AUTHENTICATION__1__USER_ID_TYPE=username
EMQX_AUTHORIZATION__SOURCES__1__TYPE=built_in_database
EMQX_AUTHORIZATION__NO_MATCH=deny
EMQX_API_KEY__BOOTSTRAP_FILE=/opt/emqx/data/bootstrap_api_keys.txt
```
mounting a host directory (for `bootstrap_api_keys.txt` plus EMQX's own
persistent state) to `/opt/emqx/data`, and exposing port `1883` (MQTT) and
`18083` (dashboard/management API -- keep this one firewalled to trusted
operator access only, see the security note above).

Then, from a machine with network access to that data directory (or by
running it directly against the mounted path if this is the same host),
run the bootstrap script:

```bash
./emqx/bootstrap.sh <bridge-mqtt-password>
```

The first run generates `emqx/data/bootstrap_api_keys.txt` and prints an
`EMQX_API_KEY`/`EMQX_API_SECRET` pair to set on the api container -- copy
that file to wherever the EMQX container's `/opt/emqx/data` is mounted
and restart EMQX so it picks it up. Run the script **again** (same
arguments) once EMQX is back up: this second run creates the
`genmon-bridge` MQTT account and its subscribe-only ACL rules over EMQX's
REST API. The api's own MQTT account and its `genmon/+/cmd` publish rule
are provisioned automatically the first time the api container starts, so
there's nothing further to do for it here.

This deployment intentionally runs plaintext on 1883 rather than TLS on
8883 -- see "Ally Operations production domains" above for the security
trade-off and how to switch to TLS instead if the network path to EMQX
ever needs it.

### 2. Container images (built automatically by CI)

`.github/workflows/{api,bridge,portal}.yml` each build and push their
package's image to `ghcr.io/jgoldenally/genmonitoring-{api,bridge,portal}`
whenever the relevant `packages/*/**` path changes on `main` (the api
workflow also fires on changes to `packages/agent/genmon_agent.py`, since
that file is bundled into the api image -- see below). Every push gets a
`sha-<short>` tag; pushes to `main` also get `latest`. Pull requests build
(and validate) the image without pushing. Nothing to run by hand once this
is merged to `main` -- just wait for the corresponding workflow to go green
under the repo's **Actions** tab, or trigger one manually with
**Run workflow**.

**One-time setup**: after the first successful run of each workflow, the
resulting GHCR packages are **private** by default. Go to each package's
settings (from the repo's sidebar: **Packages** -> `genmonitoring-api` /
`-bridge` / `-portal` -> **Package settings** -> **Change visibility** ->
**Public**) so Unraid can pull them without needing registry credentials
configured on the Unraid host. Alternatively, keep them private and
configure a registry login in Unraid's Docker settings using a GitHub PAT
with `read:packages` scope.

Notes worth knowing if you ever need to build an image by hand instead
(e.g. testing a local change before pushing):
- The **api** image's build context must be the **repo root** (`.`), not
  `packages/api` -- its Dockerfile also bundles
  `packages/agent/genmon_agent.py` so `GET /devices/agent/download` (the
  field agent's self-update source) has something to serve:
  `docker build -f packages/api/Dockerfile -t <tag> .`
- The **portal** image is built with a placeholder API URL baked in and
  rewrites it at container start from the `NEXT_PUBLIC_API_URL` env var
  (see `packages/portal/docker-entrypoint.sh`) -- this is what lets the
  same published image work for every Unraid installation's own
  IP/hostname, despite Next.js normally baking `NEXT_PUBLIC_*` values in at
  build time. Don't pass a real `--build-arg NEXT_PUBLIC_API_URL=...` when
  building the shared/published image, or you'll freeze it to one URL.

### 3. Import the Unraid templates

In Unraid's Docker tab, click **Add Container**, switch the template
dropdown to **"Template: (select one)"**, and instead paste each XML's raw
URL (or use "Add Container" > paste the repo URL for each template file
under `unraid/`):

- `unraid/genmonitoring-api.xml`
- `unraid/genmonitoring-bridge.xml`
- `unraid/genmonitoring-portal.xml`

Fill in the required fields for each (all marked `Required="true"` in the
template): database URL, `JWT_SECRET` (generate with `openssl rand -hex 32`),
EMQX host/port/credentials (and, on the api template only, `EMQX_API_URL`/
`EMQX_API_KEY`/`EMQX_API_SECRET` from step 1's bootstrap run), `CORS_ORIGINS`
(your portal's URL), and for the portal, `NEXT_PUBLIC_API_URL` (your api's
browser-reachable URL).

Start `genmonitoring-api` and `genmonitoring-bridge` first, then
`genmonitoring-portal`. The API runs its Alembic migrations and seeds the
built-in Modbus templates (`generic-generator`, `cat-emcp42`,
`generator-standard`) automatically on first startup.

### 4. Create your first admin user

The API ships with no default users, and every `/users` endpoint requires an
existing admin -- by design, there's no self-serve registration. Bootstrap
the first account directly in the running container:

```bash
docker exec -it genmonitoring-api python -m app.create_admin admin@example.com
```

You'll be prompted for a password interactively. Running this again for an
email that already exists promotes that user to admin and resets their
password, so it also works as an "I'm locked out" recovery step.

### Local development

`docker compose up --build` from the repo root brings up Postgres, EMQX,
api, bridge, and portal together, using the plaintext (`MQTT_TLS=false`)
1883 listener for convenience. Run `emqx/bootstrap.sh` once first
(`docker compose up postgres emqx -d` to get the broker running, then
bootstrap against it -- see step 1 above for the two-run flow).

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
sudo GENMON_API_BASE=https://api.allyoperations.com bash /tmp/genmon-agent/install.sh
```

(`GENMON_API_BASE` is optional but saves the manual edit in step 5 below --
omit it and the installer defaults to fetching the agent script from GitHub
instead of the api's `/devices/agent/download`, and leaves `api_base_url`
as a placeholder for you to fill in.)

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

### 5. Set the API endpoint (if not already set via `GENMON_API_BASE`) and cellular APN

Edit `/etc/genmon/device.conf` if needed:

```ini
[device]
api_base_url = https://api.allyoperations.com

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

`install.sh` runs this automatically as its final step and prints the
results, but re-run it any time (e.g. after a reboot, or before ever
connecting a live generator) with:

```bash
sudo /tmp/genmon-agent/install.sh --commission-only
```

It checks `raspi-gpio get 23` shows a plain input function (not
`SPI0_CE1` -- see the safety caveat above) and walks you through
`gpioset gpiochip0 6=1` / `6=0` to energize/de-energize OUT1 while you
confirm continuity or a relay click at the physical screw terminal. Only
after this passes should you wire OUT1 into a real generator's 2-wire start
circuit -- do not trust the pin mapping from documentation alone.

### 9. Verify RS-485 (if used)

```bash
ls -l /dev/ttyAMA5
```

should exist post-reboot. If a register configured as RTU transport isn't
reading, check baud/parity/slave-ID against the generator controller's
Modbus documentation, and confirm wiring polarity (A/B may need to be
swapped).

### Releasing a new agent version

To ship an agent update to the fleet: bump `AGENT_VERSION` in
`packages/agent/genmon_agent.py` and merge to `main`. That's the entire
manual step -- `.github/workflows/agent-release.yml` then automatically:

1. Syncs the api's `TARGET_AGENT_VERSION` default
   (`packages/api/app/config.py`) to match, in its own commit.
2. Publishes a GitHub Release tagged `agent-v<version>` with
   `genmon_agent.py` and `requirements.txt` attached -- this is what
   `packages/agent/install.sh`'s `GENMON_AGENT_SOURCE=github` fetch mode
   downloads, both for fresh installs and for a device's own
   self-update check.
3. Triggers an api image rebuild so the freshly-bundled
   `GET /devices/agent/download` copy and the synced default ship
   together.

If a deployment overrides `TARGET_AGENT_VERSION` via its own env var
(recommended for controlling fleet-wide rollout timing rather than
updating every device the instant a release is tagged), that override is
unaffected by step 1 -- only the built-in default changes, and you decide
when to actually bump the env var to roll the update out.

---

## Repository layout

```
packages/api/         FastAPI backend
packages/bridge/       MQTT -> Postgres worker
packages/portal/       Next.js web UI
packages/agent/        CM4 field-agent software + install script
shared/schema.py       Canonical MQTT topic/payload/device-key reference
unraid/                Unraid Community Applications templates
emqx/                  EMQX bootstrap script (+ generated data/ dir, gitignored)
docker-compose.yml     Local development stack
```
