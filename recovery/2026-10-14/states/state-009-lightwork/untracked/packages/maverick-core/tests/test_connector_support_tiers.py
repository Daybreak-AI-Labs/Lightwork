"""Connector presence must never silently become a support claim."""

from __future__ import annotations

import json
import re
from pathlib import Path

from maverick.tools import AUTHENTICATED_AMBIENT_CRED_TOOL_NAMES
from maverick.tools.enterprise_connectors import (
    CONNECTOR_SUPPORT_TIERS,
    ENTERPRISE_CONNECTOR_NAMES,
    READ_CONNECTOR_NAMES,
    connector_catalog,
    connector_support_catalog,
)

REPO_ROOT = Path(__file__).resolve().parents[3]


def test_every_known_connector_has_an_explicit_nonproduction_support_tier() -> None:
    entries = connector_support_catalog()
    by_name = {entry["name"]: entry for entry in entries}

    assert len(by_name) == len(entries)
    expected = (
        set(ENTERPRISE_CONNECTOR_NAMES)
        | set(READ_CONNECTOR_NAMES)
        | set(AUTHENTICATED_AMBIENT_CRED_TOOL_NAMES)
    )
    assert set(by_name) == expected
    assert all(
        set(entry)
        == {
            "name",
            "label",
            "source_class",
            "support_tier",
            "production_status",
        }
        for entry in entries
    )
    assert {entry["support_tier"] for entry in entries} <= CONNECTOR_SUPPORT_TIERS
    assert {entry["production_status"] for entry in entries} == {"unsupported"}
    assert not {
        entry["name"]
        for entry in entries
        if entry["support_tier"] in {"certified", "supported_beta"}
    }


def test_installer_catalog_labels_every_entry_without_certification() -> None:
    catalog = connector_catalog()

    assert catalog
    assert all(entry["support_tier"] in CONNECTOR_SUPPORT_TIERS for entry in catalog)
    assert {entry["production_status"] for entry in catalog} == {"unsupported"}
    assert {entry["support_tier"] for entry in catalog} == {
        "generated_experimental",
        "unsupported",
    }


def test_first_production_profile_admits_no_connector() -> None:
    manifest = json.loads(
        (REPO_ROOT / "release" / "production-surface-manifest.json").read_text(
            encoding="utf-8"
        )
    )

    assert manifest["constraints"]["connectors"] == []
    connector_surfaces = [
        surface
        for surface in manifest["surfaces"]
        if surface["id"].startswith("connector:")
    ]
    assert not [surface for surface in connector_surfaces if surface["included"]]


def test_marketplace_exposes_tiers_instead_of_a_bare_live_integration_count() -> None:
    from maverick.marketplace.storefront import connector_marketplace

    view = connector_marketplace("zendesk")

    assert sum(view["tier_counts"].values()) == view["total"]
    assert view["connectors"]
    assert all(item["production_status"] == "unsupported" for item in view["connectors"])
    assert all(item["support_tier"] in CONNECTOR_SUPPORT_TIERS for item in view["connectors"])


def test_connector_count_claims_are_labeled_as_code_catalog_not_live_support() -> None:
    count = f"{len(ENTERPRISE_CONNECTOR_NAMES):,}"
    pattern = re.compile(rf"{re.escape(count)}[^\n]{{0,140}}")
    violations: list[str] = []
    for path in REPO_ROOT.glob("**/*.md"):
        if any(part in {".git", "node_modules", "site", "_site"} for part in path.parts):
            continue
        text = path.read_text(encoding="utf-8")
        for match in pattern.finditer(text):
            claim = match.group(0).lower()
            if not any(label in claim for label in ("catalog", "experimental", "adapter")):
                violations.append(f"{path.relative_to(REPO_ROOT)}: {match.group(0)}")
    assert not violations, (
        "connector-count prose must identify code catalog/adapters, not imply "
        "live support:\n" + "\n".join(violations)
    )
