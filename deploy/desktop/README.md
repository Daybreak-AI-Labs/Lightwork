# Desktop deployment

## Terminal install (recommended)

Use the published package instead of piping a mutable remote script into a shell:

```bash
pipx install 'lightwork-agent[installer]'
maverick init
```

## Source bootstrap (advanced)

The desktop bootstrap scripts are retained for controlled source installs. Download `install.sh` or `install.ps1` from a commit or release you trust, verify it, then set `MAVERICK_REF` to a full 40-character commit SHA before running. Mutable branch/tag refs are rejected unless `MAVERICK_ALLOW_UNPINNED=1` (`$env:MAVERICK_ALLOW_UNPINNED = "1"` on Windows) is set explicitly for trusted local testing.

## Native bundles (planned)

The long-term plan ships native bundles per platform so users don't
need Python installed at all:

| Platform | Tool | Format | Auto-update |
|---|---|---|---|
| macOS | Tauri | Notarized DMG, signed `.app` | Sparkle via Tauri updater |
| Windows | Tauri | Signed MSIX | Tauri updater |
| Linux | Tauri | AppImage + `.deb` + `.rpm` | AppImageUpdate |

The Tauri shell ships an embedded Python runtime via
[PyOxidizer](https://pyoxidizer.readthedocs.io/) or
[python-build-standalone](https://github.com/indygreg/python-build-standalone),
and the wizard runs as a Svelte UI talking to a sidecar Python process.

See [`apps/installer-desktop/`](../../apps/installer-desktop/README.md)
for the scaffold and milestones.
