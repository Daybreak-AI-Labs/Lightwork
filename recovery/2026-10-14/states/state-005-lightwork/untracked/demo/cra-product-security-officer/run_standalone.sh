#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
export CRA_PSO_STANDALONE=1
exec python serve_standalone.py
