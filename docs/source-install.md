# Install from source

Lightwork is installed from a clone of its Git repository. No prebuilt binary,
PyPI package or container image is currently published; this guide is the
supported installation path, and a fresh clone is on `main`, the version to
install until a release is published.

## Requirements

- Python 3.10, 3.11 or 3.12 (the versions tested in CI).
- Git.
- An account with a model provider, or a self-hosted model, to run tasks. You
  can install and open the dashboard without one.
- Optional: Docker or Podman. The setup wizard (`lightwork init`) switches
  file changes and program execution off when Docker is not running, and the
  dashboard's initial settings start with them off. If you skip both, the
  default `local` sandbox backend runs commands directly on this computer; read
  the [execution posture](https://github.com/Daybreak-AI-Labs/Lightwork/blob/main/SECURITY.md#execution-posture) before running
  untrusted goals.

The repository root is not a pip project, so `pip install -e .` at the root
does not work. `scripts/install_release_cohort.py` installs the eight Lightwork
packages from `release-cohort.toml` together, in one pip transaction,
against the pinned constraints in `requirements/ci.txt`.

## macOS and Linux

```bash
git clone https://github.com/Daybreak-AI-Labs/Lightwork
cd Lightwork
python3 -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip
python scripts/install_release_cohort.py --core-extra release-runtime,evidence-analysis
lightwork doctor
lightwork dashboard
```

## Windows (PowerShell)

```powershell
git clone https://github.com/Daybreak-AI-Labs/Lightwork
Set-Location Lightwork
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe scripts/install_release_cohort.py --core-extra release-runtime,evidence-analysis
.\.venv\Scripts\lightwork.exe doctor
.\.venv\Scripts\lightwork.exe dashboard
```

`py -3.12` selects Python 3.12; use `py -3.11` or `py -3.10` instead if that is
the supported version you have installed.

Calling the virtual environment's executables by path avoids changing the
PowerShell execution policy to run the activation script.

## Check the installation

Run these with the same environment's Python (on Windows, use the
`.\.venv\Scripts\` paths shown above):

```bash
python scripts/install_release_cohort.py --verify-only
python -m pip check
lightwork version
lightwork doctor
```

`--verify-only` runs `pip check` and then confirms that each of the eight
distributions is installed at the version in `release-cohort.toml` and that
its module imports. `lightwork doctor` runs a health check of the installation
and configuration, with remediation hints for anything it flags, such as a
missing provider key.

## Open the dashboard

`lightwork dashboard` serves the dashboard and its REST API at
<http://127.0.0.1:8765>. The dashboard guides first-owner sign-in and provider
setup. To bind any address other than loopback, set a bearer token first
(`--token` or `MAVERICK_DASHBOARD_TOKEN`); the command refuses to start without
one.

The command-line tool is installed under two names, `lightwork` and
`maverick`, which run the same program. `lightwork init` runs the setup wizard
in the terminal instead of the dashboard.

## Next steps

- [Getting started](getting-started.md): first-owner setup and a first task.
- [Configuration](configuration.md): providers, models, budgets and channels.
- [Deployment](deployment.md): Docker, VPS and server installations.
