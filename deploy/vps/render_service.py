"""Render a VPS unit from the installed user's actual workspace configuration."""
from __future__ import annotations

import argparse
import os
import re
import tempfile
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10
    import tomli as tomllib


def _unit_value(value: str | Path) -> str:
    text = str(value)
    if any(character in text for character in "\r\n\0"):
        raise ValueError("systemd paths cannot contain newlines or NUL")
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"').replace("%", "%%") + '"'


def render_service(template: str, *, user: str, home: Path) -> str:
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.-]*\$?", user):
        raise ValueError("invalid service account name")
    home = home.resolve()
    state = home / ".maverick"
    config_file = state / "config.toml"
    config = tomllib.loads(config_file.read_text(encoding="utf-8"))
    configured = config.get("sandbox", {}).get("workdir")
    if not isinstance(configured, str) or not configured.strip():
        raise ValueError("setup must save sandbox.workdir before installing the service")
    if configured == "~":
        configured = str(home)
    elif configured.startswith("~/"):
        configured = str(home / configured[2:])
    workspace = Path(configured)
    if not workspace.is_absolute():
        raise ValueError("sandbox.workdir must be an absolute path for the VPS service")
    workspace = workspace.resolve()
    workspace.mkdir(parents=True, exist_ok=True)
    # This program is invoked as the service account, after the wizard. Test
    # the actual mount/ACL before root publishes or enables the unit.
    with tempfile.TemporaryFile(dir=workspace, prefix=".lightwork-service-") as probe:
        probe.write(b"workspace-ready\n")
        probe.flush()
        os.fsync(probe.fileno())
    values = {
        "@USER@": user,
        "@WORKDIR@": _unit_value(workspace),
        "@STATE@": _unit_value(state),
        "@ENV_FILE@": _unit_value(state / ".env"),
        "@CONFIG_ENV@": _unit_value("MAVERICK_CONFIG=" + str(config_file)),
        "@EXECUTABLE@": _unit_value(home / ".local/bin/maverick"),
    }
    for marker, value in values.items():
        template = template.replace(marker, value)
    return template


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--user", required=True)
    parser.add_argument("--home", required=True, type=Path)
    parser.add_argument("--template", type=Path, default=Path(__file__).with_name("maverick.service"))
    args = parser.parse_args()
    print(render_service(args.template.read_text(encoding="utf-8"), user=args.user, home=args.home), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
