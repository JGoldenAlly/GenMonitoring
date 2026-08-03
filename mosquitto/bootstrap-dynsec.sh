#!/usr/bin/env bash
# One-time bootstrap for Mosquitto's dynamic-security plugin.
#
# Generates mosquitto/config/dynamic-security.json with:
#   1. An admin account (used by the "api" service as MQTT_USERNAME/
#      MQTT_PASSWORD -- it needs admin rights to create/delete per-device
#      credentials at claim/unclaim time and to publish start/stop commands).
#   2. A restricted "genmon-bridge" account that can only subscribe to the
#      telemetry/io/ack topic filters the bridge actually needs.
#
# Run this ONCE before `docker compose up` for the first time, and again
# any time you delete mosquitto/config/dynamic-security.json to start over.
#
# NOTE: mosquitto_ctrl's exact command names/flags can shift between
# Mosquitto releases. Verify these against `mosquitto_ctrl dynamic-security
# help` for the image tag you're actually running before relying on this in
# production -- treat this script as a strong starting point, not gospel.
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
  mosquitto_ctrl dynamic-security init /mosquitto/config/dynamic-security.json "$API_ADMIN_USER" "$API_ADMIN_PASSWORD"

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
    mosquitto_ctrl -h "$CID" -p 1883 -u "$API_ADMIN_USER" -P "$API_ADMIN_PASSWORD" dynamic-security "$@"
}

echo "==> Creating restricted 'genmon-bridge-role' (subscribe-only on telemetry/io/ack topics)"
ctrl createRole genmon-bridge-role
ctrl addRoleACL genmon-bridge-role subscribeLiteral "genmon/+/data" allow
ctrl addRoleACL genmon-bridge-role subscribeLiteral "genmon/+/io" allow
ctrl addRoleACL genmon-bridge-role subscribeLiteral "genmon/+/cmd/ack" allow
ctrl addRoleACL genmon-bridge-role publishClientReceive "genmon/+/data" allow
ctrl addRoleACL genmon-bridge-role publishClientReceive "genmon/+/io" allow
ctrl addRoleACL genmon-bridge-role publishClientReceive "genmon/+/cmd/ack" allow

echo "==> Creating '$BRIDGE_USER' client and assigning the role"
ctrl createClient "$BRIDGE_USER" -p "$BRIDGE_PASSWORD"
ctrl addClientRole "$BRIDGE_USER" genmon-bridge-role

echo "==> Creating shared 'genmon-device-role' (per-device pub/sub scoped via %c substitution)"
ctrl createRole genmon-device-role
ctrl addRoleACL genmon-device-role publishClientSend "genmon/%c/data" allow
ctrl addRoleACL genmon-device-role publishClientSend "genmon/%c/io" allow
ctrl addRoleACL genmon-device-role publishClientSend "genmon/%c/cmd/ack" allow
ctrl addRoleACL genmon-device-role subscribeLiteral "genmon/%c/cmd" allow
ctrl addRoleACL genmon-device-role publishClientReceive "genmon/%c/cmd" allow

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

The api's mosquitto_dynsec service uses the admin account above to create a
per-device client + assign it to "genmon-device-role" at claim time, and to
delete the client at unclaim time. It never touches dynamic-security.json
directly -- everything goes over MQTT control messages.
EOF
