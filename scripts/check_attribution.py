#!/usr/bin/env python3
"""Refuse AI-attribution trailers and footers in commit messages and PR text.

House rule (CLAUDE.md, "No AI attribution in repo artifacts"): nothing that
lands in this repository or on GitHub carries a generator footer, an AI
co-author trailer, or an assistant session link. The rule kept being broken
by the MERGE step rather than by authors: a squash merge prefills the merge
commit from the branch's commit messages, so one trailer on one branch commit
lands on ``main`` inside the squash. This gate runs at each place such a line
can still be refused:

* ``pull_request`` -- every commit in the PR range plus the PR title and body
  (the squash dialog is prefilled from exactly these).
* ``merge_group`` -- the merge-queue commit: the message that will become
  ``main``'s history, including anything typed into the merge dialog.
* ``push`` to ``main`` -- detective. If something slipped past, ``main`` goes
  red instead of quietly accumulating trailers.

Usage::

    python scripts/check_attribution.py --commits BASE..HEAD [--text FILE ...]
    python scripts/check_attribution.py --text pr-body.md
    git log -1 --format=%B | python scripts/check_attribution.py --stdin

Exit 0 when clean, 1 on findings, 2 when nothing was inspected: a range that
resolves to zero commits is a misconfigured gate, not a pass.
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

#: Assistants whose name in a trailer or footer marks it as AI attribution.
#: Human co-authors and non-assistant bots (dependabot, renovate) pass.
_ASSISTANTS = (
    r"(?:claude|anthropic|copilot|chatgpt|openai|codex|gemini|cursor|devin|"
    r"aider|windsurf)"
)

RULES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("AI co-author trailer",
     re.compile(rf"^\s*co-authored-by:.*\b{_ASSISTANTS}\b", re.IGNORECASE)),
    ("assistant session trailer",
     re.compile(r"^\s*claude-session:", re.IGNORECASE)),
    ("assistant session link",
     re.compile(r"claude\.ai/code/session", re.IGNORECASE)),
    ("generator footer",
     re.compile(rf"generated (?:with|by)\b.*\b{_ASSISTANTS}\b", re.IGNORECASE)),
    ("generator footer",
     re.compile(r"\U0001F916\s*generated", re.IGNORECASE)),
    ("Anthropic no-reply trailer",
     re.compile(r"^\s*[a-z-]+-by:.*noreply@anthropic\.com", re.IGNORECASE)),
)


def findings(text: str, source: str) -> list[str]:
    """Every line of ``text`` that carries AI attribution, labelled by rule."""
    out: list[str] = []
    for number, line in enumerate(text.splitlines(), 1):
        for rule, pattern in RULES:
            if pattern.search(line):
                out.append(f"{source}:{number}: {rule}: {line.strip()}")
                break
    return out


def commit_messages(rev_range: str, repo: Path | None = None) -> list[tuple[str, str]]:
    """(sha, full message) for every commit in ``rev_range``, newest first."""
    proc = subprocess.run(
        ["git", "log", "-z", "--format=%H%n%B", rev_range],
        cwd=repo, capture_output=True, text=True, check=True,
    )
    out: list[tuple[str, str]] = []
    for chunk in proc.stdout.split("\0"):
        if not chunk.strip():
            continue
        sha, _, body = chunk.partition("\n")
        out.append((sha, body))
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--commits", metavar="RANGE",
                    help="git revision range whose commit messages are checked "
                         "(e.g. BASE..HEAD)")
    ap.add_argument("--text", action="append", default=[], type=Path, metavar="FILE",
                    help="a file of prose to check (PR title/body); repeatable")
    ap.add_argument("--stdin", action="store_true",
                    help="also check a message read from standard input")
    ap.add_argument("--repo", type=Path, default=None,
                    help="repository to read commits from (default: cwd)")
    args = ap.parse_args(argv)

    inspected = 0
    problems: list[str] = []
    if args.commits:
        try:
            messages = commit_messages(args.commits, args.repo)
        except subprocess.CalledProcessError as exc:
            print(f"check-attribution: ERROR -- git log {args.commits!r} failed: "
                  f"{exc.stderr.strip()}", file=sys.stderr)
            return 2
        for sha, body in messages:
            inspected += 1
            problems.extend(findings(body, f"commit {sha[:12]}"))
    for path in args.text:
        inspected += 1
        problems.extend(findings(path.read_text(encoding="utf-8", errors="replace"),
                                 str(path)))
    if args.stdin:
        inspected += 1
        problems.extend(findings(sys.stdin.read(), "stdin"))

    if inspected == 0:
        print("check-attribution: ERROR -- inspected nothing (empty commit range and "
              "no text); refusing to pass vacuously", file=sys.stderr)
        return 2
    if problems:
        print("check-attribution: AI attribution is not allowed in commit messages "
              "or PR text (CLAUDE.md, 'No AI attribution in repo artifacts'):",
              file=sys.stderr)
        for problem in problems:
            print(f"  {problem}", file=sys.stderr)
        print("\nDrop the trailer/footer and rewrite the commit (git commit --amend, "
              "or git rebase -i for older ones), or edit the PR description. The "
              "squash merge is prefilled from these, so they must be clean before "
              "the merge, not after.", file=sys.stderr)
        return 1
    print(f"check-attribution: clean ({inspected} message(s) inspected)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
