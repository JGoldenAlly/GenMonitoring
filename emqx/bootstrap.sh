#!/usr/bin/env bash
# Bootstrap for EMQX: generates the admin API key file the api container
# uses, and creates the bridge's MQTT credentials + ACL rules.
#
# Unlike Mosquitto's dynamic-security plugin (this project's previous
# broker), EMQX's management API needs no MQTT connection or CLI tool --
# this script is just a text file plus a couple of curl calls.
#
# ============================================================================
# NOT verified against a live EMQX broker (see packages/api/app/services/
# emqx_admin.py's module docstring for the full explanation -- this
# sandbox's network policy blocked pulling emqx/emqx to test against).
# Treat the curl calls below as a strong starting point built from EMQX
# 5.x's documented REST API, not as something already confirmed working.
# Verify against your actual running EMQX version before relying on it.
# ============================================================================
#
# Usage (two-phase -- run once to generate the key file, mount it and
# restart EMQX, then run again to provision the bridge account):
#   ./bootstrap.sh <bridge-mqtt-password> [emqx-api-base-url]

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DATA_DIR="$SCRIPT_DIR/data"
KEYS_FILE="$DATA_DIR/bootstrap_api_keys.txt"
API_BASE="${2:-http://localhost:18083}"
BRIDGE_PASSWORD="${1:?Usage: $0 <bridge-mqtt-password> [emqx-api-base-url]}"

mkdir -p "$DATA_DIR"

if [ ! -f "$KEYS_FILE" ]; then
  echo "==> Generating $KEYS_FILE (genmon-api admin key)"
  secret="$(openssl rand -hex 24)"
  # Format: <api_key>:<api_secret>:<role> -- "administrator" has full
  # management API access, which app/services/emqx_admin.py needs (it
  # creates/deletes MQTT users and ACL rules for devices, and its own
  # account). EMQX loads this file automatically on startup via the
  # EMQX_API_KEY__BOOTSTRAP_FILE env var already set in docker-compose.yml.
  echo "genmon-api:${secret}:administrator" > "$KEYS_FILE"
  cat <<EOF
    Wrote a fresh admin key/secret. Set these on the api container:
      EMQX_API_KEY=genmon-api
      EMQX_API_SECRET=${secret}

    This file lives at $KEYS_FILE, which docker-compose.yml already mounts
    into the emqx container's data directory. If EMQX was already running,
    restart it now so it picks up the bootstrap file:
      docker compose restart emqx

    Then re-run this script (same arguments) to provision the bridge's
    MQTT account.
EOF
  exit 0
fi

API_KEY="$(cut -d: -f1 "$KEYS_FILE" | head -n1)"
API_SECRET="$(cut -d: -f2 "$KEYS_FILE" | head -n1)"

auth() { curl -sS -u "${API_KEY}:${API_SECRET}" "$@"; }

echo "==> Creating genmon-bridge MQTT user"
auth -X POST "${API_BASE}/api/v5/authentication/password_based:built_in_database/users" \
  -H 'content-type: application/json' \
  -d "{\"user_id\": \"genmon-bridge\", \"password\": \"${BRIDGE_PASSWORD}\"}" \
  -o /dev/null -w '  users: HTTP %{http_code}\n'

echo "==> Setting genmon-bridge ACL rules (subscribe-only: telemetry/io/ack)"
auth -X POST "${API_BASE}/api/v5/authorization/sources/built_in_database/rules/users" \
  -H 'content-type: application/json' \
  -d '[{
        "username": "genmon-bridge",
        "rules": [
          {"topic": "genmon/+/data", "permission": "allow", "action": "subscribe"},
          {"topic": "genmon/+/io", "permission": "allow", "action": "subscribe"},
          {"topic": "genmon/+/cmd/ack", "permission": "allow", "action": "subscribe"}
        ]
      }]' \
  -o /dev/null -w '  rules: HTTP %{http_code}\n'

cat <<EOF

==> Done. Set these on the bridge container:
  MQTT_USERNAME=genmon-bridge
  MQTT_PASSWORD=${BRIDGE_PASSWORD}

The api's own MQTT user and its genmon/+/cmd publish rule are provisioned
automatically at api startup (app.main's lifespan calling
emqx_admin_client.connect() / ensure_admin_publish_role()), using the
EMQX_API_KEY/EMQX_API_SECRET printed when this script first generated
$KEYS_FILE -- nothing else to do here for the api.
EOF
