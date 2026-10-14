"""Digest pinning and signed evidence admission for sandbox images.

An OCI tag is a mutable pointer, not an execution identity. Production image
admission therefore binds the exact digest to a release-authority Ed25519
signature and to immutable copies of the upstream-signature bundle, SBOM,
vulnerability report, and toolchain allowlist. The first supported production
profile disables command execution, but this gate prevents a later profile
from enabling a container sandbox without the missing supply-chain evidence.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from ..file_lock import read_verified_bytes

ATTESTATION_SCHEMA = "lightwork.sandbox-image-attestation.v1"
ATTESTATION_PATH_ENV = "MAVERICK_SANDBOX_IMAGE_ATTESTATION"
ATTESTATION_PUBKEY_ENV = "MAVERICK_SANDBOX_IMAGE_ATTESTATION_PUBKEY"
_DIGEST_RE = re.compile(r"[^@\s]+@sha256:[0-9a-f]{64}")
_SHA256_RE = re.compile(r"[0-9a-f]{64}")
_PUBKEY_RE = re.compile(r"[0-9a-f]{64}")
_SIGNATURE_RE = re.compile(r"[0-9a-f]{128}")
_UTC_RE = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z")
_MAX_EVIDENCE_BYTES = 32 * 1024 * 1024
_MAX_ATTESTATION_LIFETIME = timedelta(days=7)

PYTHON_IMAGE = (
    "python:3.12-slim@sha256:"
    "57cd7c3a7a273101a6485ba99423ee568157882804b1124b4dd04266317710de"
)
PINNED_SANDBOX_IMAGES: Mapping[str, str] = {
    "python": PYTHON_IMAGE,
    "py": PYTHON_IMAGE,
    "rust": (
        "rust:1-slim@sha256:"
        "5c6f46a6e4472ab1ca7ba7d494e6677f2f219ebc02f32025d3986f057635ec9c"
    ),
    "go": (
        "golang:1-bookworm@sha256:"
        "1ecb7edf62a0408027bd5729dfd6b1b8766e578e8df93995b225dfd0944eb651"
    ),
    "golang": (
        "golang:1-bookworm@sha256:"
        "1ecb7edf62a0408027bd5729dfd6b1b8766e578e8df93995b225dfd0944eb651"
    ),
    "javascript": (
        "node:22-bookworm-slim@sha256:"
        "f32b81066cde10a75dbac96646099533316d94bac4150c55da1636e1f0ffdc46"
    ),
    "typescript": (
        "node:22-bookworm-slim@sha256:"
        "f32b81066cde10a75dbac96646099533316d94bac4150c55da1636e1f0ffdc46"
    ),
    "js": (
        "node:22-bookworm-slim@sha256:"
        "f32b81066cde10a75dbac96646099533316d94bac4150c55da1636e1f0ffdc46"
    ),
    "ts": (
        "node:22-bookworm-slim@sha256:"
        "f32b81066cde10a75dbac96646099533316d94bac4150c55da1636e1f0ffdc46"
    ),
    "node": (
        "node:22-bookworm-slim@sha256:"
        "f32b81066cde10a75dbac96646099533316d94bac4150c55da1636e1f0ffdc46"
    ),
    "ruby": (
        "ruby:3-slim@sha256:"
        "614edae6a80eb2a7cf1984f03a6d814f523a48355e8f96deb5ddd25faa86353e"
    ),
    "java": (
        "eclipse-temurin:21-jdk@sha256:"
        "da9d3a4f7650db39b918fc5a2c3da76556fb8cc8e5f3767cdea0bb409286951a"
    ),
    "kotlin": (
        "eclipse-temurin:21-jdk@sha256:"
        "da9d3a4f7650db39b918fc5a2c3da76556fb8cc8e5f3767cdea0bb409286951a"
    ),
}


class SandboxImagePolicyError(ValueError):
    """Image identity or signed evidence is absent, stale, or invalid."""


def is_digest_pinned(image: object) -> bool:
    return isinstance(image, str) and bool(_DIGEST_RE.fullmatch(image.strip()))


def canonical_attestation_bytes(payload: Mapping[str, Any]) -> bytes:
    return json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("utf-8")


def _exact(value: object, fields: set[str], *, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or set(value) != fields:
        raise SandboxImagePolicyError(f"{label} fields are not exact")
    return value


def _text(value: object, *, label: str, limit: int = 512) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise SandboxImagePolicyError(f"{label} is invalid")
    return value.strip()


def _utc(value: object, *, label: str) -> datetime:
    text = _text(value, label=label, limit=20)
    if not _UTC_RE.fullmatch(text):
        raise SandboxImagePolicyError(f"{label} must be whole-second UTC")
    return datetime.strptime(text, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)


def _artifact(attestation_dir: Path, value: object, *, label: str) -> tuple[Path, str]:
    item = _exact(value, {"path", "sha256"}, label=label)
    relative = Path(_text(item["path"], label=f"{label} path", limit=240))
    expected = _text(item["sha256"], label=f"{label} sha256", limit=64)
    if relative.is_absolute() or ".." in relative.parts or not _SHA256_RE.fullmatch(expected):
        raise SandboxImagePolicyError(f"{label} artifact reference is invalid")
    base = attestation_dir.absolute()
    path = base / relative
    try:
        path.relative_to(base)
        raw = read_verified_bytes(
            path,
            root=base,
            max_bytes=_MAX_EVIDENCE_BYTES,
        )
    except (OSError, RuntimeError, ValueError) as exc:
        raise SandboxImagePolicyError(f"{label} artifact is unavailable") from exc
    if not raw:
        raise SandboxImagePolicyError(f"{label} artifact size is invalid")
    digest = hashlib.sha256(raw).hexdigest()
    if digest != expected:
        raise SandboxImagePolicyError(f"{label} artifact digest mismatch")
    return path, digest


def verify_image_attestation(
    image: str,
    attestation_path: str | Path,
    trusted_public_key: str,
    *,
    now: datetime | None = None,
) -> Mapping[str, Any]:
    """Verify the complete signed image-evidence packet for ``image``."""
    if not is_digest_pinned(image):
        raise SandboxImagePolicyError("sandbox image must be pinned by sha256 digest")
    if not _PUBKEY_RE.fullmatch(str(trusted_public_key).strip().lower()):
        raise SandboxImagePolicyError("trusted image-attestation Ed25519 key is invalid")
    path = Path(attestation_path)
    try:
        raw = read_verified_bytes(path, root=path.parent, max_bytes=256 * 1024)
    except (OSError, RuntimeError, ValueError) as exc:
        raise SandboxImagePolicyError("sandbox image attestation is unavailable") from exc
    if not raw:
        raise SandboxImagePolicyError("sandbox image attestation size is invalid")
    try:
        record = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SandboxImagePolicyError("sandbox image attestation is invalid JSON") from exc
    record = _exact(
        record,
        {
            "schema",
            "image",
            "issued_at",
            "expires_at",
            "source_signature",
            "sbom",
            "vulnerability_scan",
            "toolchain",
            "signer_pubkey",
            "signature",
        },
        label="sandbox image attestation",
    )
    if record["schema"] != ATTESTATION_SCHEMA or record["image"] != image:
        raise SandboxImagePolicyError("sandbox image attestation identity mismatch")
    issued = _utc(record["issued_at"], label="attestation issued_at")
    expires = _utc(record["expires_at"], label="attestation expires_at")
    current = now or datetime.now(UTC)
    if current < issued or current >= expires or expires - issued > _MAX_ATTESTATION_LIFETIME:
        raise SandboxImagePolicyError("sandbox image attestation is stale or not yet valid")
    signer = _text(record["signer_pubkey"], label="attestation signer", limit=64).lower()
    signature = _text(record["signature"], label="attestation signature", limit=128).lower()
    if signer != trusted_public_key.strip().lower() or not _SIGNATURE_RE.fullmatch(signature):
        raise SandboxImagePolicyError("sandbox image attestation signer is untrusted")

    source = _exact(
        record["source_signature"],
        {"identity", "issuer", "bundle"},
        label="source signature",
    )
    _text(source["identity"], label="source signature identity")
    _text(source["issuer"], label="source signature issuer")
    _artifact(path.parent, source["bundle"], label="source signature bundle")

    sbom = _exact(record["sbom"], {"format", "artifact"}, label="SBOM")
    if sbom["format"] not in {"spdx-json", "cyclonedx-json"}:
        raise SandboxImagePolicyError("sandbox image SBOM format is unsupported")
    _artifact(path.parent, sbom["artifact"], label="SBOM")

    scan = _exact(
        record["vulnerability_scan"],
        {"scanner", "database_updated_at", "critical", "high", "artifact"},
        label="vulnerability scan",
    )
    _text(scan["scanner"], label="vulnerability scanner")
    database_updated = _utc(scan["database_updated_at"], label="scanner database_updated_at")
    if database_updated > issued or issued - database_updated > timedelta(days=1):
        raise SandboxImagePolicyError("vulnerability database is stale or future-dated")
    if type(scan["critical"]) is not int or type(scan["high"]) is not int:
        raise SandboxImagePolicyError("vulnerability counts must be integers")
    if scan["critical"] != 0 or scan["high"] != 0:
        raise SandboxImagePolicyError("sandbox image has critical or high vulnerabilities")
    _artifact(path.parent, scan["artifact"], label="vulnerability report")

    toolchain = _exact(
        record["toolchain"],
        {"allowlist_id", "artifact"},
        label="toolchain",
    )
    _text(toolchain["allowlist_id"], label="toolchain allowlist id")
    _artifact(path.parent, toolchain["artifact"], label="toolchain inventory")

    payload = {key: value for key, value in record.items() if key != "signature"}
    from ..audit.signing import verify_ed25519

    if not verify_ed25519(signer, signature, canonical_attestation_bytes(payload)):
        raise SandboxImagePolicyError("sandbox image attestation signature is invalid")
    return record


def require_configured_image_attestation(
    image: str,
    config: Mapping[str, Any] | None,
) -> Mapping[str, Any]:
    """Load the explicit config/env trust anchor and verify ``image``."""
    cfg = config or {}
    path = str(cfg.get("image_attestation") or os.environ.get(ATTESTATION_PATH_ENV, "")).strip()
    public_key = str(
        cfg.get("image_attestation_pubkey")
        or os.environ.get(ATTESTATION_PUBKEY_ENV, "")
    ).strip()
    if not path or not public_key:
        raise SandboxImagePolicyError(
            "sandbox image attestation path and trusted public key are required"
        )
    return verify_image_attestation(image, path, public_key)


__all__ = [
    "ATTESTATION_PATH_ENV",
    "ATTESTATION_PUBKEY_ENV",
    "ATTESTATION_SCHEMA",
    "PINNED_SANDBOX_IMAGES",
    "PYTHON_IMAGE",
    "SandboxImagePolicyError",
    "canonical_attestation_bytes",
    "is_digest_pinned",
    "require_configured_image_attestation",
    "verify_image_attestation",
]
