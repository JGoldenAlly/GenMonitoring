#!/usr/bin/env bash
# One-time bootstrap for Mosquitto's dynamic-security plugin.
#
# Generates mosquitto/config/dynamic-security.json with:
#   1. An admin account (used by the "api" service as MQTT_USERNAME/
#      MQTT_PASSWORD). The api self-provisions everything else it needs on
#      top of this account at startup -- see
#      packages/api/app/services/mosquitto_dynsec.py -- including its own
#      "genmon-device" shared device role and its own "genmon-api-publisher"
#      publish grant. This script does NOT duplicate that.
#   2. A restricted "genmon-bridge" account that can only subscribe to the
#      telemetry/io/ack topic filters the bridge actually needs (the bridge
#      isn't part of the api's self-provisioning, so it's set up here).
#
# Run this ONCE before `docker compose up` for the first time, and again
# any time you delete mosquitto/config/dynamic-security.json to start over.
#
# ============================================================================
# IMPORTANT, verified against a live Mosquitto 2.0.18 broker while building
# packages/api's dynsec client:
#   * The CLI subcommand is `mosquitto_ctrl dynsec ...`, NOT
#     `mosquitto_ctrl dynamic-security ...` (older docs/examples use the
#     latter name -- it does not work against current mosquitto-clients).
#   * ACL entries for any topic containing a wildcard (`+`/`#`) OR a `%c`/`%u`
#     substitution must use the "Pattern" acltype (`subscribePattern`), not
#     "Literal" (`subscribeLiteral`) -- Literal is for a topic that is
#     already a fully concrete string with no wildcard/substitution involved.
#   * Mosquitto's %c/%u substitution in dynsec ACLs is broken before 2.1.0
#     (see eclipse-mosquitto/mosquitto#2222) -- the broker MUST be >= 2.1.0
#     or per-device claim/unclaim and the retained cmd channel will silently
#     fail. The floating `eclipse-mosquitto:2` tag used in docker-compose.yml
#     is fine today (2.1+ has long since shipped) but pin an explicit
#     `eclipse-mosquitto:2.x.y` tag in production so a future re-pull can't
#     silently regress this. If in doubt, check `mosquitto -v`'s reported
#     version on the running container.
# ============================================================================
#
# Usage:
#   ./bootstrap-dynsec.sh <api-admin-password> <bridge-password>

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIG_DIR="$SCRIPT_DIR/config"
IMAGE="eclipse-mosquitto:2"
NETWORK="genmonitoring_default"

API_ADMIN_PASSWORD="${1:?Usage: $0 <api-admin-password> <bridge-password>}"
BRIDGE_PASSWORD="${2:?Usage: $0 <api-admin-password> <bridge-password>}"
API_ADMIN_USER="genmon-api"
BRIDGE_USER="genmon-bridge"

mkdir -p "$CONFIG_DIR"

if [ -f "$CONFIG_DIR/dynamic-security.json" ]; then
  echo "dynamic-security.json already exists at $CONFIG_DIR -- delete it first if you want to re-bootstrap." >&2
  exit 1
fi

echo "==> Generating initial dynamic-security.json (admin user: $API_ADMIN_USER)"
docker run --rm -v "$CONFIG_DIR:/mosquitto/config" "$IMAGE" \
  mosquitto_ctrl dynsec init /mosquitto/config/dynamic-security.json "$API_ADMIN_USER" "$API_ADMIN_PASSWORD"

echo "==> Starting a temporary Mosquitto instance to provision the bridge account"
docker network inspect "$NETWORK" >/dev/null 2>&1 || docker network create "$NETWORK"

CID=$(docker run -d --rm \
  --network "$NETWORK" \
  -v "$CONFIG_DIR:/mosquitto/config" \
  -v "$SCRIPT_DIR/../mosquitto/config/mosquitto.conf:/mosquitto/config/mosquitto.conf:ro" \
  "$IMAGE" mosquitto -c /mosquitto/config/mosquitto.conf)
trap 'docker stop "$CID" >/dev/null 2>&1 || true' EXIT
sleep 2

ctrl() {
  docker run --rm --network "$NETWORK" "$IMAGE" \
    mosquitto_ctrl -h "$CID" -p 1883 -u "$API_ADMIN_USER" -P "$API_ADMIN_PASSWORD" dynsec "$@"
}

echo "==> Creating restricted 'genmon-bridge-role' (subscribe-only on telemetry/io/ack topics)"
ctrl createRole genmon-bridge-role
ctrl addRoleACL genmon-bridge-role subscribePattern "genmon/+/data" allow
ctrl addRoleACL genmon-bridge-role subscribePattern "genmon/+/io" allow
ctrl addRoleACL genmon-bridge-role subscribePattern "genmon/+/cmd/ack" allow
ctrl addRoleACL genmon-bridge-role publishClientReceive "genmon/+/data" allow
ctrl addRoleACL genmon-bridge-role publishClientReceive "genmon/+/io" allow
ctrl addRoleACL genmon-bridge-role publishClientReceive "genmon/+/cmd/ack" allow

echo "==> Creating '$BRIDGE_USER' client and assigning the role"
ctrl createClient "$BRIDGE_USER" -p "$BRIDGE_PASSWORD"
ctrl addClientRole "$BRIDGE_USER" genmon-bridge-role

docker stop "$CID" >/dev/null 2>&1 || true
trap - EXIT

cat <<EOF

==> Done. Set these in your environment / docker-compose overrides:

  api:
    MQTT_USERNAME=$API_ADMIN_USER
    MQTT_PASSWORD=$API_ADMIN_PASSWORD

  bridge:
    MQTT_USERNAME=$BRIDGE_USER
    MQTT_PASSWORD=$BRIDGE_PASSWORD

The api self-provisions its own "genmon-device" shared role (used for
per-device clients created/deleted at claim/unclaim time) and its own
"genmon-api-publisher" publish grant the first time it starts up against
this broker -- you don't need to create either here. It never touches
dynamic-security.json directly, only MQTT control messages.
EOF
