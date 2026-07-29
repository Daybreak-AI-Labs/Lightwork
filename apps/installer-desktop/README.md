# Lightwork authenticated source bootstrap

Tauri GUI bootstrap for authorized repository users who prefer not to drive
the source install from a terminal. A double-click app with one button: it runs
the same bootstrap the CLI
one-liners use (`deploy/desktop/install.{ps1,sh}`) behind a live
progress screen.

This is not a self-contained native installer. It clones a pinned commit from
the private Lightwork repository, so network access and valid GitHub
authorization are required, and the installed Python source remains readable.

## How it works

```
  +------------------+   invoke('install')    +-------------------+
  | Svelte UI (TS)   | ────────────────────► | Rust shell (lib)  |
  |  Install button  | ◄───── events ──────── |  spawns bootstrap |
  +------------------+  install-log / -done    +---------+---------+
                                                          │
                                                          │ stdout/stderr
                                                          ▼
                                          deploy/desktop/install.{ps1,sh}
                                          (MAVERICK_NO_WIZARD=1):
                                          installs Python + git if needed,
                                          then pipx-installs Lightwork
```

The Rust shell owns the window and runs the bootstrap as a subprocess,
streaming each line to the UI as a Tauri event. **No Python is required
on the machine first** — the bootstrap installs it. This is why we shell
out to the existing scripts instead of embedding a Python runtime: those
scripts are already tested and are the single source of truth for "how
to install" (winget/brew/apt, PATH, pipx, PEP 668). When the bootstrap
finishes, the UI tells the user to run `maverick init` to configure.

## Status

Builds **unsigned engineering workflow artifacts** on macOS / Windows / Linux
via `.github/workflows/desktop.yml`. These artifacts are not attached to
product releases. A consumer-grade installer still requires an embedded or
verified compiled payload, a reliable signed-asset flow, platform code signing,
and real-machine install tests.

> **Needs real-machine testing.** CI confirms the bundle *builds*, but
> the actual install run (winget/brew, network, the GUI driving the
> bootstrap) has to be exercised on real Windows/macOS/Linux. Treat the
> first build as a release candidate to smoke-test, not a shipped
> artifact.

## Local development

```bash
cd apps/installer-desktop
pnpm install
pnpm tauri dev
```

Hot-reloads the Svelte frontend. Clicking **Install from authorized source**
runs the bundled
bootstrap, so test in a throwaway VM/container unless you actually want
Lightwork installed on your dev box. The desktop app pins the installed
source to the git commit captured at build time; set
`MAVERICK_INSTALL_REF=<lowercase-full-40-character-commit-sha>` while building
only when you need to override that ref intentionally. Mutable tags and
branches are rejected by the build script.

## Producing engineering source-bootstrap bundles

```bash
pnpm tauri build
```

The build script records `git rev-parse HEAD` as the install ref, so the
signed GUI installer does not fetch and execute a mutable `main` branch
bootstrap at install time.

Engineering outputs per platform:
- macOS: `.app` + `.dmg` (sign + notarize for distribution)
- Windows: `.msi` + `.exe` (NSIS)
- Linux: `.AppImage` + `.deb`

## Why Tauri vs Electron

| | Tauri | Electron |
|---|---|---|
| Bundle size | ~5 MB | ~150 MB |
| Memory at idle | ~50 MB | ~250 MB |
| Native webview | system (WebKit / WebView2 / WebKitGTK) | bundled Chromium |
| Rust shell | yes (security, smaller attack surface) | no |

Lightwork already needs Rust in the toolchain for the agent-shield
performance core, so adding Tauri is essentially free.
