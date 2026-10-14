"""Production sandbox images are immutable and evidence-admitted."""

from __future__ import annotations

import hashlib
import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519
from maverick.sandbox.image_policy import (
    ATTESTATION_SCHEMA,
    PINNED_SANDBOX_IMAGES,
    PYTHON_IMAGE,
    SandboxImagePolicyError,
    canonical_attestation_bytes,
    is_digest_pinned,
    verify_image_attestation,
)


def _write_packet(tmp_path, *, high: int = 0):
    artifacts = {
        "signature.bundle": b"verified sigstore bundle\n",
        "sbom.spdx.json": b'{"spdxVersion":"SPDX-2.3"}\n',
        "vulnerabilities.json": b'{"matches":[]}\n',
        "toolchain.json": b'{"packages":["python"]}\n',
    }
    for name, content in artifacts.items():
        (tmp_path / name).write_bytes(content)

    def artifact(name: str) -> dict[str, str]:
        return {
            "path": name,
            "sha256": hashlib.sha256(artifacts[name]).hexdigest(),
        }

    private = ed25519.Ed25519PrivateKey.generate()
    public = private.public_key().public_bytes(
        serialization.Encoding.Raw,
        serialization.PublicFormat.Raw,
    ).hex()
    now = datetime.now(UTC).replace(microsecond=0)
    record = {
        "schema": ATTESTATION_SCHEMA,
        "image": PYTHON_IMAGE,
        "issued_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "expires_at": (now + timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source_signature": {
            "identity": "https://github.com/docker-library/python/.github/workflows/official-images.yml",
            "issuer": "https://token.actions.githubusercontent.com",
            "bundle": artifact("signature.bundle"),
        },
        "sbom": {
            "format": "spdx-json",
            "artifact": artifact("sbom.spdx.json"),
        },
        "vulnerability_scan": {
            "scanner": "grype",
            "database_updated_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "critical": 0,
            "high": high,
            "artifact": artifact("vulnerabilities.json"),
        },
        "toolchain": {
            "allowlist_id": "sandbox-python-v1",
            "artifact": artifact("toolchain.json"),
        },
        "signer_pubkey": public,
    }
    record["signature"] = private.sign(canonical_attestation_bytes(record)).hex()
    path = tmp_path / "attestation.json"
    path.write_text(json.dumps(record), encoding="utf-8")
    return path, public, record, now


def test_all_builtin_sandbox_images_are_digest_pinned() -> None:
    assert PINNED_SANDBOX_IMAGES
    assert all(is_digest_pinned(image) for image in PINNED_SANDBOX_IMAGES.values())


def test_complete_signed_packet_is_admitted(tmp_path) -> None:
    path, public, _, now = _write_packet(tmp_path)

    record = verify_image_attestation(PYTHON_IMAGE, path, public, now=now)

    assert record["image"] == PYTHON_IMAGE


def test_packet_and_artifacts_do_not_reopen_verified_paths(monkeypatch, tmp_path) -> None:
    path, public, _, now = _write_packet(tmp_path)

    monkeypatch.setattr(
        Path,
        "read_bytes",
        lambda _self: pytest.fail("evidence path was reopened after verification"),
    )

    assert verify_image_attestation(PYTHON_IMAGE, path, public, now=now)[
        "image"
    ] == PYTHON_IMAGE


def test_hardlinked_evidence_artifact_is_refused(tmp_path) -> None:
    path, public, _, now = _write_packet(tmp_path)
    target = tmp_path / "sbom.spdx.json"
    source = tmp_path / "second-name.spdx.json"
    source.write_bytes(target.read_bytes())
    target.unlink()
    try:
        os.link(source, target)
    except OSError:
        pytest.skip("hardlink creation is unavailable on this host")

    with pytest.raises(SandboxImagePolicyError, match="SBOM artifact is unavailable"):
        verify_image_attestation(PYTHON_IMAGE, path, public, now=now)


@pytest.mark.parametrize(
    "mutation,match",
    [
        (lambda record: record.update(image="python:3.12-slim"), "identity mismatch"),
        (lambda record: record["vulnerability_scan"].update(high=1), "vulnerabilities"),
        (lambda record: record.update(signature="00" * 64), "signature is invalid"),
    ],
)
def test_packet_mutation_fails_closed(tmp_path, mutation, match) -> None:
    path, public, record, now = _write_packet(tmp_path)
    mutation(record)
    path.write_text(json.dumps(record), encoding="utf-8")

    with pytest.raises(SandboxImagePolicyError, match=match):
        verify_image_attestation(PYTHON_IMAGE, path, public, now=now)


def test_artifact_substitution_fails_closed(tmp_path) -> None:
    path, public, _, now = _write_packet(tmp_path)
    (tmp_path / "sbom.spdx.json").write_text("substituted", encoding="utf-8")

    with pytest.raises(SandboxImagePolicyError, match="SBOM artifact digest mismatch"):
        verify_image_attestation(PYTHON_IMAGE, path, public, now=now)


def test_mutable_image_reference_is_never_admitted(tmp_path) -> None:
    path, public, _, now = _write_packet(tmp_path)

    with pytest.raises(SandboxImagePolicyError, match="pinned by sha256"):
        verify_image_attestation("python:3.12-slim", path, public, now=now)


def test_build_sandbox_requires_packet_before_runtime_probe(monkeypatch, tmp_path) -> None:
    from maverick.sandbox import DockerBackend, SandboxPolicyError, build_sandbox

    probed = False

    def probe(_self):
        nonlocal probed
        probed = True

    monkeypatch.setattr(DockerBackend, "_verify_docker", probe)

    with pytest.raises(SandboxPolicyError, match="attestation path"):
        build_sandbox(
            workdir=tmp_path,
            backend="docker",
            sandbox_config={"require_image_attestation": True},
        )
    assert probed is False


def test_production_manifest_automatically_requires_packet(monkeypatch, tmp_path) -> None:
    from maverick.sandbox import DockerBackend, SandboxPolicyError, build_sandbox

    monkeypatch.setenv("MAVERICK_PRODUCTION_MANIFEST", str(tmp_path / "profile.json"))
    monkeypatch.setattr(DockerBackend, "_verify_docker", lambda _self: None)

    with pytest.raises(SandboxPolicyError, match="attestation path"):
        build_sandbox(workdir=tmp_path, backend="docker", sandbox_config={})


def test_build_sandbox_accepts_exact_signed_packet(monkeypatch, tmp_path) -> None:
    from maverick.sandbox import DockerBackend, build_sandbox

    path, public, _, _ = _write_packet(tmp_path)
    monkeypatch.setattr(DockerBackend, "_verify_docker", lambda _self: None)

    backend = build_sandbox(
        workdir=tmp_path,
        backend="docker",
        sandbox_config={
            "require_image_attestation": True,
            "image_attestation": str(path),
            "image_attestation_pubkey": public,
        },
    )

    assert backend.image == PYTHON_IMAGE
