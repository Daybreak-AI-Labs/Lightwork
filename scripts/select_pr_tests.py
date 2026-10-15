#!/usr/bin/env python3
"""Choose the pytest paths a pull request needs from the files it changes.

The merge queue and pushes to ``main`` always run the complete suite (ci.yml:
``test`` on the queue's commit, plus ``test-window`` and ``posture``), so this
decides only what the floor-interpreter ``test`` legs run on a ``pull_request``
event. Selecting too little costs a later failure in the queue, where the whole
suite runs anyway; selecting too much costs runner minutes. The rules err
toward running everything:

* A change under the kernel (``packages/maverick-core/maverick``), the shared
  test bootstrap (any ``conftest.py``, ``test_support/``, ``pyproject.toml``,
  ``requirements/``), ``.github/``, ``scripts/`` or ``benchmarks/`` selects the
  full suite: every package imports the kernel, and the tests read the
  workflows and scripts.
* A change inside another package or Python app selects that package's own
  tests plus every test file anywhere that names its import package.
* A changed test file is always selected.
* Any other file (docs, deploy, READMEs, SDKs, native apps) selects every test
  file whose text names the file's repository path, its basename, or a quoted
  component of its directory path, because the claims and deployment tests
  reference such files by path (``_read("README.md")``,
  ``REPO_ROOT / "deploy" / "helm"``).
* When nothing matches, the mode is ``none`` and the legs report success
  without running pytest.

Usage::

    python scripts/select_pr_tests.py --git BASE HEAD --github-output "$GITHUB_OUTPUT"
    python scripts/select_pr_tests.py --files-from changed.txt --output selection.json
    git diff --name-only BASE HEAD | python scripts/select_pr_tests.py --stdin

``--git`` diffs HEAD against its merge base with BASE (what the pull request
itself changes, even after the base branch moved) and selects the full suite
when git cannot answer, so a selection failure never skips tests. The output
JSON is ``{"mode": "full" | "paths" | "none", "paths": [...], "reasons":
[...]}``; ``paths`` are repository-relative pytest targets. ``--github-output``
appends ``mode=`` and ``paths=`` (the JSON array) lines for a workflow step.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path, PurePosixPath

REPO_ROOT = Path(__file__).resolve().parents[1]

#: Any change here means the whole suite: shared fixtures, the kernel every
#: package imports, the workflows and scripts the tests read.
FULL_SUITE_PREFIXES = (
    "packages/maverick-core/maverick/",
    "test_support/",
    "requirements/",
    ".github/",
    "scripts/",
    "benchmarks/",
)
FULL_SUITE_FILES = frozenset({"conftest.py", "pyproject.toml", "release-cohort.toml"})

#: Import package name per distribution directory.
PACKAGE_MODULES = {
    "packages/maverick-shield": "maverick_shield",
    "packages/maverick-channels": "maverick_channels",
    "packages/maverick-evolve": "maverick_evolve",
    "packages/maverick-dashboard": "maverick_dashboard",
    "packages/maverick-mcp": "maverick_mcp",
    "packages/maverick-knowledge": "maverick_knowledge",
    "apps/installer-cli": "maverick_installer",
}

_TEST_FILE = re.compile(r"(^|/)test_[^/]+\.py$")


def test_files(root: Path = REPO_ROOT) -> list[Path]:
    """Every pytest file the root pyproject collects (packages, apps, benchmarks)."""
    found: list[Path] = []
    for base in ("packages", "apps", "benchmarks"):
        top = root / base
        if top.is_dir():
            found.extend(p for p in top.rglob("test_*.py") if ".venv" not in p.parts)
    return sorted(found)


def _rel(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()


def _is_test_file(rel: str) -> bool:
    return bool(_TEST_FILE.search(rel)) and rel.startswith(("packages/", "apps/", "benchmarks/"))


def _full_reason(rel: str) -> str | None:
    if rel in FULL_SUITE_FILES:
        return f"{rel} is shared by the whole suite"
    if PurePosixPath(rel).name == "conftest.py":
        return f"{rel} is a shared fixture file"
    for prefix in FULL_SUITE_PREFIXES:
        if rel.startswith(prefix):
            return f"{rel} is under {prefix}"
    return None


def _package_of(rel: str) -> tuple[str, str] | None:
    for directory, module in PACKAGE_MODULES.items():
        if rel.startswith(directory + "/"):
            return directory, module
    return None


def _reference_candidates(rel: str) -> set[str]:
    """Literals a test might use to name ``rel``: the path, its basename, and
    quoted directory components (``"deploy"``, ``'terraform'``)."""
    path = PurePosixPath(rel)
    candidates = {rel, path.name}
    for part in path.parts[:-1]:
        candidates.add(f'"{part}"')
        candidates.add(f"'{part}'")
    # The path with any leading directory stripped, as docs link to each other.
    for i in range(1, len(path.parts) - 1):
        candidates.add("/".join(path.parts[i:]))
    return candidates


_GIT_REF = re.compile(r"[0-9A-Za-z][0-9A-Za-z._/-]*")


def changed_files_from_git(base: str, head: str, root: Path = REPO_ROOT) -> tuple[list[str] | None, str]:
    """The files ``head`` changes since its merge base with ``base``.

    Returns ``(paths, merge_base)``, or ``(None, reason)`` when git cannot
    answer (unknown ref, no common history, no git); the caller then selects
    the full suite. ``--no-renames`` lists both names of a moved file so the
    tests naming either are selected.
    """
    for ref in (base, head):
        if not _GIT_REF.fullmatch(ref):
            return None, f"refusing to pass {ref!r} to git"
    git = ["git", "-C", str(root)]
    try:
        merge_base = subprocess.run(
            [*git, "merge-base", base, head], check=True, capture_output=True, text=True
        ).stdout.strip()
        diff = subprocess.run(
            [*git, "diff", "--name-only", "--no-renames", merge_base, head],
            check=True, capture_output=True, text=True,
        ).stdout
    except (OSError, subprocess.CalledProcessError) as exc:
        detail = (getattr(exc, "stderr", "") or str(exc)).strip()
        return None, f"git could not diff {base}...{head}: {detail}"
    return diff.splitlines(), merge_base


def select(changed: list[str], root: Path = REPO_ROOT) -> dict:
    """Return ``{"mode", "paths", "reasons"}`` for the changed repository paths."""
    changed = sorted({c.strip().replace("\\", "/") for c in changed if c.strip()})
    reasons: list[str] = []
    for rel in changed:
        reason = _full_reason(rel)
        if reason:
            return {"mode": "full", "paths": [], "reasons": [reason]}

    all_tests = test_files(root)
    texts = {t: t.read_text(encoding="utf-8", errors="ignore") for t in all_tests}
    selected: set[Path] = set()

    for rel in changed:
        if _is_test_file(rel):
            path = root / rel
            if path.exists():
                selected.add(path)
                reasons.append(f"{rel} changed")
            continue
        package = _package_of(rel)
        if package and rel.endswith(".py"):
            directory, module = package
            own = root / directory / "tests"
            if own.is_dir():
                selected.update(t for t in all_tests if own in t.parents)
            importers = [t for t in all_tests if module in texts[t] and own not in t.parents]
            selected.update(importers)
            reasons.append(f"{rel}: {directory}/tests plus {len(importers)} files naming {module}")
            continue
        candidates = _reference_candidates(rel)
        hits = [t for t in all_tests if any(c in texts[t] for c in candidates)]
        selected.update(hits)
        reasons.append(f"{rel}: {len(hits)} files name it")

    if not selected:
        return {"mode": "none", "paths": [], "reasons": reasons or ["no files changed"]}
    return {
        "mode": "paths",
        "paths": sorted(_rel(t, root) for t in selected),
        "reasons": reasons,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--files-from", metavar="FILE", help="newline-separated changed paths")
    source.add_argument("--stdin", action="store_true", help="read changed paths from stdin")
    source.add_argument("--files", nargs="+", metavar="PATH", help="changed paths")
    source.add_argument(
        "--git", nargs=2, metavar=("BASE", "HEAD"),
        help="diff HEAD against its merge base with BASE; full suite if git cannot",
    )
    parser.add_argument("--output", metavar="FILE", help="write the selection JSON here")
    parser.add_argument(
        "--github-output", metavar="FILE",
        help="append mode= and paths= lines for a workflow step ($GITHUB_OUTPUT)",
    )
    parser.add_argument(
        "--root", type=Path, default=REPO_ROOT, help="repository root (default: this checkout)"
    )
    args = parser.parse_args(argv)

    result: dict | None = None
    if args.files_from:
        changed = Path(args.files_from).read_text(encoding="utf-8").splitlines()
    elif args.stdin:
        changed = sys.stdin.read().splitlines()
    elif args.git:
        changed, note = changed_files_from_git(*args.git, root=args.root)
        if changed is None:
            result = {"mode": "full", "paths": [], "reasons": [note]}
        else:
            print(f"{len(changed)} files changed since merge base {note}")
    else:
        changed = list(args.files)

    if result is None:
        result = select(changed, args.root)
    if args.output:
        Path(args.output).write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    if args.github_output:
        with open(args.github_output, "a", encoding="utf-8") as handle:
            handle.write(f"mode={result['mode']}\npaths={json.dumps(result['paths'])}\n")
    print(f"mode={result['mode']} paths={len(result['paths'])}")
    for reason in result["reasons"][:20]:
        print(f"  {reason}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
