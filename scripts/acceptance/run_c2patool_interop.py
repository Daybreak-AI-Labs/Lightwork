"""Run a real-binary c2patool signer/verifier interoperability check.

This script intentionally accepts fixture paths rather than downloading or
embedding private material.  For a development-only run, the public fixtures
from the pinned ``contentauth/c2pa-rs`` release may be supplied.  Production
keys must never be passed to this script; use the external signer/KMS seam.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from pathlib import Path
from typing import Any

from maverick.ai_media_provenance import C2PABinding
from maverick.ai_media_provenance_c2pa import (
    C2PAToolSigner,
    C2PAToolSigningCredentials,
    C2PAToolVerifier,
)

SCHEMA = "lightwork.c2patool-interoperability.v1"


def _digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _read(path: Path, *, maximum: int, label: str) -> bytes:
    if path.is_symlink():
        raise ValueError(f"{label} must be a regular non-symlink file")
    resolved = path.resolve(strict=True)
    if not resolved.is_file() or resolved.is_symlink():
        raise ValueError(f"{label} must be a regular non-symlink file")
    data = resolved.read_bytes()
    if not 1 <= len(data) <= maximum:
        raise ValueError(f"{label} exceeds its acceptance bound")
    return data


def _manifest(
    _binding: C2PABinding,
    _assertions: tuple[str, ...],
) -> dict[str, Any]:
    return {
        "title": "Lightwork c2patool interoperability fixture",
        "vendor": "DaybreakAI",
        "claim_generator_info": [
            {"name": "Lightwork", "version": "0.1.7"}
        ],
        "assertions": [
            {
                "label": "c2pa.actions",
                "data": {
                    "actions": [
                        {
                            "action": "c2pa.created",
                            "digitalSourceType": (
                                "http://cv.iptc.org/newscodes/"
                                "digitalsourcetype/digitalArt"
                            ),
                        }
                    ]
                },
            }
        ],
    }


def run(
    *,
    executable: Path,
    image_path: Path,
    certificate_path: Path,
    private_key_path: Path,
    trust_anchors_path: Path | None = None,
) -> dict[str, Any]:
    executable_sha256 = _digest(
        _read(
            executable,
            maximum=256 * 1024 * 1024,
            label="c2patool executable",
        )
    )
    executable = executable.resolve(strict=True)
    media = _read(image_path, maximum=64 * 1024 * 1024, label="image")
    certificate = _read(
        certificate_path,
        maximum=256 * 1024,
        label="test certificate chain",
    )
    private_key = _read(
        private_key_path,
        maximum=64 * 1024,
        label="test private key",
    )
    anchors = (
        None
        if trust_anchors_path is None
        else _read(
            trust_anchors_path,
            maximum=1024 * 1024,
            label="trust anchors",
        )
    )
    source_digest = _digest(media)
    binding = C2PABinding(
        media_kind="image",
        source_content_sha256=source_digest,
        asset_id="MAA-" + source_digest[:32],
        asset_revision=1,
        asset_version_digest=_digest(b"c2patool-interop-asset-v1"),
        deployment_id="acceptance-c2patool-local",
        deployment_revision=1,
        deployment_artifact_digest=_digest(b"c2patool-interop-deployment-v1"),
        context_id="acceptance-c2patool-v1",
        context_revision=1,
        context_digest=_digest(b"c2patool-interop-context-v1"),
    )

    def credentials() -> C2PAToolSigningCredentials:
        return C2PAToolSigningCredentials(
            certificate_chain=certificate,
            private_key=private_key,
            algorithm="es256",
        )

    common: dict[str, Any] = {
        "executable": executable,
        "expected_executable_sha256": executable_sha256,
        "timeout_seconds": 45.0,
        "clock": time.time,
    }
    if anchors is not None:
        common["trust_anchor_provider"] = lambda: anchors
    signer = C2PAToolSigner(
        **common,
        credential_provider=credentials,
        manifest_provider=_manifest,
        timestamp_authority_url="http://timestamp.digicert.com",
        allow_insecure_timestamp_authority=True,
    )
    verifier = C2PAToolVerifier(**common)
    if not signer.is_available() or not verifier.is_available():
        raise RuntimeError("the selected c2patool executable is unavailable")

    signed, signing_receipt = signer.sign(
        media,
        binding=binding,
        assertion_codes=("c2pa.actions",),
    )
    verification_binding = C2PABinding(
        **{
            **binding.__dict__,
            "source_content_sha256": _digest(signed),
        }
    )
    verification_receipt = verifier.verify(
        signed,
        binding=verification_binding,
    )
    if signing_receipt["validation_state"] != "valid":
        raise RuntimeError("c2patool signing receipt was not valid")
    if verification_receipt["validation_state"] != "valid":
        raise RuntimeError("c2patool verification receipt was not valid")
    if "c2pa.actions" not in verification_receipt["assertion_codes"]:
        raise RuntimeError("c2patool verification omitted the requested assertion")
    result = {
        "schema": SCHEMA,
        "status": "passed",
        "mode": "real_binary_development_fixture",
        "adapter_id": "c2patool",
        "adapter_version": verifier.adapter_version,
        "c2patool_executable_sha256": executable_sha256,
        "source_sha256": source_digest,
        "signed_sha256": _digest(signed),
        "signed_size_bytes": len(signed),
        "signing_receipt_sha256": signing_receipt["receipt_sha256"],
        "verification_receipt_sha256": verification_receipt["receipt_sha256"],
        "signing_trust_chain_state": signing_receipt["trust_chain_state"],
        "verification_trust_chain_state": verification_receipt[
            "trust_chain_state"
        ],
        "assertion_codes": verification_receipt["assertion_codes"],
        "private_key_persisted_by_lightwork": False,
        "production_custody_accepted": False,
        "limitations": [
            "Public development fixtures do not establish production signing custody.",
            "C2PA validity does not prove authorship, ownership, truth, consent, or completeness.",
        ],
    }
    result["report_sha256"] = _digest(
        json.dumps(
            result,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        ).encode("ascii")
    )
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--c2patool", type=Path, required=True)
    parser.add_argument("--image", type=Path, required=True)
    parser.add_argument("--certificate", type=Path, required=True)
    parser.add_argument("--private-key", type=Path, required=True)
    parser.add_argument("--trust-anchors", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    report = run(
        executable=args.c2patool,
        image_path=args.image,
        certificate_path=args.certificate,
        private_key_path=args.private_key,
        trust_anchors_path=args.trust_anchors,
    )
    rendered = json.dumps(report, sort_keys=True, indent=2) + "\n"
    if args.output is None:
        sys.stdout.write(rendered)
    else:
        from maverick.file_lock import atomic_write_text

        atomic_write_text(args.output, rendered, mode=0o600, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
