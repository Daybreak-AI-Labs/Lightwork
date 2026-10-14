#!/usr/bin/env python3
"""Verify the code-derived first-profile runtime contract at an explicit root."""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

RUNTIME_SOURCE = Path(
    "packages/maverick-core/maverick/production_runtime_contract.py"
)
CONTRACT = Path("release/production-runtime-contract.json")
SURFACE = Path("release/production-surface-manifest.json")
RUNTIME_SOURCE_SHA256 = "6dd947299323a2cacde7c7ae58a49de9e2316ee7be2a6ef16b46fbd6b929d5e1"
CONTRACT_SHA256 = "7f6e77710c199264c42f32b6ebbd3a8df178028dae5fa8dbe504b5daa66ff55c"


class ProductionRuntimeContractVerificationError(RuntimeError):
    """The runtime contract is missing, substituted, or no longer code-derived."""


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON constant: {value}")


def _read_json(path: Path, *, label: str) -> tuple[bytes, dict[str, Any]]:
    try:
        if path.is_symlink() or not path.is_file() or path.stat().st_size <= 0:
            raise ProductionRuntimeContractVerificationError(
                f"{label} is missing or not a non-empty regular file"
            )
        raw = path.read_bytes()
        value = json.loads(
            raw,
            object_pairs_hook=_strict_object,
            parse_constant=_reject_constant,
        )
    except ProductionRuntimeContractVerificationError:
        raise
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise ProductionRuntimeContractVerificationError(
            f"{label} is not strict UTF-8 JSON"
        ) from exc
    if not isinstance(value, dict):
        raise ProductionRuntimeContractVerificationError(
            f"{label} must be an object"
        )
    return raw, value


def verify(root: Path) -> dict[str, object]:
    root = root.resolve()
    source = (root / RUNTIME_SOURCE).resolve()
    if root not in source.parents or source.is_symlink() or not source.is_file():
        raise ProductionRuntimeContractVerificationError(
            "runtime contract source is missing or unsafe"
        )
    try:
        source_text = source.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise ProductionRuntimeContractVerificationError(
            "runtime contract source is not readable UTF-8"
        ) from exc
    normalized_source = source_text.replace("\r\n", "\n").replace("\r", "\n")
    source_digest = hashlib.sha256(normalized_source.encode("utf-8")).hexdigest()
    if source_digest != RUNTIME_SOURCE_SHA256:
        raise ProductionRuntimeContractVerificationError(
            "runtime contract source binding drifted"
        )

    raw, contract = _read_json(root / CONTRACT, label="production runtime contract")
    _surface_raw, surface = _read_json(root / SURFACE, label="production surface")
    if hashlib.sha256(raw).hexdigest() != CONTRACT_SHA256:
        raise ProductionRuntimeContractVerificationError(
            "production runtime contract bytes drifted from the reviewed artifact"
        )
    if (
        contract.get("schema") != "lightwork.production-runtime-contract.v1"
        or contract.get("profile_id") != surface.get("profile_id")
        or not isinstance(surface.get("source_version"), str)
        or not surface["source_version"]
    ):
        raise ProductionRuntimeContractVerificationError(
            "production runtime contract is not bound to the production surface"
        )
    network = contract.get("network")
    if not isinstance(network, dict) or network.get("live_cluster_proof_required") is not True:
        raise ProductionRuntimeContractVerificationError(
            "runtime contract must preserve the live-cluster evidence boundary"
        )
    return {
        "schema": contract["schema"],
        "profile_id": contract["profile_id"],
        "source_version": surface["source_version"],
        "status": "code-derived-contract-live-cluster-proof-required",
        "runtime_source_sha256": source_digest,
        "contract_sha256": hashlib.sha256(raw).hexdigest(),
        "live_cluster_proof_required": True,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    args = parser.parse_args(argv)
    try:
        result = verify(args.root)
    except ProductionRuntimeContractVerificationError as exc:
        print(f"production-runtime-contract: REFUSED: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
