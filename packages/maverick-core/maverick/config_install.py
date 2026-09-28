"""Headless provisioning: validate a settings file and install it as config.toml.

The work behind ``maverick init --from-file``, shared with the dashboard's
setup page so an administrator can install a prepared settings file without a
terminal. The file is parsed (a syntax error refuses the install), linted
(unknown sections or type problems are reported but never block, since an
operator may use newer keys than this build knows) and published privately.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

try:
    import tomllib
except ModuleNotFoundError:  # 3.10
    import tomli as tomllib  # type: ignore[no-redef]


class ConfigInstallError(ValueError):
    """The settings file is not valid TOML (the parser's reason is the text)."""


def parse_config_bytes(data: bytes) -> dict[str, Any]:
    try:
        return tomllib.loads(data.decode("utf-8"))
    except Exception as exc:  # noqa: BLE001 -- the parser's reason is kept as the text
        raise ConfigInstallError(str(exc)) from exc


def config_findings(cfg: dict[str, Any]) -> list[Any]:
    """Lint findings for a parsed settings file (best effort, never raises)."""
    try:
        from .config_lint import lint_config
        return list(lint_config(cfg))
    except Exception:  # noqa: BLE001 -- linting never blocks provisioning
        return []


def install_config_bytes(data: bytes, dst: Path | None = None, *,
                         backup: bool = False) -> dict[str, Any]:
    """Publish ``data`` as the active config.toml (0600).

    Returns ``{"path": dst, "unchanged": bool, "backup": Path | None}``. The
    same logical file (line endings aside) is a true no-op that only repairs
    the file's privacy. With ``backup`` an existing, different file is first
    copied to a dated ``config.toml.bak-YYYYmmdd-HHMMSS`` beside it (the same
    naming ``maverick migrate`` uses).
    """
    from .config import config_path
    from .file_lock import atomic_write_bytes, ensure_private_directory, ensure_private_file

    dst = config_path() if dst is None else dst
    # A provisioned config may contain inline provider keys. Tighten the
    # directory with a protected Windows DACL and publish the bytes from a
    # create-time protected temp; POSIX mode bits passed to os.open do not
    # provide the equivalent Windows custody boundary.
    ensure_private_directory(dst.parent)
    try:
        installed = dst.read_bytes() if dst.is_file() else b""
        unchanged = (
            dst.is_file()
            and installed.replace(b"\r\n", b"\n")
            == data.replace(b"\r\n", b"\n")
        )
    except OSError:
        unchanged = False
    if unchanged:
        # A retried deployment should be a true no-op: do not rotate the inode,
        # mtime, or watcher state when the same logical TOML is installed,
        # including when a cross-platform checkout changed line endings.
        # Still repair/verify custody in case an older release left wide perms.
        ensure_private_file(dst, 0o600)
        return {"path": dst, "unchanged": True, "backup": None}
    saved = None
    if backup and dst.is_file():
        import shutil
        import time

        saved = dst.with_name(f"{dst.name}.bak-{time.strftime('%Y%m%d-%H%M%S')}")
        shutil.copy2(dst, saved)
        ensure_private_file(saved, 0o600)
    atomic_write_bytes(dst, data, mode=0o600)
    return {"path": dst, "unchanged": False, "backup": saved}


__all__ = ["ConfigInstallError", "parse_config_bytes", "config_findings", "install_config_bytes"]
