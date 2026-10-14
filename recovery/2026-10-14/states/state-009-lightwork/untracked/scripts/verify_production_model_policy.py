#!/usr/bin/env python3
"""Validate the deployment-bound production model/provider policy."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def _core_path(root: Path) -> Path:
    return root / "packages" / "maverick-core"


def verify_production_model_policy(
    root: Path,
    *,
    release_candidate: bool = False,
) -> dict[str, object]:
    root = root.resolve()
    core = str(_core_path(root))
    if core not in sys.path:
        sys.path.insert(0, core)

    from maverick.file_lock import read_verified_bytes
    from maverick.production_model_policy import (
        load_policy_file,
        production_surface_sha256,
    )

    surface_path = root / "release" / "production-surface-manifest.json"
    surface_raw = read_verified_bytes(
        surface_path,
        root=surface_path.parent,
        max_bytes=1024 * 1024,
    )
    try:
        surface = json.loads(surface_raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("production surface is not valid UTF-8 JSON") from exc
    policy = load_policy_file(
        root / "release" / "production-model-policy.json",
        production_surface_sha256=production_surface_sha256(surface_raw),
        require_approved=release_candidate,
    )
    if policy.profile_id != surface.get("profile_id"):
        raise ValueError("production model policy targets a different profile")
    if policy.source_version != surface.get("source_version"):
        raise ValueError("production model policy targets a different version")
    providers = sorted({model.provider for model in policy.models.values()})
    admitted = sorted(surface.get("constraints", {}).get("model_providers", []))
    if any(provider not in admitted for provider in providers):
        raise ValueError("production model policy admits a provider outside the surface")
    return {
        "policy_id": policy.policy_id,
        "profile_id": policy.profile_id,
        "source_version": policy.source_version,
        "production_surface_digest_mode": (
            policy.production_surface_digest_mode
        ),
        "status": policy.status,
        "model_count": len(policy.models),
        "providers": providers,
        "release_candidate": release_candidate,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--release-candidate", action="store_true")
    args = parser.parse_args(argv)
    try:
        result = verify_production_model_policy(
            args.root,
            release_candidate=args.release_candidate,
        )
    except Exception as exc:
        parser.exit(1, f"production-model-policy: ERROR: {exc}\n")
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
