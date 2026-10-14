#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
export AGENT_TRUST_AUDITOR_STANDALONE=1
exec python serve_standalone.py

