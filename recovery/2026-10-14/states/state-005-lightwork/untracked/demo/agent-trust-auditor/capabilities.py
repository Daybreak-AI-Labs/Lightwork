"""Capability matrix for the Agent Trust Auditor standalone SKU."""
from __future__ import annotations

import importlib
import importlib.util
import os


def _core_analysis_available() -> bool:
    try:
        if importlib.util.find_spec("maverick.agent_trust_auditor") is None:
            return False
        module = importlib.import_module("maverick.agent_trust_auditor")
    except (ImportError, ValueError):
        return False
    return callable(getattr(module, "_entity_findings", None))


FORCED_STANDALONE = os.environ.get("AGENT_TRUST_AUDITOR_STANDALONE") == "1"
CORE_ANALYSIS = False if FORCED_STANDALONE else _core_analysis_available()
STANDALONE = not CORE_ANALYSIS
CAPS = {
    "read_only_inventory": True,
    "deterministic_findings": True,
    "cited_observations": True,
    "safe_metadata_only": True,
    "local_cas_store": True,
    "core_analysis_adapter": CORE_ANALYSIS,
    "live_discovery": False,
    "authority_mutation": False,
    "governed_lifecycle": False,
    "signed_audit": False,
    "evidence_graph": False,
    "signed_evidence_pack": False,
    "continuous_monitoring": False,
}
CAPABILITY_LABELS = {
    "read_only_inventory": "Read-only declared-versus-observed inventory",
    "deterministic_findings": "Deterministic trust-posture findings",
    "cited_observations": "Findings cite stable observation identifiers",
    "safe_metadata_only": "Allowlisted metadata fields; values are not secret-scanned",
    "local_cas_store": "Cross-process local CAS for derived findings",
    "core_analysis_adapter": "Lightwork Agent Trust Auditor analysis adapter",
    "live_discovery": "Configured and live estate discovery",
    "authority_mutation": "Agent, tool, MCP, or provider authority changes",
    "governed_lifecycle": "Human-governed ownership and lifecycle records",
    "signed_audit": "Ed25519 tamper-evident audit trail",
    "evidence_graph": "Continuous governed evidence graph",
    "signed_evidence_pack": "Signed and independently verifiable evidence pack",
    "continuous_monitoring": "Scheduled collection and freshness monitoring",
}


def caps_summary() -> dict:
    mode = (
        "Standalone Agent Trust Auditor (vendored analysis)"
        if STANDALONE
        else "Standalone Agent Trust Auditor (Lightwork core analysis)"
    )
    return {
        "standalone": True,
        "forced_standalone": FORCED_STANDALONE,
        "vendored_analysis": STANDALONE,
        "core_analysis": CORE_ANALYSIS,
        "analysis_backend": "vendored" if STANDALONE else "lightwork-core",
        "integrated_governance": False,
        "authority": "unsigned-local-derived-only",
        "mode": mode,
        "active": [
            CAPABILITY_LABELS[key] for key, enabled in CAPS.items() if enabled
        ],
        "gated": [
            CAPABILITY_LABELS[key] for key, enabled in CAPS.items() if not enabled
        ],
    }
