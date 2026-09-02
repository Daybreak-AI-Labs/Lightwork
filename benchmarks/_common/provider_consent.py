"""Consent predicates shared by paid standalone benchmarks and their tests."""

from __future__ import annotations

import os
from collections.abc import Mapping


def paid_anthropic_benchmark_ready(
    environ: Mapping[str, str] | None = None,
) -> bool:
    """Require explicit provider readiness and a nonblank Anthropic key."""
    values = os.environ if environ is None else environ
    return (
        values.get("MAVERICK_PROVIDER_READY") == "1"
        and bool(values.get("ANTHROPIC_API_KEY", "").strip())
    )


__all__ = ["paid_anthropic_benchmark_ready"]
