"""Public claims in the docs must match what the repository ships.

Each test pins one wording fix so the claim cannot quietly drift back: a
supported-version line that was never released, a "tamper-evident" headline
without its key-custody qualifier, a CLI command that is not registered, a
backend listed as a deploy target while its code is a scaffold, a queue called
exactly-once while its reclaim path is at-least-once, and benchmark results
that look recorded when none are.
"""
from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]


def _read(relpath: str) -> str:
    return (REPO_ROOT / relpath).read_text(encoding="utf-8")


def test_security_policy_does_not_claim_an_unreleased_supported_line():
    """SECURITY.md named v0.2.x as the supported line. No release is published
    (the releases API is empty; the latest git tag is v0.1.6; main is 0.1.7 in
    release-cohort.toml), so the supported-versions section must say so."""
    text = _read("SECURITY.md")
    assert "v0.2.x" not in text
    section = text.split("## Supported versions", 1)[1].split("\n## ", 1)[0]
    assert "No release has been published" in section
    assert "v0.1.6" in section
    cohort = re.search(r'^version = "([^"]+)"', _read("release-cohort.toml"), re.MULTILINE)
    assert cohort is not None
    assert cohort.group(1) in section
