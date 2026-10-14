"""Runtime capability and authority matrix for the reduced CRA Officer SKU."""
from __future__ import annotations

import importlib.util
import os


def _can_import(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError):
        return False


# Do not probe a nested module in forced mode: find_spec may import its parent.
FORCED_STANDALONE = os.environ.get("CRA_PSO_STANDALONE") == "1"
CORE_ANALYSIS = False if FORCED_STANDALONE else _can_import("maverick.cra_ops")
STANDALONE = not CORE_ANALYSIS
PLATFORM_ENGINE = CORE_ANALYSIS

CAPS = {
    "product_release_inventory": True,
    "cyclonedx_spdx_normalization": True,
    "human_applicability_gate": True,
    "human_reportability_gate": True,
    "article14_clock_projection": True,
    "local_vex_drafts": True,
    "local_report_drafts": True,
    "packet_digest_approval": True,
    "receipt_digest_binding": True,
    "local_cas_store": True,
    "platform_clock_rule_adapter": PLATFORM_ENGINE,
    "automatic_legal_decisions": False,
    "regulator_network_filing": False,
    "signed_audit": False,
    "continuous_evidence_graph": False,
    "evidence_connectors": False,
    "governed_platform_authority": False,
    "raw_secret_retention": False,
}

CAPABILITY_LABELS = {
    "product_release_inventory": "Local product and release inventory",
    "cyclonedx_spdx_normalization": "Bounded CycloneDX 1.5/1.6 and SPDX 2.3 JSON normalization",
    "human_applicability_gate": "Human CRA product-applicability decisions",
    "human_reportability_gate": "Human case-reportability decisions",
    "article14_clock_projection": "Article 14 24-hour, 72-hour, and final-report clock projection",
    "local_vex_drafts": "Local VEX drafts",
    "local_report_drafts": "Local notification and final-report drafts",
    "packet_digest_approval": "Human approval bound to an exact report-packet digest",
    "receipt_digest_binding": "Local receipt recording bound to the approved packet digest",
    "local_cas_store": "Private unsigned local JSON store with revision CAS",
    "platform_clock_rule_adapter": "Lightwork CRA clock declarations analysis adapter",
    "automatic_legal_decisions": "Automatic legal applicability or reportability decisions",
    "regulator_network_filing": "Network filing to ENISA or a CSIRT",
    "signed_audit": "Signed tamper-evident audit",
    "continuous_evidence_graph": "Continuous governed evidence graph",
    "evidence_connectors": "Product-security and regulator connectors",
    "governed_platform_authority": "Tenant-scoped governed Lightwork authority",
    "raw_secret_retention": "Dedicated raw-secret or source-SBOM retention",  # pragma: allowlist secret
}


def caps_summary() -> dict:
    backend = "lightwork-core-rules" if CORE_ANALYSIS else "vendored-rules"
    return {
        "standalone": True,
        "forced_standalone": FORCED_STANDALONE,
        "vendored_analysis": STANDALONE,
        "core_analysis": CORE_ANALYSIS,
        "analysis_backend": backend,
        "integrated_governance": False,
        "authority": "unsigned-local",
        "network_filing": False,
        "legal_decision_automation": False,
        "mode": f"CRA Product Security Officer ({backend})",
        "active": [CAPABILITY_LABELS[key] for key, enabled in CAPS.items() if enabled],
        "gated": [CAPABILITY_LABELS[key] for key, enabled in CAPS.items() if not enabled],
    }


__all__ = [
    "CAPS",
    "CAPABILITY_LABELS",
    "CORE_ANALYSIS",
    "FORCED_STANDALONE",
    "PLATFORM_ENGINE",
    "STANDALONE",
    "caps_summary",
]
