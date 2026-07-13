#!/usr/bin/env bash
# PIA Concierge demo launcher.
#
# Starts TWO processes sharing one MAVERICK_HOME (world.db + signed audit chain):
#   :8765  the REAL Lightwork dashboard  — goals, approvals, assessments, audit
#   :8890  the external world            — mock ServiceNow, requester inbox +
#          questionnaire, mock OneTrust tenant
#
# No external accounts and no LLM key required. Real platform code does the
# work; only the ServiceNow/OneTrust *tenants* are simulated.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DASH_PORT="${DASH_PORT:-8765}"
WORLD_PORT="${WORLD_PORT:-8890}"

# --- shared, isolated platform home (world db, audit chain, keys)
export MAVERICK_HOME="${MAVERICK_HOME:-$HERE/.demo-home}"

# --- email: the real email tool delivers into the in-process capture inbox
export EMAIL_USER="${EMAIL_USER:-privacy-office@company.com}"
export EMAIL_APP_PASSWORD="${EMAIL_APP_PASSWORD:-demo-app-password}"
export EMAIL_SMTP_HOST="${EMAIL_SMTP_HOST:-127.0.0.1}"
export EMAIL_SMTP_PORT="${EMAIL_SMTP_PORT:-1025}"   # non-465 -> STARTTLS path

# --- OneTrust: the real onetrust tool pointed at the mock tenant
export PIA_BASE_URL="${PIA_BASE_URL:-http://127.0.0.1:$WORLD_PORT}"
export LIGHTWORK_DASHBOARD_URL="${LIGHTWORK_DASHBOARD_URL:-http://127.0.0.1:$DASH_PORT}"
export ONETRUST_HOSTNAME="${ONETRUST_HOSTNAME:-$PIA_BASE_URL/ot-api}"
export ONETRUST_TOKEN="${ONETRUST_TOKEN:-demo-bearer-token}"

# --- shield hardening for anything shown to a security audience
export MAVERICK_SHIELD_PROFILE="${MAVERICK_SHIELD_PROFILE:-strict}"
# --- dashboard-created goals otherwise clamp to $2 and can die mid-demo
export MAVERICK_DEFAULT_MAX_DOLLARS="${MAVERICK_DEFAULT_MAX_DOLLARS:-10}"

echo "MAVERICK_HOME        → $MAVERICK_HOME"

# Single process for BOTH servers: the Ed25519 audit chain requires one writing
# process (the signer holds the chain head), matching how `maverick dashboard`
# itself runs the platform in-process.
export DASH_PORT WORLD_PORT
cd "$HERE"
exec python3 serve.py
