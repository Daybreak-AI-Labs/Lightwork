#!/usr/bin/env bash
# Devcontainer / Codespaces bootstrap: editable-install the workspace packages
# the same way CI does, so tests and the CLI work out of the box.
set -euo pipefail

python3 -m venv .venv
export PATH="$PWD/.venv/bin:$PATH"

pip install -e ./packages/maverick-core
pip install --no-deps -e ./packages/maverick-shield
pip install --no-deps -e ./packages/maverick-channels
pip install --no-deps -e ./packages/maverick-evolve
pip install --no-deps -e ./packages/maverick-dashboard
pip install --no-deps -e ./packages/maverick-mcp
pip install --no-deps -e ./packages/maverick-knowledge
pip install --no-deps -e ./apps/installer-cli

# Runtime deps the --no-deps installs dropped, plus the dev tooling.
pip install 'questionary>=2.0' 'rich>=13.7' \
            'fastapi>=0.110' 'uvicorn>=0.27' 'jinja2>=3.1' \
            'httpx>=0.27' 'python-multipart>=0.0.9' 'pywhispercpp>=1.5'
# pyjwt[crypto] mirrors the CI test job (test_oidc.py self-skips without it).
# cffi: distro-owned cryptography builds (e.g. Debian's 41.x) lack
# _cffi_backend for this interpreter and crash pytest collection with a
# pyo3 PanicException; pip can't replace the distro package, but installing
# cffi fixes the import. build: so `python3 -m build --wheel` works per-package.
# pytest-xdist: the documented `python3 -m pytest -n auto --dist loadfile`
# invocation (the one CI uses) needs it.
pip install pytest pytest-asyncio pytest-xdist 'ruff>=0.5' 'vulture>=2.11' \
            'pyjwt[crypto]' cffi build 'pyyaml>=6.0.3'
# AI-evidence local parsers: Pillow mirrors the CI environment
# (requirements/ci.txt pins it); xlrd + olefile power the legacy-XLS worker.
# Their tests self-skip without these (test_oidc pattern), so this install is
# what makes them actually RUN in the devcontainer.
pip install 'pillow>=10' 'xlrd>=2.0' 'olefile>=0.47'
# openai: every OpenAI-compatible provider (openai/ollama/vllm/tgi/azure/...)
# imports it; without it a vllm:/ollama:-routed run dies at the provider.
pip install 'openai>=1.30'

echo
echo "Lightwork devcontainer ready."
echo "  try:  maverick --help"
echo "  test: python3 -m pytest packages/maverick-core/tests -q"
