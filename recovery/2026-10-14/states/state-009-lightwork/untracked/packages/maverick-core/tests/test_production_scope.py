"""The reviewed production surface is exact, default-deny, and hash-pinnable."""

from __future__ import annotations

import json

import pytest
from maverick import production_scope


def test_source_manifest_is_exact_and_unknown_surfaces_default_to_unsupported() -> None:
    manifest = production_scope.load_manifest()

    assert manifest.profile_id == "enterprise-single-control-plane-v1"
    assert manifest.source_version == "0.1.7"
    assert manifest.status_for("client:mobile") == "unsupported"
    assert manifest.status_for("future:unreviewed") == "unsupported"
    assert manifest.includes("interface:dashboard-rest") is True
    assert manifest.constraints["attachments_enabled"] is False
    assert manifest.includes("feature:attachments") is False
    assert manifest.includes("feature:code-dgm") is False
    assert manifest.status_for("package:npm:@maverick/plugin-sdk") == "experimental"
    assert manifest.status_for("package:pypi:maverick-native") == "experimental"
    assert manifest.status_for("package:npm:mvk-scan-wasm") == "experimental"
    assert manifest.includes("package:pypi:maverick-native") is False
    assert manifest.includes("package:npm:mvk-scan-wasm") is False
    assert manifest.included_packages() == (
        "maverick-agent",
        "maverick-shield",
        "maverick-dashboard",
    )
    assert manifest.constraints["architectures"] == ["amd64"]
    assert manifest.constraints["kms_providers"] == ["vault"]


def test_manifest_digest_pin_rejects_byte_drift(monkeypatch) -> None:
    manifest = production_scope.load_manifest()
    monkeypatch.setenv(production_scope.EXPECTED_DIGEST_ENV, "0" * 64)

    with pytest.raises(production_scope.ProductionScopeError, match="digest"):
        production_scope.load_manifest(manifest.path)


def test_manifest_rejects_an_included_research_surface(tmp_path) -> None:
    source = production_scope.load_manifest()
    document = json.loads(source.path.read_text(encoding="utf-8"))
    surface = next(item for item in document["surfaces"] if item["id"] == "feature:code-dgm")
    surface["included"] = True
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(document), encoding="utf-8")

    with pytest.raises(production_scope.ProductionScopeError, match="non-supported"):
        production_scope.load_manifest(path)


def test_current_source_is_explicitly_not_a_released_production_artifact() -> None:
    manifest = production_scope.load_manifest()

    assert manifest.release_eligible is False
    assert "unreleased" in manifest.release_reason


def test_production_mode_requires_an_explicit_well_formed_flag(monkeypatch) -> None:
    monkeypatch.delenv(production_scope.REQUIRE_PRODUCTION_SURFACE_ENV, raising=False)
    assert production_scope.production_surface_required() is False

    monkeypatch.setenv(production_scope.REQUIRE_PRODUCTION_SURFACE_ENV, "1")
    assert production_scope.production_surface_required() is True

    monkeypatch.setenv(production_scope.REQUIRE_PRODUCTION_SURFACE_ENV, "maybe")
    with pytest.raises(production_scope.ProductionScopeError, match="boolean"):
        production_scope.production_surface_required()


@pytest.mark.parametrize("section", ["constraints", "release_state"])
def test_manifest_nested_schemas_are_exact(tmp_path, section: str) -> None:
    source = production_scope.load_manifest()
    document = json.loads(source.path.read_text(encoding="utf-8"))
    document[section]["unreviewed_field"] = True
    path = tmp_path / f"{section}.json"
    path.write_text(json.dumps(document), encoding="utf-8")

    with pytest.raises(production_scope.ProductionScopeError, match="schema is not exact"):
        production_scope.load_manifest(path)


def test_surface_category_must_match_its_identifier(tmp_path) -> None:
    source = production_scope.load_manifest()
    document = json.loads(source.path.read_text(encoding="utf-8"))
    document["surfaces"][0]["category"] = "client"
    path = tmp_path / "category.json"
    path.write_text(json.dumps(document), encoding="utf-8")

    with pytest.raises(production_scope.ProductionScopeError, match="category does not match"):
        production_scope.load_manifest(path)
