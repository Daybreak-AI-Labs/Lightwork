"""Host-side git hardening shared by every module that runs `git` against a
workdir the agent (or an untrusted clone) can write to.

Git honours repo-local config: a `core.fsmonitor` hook, a `[filter "x"]`
smudge/clean command, or a config `include`/`includeIf` can make git execute
an attacker-controlled program during `diff`, `add`, `reset --hard`, or
`worktree add` -- independent of `--no-ext-diff`/`--no-textconv`, which only
disable two of those surfaces (the external-diff driver and textconv), not
fsmonitor or filter commands. A workdir the agent can write to (via
`write_file`/SEARCH-REPLACE) or that came from cloning an untrusted repo
(`github_app.process_issue`) is exactly such a workdir.

Every host-side git call against a workdir in that position must:
  1. Refuse when `git_metadata_may_execute_filters` finds config/attributes
     that could define one of these hooks (fail open: skip the git call
     rather than let it run).
  2. Run under `git_env_without_user_config` so global/system config, the
     calling process's environment (including provider API keys and other
     secrets), and repo-local fsmonitor are never in scope even when (1) is
     a false negative.

This was previously implemented once, inside `orchestrator.py`'s best-of-N
candidate path, and nowhere else -- `edit_format.render_diff`,
`reviewer.get_diff`, and `coding_mode`'s candidate evaluation each ran `git`
on the host with none of these protections. See PLATFORM_IMPROVEMENT_AUDIT.md
finding W2-1 (host-git RCE) / W2-2 (secret leakage into the child env).
"""
from __future__ import annotations

import os
from pathlib import Path


def git_env_without_user_config() -> dict[str, str]:
    """Return a scrubbed environment for host-side git calls against a
    workdir that isn't a fully-isolated ephemeral checkout.

    Never let global/system config, the calling process's environment, or
    repo-local fsmonitor settings add executable helpers to these host-side
    git invocations. Local repo config is still checked separately (see
    `git_metadata_may_execute_filters`) before any git command runs.
    """
    env = {
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_SYSTEM": os.devnull,
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_PAGER": "",
        "HOME": os.devnull,
        "PATH": os.environ.get("PATH", ""),
        # Override repo-local config entries that can otherwise make a
        # read-only host-side git probe execute workspace-controlled programs.
        "GIT_CONFIG_COUNT": "2",
        "GIT_CONFIG_KEY_0": "core.fsmonitor",
        "GIT_CONFIG_VALUE_0": "false",
        "GIT_CONFIG_KEY_1": "core.untrackedCache",
        "GIT_CONFIG_VALUE_1": "false",
    }
    for key in ("LANG", "LC_ALL", "LC_CTYPE", "TMPDIR"):
        if key in os.environ:
            env[key] = os.environ[key]
    return env


def git_metadata_may_execute_filters(wd: Path) -> bool:
    """Conservatively detect local git metadata that can make git run commands.

    A coding attempt can write the mounted workdir, including
    `.gitattributes`, `.git/info/attributes`, and local config. If those
    mention filters, config includes, or fsmonitor hooks, host-side
    `git diff`/`add`/`reset`/`worktree` may execute attacker-controlled
    commands. Fail open by skipping the git call instead.
    """
    try:
        # Git executes a command via filter sections (modern `[filter "name"]`
        # or deprecated-but-supported `[filter.name]` syntax), attributes files
        # selected from config, or config includes that could pull either in.
        # The bare word "filter" is NOT sufficient and caused a false positive
        # on every partial clone: `git clone --filter=blob:none` writes a
        # benign `partialclonefilter = blob:none` into .git/config that never
        # runs a command, so matching "filter" would skip every git call on
        # the entire staged corpus. Match the executable surfaces precisely.
        config_files = [wd / ".git" / "config"]
        config_needles = (
            '[filter "',
            "[filter.",
            "[include",
            "include.path",
            "includeif",
            "fsmonitor",
            "attributesfile",
        )
        for path in config_files:
            try:
                if path.is_file():
                    text = path.read_text(encoding="utf-8", errors="ignore").lower()
                    if any(needle in text for needle in config_needles):
                        return True
            except OSError:
                return True
        # An attributes file assigning a `filter` attribute references a named
        # filter whose clean/smudge command lives in config -- treat that
        # reference as unsafe. Attributes files never contain a
        # `partialclonefilter` value, so there is no blobless-clone false
        # positive here.
        attr_files = [wd / ".git" / "info" / "attributes"]
        attr_files.extend(wd.glob("**/.gitattributes"))
        for path in attr_files:
            try:
                if path.is_file():
                    text = path.read_text(encoding="utf-8", errors="ignore").lower()
                    if "filter=" in text or "filter " in text:
                        return True
            except OSError:
                return True
    except Exception:
        return True
    return False
