"""Release readiness is evidence-derived, never a self-declared Boolean."""

from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]


def _load_script(name: str):
    path = REPO_ROOT / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _load_verifier():
    return _load_script("verify_production_readiness")


def _documents() -> tuple[dict, dict]:
    evidence = json.loads(
        (REPO_ROOT / "release" / "production-readiness-evidence.json").read_text(
            encoding="utf-8"
        )
    )
    surface = json.loads(
        (REPO_ROOT / "release" / "production-surface-manifest.json").read_text(
            encoding="utf-8"
        )
    )
    return evidence, surface


def _write_documents(tmp_path: Path, evidence: dict, surface: dict) -> tuple[Path, Path]:
    surface_path = tmp_path / "surface.json"
    evidence_path = tmp_path / "evidence.json"
    surface_path.write_text(json.dumps(surface, sort_keys=True), encoding="utf-8")
    verifier = _load_verifier()
    evidence["production_surface_digest_mode"] = (
        verifier.PRODUCTION_SURFACE_DIGEST_MODE
    )
    evidence["production_surface_sha256"] = verifier.production_surface_sha256(
        surface_path
    )
    evidence_path.write_text(json.dumps(evidence, sort_keys=True), encoding="utf-8")
    return evidence_path, surface_path


def _git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def test_draft_evidence_enumerates_every_required_gate_and_open_work():
    verifier = _load_verifier()
    summary = verifier.validate_production_readiness(
        REPO_ROOT,
        evidence_path=REPO_ROOT / "release" / "production-readiness-evidence.json",
        production_surface_path=(
            REPO_ROOT / "release" / "production-surface-manifest.json"
        ),
    )

    assert len(verifier.ALL_GATES) == 219
    assert len(verifier.P0_GATES) == 37
    assert len(verifier.P1_GATES) == 157
    assert len(verifier.P2_GATES) == 25
    assert summary["recorded_gates"] == 194
    assert summary["required_gates"] == 194
    assert summary["unresolved_required_gates"] == 82
    assert summary["status"] == "draft"
    assert summary["release_eligible"] is False
    evidence, _surface = _documents()
    assert evidence["schema"] == "lightwork.production-readiness-evidence.v3"
    assert evidence["production_surface_digest_mode"] == "utf8-lf-sha256"
    assert evidence["assessed_source_content"] is None
    gates = {item["id"]: item for item in evidence["gates"]}
    assert set(gates) == verifier.P0_GATES | verifier.P1_GATES
    assert evidence["assessed_source_revision"] is None
    qa03 = next(item for item in evidence["gates"] if item["id"] == "QA-03")
    assert qa03["priority"] == "P1"
    assert qa03["disposition"] == "satisfied"
    assert qa03["evidence_refs"] == ["pytest-exception-governance"]
    data_gates = {
        item["id"]: item
        for item in evidence["gates"]
        if item["id"] in {"DATA-09", "DATA-17"}
    }
    assert set(data_gates) == {"DATA-09", "DATA-17"}
    assert all(item["priority"] == "P1" for item in data_gates.values())
    assert all(item["disposition"] == "pending" for item in data_gates.values())
    assert data_gates["DATA-09"]["evidence_refs"] == ["postgres-tenant-rls"]
    assert data_gates["DATA-17"]["evidence_refs"] == [
        "postgres-encryption-migration"
    ]
    assert gates["REL-17"]["disposition"] == "pending"
    assert gates["REL-18"]["disposition"] == "blocked"
    assert gates["RUN-09"]["disposition"] == "excluded"
    assert "RUN-09" in verifier.CONDITIONAL_GATES
    assert "Cross-process A2A" in gates["RUN-09"]["rationale"]
    assert "operator CLI" in gates["RUN-19"]["rationale"]
    for number in (*range(1, 11), *range(14, 17)):
        assert gates[f"DGM-{number:02d}"]["disposition"] == "excluded"
    for number in (*range(1, 11), 13, 14, 15, 16, 17):
        assert f"DGM-{number:02d}" in verifier.CONDITIONAL_GATES
    assert {"DGM-11", "DGM-12"}.isdisjoint(verifier.CONDITIONAL_GATES)
    assert gates["DGM-11"]["disposition"] == "satisfied"
    assert gates["DGM-12"]["disposition"] == "satisfied"
    source_closed = {
        "DATA-21",
        "DGM-11",
        "RUN-10",
        "RUN-23",
        "OPS-02",
        "OPS-08",
        "REL-08",
        "REL-15",
        "REL-24",
        "REL-25",
        "QA-05",
        "QA-07",
        "QA-08",
        "QA-16",
        "QA-19",
    }
    assert all(gates[gate_id]["disposition"] == "satisfied" for gate_id in source_closed)
    assert gates["RUN-23"]["evidence_refs"][-1] == "flow-transition-ledger"
    assert gates["REL-25"]["evidence_refs"] == ["production-onboarding-contract"]
    assert gates["QA-08"]["evidence_refs"][0] == "production-qa-assurance"
    for gate_id in ("QA-05", "QA-07", "QA-16"):
        assert gates[gate_id]["disposition"] == "satisfied"
        assert "production-qa-assurance" in gates[gate_id]["evidence_refs"]
    assert gates["DATA-21"]["evidence_refs"] == [
        "shield-fail-closed",
        "knowledge-ingestion-integrity",
        "excluded-surfaces",
    ]
    for gate_id in ("QA-10", "QA-11"):
        assert gates[gate_id]["disposition"] == "pending"
        assert "production-qa-assurance" in gates[gate_id]["evidence_refs"]
    external_live_or_release_only = {
        "SCOPE-06",
        "SEC-17",
        "DATA-05",
        "DATA-06",
        "DATA-08",
        "DATA-09",
        "DATA-17",
        "DATA-18",
        "DATA-19",
        "DATA-20",
        "RUN-17",
        "RUN-24",
        "INT-02",
        "REL-01",
        "REL-02",
        "REL-03",
        "REL-04",
        "REL-05",
        "REL-06",
        "REL-17",
        "REL-18",
        "REL-19",
        "QA-02",
        "QA-04",
        "QA-06",
        "QA-09",
        "QA-12",
        "QA-17",
        "QA-18",
    }
    external_live_or_release_only.update(
        f"DEP-{number:02d}" for number in (*range(2, 15), 17)
    )
    external_live_or_release_only.update(
        f"OPS-{number:02d}" for number in (1, 3, 4, 5, 6, 7, 9, 11)
    )
    assert all(
        gates[gate_id]["disposition"] in {"pending", "blocked"}
        for gate_id in external_live_or_release_only
    )


def test_draft_cannot_silently_default_an_omitted_required_gate_to_pending(tmp_path):
    verifier = _load_verifier()
    evidence, surface = _documents()
    evidence["gates"] = [
        item for item in evidence["gates"] if item["id"] != "SEC-08"
    ]
    evidence_path, surface_path = _write_documents(tmp_path, evidence, surface)

    with pytest.raises(
        verifier.ProductionReadinessError,
        match="omits required gates.*SEC-08",
    ):
        verifier.validate_production_readiness(
            REPO_ROOT,
            evidence_path=evidence_path,
            production_surface_path=surface_path,
        )


def test_readiness_refuses_a_surface_digest_substitution(tmp_path):
    verifier = _load_verifier()
    evidence, surface = _documents()
    evidence_path, surface_path = _write_documents(tmp_path, evidence, surface)
    evidence["production_surface_sha256"] = "0" * 64
    evidence_path.write_text(json.dumps(evidence), encoding="utf-8")

    with pytest.raises(
        verifier.ProductionReadinessError,
        match="targets different normalized surface content",
    ):
        verifier.validate_production_readiness(
            REPO_ROOT,
            evidence_path=evidence_path,
            production_surface_path=surface_path,
        )


def test_production_surface_digest_is_utf8_lf_portable(tmp_path):
    verifier = _load_verifier()
    lf_path = tmp_path / "surface-lf.json"
    crlf_path = tmp_path / "surface-crlf.json"
    lf_bytes = b'{\n  "schema": "fixture"\n}\n'
    lf_path.write_bytes(lf_bytes)
    crlf_path.write_bytes(lf_bytes.replace(b"\n", b"\r\n"))

    expected = hashlib.sha256(lf_bytes).hexdigest()
    assert verifier.production_surface_sha256(lf_path) == expected
    assert verifier.production_surface_sha256(crlf_path) == expected


def test_readiness_refuses_an_unreviewed_surface_digest_mode(tmp_path):
    verifier = _load_verifier()
    evidence, surface = _documents()
    evidence_path, surface_path = _write_documents(tmp_path, evidence, surface)
    evidence["production_surface_digest_mode"] = "raw-byte-sha256"
    evidence_path.write_text(json.dumps(evidence), encoding="utf-8")

    with pytest.raises(
        verifier.ProductionReadinessError,
        match="surface evidence digest mode is unsupported",
    ):
        verifier.validate_production_readiness(
            REPO_ROOT,
            evidence_path=evidence_path,
            production_surface_path=surface_path,
        )


def test_ready_boolean_cannot_hide_omitted_p1_gates(tmp_path):
    verifier = _load_verifier()
    evidence, surface = _documents()
    evidence["status"] = "ready"
    evidence["assessed_source_revision"] = "a" * 40
    evidence["gates"] = [
        item for item in evidence["gates"] if item["id"] != "RUN-04"
    ]
    surface["release_state"]["eligible"] = True
    evidence_path, surface_path = _write_documents(tmp_path, evidence, surface)

    with pytest.raises(
        verifier.ProductionReadinessError,
        match="omits required gates",
    ):
        verifier.validate_production_readiness(
            REPO_ROOT,
            evidence_path=evidence_path,
            production_surface_path=surface_path,
            require_ready=True,
        )


def test_nonconditional_gate_cannot_be_closed_by_exclusion(tmp_path):
    verifier = _load_verifier()
    evidence, surface = _documents()
    gate = next(item for item in evidence["gates"] if item["id"] == "SEC-01")
    gate["disposition"] = "excluded"
    evidence_path, surface_path = _write_documents(tmp_path, evidence, surface)

    with pytest.raises(
        verifier.ProductionReadinessError,
        match="non-conditional production gate cannot be excluded",
    ):
        verifier.validate_production_readiness(
            REPO_ROOT,
            evidence_path=evidence_path,
            production_surface_path=surface_path,
        )


@pytest.mark.parametrize(
    ("mutation", "description"),
    (
        (
            lambda evidence: next(
                item for item in evidence["gates"] if item["id"] == "DATA-09"
            ).__setitem__("disposition", "satisfied"),
            "disposition substitution",
        ),
        (
            lambda evidence: next(
                item for item in evidence["gates"] if item["id"] == "DATA-09"
            ).__setitem__("evidence_refs", ["pytest-exception-governance"]),
            "evidence-reference substitution",
        ),
        (
            lambda evidence: evidence["evidence_sets"]["postgres-tenant-rls"][
                "implementation"
            ].__setitem__(0, "README.md"),
            "evidence-path substitution",
        ),
        (
            lambda evidence: next(
                item for item in evidence["gates"] if item["id"] == "DATA-09"
            ).__setitem__("rationale", "An arbitrary existing file says this is ready."),
            "rationale substitution",
        ),
        (
            lambda evidence: next(
                item for item in evidence["gates"] if item["id"] == "DATA-09"
            ).__setitem__("owner", "unreviewed-owner"),
            "owner substitution",
        ),
        (
            lambda evidence: evidence["evidence_sets"]["postgres-tenant-rls"].__setitem__(
                "adverse_result", "An unreviewed prose assertion passed."
            ),
            "evidence-result substitution",
        ),
    ),
)
def test_draft_semantics_require_explicit_verifier_review(
    tmp_path,
    mutation,
    description,
):
    verifier = _load_verifier()
    evidence, surface = _documents()
    original_digest = verifier.readiness_semantic_sha256(copy.deepcopy(evidence))
    mutation(evidence)
    evidence_path, surface_path = _write_documents(tmp_path, evidence, surface)

    assert verifier.readiness_semantic_sha256(evidence) != original_digest, description
    with pytest.raises(
        verifier.ProductionReadinessError,
        match="semantics differ from the reviewed contract",
    ):
        verifier.validate_production_readiness(
            REPO_ROOT,
            evidence_path=evidence_path,
            production_surface_path=surface_path,
        )


def test_semantic_digest_binds_release_fields_outside_approval_envelope():
    verifier = _load_verifier()
    evidence, _surface = _documents()
    original_digest = verifier.readiness_semantic_sha256(evidence)

    evidence["approval"] = {"signature": "outside signed semantics"}
    assert verifier.readiness_semantic_sha256(evidence) == original_digest

    for field, value in (
        ("updated_at", "2099-12-31T23:59:59Z"),
        ("assessed_source_content", {"sha256": "c" * 64}),
        ("assessed_source_revision", "a" * 40),
        ("production_surface_sha256", "b" * 64),
    ):
        changed = copy.deepcopy(evidence)
        changed[field] = value
        assert verifier.readiness_semantic_sha256(changed) != original_digest


def test_require_ready_rejects_the_current_draft_packet():
    verifier = _load_verifier()

    with pytest.raises(
        verifier.ProductionReadinessError,
        match="requires ready status and production release eligibility",
    ):
        verifier.validate_production_readiness(
            REPO_ROOT,
            evidence_path=REPO_ROOT / "release/production-readiness-evidence.json",
            production_surface_path=REPO_ROOT / "release/production-surface-manifest.json",
            require_ready=True,
        )


def test_ready_packet_cannot_cite_source_excluded_artifacts(tmp_path):
    verifier = _load_verifier()
    evidence, surface = _documents()
    evidence["status"] = "ready"
    evidence["assessed_source_content"] = verifier.readiness_source_content_identity(
        REPO_ROOT
    )
    surface["release_state"]["eligible"] = True
    for gate in evidence["gates"]:
        gate["disposition"] = "satisfied"
    evidence_path, surface_path = _write_documents(tmp_path, evidence, surface)

    with pytest.raises(
        verifier.ProductionReadinessError,
        match="cites a source-excluded artifact",
    ):
        verifier.validate_production_readiness(
            REPO_ROOT,
            evidence_path=evidence_path,
            production_surface_path=surface_path,
        )


def test_ready_evidence_paths_must_be_tracked_regular_files(tmp_path):
    verifier = _load_verifier()
    repo = tmp_path / "tracked-evidence"
    repo.mkdir()
    provenance = repo / "packages/maverick-core/maverick/proof_provenance.py"
    provenance.parent.mkdir(parents=True)
    shutil.copy2(
        REPO_ROOT / "packages/maverick-core/maverick/proof_provenance.py",
        provenance,
    )
    (repo / "tracked.txt").write_text("tracked\n", encoding="utf-8")
    (repo / ".gitignore").write_text("ignored.txt\n", encoding="utf-8")
    _git(repo, "init")
    _git(repo, "config", "user.name", "Readiness Evidence Test")
    _git(repo, "config", "user.email", "readiness-evidence@example.invalid")
    _git(repo, "add", "tracked.txt", ".gitignore", provenance.relative_to(repo).as_posix())
    _git(repo, "commit", "-m", "bind evidence")
    (repo / "ignored.txt").write_text("ignored but present\n", encoding="utf-8")

    verifier._require_tracked_regular_evidence(repo, {"tracked.txt"})
    with pytest.raises(
        verifier.ProductionReadinessError,
        match="not all tracked regular files",
    ):
        verifier._require_tracked_regular_evidence(repo, {"ignored.txt"})


def test_ready_packet_and_surface_paths_must_be_canonical(tmp_path):
    verifier = _load_verifier()
    repo = tmp_path / "canonical-ready"
    release = repo / "release"
    release.mkdir(parents=True)
    evidence = release / "production-readiness-evidence.json"
    surface = release / "production-surface-manifest.json"
    evidence.write_text("{}\n", encoding="utf-8")
    surface.write_text("{}\n", encoding="utf-8")

    assert verifier._require_canonical_ready_path(
        repo,
        evidence,
        "release/production-readiness-evidence.json",
        label="production readiness evidence packet",
    ) == "release/production-readiness-evidence.json"
    external = tmp_path / "external-evidence.json"
    external.write_bytes(evidence.read_bytes())
    with pytest.raises(
        verifier.ProductionReadinessError,
        match="must use the canonical production readiness evidence packet",
    ):
        verifier._require_canonical_ready_path(
            repo,
            external,
            "release/production-readiness-evidence.json",
            label="production readiness evidence packet",
        )


def test_ready_approval_requires_an_independently_trusted_ed25519_signature(tmp_path):
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    verifier = _load_verifier()
    repo = tmp_path / "signed-ready"
    trust_path = repo / verifier.TRUST_PATH
    trust_path.parent.mkdir(parents=True)
    private_key = Ed25519PrivateKey.generate()
    public_key = private_key.public_key().public_bytes(
        serialization.Encoding.Raw,
        serialization.PublicFormat.Raw,
    )
    key_id = hashlib.sha256(public_key).hexdigest()
    trust_path.write_text(
        json.dumps(
            {
                "schema": verifier.TRUST_SCHEMA,
                "status": "active",
                "trusted_ed25519_public_keys": {key_id: public_key.hex()},
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    semantic_digest = "a" * 64
    approval = {
        "schema": verifier.APPROVAL_SCHEMA,
        "algorithm": "ed25519",
        "key_id": key_id,
        "semantic_sha256": semantic_digest,
        "signature": private_key.sign(
            verifier.readiness_approval_message(semantic_digest)
        ).hex(),
    }
    verifier._validate_signed_approval(
        repo,
        approval,
        semantic_digest=semantic_digest,
    )
    tampered_signature = (
        f"{int(approval['signature'][:2], 16) ^ 1:02x}" + approval["signature"][2:]
    )

    for mutation, message in (
        ({"key_id": "b" * 64}, "not independently trusted"),
        ({"key_id": key_id.upper()}, "approval is malformed"),
        ({"signature": "0" * 128}, "approval is malformed"),
        ({"signature": tampered_signature}, "signature is invalid"),
        ({"semantic_sha256": "b" * 64}, "approval is malformed"),
    ):
        candidate = {**approval, **mutation}
        with pytest.raises(verifier.ProductionReadinessError, match=message):
            verifier._validate_signed_approval(
                repo,
                candidate,
                semantic_digest=semantic_digest,
            )

    trust_path.write_text(
        json.dumps(
            {
                "schema": verifier.TRUST_SCHEMA,
                "status": "unconfigured",
                "trusted_ed25519_public_keys": {},
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    with pytest.raises(
        verifier.ProductionReadinessError,
        match="not independently trusted",
    ):
        verifier._validate_signed_approval(
            repo,
            approval,
            semantic_digest=semantic_digest,
        )


@pytest.mark.parametrize(
    "payload",
    (
        b'{"status":"draft","status":"ready"}',
        b'{"value":NaN}',
        b'{"value":Infinity}',
    ),
)
def test_readiness_json_parser_rejects_ambiguous_values(tmp_path, payload):
    verifier = _load_verifier()
    path = tmp_path / "ambiguous.json"
    path.write_bytes(payload)

    with pytest.raises(
        verifier.ProductionReadinessError,
        match="not valid UTF-8 JSON",
    ):
        verifier._read_json(path, label="ambiguous readiness fixture")


def test_rel17_cannot_close_in_a_draft_ineligible_packet(tmp_path):
    verifier = _load_verifier()
    evidence, surface = _documents()
    gate = next(item for item in evidence["gates"] if item["id"] == "REL-17")
    gate["disposition"] = "satisfied"
    evidence_path, surface_path = _write_documents(tmp_path, evidence, surface)

    with pytest.raises(
        verifier.ProductionReadinessError,
        match="REL-17 requires a source-content-bound, release-eligible evidence packet",
    ):
        verifier.validate_production_readiness(
            REPO_ROOT,
            evidence_path=evidence_path,
            production_surface_path=surface_path,
        )


def test_ready_packet_survives_commit_and_tag_but_source_drift_fails(tmp_path):
    """A ready packet binds content, not the impossible commit containing itself."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    verifier = _load_verifier()
    release_state = _load_script("verify_release_state")
    release_assets = _load_script("verify_github_release_assets")
    repo = tmp_path / "source"
    assets = tmp_path / "assets"
    (repo / "packages" / "maverick-core" / "maverick").mkdir(parents=True)
    (repo / "release").mkdir()
    assets.mkdir()
    shutil.copy2(
        REPO_ROOT
        / "packages"
        / "maverick-core"
        / "maverick"
        / "proof_provenance.py",
        repo / "packages" / "maverick-core" / "maverick" / "proof_provenance.py",
    )
    source_path = repo / "source.txt"
    source_path.write_text("bound source\n", encoding="utf-8")
    (repo / "evidence.txt").write_text("reviewed evidence\n", encoding="utf-8")

    evidence, surface = _documents()
    surface["source_version"] = "1.2.3"
    surface["release_state"]["eligible"] = True
    surface_path = repo / "release" / "production-surface-manifest.json"
    surface_path.write_text(json.dumps(surface, sort_keys=True), encoding="utf-8")
    private_key = Ed25519PrivateKey.generate()
    public_key = private_key.public_key().public_bytes(
        serialization.Encoding.Raw,
        serialization.PublicFormat.Raw,
    )
    key_id = hashlib.sha256(public_key).hexdigest()
    (repo / verifier.TRUST_PATH).write_text(
        json.dumps(
            {
                "schema": verifier.TRUST_SCHEMA,
                "status": "active",
                "trusted_ed25519_public_keys": {key_id: public_key.hex()},
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    evidence.update(
        {
            "schema": verifier.SCHEMA,
            "source_version": "1.2.3",
            "production_surface_digest_mode": verifier.PRODUCTION_SURFACE_DIGEST_MODE,
            "production_surface_sha256": verifier.production_surface_sha256(
                surface_path
            ),
            "assessed_source_content": None,
            "assessed_source_revision": None,
            "status": "ready",
        }
    )
    evidence["evidence_sets"] = {
        "fixture": {
            "implementation": ["evidence.txt"],
            "regressions": ["evidence.txt"],
            "adverse_result": "The focused source-content regression passed.",
            "runbook": "evidence.txt",
        }
    }
    for gate in evidence["gates"]:
        gate["disposition"] = "satisfied"
        gate["evidence_refs"] = ["fixture"]
    evidence_path = repo / "release" / "production-readiness-evidence.json"
    evidence_path.write_text(json.dumps(evidence, sort_keys=True), encoding="utf-8")
    state = {
        "manifests": {
            "production_readiness": "release/production-readiness-evidence.json",
            "production_surface": "release/production-surface-manifest.json",
        }
    }
    (repo / "release" / "release-state.json").write_text(
        json.dumps(state, sort_keys=True),
        encoding="utf-8",
    )

    _git(repo, "init")
    _git(repo, "config", "user.name", "Readiness Test")
    _git(repo, "config", "user.email", "readiness@example.invalid")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "commit reviewed source")
    assessed_revision = _git(repo, "rev-parse", "HEAD")
    evidence["assessed_source_revision"] = assessed_revision
    evidence["assessed_source_content"] = (
        verifier.readiness_source_content_identity(repo)
    )
    semantic_digest = verifier.readiness_semantic_sha256(evidence)
    evidence["approval"] = {
        "schema": verifier.APPROVAL_SCHEMA,
        "algorithm": "ed25519",
        "key_id": key_id,
        "semantic_sha256": semantic_digest,
        "signature": private_key.sign(
            verifier.readiness_approval_message(semantic_digest)
        ).hex(),
    }
    evidence_path.write_text(json.dumps(evidence, sort_keys=True), encoding="utf-8")
    _git(repo, "add", "release/production-readiness-evidence.json")
    _git(repo, "commit", "-m", "promote readiness packet")
    _git(repo, "tag", "v1.2.3")
    tagged_revision = _git(repo, "rev-list", "-n", "1", "v1.2.3")
    assert tagged_revision != assessed_revision

    relocated = tmp_path / "relocated"
    subprocess.run(
        [
            "git",
            "-c",
            "core.autocrlf=false",
            "clone",
            "--no-local",
            str(repo),
            str(relocated),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    _git(relocated, "config", "core.autocrlf", "false")
    _git(relocated, "checkout", "--detach", "v1.2.3")
    assert _git(relocated, "rev-parse", "HEAD") == tagged_revision
    relocated_evidence = (
        relocated / "release" / "production-readiness-evidence.json"
    )
    relocated_surface = relocated / "release" / "production-surface-manifest.json"

    summary = verifier.validate_production_readiness(
        relocated,
        evidence_path=relocated_evidence,
        production_surface_path=relocated_surface,
        require_ready=True,
    )
    assert summary["assessed_source_revision"] == assessed_revision
    assert (
        summary["assessed_source_content_sha256"]
        == evidence["assessed_source_content"]["sha256"]
    )
    committed_packet = relocated_evidence.read_bytes()
    relocated_evidence.write_bytes(committed_packet + b"\n")
    with pytest.raises(
        verifier.ProductionReadinessError,
        match="must exactly match its committed HEAD bytes",
    ):
        verifier.validate_production_readiness(
            relocated,
            evidence_path=relocated_evidence,
            production_surface_path=relocated_surface,
            require_ready=True,
        )
    relocated_evidence.write_bytes(committed_packet)
    assert release_state._validate_production_readiness(
        relocated,
        state,
        require_ready=True,
    )["status"] == "ready"

    asset_surface = assets / "lightwork-production-surface-1.2.3.json"
    asset_readiness = assets / "lightwork-production-readiness-1.2.3.json"
    shutil.copy2(surface_path, asset_surface)
    shutil.copy2(evidence_path, asset_readiness)
    assert release_assets._verify_production_readiness_asset(
        assets,
        version="1.2.3",
        production_surface_name=asset_surface.name,
        tagged_source_root=relocated,
    ) == asset_readiness.name

    relocated_source = relocated / "source.txt"

    def assert_source_drift_fails() -> None:
        with pytest.raises(
            verifier.ProductionReadinessError,
            match="does not assess the current canonical source content",
        ):
            verifier.validate_production_readiness(
                relocated,
                evidence_path=relocated_evidence,
                production_surface_path=relocated_surface,
                require_ready=True,
            )
        with pytest.raises(
            ValueError,
            match="failed canonical review.*current canonical source content",
        ):
            release_assets._verify_production_readiness_asset(
                assets,
                version="1.2.3",
                production_surface_name=asset_surface.name,
                tagged_source_root=relocated,
            )

    relocated_source.write_text("modified source\n", encoding="utf-8")
    assert_source_drift_fails()
    relocated_source.write_text("bound source\n", encoding="utf-8")

    added_path = relocated / "new-source.txt"
    added_path.write_text("new untracked source\n", encoding="utf-8")
    assert_source_drift_fails()
    added_path.unlink()

    relocated_source.unlink()
    assert_source_drift_fails()
    relocated_source.write_text("bound source\n", encoding="utf-8")


def test_source_content_bound_fixture_results_cannot_read_as_release_or_capability_evidence():
    results = (REPO_ROOT / "proof" / "RESULTS.md").read_text(encoding="utf-8")
    scoreboard = (REPO_ROOT / "proof" / "run_proof.py").read_text(
        encoding="utf-8"
    )
    invalid_moat = (
        REPO_ROOT / "benchmarks" / "MOAT_RIGOROUS_RESULTS.md"
    ).read_text(encoding="utf-8")
    effect_contract = (
        REPO_ROOT / "docs" / "specs" / "external-effect-semantics.md"
    ).read_text(encoding="utf-8")
    fixture_scripts = [
        (REPO_ROOT / "proof" / name).read_text(encoding="utf-8")
        for name in (
            "self_harness_proof.py",
            "dgm_code_rung_proof.py",
            "swebench_governed_proof.py",
            "dgm_uplift_proof.py",
            "adapter_rung_proof.py",
        )
    ]

    assert "Evidence status: source-content-bound local fixture execution" in results
    assert "Stable source content:" in results
    assert "Assessed source revision:" in results
    assert "Worktree state:" in results
    assert "Generation timestamp:" in results
    assert "Evidence manifest: `proof/results/manifest.json`" in results
    assert "Raw stdout:" in results
    assert "Combined raw-output digest:" in results
    assert "11 fixture checks passed" in results
    assert "Not production-release evidence" in results
    for forbidden in (
        "guarantees PROVEN",
        "No other entry on the SWE-bench",
        "The Darwin-Gödel claim itself",
        "Results are reproducible on any source checkout",
    ):
        assert forbidden not in results

    assert "no CI result asserted" in scoreboard
    assert "verified in the CI test matrix" not in scoreboard
    assert invalid_moat.startswith(
        "# INVALID historical compounding-moat run — not evidence"
    )
    assert "## Defensible claim" not in invalid_moat
    assert "No defensible performance, capability, learning, cost, ROI" in invalid_moat
    assert "causally tied" not in invalid_moat
    assert "Every admitted model-visible consequential handler" in effect_contract
    assert "does not claim that every\nsupported operator CLI" in effect_contract
    assert all("fixture checks" in script for script in fixture_scripts)
    assert all("guarantees PROVEN" not in script for script in fixture_scripts)
    assert all("verified in CI" not in script for script in fixture_scripts)
    assert all("verified in the CI" not in script for script in fixture_scripts)
    assert all(
        "no hosted CI result is asserted" in script for script in fixture_scripts[1:]
    )
