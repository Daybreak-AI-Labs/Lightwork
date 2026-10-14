from __future__ import annotations

import sys
from types import SimpleNamespace

import pytest
from maverick import agent_trust, authority_policy
from maverick.agent_trust import TrustedAgent
from maverick.capability import Capability
from maverick_mcp.http_transport import _authorization_for_bearer
from maverick_mcp.server import (
    MCPServer,
    MCPStdioAuthorizationError,
    _resolve_stdio_authorization,
    _StdioAuthorization,
)


def _registry(monkeypatch, registry, *, enforced=False):
    monkeypatch.setattr(
        agent_trust,
        "load_trust_state",
        lambda: (enforced, registry),
    )


def test_mcp_strict_rejects_shared_bearer(monkeypatch):
    monkeypatch.setattr(authority_policy, "strict_authority", lambda: True)
    monkeypatch.setenv("MAVERICK_MCP_TOKEN", "shared-token")
    _registry(monkeypatch, {})
    assert _authorization_for_bearer("Bearer shared-token") is None


def test_mcp_strict_accepts_individual_agent_credential(monkeypatch):
    monkeypatch.setattr(authority_policy, "strict_authority", lambda: True)
    monkeypatch.setenv("MAVERICK_MCP_TOKEN", "shared-token")
    registry = {
        "alice": TrustedAgent(id="alice", mcp_token="alice-token"),
    }
    _registry(monkeypatch, registry)
    authorization = _authorization_for_bearer("Bearer alice-token")
    assert authorization is not None
    assert authorization.caller_identity == "alice"
    assert authorization.credential_kind == "agent"


def test_mcp_strict_rejects_synthetic_registry_principal(monkeypatch):
    monkeypatch.setattr(authority_policy, "strict_authority", lambda: True)
    registry = {
        "mcp": TrustedAgent(id="mcp", mcp_token="surface-token"),
    }
    _registry(monkeypatch, registry)
    assert _authorization_for_bearer("Bearer surface-token") is None


def test_mcp_community_compatibility_keeps_shared_bearer(monkeypatch):
    monkeypatch.setattr(authority_policy, "strict_authority", lambda: False)
    monkeypatch.setenv("MAVERICK_MCP_TOKEN", "shared-token")
    _registry(monkeypatch, {})
    authorization = _authorization_for_bearer("Bearer shared-token")
    assert authorization is not None
    assert authorization.credential_kind == "shared"


def test_strict_stdio_resolves_identity_and_capability_from_one_snapshot(monkeypatch):
    from maverick import config

    snapshot = {
        "mcp": {"stdio_principal": "alice"},
        "agent_trust": {"agents": [{"id": "alice"}]},
    }
    registry = {
        "alice": TrustedAgent(
            id="alice",
            direction="inbound",
            allow_tools=frozenset({"maverick_status"}),
        ),
    }
    calls = {"config": 0, "registry": 0, "errors": 0}

    def load_config():
        calls["config"] += 1
        return snapshot

    def source_errors():
        calls["errors"] += 1
        return {}

    def load_registry(received):
        calls["registry"] += 1
        assert received is snapshot
        return registry

    monkeypatch.setattr(authority_policy, "strict_authority", lambda: True)
    monkeypatch.setattr(config, "load_config", load_config)
    monkeypatch.setattr(config, "config_source_errors", source_errors)
    monkeypatch.setattr(agent_trust, "load_registry", load_registry)

    authorization = _resolve_stdio_authorization()

    assert authorization is not None
    assert authorization.caller_identity == "alice"
    assert authorization.trust_principal == "alice"
    assert authorization.task_owner == "stdio:alice"
    assert authorization.capability.principal == "agent:alice"
    assert authorization.capability.permits("maverick_status")
    assert not authorization.capability.permits("maverick_start")
    assert calls == {"config": 1, "registry": 1, "errors": 1}


@pytest.mark.parametrize("principal", [None, "", "mcp", "shared", 17])
def test_strict_stdio_refuses_missing_or_synthetic_principal(monkeypatch, principal):
    from maverick import config

    mcp = {} if principal is None else {"stdio_principal": principal}
    monkeypatch.setattr(authority_policy, "strict_authority", lambda: True)
    monkeypatch.setattr(config, "load_config", lambda: {"mcp": mcp})
    monkeypatch.setattr(config, "config_source_errors", dict)

    with pytest.raises(MCPStdioAuthorizationError):
        _resolve_stdio_authorization()


@pytest.mark.parametrize(
    "registered",
    [
        None,
        TrustedAgent(id="alice", direction="outbound"),
        TrustedAgent(id="alice", revoked=True),
        TrustedAgent(id="alice", expires_at=1.0),
    ],
)
def test_strict_stdio_refuses_unregistered_or_inactive_principal(
    monkeypatch,
    registered,
):
    from maverick import config

    snapshot = {"mcp": {"stdio_principal": "alice"}}
    registry = {} if registered is None else {"alice": registered}
    monkeypatch.setattr(authority_policy, "strict_authority", lambda: True)
    monkeypatch.setattr(config, "load_config", lambda: snapshot)
    monkeypatch.setattr(config, "config_source_errors", dict)
    monkeypatch.setattr(agent_trust, "load_registry", lambda received: registry)

    with pytest.raises(MCPStdioAuthorizationError):
        _resolve_stdio_authorization()


def test_strict_stdio_fails_before_reading_client_input(monkeypatch):
    from maverick import config

    class _UnreadableStdin:
        def readline(self):
            pytest.fail("strict stdio read client input before authority admission")

    monkeypatch.setattr(authority_policy, "strict_authority", lambda: True)
    monkeypatch.setattr(config, "load_config", dict)
    monkeypatch.setattr(config, "config_source_errors", dict)
    monkeypatch.setattr(sys, "stdin", _UnreadableStdin())
    server = MCPServer()

    with pytest.raises(MCPStdioAuthorizationError):
        server.run()

    assert server._stdio is False
    assert server._tasks_enabled is False


def test_compat_stdio_does_not_require_or_read_principal_config(monkeypatch):
    monkeypatch.setattr(authority_policy, "strict_authority", lambda: False)
    monkeypatch.setattr(
        "maverick.config.load_config",
        lambda: pytest.fail("compat stdio unexpectedly read strict principal config"),
    )

    assert _resolve_stdio_authorization() is None


def test_strict_stdio_propagates_authority_to_every_dispatch_handler(monkeypatch):
    from maverick import fleet_memory

    capability = Capability(
        principal="agent:alice",
        allow_tools=frozenset({"maverick_status"}),
    )
    server = MCPServer()
    server._stdio_authorization = _StdioAuthorization(
        caller_identity="alice",
        trust_principal="alice",
        capability=capability,
        task_owner="stdio:alice",
    )
    monkeypatch.setattr(server, "_send_result", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(server, "_flush_resource_updates", lambda: None)

    methods = {
        "tools/list": "handle_tools_list",
        "tools/call": "handle_tools_call",
        "resources/list": "handle_resources_list",
        "resources/read": "handle_resources_read",
        "resources/subscribe": "handle_resources_subscribe",
        "resources/unsubscribe": "handle_resources_unsubscribe",
        "prompts/list": "handle_prompts_list",
        "prompts/get": "handle_prompts_get",
        "tasks/get": "handle_tasks_get",
        "tasks/result": "handle_tasks_result",
        "tasks/cancel": "handle_tasks_cancel",
        "tasks/list": "handle_tasks_list",
    }
    seen = {}

    def handler_for(method):
        def handler(_params, **kwargs):
            seen[method] = (kwargs, fleet_memory._caller.get())
            return {}

        return handler

    for method, handler_name in methods.items():
        monkeypatch.setattr(server, handler_name, handler_for(method))
        server._dispatch_stdio_message(method, 1, {}, False)

    assert set(seen) == set(methods)
    for method, (kwargs, bound_caller) in seen.items():
        assert bound_caller == "alice"
        assert kwargs["caller_identity"] == "alice"
        assert kwargs["caller_trust_principal"] == "alice"
        assert kwargs["capability"] is capability
        if method == "tools/call" or method.startswith("tasks/"):
            assert kwargs["task_owner"] == "stdio:alice"
    assert seen["tools/call"][0]["task_quota_owner"] == "stdio:alice"
    assert seen["tools/call"][0]["caller_credential_kind"] == "stdio"
    assert seen["tasks/result"][0]["caller_credential_kind"] == "stdio"


def test_strict_stdio_task_carries_frozen_authority_and_rechecks_narrowly(monkeypatch):
    frozen = Capability(
        principal="agent:alice",
        allow_tools=frozenset({"maverick_start", "maverick_resume"}),
    )
    captured = {}

    class _TaskStore:
        def create(
            self,
            _name,
            _arguments,
            _task_param,
            *,
            owner,
            quota_owner,
            context,
        ):
            captured.update(
                owner=owner,
                quota_owner=quota_owner,
                context=context,
            )
            return SimpleNamespace(create_result={"taskId": "task-1"})

    server = MCPServer()
    server._tasks_enabled = True
    server._tasks = _TaskStore()
    monkeypatch.setattr(
        server,
        "_required_shield_input_block_or_none",
        lambda *_args, **_kwargs: None,
    )

    result = server.handle_tools_call(
        {
            "name": "maverick_start",
            "arguments": {"title": "bounded"},
            "task": {},
        },
        task_owner="stdio:alice",
        task_quota_owner="stdio:alice",
        caller_identity="alice",
        caller_trust_principal="alice",
        caller_credential_kind="stdio",
        capability=frozen,
    )

    context = captured["context"]
    assert result == {"task": {"taskId": "task-1"}}
    assert captured["owner"] == captured["quota_owner"] == "stdio:alice"
    assert context.caller_identity == "alice"
    assert context.trust_principal == "alice"
    assert context.credential_kind == "stdio"
    assert context.capability is frozen

    current = TrustedAgent(
        id="alice",
        allow_tools=frozenset({"maverick_start"}),
    )
    monkeypatch.setattr(
        agent_trust,
        "load_trust_state",
        lambda: (True, {"alice": current}),
    )
    narrowed = server._refresh_task_capability(context)
    assert narrowed.permits("maverick_start")
    assert not narrowed.permits("maverick_resume")
