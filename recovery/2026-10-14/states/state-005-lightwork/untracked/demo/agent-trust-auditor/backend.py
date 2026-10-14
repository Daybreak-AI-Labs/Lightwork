"""Backend seam for the Agent Trust Auditor standalone SKU."""
from __future__ import annotations

import trust_engine as local
from capabilities import (
    CAPABILITY_LABELS,
    CAPS,
    CORE_ANALYSIS,
    FORCED_STANDALONE,
    STANDALONE,
)
from capabilities import caps_summary as _caps_summary

if CORE_ANALYSIS:
    from maverick import agent_trust_auditor as _auditor
else:
    _auditor = None

STORE = local.LocalDerivedStore()


def _binding(implementation: str, authority: str, active: bool = True) -> dict:
    return {
        "implementation": implementation,
        "authority": authority,
        "active": active,
    }


def _integrated_only(implementation: str) -> dict:
    return _binding(implementation, "integrated-lightwork-only", False)


_ANALYSIS_ENGINE = (
    "maverick.agent_trust_auditor pure finding rules"
    if CORE_ANALYSIS
    else "trust_engine.analyze_snapshot vendored rules"
)

CAPABILITY_BINDINGS = {
    "read_only_inventory": _binding(
        _ANALYSIS_ENGINE, "ephemeral-untrusted-input"
    ),
    "deterministic_findings": _binding(
        _ANALYSIS_ENGINE, "analysis-only-no-platform-authority"
    ),
    "cited_observations": _binding(
        "trust_engine stable observation citations", "unsigned-local-input"
    ),
    "safe_metadata_only": _binding(
        "request_limits strict fields; allowed values are not secret-scanned",
        "input-boundary",
    ),
    "local_cas_store": _binding(
        "trust_engine.LocalDerivedStore", "unsigned-local-derived-records"
    ),
    "core_analysis_adapter": _binding(
        (
            "maverick.agent_trust_auditor._entity_findings (pure analysis only)"
            if CORE_ANALYSIS
            else "Not bound: core pure analysis is unavailable or forced off"
        ),
        "analysis-only-no-audit-or-authority",
        CORE_ANALYSIS,
    ),
    "live_discovery": _integrated_only(
        "Configured MCP, provider, agent, and tool projection"
    ),
    "authority_mutation": _integrated_only(
        "Governed agent-trust and connector authority planes"
    ),
    "governed_lifecycle": _integrated_only(
        "Tenant-scoped human ownership and lifecycle workflow"
    ),
    "signed_audit": _integrated_only("Ed25519 audit authority"),
    "evidence_graph": _integrated_only("Governed evidence graph"),
    "signed_evidence_pack": _integrated_only(
        "Cryptographically signed Agent Trust Auditor evidence pack"
    ),
    "continuous_monitoring": _integrated_only(
        "Manual governed refresh, freshness evaluation, and evidence findings"
    ),
}


def _validate_capability_bindings() -> None:
    if set(CAPABILITY_BINDINGS) != set(CAPS):
        raise RuntimeError("Agent Trust Auditor capability bindings do not match CAPS")
    for capability, enabled in CAPS.items():
        binding = CAPABILITY_BINDINGS[capability]
        if binding.get("active") is not enabled:
            raise RuntimeError(
                f"Agent Trust Auditor capability binding drift: {capability}"
            )
        if not binding.get("implementation") or not binding.get("authority"):
            raise RuntimeError(
                f"Agent Trust Auditor capability binding is incomplete: {capability}"
            )


_validate_capability_bindings()


def caps_summary() -> dict:
    summary = _caps_summary()
    summary["capabilities"] = [
        {
            "id": capability,
            "label": CAPABILITY_LABELS[capability],
            **CAPABILITY_BINDINGS[capability],
        }
        for capability in CAPS
    ]
    return summary


def analyze(
    declared_assets: list[dict],
    observations: list[dict],
    *,
    now: float | None = None,
    stale_after_seconds: float = local.DEFAULT_STALE_AFTER_SECONDS,
) -> list[dict]:
    finder = getattr(_auditor, "_entity_findings", None) if _auditor else None
    return local.analyze_snapshot(
        declared_assets,
        observations,
        now=now,
        stale_after_seconds=stale_after_seconds,
        entity_finder=finder,
    )


def save_derived(records: list[dict], expected_revision: int) -> list[dict]:
    return STORE.save_findings(records, expected_revision)


def state() -> dict:
    return STORE.state()


__all__ = [
    "CAPABILITY_BINDINGS",
    "CAPS",
    "CORE_ANALYSIS",
    "FORCED_STANDALONE",
    "STANDALONE",
    "analyze",
    "caps_summary",
    "save_derived",
    "state",
]
