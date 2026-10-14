from __future__ import annotations

import asyncio
from dataclasses import MISSING, fields
from pathlib import Path
from types import SimpleNamespace

import pytest
from maverick.production_scope import (
    ProductionManifest,
    ProductionSurface,
    load_manifest,
)
from maverick.production_tools import (
    ApprovalClass,
    AuditClass,
    CredentialBehavior,
    IdempotencyContract,
    ProductionToolContract,
    ProductionToolPolicy,
    ProductionToolPolicyError,
    ReconciliationMethod,
    ResourceExtractor,
    SideEffectClass,
    TenantScope,
    ToolRisk,
)
from maverick.safety.rate_limiter import apply_to_registry
from maverick.tools import Tool, ToolRegistry


def _manifest(
    *names: str,
    consequential_actions_enabled: bool = False,
) -> ProductionManifest:
    surfaces = {
        f"tool:{name}": ProductionSurface(
            surface_id=f"tool:{name}",
            category="tool",
            status="supported_beta",
            included=True,
        )
        for name in names
    }
    return ProductionManifest(
        path=Path("production-surface-manifest.json"),
        sha256="0" * 64,
        profile_id="test-production",
        source_version="0.0.0-test",
        release_eligible=False,
        release_reason="test manifest",
        constraints={
            "consequential_actions_enabled": consequential_actions_enabled,
        },
        surfaces=surfaces,
    )


def _no_resources() -> ResourceExtractor:
    return ResourceExtractor(
        argument_names=(),
        required=False,
        fallback_values=(),
    )


def _read_policy(*, with_path: bool = False) -> ProductionToolPolicy:
    return ProductionToolPolicy(
        risk=ToolRisk.MEDIUM,
        side_effect=SideEffectClass.NONE,
        tenant_scope=TenantScope.RUN_WORKSPACE,
        path_extractor=(
            ResourceExtractor(
                argument_names=("path",),
                required=True,
                fallback_values=(),
            )
            if with_path
            else _no_resources()
        ),
        host_extractor=_no_resources(),
        credential_behavior=CredentialBehavior.NONE,
        approval_class=ApprovalClass.NOT_REQUIRED,
        audit_class=AuditClass.DATA_ACCESS,
        idempotency_contract=IdempotencyContract.NOT_APPLICABLE,
        reconciliation_method=ReconciliationMethod.NOT_APPLICABLE,
    )


def _mutation_policy() -> ProductionToolPolicy:
    return ProductionToolPolicy(
        risk=ToolRisk.HIGH,
        side_effect=SideEffectClass.WORKSPACE_MUTATION,
        tenant_scope=TenantScope.RUN_WORKSPACE,
        path_extractor=ResourceExtractor(
            argument_names=("path",),
            required=True,
            fallback_values=(),
        ),
        host_extractor=_no_resources(),
        credential_behavior=CredentialBehavior.NONE,
        approval_class=ApprovalClass.EXACT_ACTION,
        audit_class=AuditClass.CONSEQUENTIAL,
        idempotency_contract=IdempotencyContract.REQUIRED_CALLER_KEY,
        reconciliation_method=ReconciliationMethod.AUTHORITATIVE_READ_AFTER_WRITE,
    )


def _tool(
    name: str,
    *,
    policy: ProductionToolPolicy | None = None,
    fn=None,
    with_path: bool = False,
) -> Tool:
    schema = {"type": "object", "properties": {}}
    if with_path:
        schema = {
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
        }
    return Tool(
        name=name,
        description=f"test tool {name}",
        input_schema=schema,
        fn=fn or (lambda _args: "ok"),
        production_policy=policy,
    )


def test_standard_registry_keeps_legacy_tool_contract() -> None:
    registry = ToolRegistry(production_manifest=None)
    registry.register(_tool("legacy"))

    assert asyncio.run(registry.run("legacy", {})) == "ok"


def test_production_registry_default_denies_unlisted_tool() -> None:
    registry = ToolRegistry(production_manifest=_manifest("reviewed"))

    registry.register(_tool("plugin_unknown"))

    assert registry.all() == []
    assert registry.production_exclusions() == frozenset({"plugin_unknown"})


def test_included_tool_without_metadata_fails_registry_construction() -> None:
    registry = ToolRegistry(production_manifest=_manifest("reviewed"))

    with pytest.raises(ProductionToolPolicyError, match="lacks complete policy"):
        registry.register(_tool("reviewed"))


def test_registry_finalization_rejects_a_missing_included_factory() -> None:
    registry = ToolRegistry(production_manifest=_manifest("reviewed"))

    with pytest.raises(ProductionToolPolicyError, match=r"missing=\['reviewed'\]"):
        registry.finalize_production_admission()


def test_partial_resource_metadata_is_rejected() -> None:
    policy = _read_policy(with_path=True)
    registry = ToolRegistry(production_manifest=_manifest("reviewed"))

    with pytest.raises(ProductionToolPolicyError, match="unknown arguments"):
        registry.register(_tool("reviewed", policy=policy))


def test_complete_read_only_policy_is_admitted_and_dispatched() -> None:
    registry = ToolRegistry(production_manifest=_manifest("reviewed"))
    tool = _tool(
        "reviewed",
        policy=_read_policy(with_path=True),
        with_path=True,
        fn=lambda args: f"read {args['path']}",
    )

    registry.register(tool)

    assert registry.production_policy_required() is True
    assert asyncio.run(registry.run("reviewed", {"path": "notes.txt"})) == (
        "read notes.txt"
    )


def test_mutation_is_rejected_when_production_surface_disables_actions() -> None:
    registry = ToolRegistry(production_manifest=_manifest("mutate"))

    with pytest.raises(ProductionToolPolicyError, match="mutation is excluded"):
        registry.register(
            _tool(
                "mutate",
                policy=_mutation_policy(),
                with_path=True,
            )
        )


def test_dispatch_rechecks_policy_and_refuses_registry_tampering() -> None:
    calls: list[str] = []
    registry = ToolRegistry(production_manifest=_manifest("mutate"))
    mutation = _tool(
        "mutate",
        policy=_mutation_policy(),
        with_path=True,
        fn=lambda args: calls.append(args["path"]) or "changed",
    )
    registry._tools["mutate"] = mutation

    result = asyncio.run(registry.run("mutate", {"path": "state.db"}))

    assert result.startswith("REFUSED (production tool policy)")
    assert calls == []


def test_dispatch_refuses_unextractable_resource_before_tool_runs() -> None:
    calls: list[object] = []
    registry = ToolRegistry(production_manifest=_manifest("reviewed"))
    registry.register(
        _tool(
            "reviewed",
            policy=_read_policy(with_path=True),
            with_path=True,
            fn=lambda args: calls.append(args) or "ran",
        )
    )

    result = asyncio.run(registry.run("reviewed", {"path": 42}))

    assert result.startswith("REFUSED (production tool policy)")
    assert calls == []


def test_consequential_surface_requires_typed_receipt_tool_marker() -> None:
    manifest = _manifest("mutate", consequential_actions_enabled=True)
    contract = ProductionToolContract.from_manifest(manifest)
    registry = ToolRegistry(production_manifest=manifest)

    assert contract.consequential_actions_enabled is True
    with pytest.raises(ProductionToolPolicyError, match="typed receipt contract"):
        registry.register(
            _tool(
                "mutate",
                policy=_mutation_policy(),
                with_path=True,
            )
        )


def test_registry_auto_uses_explicitly_configured_production_manifest(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest = _manifest("reviewed")
    monkeypatch.setattr(
        "maverick.production_tools.configured_production_manifest",
        lambda: manifest,
    )

    registry = ToolRegistry()
    registry.register(_tool("unreviewed"))

    assert registry.production_policy_required() is True
    assert registry.all() == []


def test_manifest_path_alone_does_not_turn_dev_registry_into_production(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from maverick.production_scope import (
        MANIFEST_ENV,
        REQUIRE_PRODUCTION_SURFACE_ENV,
    )

    manifest = load_manifest()
    monkeypatch.setenv(MANIFEST_ENV, str(manifest.path))
    monkeypatch.delenv(REQUIRE_PRODUCTION_SURFACE_ENV, raising=False)
    registry = ToolRegistry()
    registry.register(_tool("ordinary-dev-tool"))

    assert registry.production_policy_required() is False
    assert registry.get("ordinary-dev-tool") is not None


def test_rate_limit_wrapper_preserves_production_policy() -> None:
    registry = ToolRegistry(production_manifest=_manifest("reviewed"))
    policy = _read_policy()
    registry.register(_tool("reviewed", policy=policy))

    assert apply_to_registry(registry, limits={"reviewed": (1, 60.0)}) == 1

    assert registry.get("reviewed").production_policy is policy
    assert asyncio.run(registry.run("reviewed", {})) == "ok"
    assert asyncio.run(registry.run("reviewed", {})).startswith(
        "ERROR: rate limit exceeded"
    )


def test_production_policy_shapes_have_no_implicit_field_defaults() -> None:
    for data_class in (ProductionToolPolicy, ResourceExtractor):
        for field in fields(data_class):
            assert field.default is MISSING
            assert field.default_factory is MISSING


def test_manifest_tool_allowlist_matches_policy_bearing_factories(tmp_path: Path) -> None:
    from maverick.tools.budget_status import budget_status
    from maverick.tools.cidr_check import cidr_check
    from maverick.tools.control_tools import find_controls_tool
    from maverick.tools.format_money import format_money_tool
    from maverick.tools.fs import list_dir, read_file
    from maverick.tools.redact import redact_tool
    from maverick.tools.repo_map import repo_map
    from maverick.tools.semver_check import semver_check

    manifest = load_manifest()
    expected = {
        surface_id.removeprefix("tool:")
        for surface_id, surface in manifest.surfaces.items()
        if surface.category == "tool" and surface.included
    }
    sandbox = SimpleNamespace(workdir=tmp_path)
    tools = [
        read_file(sandbox),
        list_dir(sandbox),
        repo_map(sandbox),
        budget_status(SimpleNamespace()),
        find_controls_tool(),
        format_money_tool(),
        redact_tool(),
        cidr_check(),
        semver_check(),
    ]
    registry = ToolRegistry(production_manifest=manifest)

    for tool in tools:
        registry.register(tool)

    assert expected == {tool.name for tool in tools}
    assert {tool.name for tool in registry.all()} == expected


def test_base_registry_enforces_manifest_allowlist_end_to_end(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from maverick.production_scope import (
        EXPECTED_DIGEST_ENV,
        MANIFEST_ENV,
        REQUIRE_PRODUCTION_SURFACE_ENV,
    )
    from maverick.sandbox.local import LocalBackend
    from maverick.tools import base_registry
    from maverick.world_model import close_world_if_owned, open_world

    manifest = load_manifest()
    monkeypatch.setenv(MANIFEST_ENV, str(manifest.path))
    monkeypatch.setenv(REQUIRE_PRODUCTION_SURFACE_ENV, "1")
    monkeypatch.delenv(EXPECTED_DIGEST_ENV, raising=False)
    expected = {
        surface_id.removeprefix("tool:")
        for surface_id, surface in manifest.surfaces.items()
        if surface.category == "tool" and surface.included
    }
    world = open_world(tmp_path / "world.db")
    try:
        registry = base_registry(
            world,
            LocalBackend(str(tmp_path)),
            _include_generated_tools=False,
        )
    finally:
        close_world_if_owned(world)

    assert registry.production_policy_required() is True
    assert {tool.name for tool in registry.all()} == expected


def test_production_registry_never_imports_broad_catalog(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import builtins

    from maverick.production_scope import (
        EXPECTED_DIGEST_ENV,
        MANIFEST_ENV,
        REQUIRE_PRODUCTION_SURFACE_ENV,
    )
    from maverick.sandbox.local import LocalBackend
    from maverick.tools import base_registry
    from maverick.world_model import close_world_if_owned, open_world

    manifest = load_manifest()
    monkeypatch.setenv(MANIFEST_ENV, str(manifest.path))
    monkeypatch.setenv(REQUIRE_PRODUCTION_SURFACE_ENV, "1")
    monkeypatch.delenv(EXPECTED_DIGEST_ENV, raising=False)
    forbidden = {
        "maverick.mcp_tools",
        "maverick.plugins",
        "maverick.tools.ask_user",
        "maverick.tools.enterprise_connectors",
        "maverick.tools.openapi_runner",
    }
    forbidden_relative = {
        "ask_user",
        "enterprise_connectors",
        "openapi_runner",
    }
    real_import = builtins.__import__

    def guarded_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name in forbidden or (level and name in forbidden_relative):
            raise AssertionError(f"production imported excluded module {name}")
        return real_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", guarded_import)
    world = open_world(tmp_path / "world.db")
    try:
        registry = base_registry(world, LocalBackend(str(tmp_path)))
    finally:
        close_world_if_owned(world)

    assert {tool.name for tool in registry.all()} == {
        "budget_status",
        "cidr_check",
        "find_controls",
        "format_money",
        "list_dir",
        "read_file",
        "redact",
        "repo_map",
        "semver_check",
    }
