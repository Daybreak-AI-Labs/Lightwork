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

import pytest

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


def test_firecracker_is_described_as_a_scaffold_not_a_deploy_target():
    """The Firecracker backend labels itself a SCAFFOLD (sandbox/firecracker.py
    docstring) and deploy/aws/EXECUTOR.md and the deployment playbook call the
    local scaffold unsupported. Any doc line that names Firecracker as a deploy
    target or shipped backend must say it is an experimental scaffold."""
    checked = {
        "SECURITY.md": None,
        "pitch/SECURITY-QUESTIONNAIRE.md": "deploy targets",
        "docs/architecture.md": None,
        "docs/handbook.md": None,
        "docs/programs/press-and-case-studies.md": None,
    }
    enterprise = sorted(
        path.relative_to(REPO_ROOT).as_posix()
        for path in (REPO_ROOT / "docs" / "enterprise").rglob("*.md")
    )
    for relpath in enterprise:
        checked.setdefault(relpath, None)
    for relpath, needle in checked.items():
        for line in _read(relpath).splitlines():
            if "firecracker" not in line.lower() or (needle and needle not in line):
                continue
            assert "scaffold" in line.lower(), f"{relpath}: {line[:120]}"


def test_queue_delivery_is_not_described_as_exactly_once():
    """JobQueue.claim is atomic (one worker per job at a time), but a job whose
    worker dies after its side effect and before complete() runs again after the
    lease expires (JobQueue.reclaim_stale; Worker._reclaim_stale). Delivery is
    at-least-once, so prose must not say exactly-once for the queue as a whole."""
    stale = {
        "packages/maverick-dashboard/maverick_dashboard/app.py":
            "durable, retrying, exactly-once",
        "packages/maverick-dashboard/maverick_dashboard/automation_queue.py":
            "JobQueue's exactly-once",
        "docs/FEATURES.md":
            "**exactly-once** — the contention",
        "docs/evidence/README.md":
            "**exactly-once** execution",
        "STRESS_TEST.md":
            "**JobQueue exactly-once**",
    }
    for relpath, phrase in stale.items():
        assert phrase not in _read(relpath), f"{relpath} still says {phrase!r}"


def test_benchmark_results_say_when_no_live_run_is_recorded():
    """benchmarks/RESULTS.md holds only a dry-run CI smoke row. While that is the
    case the file must say that no live-model result is recorded and give the
    harness command that records one, so the table is not read as measured."""
    text = _read("benchmarks/RESULTS.md")
    rows = [line for line in text.splitlines() if line.startswith("| benchmarks/")]
    if all("dry-run" in row for row in rows):
        assert "no live-model results are recorded yet" in text.lower()
    command = "python benchmarks/harness.py benchmarks/longhorizon/research-report.md"
    assert command in text
    harness = _read("benchmarks/harness.py")
    assert '"--max-dollars"' in harness and '"--tag"' in harness


AUDIT_DOCS = (
    "PLATFORM_IMPROVEMENT_AUDIT.md",
    "USER_EXPERIENCE_AUDIT.md",
    "CODE_QUALITY_AUDIT.md",
)


@pytest.mark.parametrize("name", AUDIT_DOCS)
def test_audit_documents_carry_a_point_in_time_status_note(name):
    """The three audits list findings with no per-item status. Each must say it is
    a point-in-time record and point readers at docs/OPEN_ITEMS.md."""
    head = "\n".join(_read(name).splitlines()[:25])
    assert "point-in-time" in head.lower()
    assert "docs/OPEN_ITEMS.md" in head


def test_open_items_lists_the_items_still_open():
    """docs/OPEN_ITEMS.md must carry the candidate items that were verified as
    open, and mark the first three as in progress in a separate change."""
    text = _read("docs/OPEN_ITEMS.md")
    for phrase in (
        "row-level-security", "unsafe-inline", "SAML", "oidc.auto.tfvars",
        "deployer.tf", "image_tag", "recovery_window_in_days", "sslmode",
        "automountServiceAccountToken", "ingress", "use_lockfile",
        "app.py", "prompt_dsl", "REWRITES", "DigestIndex", "at-least-once",
    ):
        assert phrase in text, phrase
    assert text.count("In progress in a separate change") >= 3
