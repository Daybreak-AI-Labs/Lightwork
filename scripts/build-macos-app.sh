#!/usr/bin/env bash
# Build a double-click Lightwork.app (+ optional .dmg) on macOS — compiled,
# no PyPI, no source shipped. Mirrors the proven release.yml "binaries" recipe
# (non-editable installs so PyInstaller can bundle them), then wraps the
# compiled `maverick` binary into an .app that opens the local dashboard.
#
# PREREQUISITES (Apple Silicon Mac):
#   xcode-select --install                         # command-line tools
#   /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
#   brew install python@3.12 create-dmg            # create-dmg optional (for .dmg)
#
# RUN (from the repo root):
#   bash scripts/build-macos-app.sh
#
# OUTPUT:
#   dist/maverick            — the compiled single-file CLI binary
#   dist/Lightwork.app       — double-click app (opens the dashboard)
#   dist/Lightwork.dmg       — drag-to-Applications installer (if create-dmg present)
#
# NOTE: unsigned. First open: right-click Lightwork.app -> Open -> Open.
# Signing/notarization needs an Apple Developer account (see docs/DISTRIBUTION.md).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

PY="${PYTHON:-python3.12}"
command -v "$PY" >/dev/null 2>&1 || { echo "need $PY (brew install python@3.12) or set PYTHON=..."; exit 1; }
[ "$(uname)" = "Darwin" ] || { echo "run this on macOS"; exit 1; }

echo ">> [1/6] clean venv"
rm -rf .buildvenv dist build/dist build/build
"$PY" -m venv .buildvenv
# shellcheck disable=SC1091
source .buildvenv/bin/activate
python -m pip install --upgrade pip wheel >/dev/null

echo ">> [2/6] install packages NON-editable (so PyInstaller can bundle them)"
pip install ./packages/maverick-core
pip install --no-deps ./packages/maverick-shield
pip install --no-deps ./packages/maverick-channels
pip install --no-deps ./packages/maverick-dashboard
pip install --no-deps ./packages/maverick-mcp
pip install --no-deps ./apps/installer-cli
# bundled deps the --no-deps installs skipped:
pip install 'questionary>=2.0' 'rich>=13.7' \
            'fastapi>=0.110' 'uvicorn>=0.27' 'jinja2>=3.1' 'python-multipart>=0.0.9'
# a provider client so ollama:/azure/openai-compatible runs work in the binary:
pip install 'openai>=1.30'
pip install 'pyinstaller>=6.0,<7.0'

echo ">> [3/6] sanity: sqlite3 present in this interpreter"
python -c "import sqlite3, _sqlite3; print('sqlite3', sqlite3.sqlite_version)"

echo ">> [4/6] compile the binary (PyInstaller, from build/)"
( cd build && pyinstaller --clean --noconfirm --distpath ../dist maverick.spec )
./dist/maverick version
./dist/maverick dashboard --help >/dev/null && echo "   dashboard subcommand ok"

echo ">> [5/6] wrap the compiled binary into Lightwork.app"
APP="dist/Lightwork.app"
rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"
cp dist/maverick "$APP/Contents/MacOS/maverick"
# icon, if the Tauri source icon exists
if [ -f apps/installer-desktop/src-tauri/icons/icon.icns ]; then
  cp apps/installer-desktop/src-tauri/icons/icon.icns "$APP/Contents/Resources/Lightwork.icns"
fi
cat > "$APP/Contents/Info.plist" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>CFBundleName</key><string>Lightwork</string>
  <key>CFBundleDisplayName</key><string>Lightwork</string>
  <key>CFBundleIdentifier</key><string>com.daybreaklabs.lightwork</string>
  <key>CFBundleVersion</key><string>0.1.7</string>
  <key>CFBundleShortVersionString</key><string>0.1.7</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleExecutable</key><string>Lightwork</string>
  <key>CFBundleIconFile</key><string>Lightwork</string>
  <key>LSMinimumSystemVersion</key><string>12.0</string>
  <key>NSHighResolutionCapable</key><true/>
</dict></plist>
PLIST
# Launcher: start the compiled dashboard, then open the URL it prints.
cat > "$APP/Contents/MacOS/Lightwork" <<'LAUNCH'
#!/bin/bash
DIR="$(cd "$(dirname "$0")" && pwd)"
LOG="${TMPDIR:-/tmp}/lightwork-dashboard.log"
# First run drops into the wizard if unconfigured; the dashboard serves the
# local web UI. Both are the SAME compiled binary — no Python on the machine.
if [ ! -f "$HOME/.maverick/config.toml" ]; then
  osascript -e 'display dialog "First run: open Terminal and run the maverick binary once to set up (maverick init). Then reopen Lightwork." buttons {"OK"} default button 1' >/dev/null 2>&1 || true
fi
"$DIR/maverick" dashboard >"$LOG" 2>&1 &
SRV=$!
for _ in $(seq 1 40); do
  URL="$(grep -oE 'http://127\.0\.0\.1:[0-9]+' "$LOG" | head -1 || true)"
  [ -n "$URL" ] && break; sleep 0.5
done
open "${URL:-http://127.0.0.1:8000}"
wait "$SRV"
LAUNCH
chmod +x "$APP/Contents/MacOS/Lightwork" "$APP/Contents/MacOS/maverick"

echo ">> [6/6] optional .dmg"
if command -v create-dmg >/dev/null 2>&1; then
  rm -f dist/Lightwork.dmg
  create-dmg --volname "Lightwork" --app-drop-link 480 170 \
    --window-size 720 380 dist/Lightwork.dmg "$APP" >/dev/null 2>&1 \
    && echo "   dist/Lightwork.dmg" || echo "   (create-dmg failed; the .app still works)"
else
  echo "   create-dmg not installed (brew install create-dmg) — skipping .dmg; the .app works."
fi

deactivate || true
echo
echo "DONE."
echo "  dist/Lightwork.app   double-click (first open: right-click -> Open -> Open)"
echo "  dist/maverick        the compiled CLI"
echo "Unsigned build — see docs/DISTRIBUTION.md for signing/notarization."
