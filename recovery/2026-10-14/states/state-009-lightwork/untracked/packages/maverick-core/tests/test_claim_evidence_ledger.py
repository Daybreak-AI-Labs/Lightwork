from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "scripts" / "verify_claim_evidence_ledger.py"
SPEC = importlib.util.spec_from_file_location("claim_evidence_verifier", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
verifier = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(verifier)


def _copy_repository_inputs(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    paths = {
        Path(".gitattributes"),
        verifier.LEDGER_PATH,
        verifier.LEDGER_DOC_PATH,
    }
    paths.update(Path(path) for path in verifier.DOC_CLAIMS)
    for claim in verifier.CLAIM_DEFINITIONS:
        paths.update(Path(path) for path, _kind in claim["evidence"])
    for relative in paths:
        source = ROOT / relative
        destination = root / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)
    return root


def _write_ledger(root: Path, payload: object) -> None:
    (root / verifier.LEDGER_PATH).write_text(
        json.dumps(payload, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def _git(root: Path, autocrlf: bool, *args: str) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        [
            "git",
            "-c",
            f"core.autocrlf={'true' if autocrlf else 'false'}",
            "-c",
            "user.name=Claim Ledger Portability Test",
            "-c",
            "user.email=claim-ledger-portability@example.invalid",
            *args,
        ],
        cwd=root,
        check=True,
        capture_output=True,
    )


def test_committed_claim_evidence_ledger_is_exact_and_conservative() -> None:
    assert verifier.verify_claim_evidence_ledger(ROOT) == {
        "schema": verifier.SCHEMA,
        "status": "draft",
        "claims": 47,
        "buyer_documents": 119,
        "buyer_allowed_claims": 24,
    }


def test_claim_evidence_builder_inventory_is_exact_and_conservative() -> None:
    payload = verifier.build_claim_evidence_ledger(ROOT)
    assert len(payload["claims"]) == 47
    assert len(payload["buyer_documents"]) == 119
    assert sum(
        bool(claim["buyer_claim_allowed"]) for claim in payload["claims"]
    ) == 24
    assert len(verifier.COMPLIANCE_BUYER_DOCS) == 44
    assert len(verifier.STRATEGY_BUYER_DOCS) == 11
    rows = [
        row
        for claim in payload["claims"]
        for row in claim["evidence"]
    ] + list(payload["buyer_documents"])
    assert all(
        row["digest_mode"]
        == (
            "exact-byte-sha256"
            if row["path"] in verifier.EXACT_BYTE_BINDINGS
            else "utf8-lf-sha256"
        )
        for row in rows
    )


def test_ledger_survives_windows_generation_commit_and_linux_clone(
    tmp_path: Path,
) -> None:
    root = _copy_repository_inputs(tmp_path)
    _git(root, True, "init", "-q")
    _git(root, True, "config", "core.autocrlf", "true")
    _git(root, True, "add", "-A")
    _git(root, True, "commit", "-q", "-m", "source inputs")

    # Model a Windows worktree after the source commit. The release JSON files
    # are intentionally dirty in-memory bytes even though Git's clean form is LF.
    for relative in (
        "release/production-readiness-evidence.json",
        "release/release-state.json",
    ):
        path = root / relative
        normalized = (
            path.read_bytes()
            .decode("utf-8")
            .replace("\r\n", "\n")
            .replace("\r", "\n")
        )
        path.write_bytes(normalized.replace("\n", "\r\n").encode("utf-8"))

    payload = verifier.build_claim_evidence_ledger(root)
    bound_rows = [
        row
        for claim in payload["claims"]
        for row in claim["evidence"]
        if row["path"]
        in {
            "release/production-readiness-evidence.json",
            "release/release-state.json",
        }
    ]
    assert {row["path"] for row in bound_rows} == {
        "release/production-readiness-evidence.json",
        "release/release-state.json",
    }
    assert {row["digest_mode"] for row in bound_rows} == {"utf8-lf-sha256"}
    _write_ledger(root, payload)
    assert _git(root, True, "status", "--porcelain").stdout
    _git(root, True, "add", "-A")
    _git(root, True, "commit", "-q", "-m", "ledger generated on Windows")
    _git(root, True, "tag", "portable-ledger")

    relocated = tmp_path / "linux-checkout"
    subprocess.run(
        [
            "git",
            "-c",
            "core.autocrlf=false",
            "clone",
            "-q",
            "--no-local",
            str(root),
            str(relocated),
        ],
        check=True,
        capture_output=True,
    )
    _git(relocated, False, "config", "core.autocrlf", "false")
    _git(relocated, False, "checkout", "-q", "--detach", "portable-ledger")
    assert b"\r" not in (
        relocated / "release/production-readiness-evidence.json"
    ).read_bytes()
    assert b"\r" not in (
        relocated / "release/release-state.json"
    ).read_bytes()
    assert verifier.verify_claim_evidence_ledger(relocated)["claims"] == 47


def test_proof_disclaimer_markers_are_emitted_by_generator() -> None:
    generator = (ROOT / "proof/generate_results.py").read_text(encoding="utf-8")

    for marker in verifier.DISCLAIMER_MARKERS["proof/RESULTS.md"]:
        assert marker in generator


def test_ledger_refuses_buyer_document_drift(tmp_path: Path) -> None:
    root = _copy_repository_inputs(tmp_path)
    path = root / "docs/press-kit.md"
    path.write_text(path.read_text(encoding="utf-8") + "\nOverclaim.\n", encoding="utf-8")

    with pytest.raises(verifier.ClaimEvidenceLedgerError, match="stale"):
        verifier.verify_claim_evidence_ledger(root)


def test_ledger_refuses_evidence_drift(tmp_path: Path) -> None:
    root = _copy_repository_inputs(tmp_path)
    path = root / "packages/maverick-core/maverick/operator_contract.py"
    path.write_bytes(path.read_bytes() + b"\n")

    with pytest.raises(verifier.ClaimEvidenceLedgerError, match="stale"):
        verifier.verify_claim_evidence_ledger(root)


def test_ledger_refuses_digest_mode_drift(tmp_path: Path) -> None:
    root = _copy_repository_inputs(tmp_path)
    payload = verifier.build_claim_evidence_ledger(root)
    payload["claims"][0]["evidence"][0]["digest_mode"] = "exact-byte-sha256"
    _write_ledger(root, payload)

    with pytest.raises(verifier.ClaimEvidenceLedgerError, match="stale"):
        verifier.verify_claim_evidence_ledger(root)


def test_ledger_refuses_unmapped_buyer_document(tmp_path: Path) -> None:
    root = _copy_repository_inputs(tmp_path)
    (root / "docs/enterprise/new-buyer-claim.md").write_text(
        "# New claim\n",
        encoding="utf-8",
    )

    with pytest.raises(verifier.ClaimEvidenceLedgerError, match="incomplete"):
        verifier.verify_claim_evidence_ledger(root)


def test_ledger_refuses_missing_disclaimer(tmp_path: Path) -> None:
    root = _copy_repository_inputs(tmp_path)
    path = root / "README.md"
    path.write_text(
        path.read_text(encoding="utf-8").replace(
            "Current production-support boundary",
            "Production",
        ),
        encoding="utf-8",
    )

    with pytest.raises(verifier.ClaimEvidenceLedgerError, match="disclaimer"):
        verifier.build_claim_evidence_ledger(root)


def test_ledger_refuses_status_permission_overclaim(tmp_path: Path) -> None:
    root = _copy_repository_inputs(tmp_path)
    payload = json.loads((root / verifier.LEDGER_PATH).read_text(encoding="utf-8"))
    pending = next(
        claim
        for claim in payload["claims"]
        if claim["status"] == "deployment_evidence_required"
    )
    pending["buyer_claim_allowed"] = True
    _write_ledger(root, payload)

    with pytest.raises(verifier.ClaimEvidenceLedgerError, match="stale"):
        verifier.verify_claim_evidence_ledger(root)


def test_ledger_refuses_duplicate_json_keys(tmp_path: Path) -> None:
    root = _copy_repository_inputs(tmp_path)
    (root / verifier.LEDGER_PATH).write_text(
        '{"schema":"one","schema":"two"}\n',
        encoding="utf-8",
    )

    with pytest.raises(verifier.ClaimEvidenceLedgerError, match="duplicate"):
        verifier.verify_claim_evidence_ledger(root)


def test_draft_ledger_refuses_release_candidate_use(tmp_path: Path) -> None:
    root = _copy_repository_inputs(tmp_path)
    _write_ledger(root, verifier.build_claim_evidence_ledger(root))

    with pytest.raises(verifier.ClaimEvidenceLedgerError, match="approved"):
        verifier.verify_claim_evidence_ledger(root, release_candidate=True)


def test_current_two_party_approval_can_qualify_unchanged_ledger(
    tmp_path: Path,
) -> None:
    root = _copy_repository_inputs(tmp_path)
    payload = verifier.build_claim_evidence_ledger(root)
    payload["status"] = "approved"
    payload["approval"] = {
        "status": "approved",
        "approved_at": verifier.REVIEWED_AT,
        "approvers": ["release-owner", "security-owner"],
        "external_reference": "change-control:example",
    }
    _write_ledger(root, payload)

    assert verifier.verify_claim_evidence_ledger(
        root,
        release_candidate=True,
    )["claims"] == 47


def test_ledger_refuses_unmapped_program_document(tmp_path: Path) -> None:
    root = _copy_repository_inputs(tmp_path)
    (root / "docs/programs/unreviewed-program.md").write_text(
        "# Unreviewed program\n\nRoadmap/proposal only:\n",
        encoding="utf-8",
    )

    with pytest.raises(verifier.ClaimEvidenceLedgerError, match="incomplete"):
        verifier.build_claim_evidence_ledger(root)


def test_program_document_must_retain_proposal_boundary(tmp_path: Path) -> None:
    root = _copy_repository_inputs(tmp_path)
    path = root / "docs/programs/university-outreach.md"
    path.write_text(
        path.read_text(encoding="utf-8").replace(
            "Roadmap/proposal only:",
            "Current program:",
            1,
        ),
        encoding="utf-8",
    )

    with pytest.raises(verifier.ClaimEvidenceLedgerError, match="disclaimer"):
        verifier.build_claim_evidence_ledger(root)


def test_ledger_refuses_unmapped_legal_document(tmp_path: Path) -> None:
    root = _copy_repository_inputs(tmp_path)
    (root / "docs/enterprise/legal/new-terms.md").write_text(
        "# New terms\n",
        encoding="utf-8",
    )

    with pytest.raises(verifier.ClaimEvidenceLedgerError, match="incomplete"):
        verifier.build_claim_evidence_ledger(root)


def test_legal_document_must_retain_template_boundary(tmp_path: Path) -> None:
    root = _copy_repository_inputs(tmp_path)
    path = root / "docs/enterprise/legal/dpa-template.md"
    path.write_text(
        path.read_text(encoding="utf-8").replace(
            "Not legal advice",
            "Customer agreement",
            1,
        ),
        encoding="utf-8",
    )

    with pytest.raises(verifier.ClaimEvidenceLedgerError, match="disclaimer"):
        verifier.build_claim_evidence_ledger(root)


def test_ledger_refuses_unmapped_compliance_claim_document(tmp_path: Path) -> None:
    root = _copy_repository_inputs(tmp_path)
    (root / "docs/compliance/new-assurance.md").write_text(
        "# New assurance\n\n"
        "> **Compliance status — source-controlled draft:** example.\n",
        encoding="utf-8",
    )

    with pytest.raises(verifier.ClaimEvidenceLedgerError, match="incomplete"):
        verifier.build_claim_evidence_ledger(root)


def test_compliance_document_must_retain_status_boundary(tmp_path: Path) -> None:
    root = _copy_repository_inputs(tmp_path)
    path = root / "docs/compliance/policies/incident-response-policy.md"
    path.write_text(
        path.read_text(encoding="utf-8").replace(
            "Compliance status — source-controlled draft:",
            "Compliance status — operating:",
            1,
        ),
        encoding="utf-8",
    )

    with pytest.raises(verifier.ClaimEvidenceLedgerError, match="disclaimer"):
        verifier.build_claim_evidence_ledger(root)


def test_ledger_refuses_unmapped_strategy_document(tmp_path: Path) -> None:
    root = _copy_repository_inputs(tmp_path)
    path = root / "docs/strategy/archive/unreviewed-pitch.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "# Unreviewed pitch\n\n"
        "> **Strategy status - source-only / historical / proposal:** example.\n",
        encoding="utf-8",
    )

    with pytest.raises(verifier.ClaimEvidenceLedgerError, match="incomplete"):
        verifier.build_claim_evidence_ledger(root)


def test_strategy_document_must_retain_claim_status_boundary(
    tmp_path: Path,
) -> None:
    root = _copy_repository_inputs(tmp_path)
    path = root / "docs/strategy/investor-narrative.md"
    path.write_text(
        path.read_text(encoding="utf-8").replace(
            verifier.STRATEGY_STATUS_MARKER,
            "Strategy status - current:",
            1,
        ),
        encoding="utf-8",
    )

    with pytest.raises(verifier.ClaimEvidenceLedgerError, match="disclaimer"):
        verifier.build_claim_evidence_ledger(root)


@pytest.mark.parametrize(
    "stale_claim",
    (
        "The repository has 12,345 tests.",
        "The product includes 2,020 specialist packs.",
        "Controls were fuzzed up to 5,000 iterations.",
    ),
)
def test_strategy_document_refuses_stale_dynamic_totals(
    tmp_path: Path,
    stale_claim: str,
) -> None:
    root = _copy_repository_inputs(tmp_path)
    path = root / "docs/strategy/investor-narrative.md"
    path.write_text(
        path.read_text(encoding="utf-8") + f"\n{stale_claim}\n",
        encoding="utf-8",
    )

    with pytest.raises(
        verifier.ClaimEvidenceLedgerError,
        match="stale dynamic total",
    ):
        verifier.build_claim_evidence_ledger(root)


def test_strategy_claim_statuses_are_fail_closed() -> None:
    claims = {
        claim["id"]: claim for claim in verifier.build_claim_evidence_ledger(ROOT)["claims"]
    }

    assert claims["strategy-documents-source-only"]["status"] == (
        "source_inventory_only"
    )
    assert claims["strategy-documents-source-only"]["buyer_claim_allowed"] is True
    assert claims["strategy-documents-historical-only"]["status"] == "historical_only"
    assert claims["strategy-documents-historical-only"]["buyer_claim_allowed"] is False
    assert claims["strategy-documents-proposal-only"]["status"] == "draft"
    assert claims["strategy-documents-proposal-only"]["buyer_claim_allowed"] is False
