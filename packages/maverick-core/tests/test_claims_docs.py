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


def test_readme_tamper_evidence_headline_names_its_key_custody():
    """The README headline said "Tamper-evident" with no qualifier. The audit
    signing key is held on the same host by default (audit/signing.py), so the
    headline must say so and name the off-host setting."""
    headline = next(
        line for line in _read("README.md").splitlines() if "Tamper-evident" in line
    )
    assert "same host" in headline
    assert "MAVERICK_AUDIT_SIGNING_KEY" in headline


def test_public_material_names_only_a_registered_proof_pack_command():
    """`maverick proof-pack` is not a registered CLI command; the click group has
    `proof` (the workforce value report) and no proof-pack. The signed evidence
    bundle is built by `python -m maverick.proof_pack`, which has a __main__ block."""
    module = _read("packages/maverick-core/maverick/proof_pack.py")
    assert 'if __name__ == "__main__"' in module
    paths = ["README.md"]
    for root in ("pitch", "docs"):
        for path in sorted((REPO_ROOT / root).rglob("*")):
            if path.suffix in {".md", ".html"}:
                paths.append(path.relative_to(REPO_ROOT).as_posix())
    stale = [p for p in paths if "maverick proof-pack" in _read(p)]
    assert stale == [], f"unregistered 'maverick proof-pack' command in: {stale}"
