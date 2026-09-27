"""The code vault: a company's repositories held inside Lightwork, sealed at rest.

Each registered repository is one git bundle (every branch and tag), sealed with
the tenant's at-rest key and bound to ``codebase/<name>`` when encryption is on.
Plaintext exists only in a private working copy for as long as a task needs it
(:func:`checkout`), and every run over it starts labelled ``code:<name>`` so
information-flow control keeps the code inside (:mod:`maverick.ifc`).

Git runs host-side with the hardened environment from :mod:`maverick.git_safety`
(no user/system config, no fsmonitor, no inherited secrets). A remote credential
is never stored: the registry names an environment variable, read at use time
and passed through ``GIT_ASKPASS``, never argv or a persisted URL.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import stat
import subprocess
import tempfile
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from pathlib import Path
from urllib.parse import urlsplit

from ..file_lock import atomic_write_bytes, atomic_write_text, cross_process_lock
from ..git_safety import git_env_without_user_config, git_metadata_may_execute_filters
from ..paths import data_dir

NAME = re.compile(r"[a-z0-9][a-z0-9._-]{0,63}\Z")
PROTECT = ("audit", "approve", "block")
_GIT_TIMEOUT = 600


class VaultError(RuntimeError):
    """A vault operation could not be completed safely."""


@dataclass(frozen=True)
class Repo:
    name: str
    source: str
    default_branch: str
    test_command: str | None = None
    protect: str = "block"
    token_env: str | None = None
    head: str = ""
    updated_at: float = 0.0


def enabled() -> bool:
    from ..config import get_codebase
    return get_codebase()["enable"]


def _root(*parts: str) -> Path:
    return data_dir("codebase", *parts)


def _context(name: str) -> str:
    return f"codebase/{name}"


def _seal(data: bytes, context: str) -> bytes:
    from ..crypto_at_rest import at_rest_enabled, seal_bound
    return seal_bound(data, context) if at_rest_enabled() else data


def _unseal(data: bytes, context: str) -> bytes:
    from ..crypto_at_rest import is_bound, unseal_bound
    return unseal_bound(data, context) if is_bound(data) else data


def _git(args: list[str], cwd: Path | None = None, *, token_env: str | None = None,
         timeout: int = _GIT_TIMEOUT) -> str:
    env = git_env_without_user_config()
    askpass = None
    try:
        if token_env:
            token = os.environ.get(token_env)
            if not token:
                raise VaultError(f"credential variable {token_env} is not set")
            fd, askpass = tempfile.mkstemp(prefix="lw-askpass-")
            with os.fdopen(fd, "w") as fh:
                fh.write('#!/bin/sh\nprintf "%s\\n" "$LW_GIT_TOKEN"\n')
            os.chmod(askpass, stat.S_IRWXU)
            env.update({"GIT_ASKPASS": askpass, "LW_GIT_TOKEN": token})
        done = subprocess.run(["git", *args], cwd=cwd, env=env, capture_output=True,
                              text=True, timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise VaultError(f"git {args[0]} failed: {type(exc).__name__}") from exc
    finally:
        if askpass:
            os.unlink(askpass)
    if done.returncode != 0:
        from ..secrets import scrub
        raise VaultError(scrub(f"git {args[0]} failed: {done.stderr.strip()[:500]}"))
    return done.stdout


def _check_source(source: str) -> str:
    parts = urlsplit(source)
    if parts.scheme in ("http", "https", "ssh", "git"):
        if parts.username or parts.password:
            raise VaultError("put credentials in token_env, not in the source URL")
        return source
    path = Path(source).expanduser()
    if not path.exists():
        raise VaultError(f"source {source!r} is neither a URL nor an existing path")
    return str(path.resolve())


# -- registry ----------------------------------------------------------------------

def _registry_path() -> Path:
    return _root("registry.json")


def _load() -> dict[str, dict]:
    try:
        return json.loads(_registry_path().read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}


def _save(registry: dict[str, dict]) -> None:
    atomic_write_text(_registry_path(), json.dumps(registry, indent=2, sort_keys=True))


def list_repos() -> list[Repo]:
    return [Repo(**v) for _, v in sorted(_load().items())]


def get(name: str) -> Repo:
    try:
        return Repo(**_load()[name])
    except KeyError:
        raise VaultError(f"no repository named {name!r}") from None


def _bundle_path(name: str) -> Path:
    return _root(name, "vault.bundle")


def _store_bundle(name: str, repo_dir: Path) -> str:
    """Seal every ref of ``repo_dir`` into the vault; returns the default head."""
    with tempfile.TemporaryDirectory(prefix="lw-bundle-") as tmp:
        out = Path(tmp) / "b.bundle"
        _git(["bundle", "create", str(out), "--all"], cwd=repo_dir)
        atomic_write_bytes(_bundle_path(name), _seal(out.read_bytes(), _context(name)))
    return _git(["rev-parse", "HEAD"], cwd=repo_dir).strip()


def register(name: str, source: str, *, default_branch: str | None = None,
             test_command: str | None = None, protect: str = "block",
             token_env: str | None = None) -> Repo:
    """Import a repository (every branch and tag) into the vault."""
    if not NAME.fullmatch(name):
        raise VaultError("name: lowercase letters, digits, '.', '_' and '-' (max 64)")
    if protect not in PROTECT:
        raise VaultError(f"protect must be one of {PROTECT}")
    source = _check_source(source)
    with cross_process_lock(_registry_path(), strict=True):
        if name in _load():
            raise VaultError(f"{name!r} is already registered")
        with tempfile.TemporaryDirectory(prefix="lw-import-") as tmp:
            work = Path(tmp) / "work"
            _git(["init", "-q", str(work)])
            _git(["fetch", "-q", source, "+refs/heads/*:refs/heads/*", "+refs/tags/*:refs/tags/*"],
                 cwd=work, token_env=token_env)
            branch = default_branch or _remote_default(source, token_env) or "main"
            _git(["checkout", "-q", branch], cwd=work)
            head = _store_bundle(name, work)
        repo = Repo(name, source, branch, test_command, protect, token_env, head, time.time())
        registry = _load()
        registry[name] = asdict(repo)
        _save(registry)
    return repo


def _remote_default(source: str, token_env: str | None) -> str | None:
    try:
        out = _git(["ls-remote", "--symref", source, "HEAD"], token_env=token_env)
    except VaultError:
        return None
    m = re.search(r"ref: refs/heads/(\S+)\s+HEAD", out)
    return m.group(1) if m else None


def update_registry(name: str, **changes) -> Repo:
    with cross_process_lock(_registry_path(), strict=True):
        registry = _load()
        if name not in registry:
            raise VaultError(f"no repository named {name!r}")
        registry[name].update(changes)
        _save(registry)
        return Repo(**registry[name])


@contextmanager
def checkout(name: str) -> Iterator[Path]:
    """A private plaintext working copy on the default branch, with every vault
    ref as a local branch. Removed on exit; nothing else holds plaintext."""
    repo = get(name)
    tmp = Path(tempfile.mkdtemp(prefix="lw-code-"))
    os.chmod(tmp, stat.S_IRWXU)
    try:
        bundle = tmp / "in.bundle"
        bundle.write_bytes(_unseal(_bundle_path(name).read_bytes(), _context(name)))
        work = tmp / name
        _git(["init", "-q", str(work)])
        _git(["fetch", "-q", str(bundle), "+refs/*:refs/*"], cwd=work)
        bundle.unlink()
        _git(["checkout", "-q", repo.default_branch], cwd=work)
        yield work
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def commit_back(name: str, work: Path) -> str:
    """Seal the working copy's refs back into the vault (callers hold the lock)."""
    if git_metadata_may_execute_filters(work):
        raise VaultError("working copy carries git filter/hook metadata; refusing to store it")
    head = _store_bundle(name, work)
    update_registry(name, head=head, updated_at=time.time())
    return head


def sync(name: str) -> Repo:
    """Fetch new commits from the source into the vault. Lightwork branches that
    the source does not have are kept."""
    repo = get(name)
    with cross_process_lock(_bundle_path(name), strict=True), checkout(name) as work:
        _git(["checkout", "-q", "--detach"], cwd=work)
        _git(["fetch", "-q", repo.source, "+refs/heads/*:refs/heads/*", "+refs/tags/*:refs/tags/*"],
             cwd=work, token_env=repo.token_env)
        _git(["checkout", "-q", repo.default_branch], cwd=work)
        commit_back(name, work)
    return get(name)


def export(name: str, dest: Path) -> Path:
    """A plaintext clone for the owner, outside the vault's protection."""
    dest = Path(dest)
    if dest.exists():
        raise VaultError(f"{dest} already exists")
    with checkout(name) as work:
        shutil.copytree(work, dest, symlinks=True)
    return dest


__all__ = ["PROTECT", "Repo", "VaultError", "checkout", "commit_back", "enabled", "export",
           "get", "list_repos", "register", "sync", "update_registry"]
