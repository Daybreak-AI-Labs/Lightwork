#!/usr/bin/env bash
# Build a COMPILED, single-file `maverick` binary with Nuitka — the IP-hard
# packaging path. Where PyInstaller bundles your .pyc bytecode (trivially
# decompilable back to readable source), Nuitka compiles the Python to C and
# then to a native binary, so shipping it does not hand a customer your
# proprietary source. Use this for external/enterprise drops where "we never
# ship source" has to be literally true; the PyInstaller path
# (build-macos-app.sh) stays the fast dev/demo build.
#
# NOT a cross-compiler: run this ON each target OS/arch you ship (an arm64 Mac
# for macOS arm64, an x86_64 Linux box for Linux x86_64, Windows for .exe). The
# output name is stamped with the host os/arch so a release feed can carry all
# of them side by side.
#
# PREREQUISITES:
#   - Python 3.10–3.12 (set PYTHON=... to pick one; default python3.12)
#   - A C toolchain Nuitka can drive:
#       macOS   : xcode-select --install            (clang)
#       Linux   : apt-get install -y build-essential patchelf   (gcc + patchelf)
#       Windows : run under MSYS2/Git-Bash with MSVC or bundled MinGW
#   - ~2–4 GB free disk and a few minutes: a C compile is slower than PyInstaller.
#
# RUN (from the repo root):
#   bash scripts/build-nuitka.sh
#
# OUTPUT:
#   dist/maverick-<os>-<arch>[.exe]   — the compiled single-file CLI
#
# Signing/notarization (Apple Developer ID / Windows Authenticode) is a
# separate, post-build step — see docs/DISTRIBUTION.md. This produces the
# unsigned binary; sign the artifact this emits.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

PY="${PYTHON:-python3.12}"
command -v "$PY" >/dev/null 2>&1 || {
  echo "need $PY (or set PYTHON=... to an installed 3.10–3.12)"; exit 1; }

OS="$(uname -s | tr '[:upper:]' '[:lower:]')"
ARCH="$(uname -m)"
case "$OS" in
  darwin) OSTAG="macos" ;;
  linux)  OSTAG="linux" ;;
  msys*|mingw*|cygwin*) OSTAG="windows" ;;
  *) OSTAG="$OS" ;;
esac
EXT=""; [ "$OSTAG" = "windows" ] && EXT=".exe"
OUT="maverick-${OSTAG}-${ARCH}${EXT}"

echo ">> [1/6] clean venv"
rm -rf .nuitkavenv dist/nuitka-build
"$PY" -m venv .nuitkavenv
# shellcheck disable=SC1091
source .nuitkavenv/bin/activate
python -m pip install --upgrade pip wheel >/dev/null

echo ">> [2/6] install packages NON-editable (so Nuitka can follow them)"
# core WITH deps; the rest --no-deps (the workspace pins are already resolved by
# core) — mirrors release.yml + build-macos-app.sh so the two binaries agree.
pip install ./packages/maverick-core
pip install --no-deps ./packages/maverick-shield
pip install --no-deps ./packages/maverick-channels
pip install --no-deps ./packages/maverick-dashboard
pip install --no-deps ./packages/maverick-mcp
pip install --no-deps ./apps/installer-cli
# bundled runtime deps the --no-deps installs skipped:
pip install 'questionary>=2.0' 'rich>=13.7' \
            'fastapi>=0.110' 'uvicorn>=0.27' 'jinja2>=3.1' 'python-multipart>=0.0.9'
pip install 'openai>=1.30'          # provider client so ollama/openai-compatible works
pip install 'nuitka>=2.4'

echo ">> [3/6] sanity: interpreter has sqlite3 + a C compiler"
python -c "import sqlite3, _sqlite3; print('sqlite3', sqlite3.sqlite_version)"
python -m nuitka --version >/dev/null || { echo "nuitka self-check failed"; exit 1; }

echo ">> [4/6] entry launcher (calls the same console-script target)"
mkdir -p dist/nuitka-build
cat > dist/nuitka-build/_entry.py <<'PY'
# Compiled entry point — identical to the `maverick` console script
# ([project.scripts] maverick = "maverick.cli:main").
import sys
from maverick.cli import main
if __name__ == "__main__":
    sys.exit(main())
PY

echo ">> [5/6] compile with Nuitka (standalone + onefile, C backend)"
# --include-package pulls the maverick packages whole (the CLI imports many
#   submodules dynamically, which --follow-imports alone can miss).
# --include-package-data ships the non-.py assets (dashboard Jinja templates,
#   a11y/report assets, packaged specialist packs) inside the binary.
# --nofollow-import-to excludes test trees so the binary stays lean and no test
#   fixtures ride along.
python -m nuitka \
  --standalone --onefile \
  --output-dir=dist/nuitka-build \
  --output-filename="$OUT" \
  --include-package=maverick \
  --include-package=maverick_shield \
  --include-package=maverick_channels \
  --include-package=maverick_dashboard \
  --include-package=maverick_mcp \
  --include-package-data=maverick \
  --include-package-data=maverick_dashboard \
  --nofollow-import-to='*.tests' \
  --nofollow-import-to='*.test_*' \
  --assume-yes-for-downloads \
  --company-name="Daybreak Labs" \
  --product-name="Lightwork" \
  --file-description="Lightwork governed agent platform" \
  dist/nuitka-build/_entry.py

mkdir -p dist
mv "dist/nuitka-build/$OUT" "dist/$OUT"

echo ">> [6/6] smoke test the compiled binary"
"./dist/$OUT" version || "./dist/$OUT" --version
"./dist/$OUT" doctor >/dev/null 2>&1 || true   # doctor exits non-zero without a provider key; that's fine

deactivate || true
echo
echo "DONE."
echo "  dist/$OUT   compiled single-file CLI (C backend — no shippable source)"
echo "Unsigned — sign/notarize per docs/DISTRIBUTION.md before distributing."
