from __future__ import annotations

import contextlib
import hashlib
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest
from maverick.effect_protocol import (
    ClaimOutcome,
    CompensationState,
    CompensationUnavailable,
    EffectIdentity,
    EffectKind,
    EffectSpec,
    EffectState,
    EffectStore,
    IdempotencyMode,
    InvalidTransition,
    ProviderCompensationStatus,
    ProviderReceipt,
    ProviderStatus,
    bind_effect_context,
    bind_idempotency_key,
    compensate_effect,
    current_effect_context,
    current_idempotency_key,
    reconcile_compensation,
    reconcile_effect,
    request_digest,
)
from maverick.governed_tools import wrap_connector_tool
from maverick.tools import Tool, ToolRegistry
from maverick.tools._rest_connector import make_rest_tool


class _SharedEffectStore(EffectStore):
    """Explicit shared test double; strict production may not inject SQLite."""

    @property
    def backend(self) -> str:
        return "test-shared"

    @property
    def shared(self) -> bool:
        return True


@pytest.fixture(autouse=True)
def _effect_storage_policy(monkeypatch):
    # Strict governed writes require encrypted durable state.  Individual
    # non-strict storage regressions explicitly disable it where needed.
    monkeypatch.setenv("MAVERICK_ENCRYPT_AT_REST", "1")
    monkeypatch.setenv("MAVERICK_ENCRYPTION_KEY", "10" * 32)


def _identity() -> EffectIdentity:
    return EffectIdentity(
        tenant="tenant-a",
        principal="user:alice",
        run_id="run-7",
        node_id="node-send",
        connector="Acme",
        operation="POST",
    )


def _digest(body: str = "same") -> str:
    return request_digest({"body": body})


def test_logical_key_uses_stable_authority_tuple_not_request_digest():
    first = _identity()
    second = EffectIdentity(
        tenant="tenant-a",
        principal="user:alice",
        run_id="run-7",
        node_id="node-send",
        connector="acme",
        operation="post",
    )

    assert first.logical_key == second.logical_key
    assert len(first.logical_key) == 64
    assert _digest("one") != _digest("two")


def test_request_digest_requires_canonical_json_and_is_order_independent():
    assert request_digest({"a": 1, "b": [2, 3]}) == request_digest(
        {"b": [2, 3], "a": 1}
    )
    with pytest.raises(ValueError, match="canonical JSON"):
        request_digest({"not_json": object()})
    with pytest.raises(ValueError, match="canonical JSON"):
        request_digest({"not_finite": float("nan")})


def test_concurrent_claim_has_exactly_one_owner_and_detects_digest_conflict(tmp_path):
    path = tmp_path / "effects.sqlite3"
    identity = _identity()

    def claim():
        return EffectStore(path).claim(identity, _digest()).outcome

    with ThreadPoolExecutor(max_workers=8) as pool:
        outcomes = list(pool.map(lambda _index: claim(), range(8)))

    assert outcomes.count(ClaimOutcome.OWNER) == 1
    assert outcomes.count(ClaimOutcome.IN_PROGRESS) == 7
    conflict = EffectStore(path).claim(identity, _digest("different"))
    assert conflict.outcome is ClaimOutcome.CONFLICT


def test_cas_state_machine_replays_committed_result_without_reopening(tmp_path):
    store = EffectStore(tmp_path / "effects.sqlite3")
    claim = store.claim(_identity(), _digest())

    assert claim.outcome is ClaimOutcome.OWNER
    assert store.mark_sent(claim).state is EffectState.SENT
    committed = store.commit(
        claim,
        "created:42",
        ProviderReceipt(provider_id="42", status="created"),
    )
    assert committed.state is EffectState.COMMITTED
    assert committed.receipt == ProviderReceipt(provider_id="42", status="created")

    replay = store.claim(_identity(), _digest())
    assert replay.outcome is ClaimOutcome.CACHED
    assert replay.record.result == "committed result content withheld; bytes=10"
    assert replay.record.result_digest == hashlib.sha256(b"created:42").hexdigest()
    with pytest.raises(InvalidTransition):
        store.commit(claim, "duplicate")


def test_effect_results_are_encrypted_and_receipt_metadata_is_redacted_at_rest(
    tmp_path,
    monkeypatch,
):
    path = tmp_path / "effects.sqlite3"
    monkeypatch.setenv("MAVERICK_ENCRYPT_AT_REST", "1")
    monkeypatch.setenv("MAVERICK_ENCRYPTION_KEY", "11" * 32)
    store = EffectStore(path)
    claim = store.claim(_identity(), _digest())
    store.mark_sent(claim)
    committed = store.commit(
        claim,
        "provider response contains customer-private-value",
        ProviderReceipt(
            provider_id="remote-42",
            status="created",
            metadata={
                "access_token": "must-not-persist",
                "safe": "bounded status",
            },
        ),
    )

    row = sqlite3.connect(str(path)).execute(
        "SELECT result, result_digest, receipt_json FROM effect_transactions"
    ).fetchone()
    assert row is not None
    serialized = json.dumps(row)
    assert "customer-private-value" not in serialized
    assert "must-not-persist" not in serialized
    assert row[0].startswith("MVKAR1:")
    assert row[1] == hashlib.sha256(
        b"provider response contains customer-private-value"
    ).hexdigest()
    assert row[2].startswith("MVKAR1:")
    assert committed.result == "committed result content withheld; bytes=49"
    assert committed.result_digest == row[1]
    assert committed.receipt is not None
    assert committed.receipt.metadata["access_token"] == "[REDACTED:sensitive-key]"


def test_indeterminate_effect_uses_typed_status_resolver(tmp_path):
    store = EffectStore(tmp_path / "effects.sqlite3")
    claim = store.claim(_identity(), _digest())
    store.mark_sent(claim)
    store.mark_indeterminate(claim, "TimeoutError")
    seen = []
    spec = EffectSpec(
        connector="acme",
        status_resolver=lambda record: (
            seen.append(record.state)
            or ProviderStatus(
                EffectState.COMMITTED,
                ProviderReceipt(provider_id="remote-9", status="complete"),
                "created:remote-9",
            )
        ),
    )

    record = reconcile_effect(store, _identity(), spec)

    assert seen == [EffectState.INDETERMINATE]
    assert record.state is EffectState.COMMITTED
    assert record.result == "committed result content withheld; bytes=16"
    assert record.result_digest == hashlib.sha256(b"created:remote-9").hexdigest()
    assert record.receipt and record.receipt.provider_id == "remote-9"


def test_compensation_is_claimed_once_and_requires_typed_receipt(tmp_path):
    store = EffectStore(tmp_path / "effects.sqlite3")
    claim = store.claim(_identity(), _digest())
    store.mark_sent(claim)
    original_receipt = ProviderReceipt(provider_id="create-1", status="created")
    store.commit(claim, "created", original_receipt)
    calls = []
    spec = EffectSpec(
        connector="acme",
        compensator=lambda record: (
            calls.append(record.logical_key)
            or ProviderReceipt(provider_id="undo-1", status="reverted")
        ),
    )

    compensated = compensate_effect(store, _identity(), spec)

    assert compensated.state is EffectState.COMPENSATED
    assert compensated.compensation_state is CompensationState.COMPENSATED
    assert compensated.receipt == original_receipt
    assert compensated.compensation_receipt == ProviderReceipt(
        provider_id="undo-1",
        status="reverted",
    )
    assert calls == [_identity().logical_key]
    with pytest.raises(CompensationUnavailable):
        compensate_effect(store, _identity(), spec)


def test_compensator_exception_is_durably_indeterminate_until_reconciled(tmp_path):
    path = tmp_path / "effects.sqlite3"
    store = EffectStore(path)
    claim = store.claim(_identity(), _digest())
    store.mark_sent(claim)
    store.commit(claim, "created")
    calls = []

    def ambiguous_compensator(record):
        calls.append(record.compensation_state)
        raise TimeoutError("provider may have reverted before timing out")

    spec = EffectSpec(
        connector="acme",
        compensator=ambiguous_compensator,
        compensation_status_resolver=lambda record: ProviderCompensationStatus(
            CompensationState.NOT_STARTED,
            result=f"provider proved no undo for {record.logical_key}",
        ),
    )

    with pytest.raises(TimeoutError):
        compensate_effect(store, _identity(), spec)

    quarantined = EffectStore(path).get(_identity())
    assert quarantined is not None
    assert quarantined.state is EffectState.COMMITTED
    assert quarantined.compensation_state is CompensationState.INDETERMINATE
    assert quarantined.compensation_result == (
        "compensation-indeterminate result content withheld; bytes=12"
    )
    assert quarantined.compensation_result_digest == hashlib.sha256(
        b"TimeoutError"
    ).hexdigest()
    with pytest.raises(CompensationUnavailable):
        compensate_effect(store, _identity(), spec)
    assert calls == [CompensationState.COMPENSATING]

    with pytest.raises(CompensationUnavailable, match="not terminal proof"):
        reconcile_compensation(store, _identity(), spec)
    still_quarantined = store.get(_identity())
    assert still_quarantined is not None
    assert still_quarantined.state is EffectState.COMMITTED
    assert still_quarantined.compensation_state is CompensationState.INDETERMINATE
    with pytest.raises(CompensationUnavailable):
        compensate_effect(store, _identity(), spec)
    assert calls == [CompensationState.COMPENSATING]


def test_crash_after_compensation_claim_requires_stale_typed_reconciliation(tmp_path):
    path = tmp_path / "effects.sqlite3"
    store = EffectStore(path)
    claim = store.claim(_identity(), _digest())
    store.mark_sent(claim)
    committed = store.commit(
        claim,
        "created",
        ProviderReceipt(provider_id="create-1", status="created"),
    )

    _token, claimed = store._claim_compensation(committed)
    assert claimed.compensation_state is CompensationState.COMPENSATING

    recovered_store = EffectStore(path)
    spec = EffectSpec(
        connector="acme",
        compensation_status_resolver=lambda _record: ProviderCompensationStatus(
            CompensationState.COMPENSATED,
            ProviderReceipt(provider_id="undo-remote", status="reverted"),
            "provider-confirmed",
        ),
    )
    with pytest.raises(CompensationUnavailable, match="live owner"):
        reconcile_compensation(recovered_store, _identity(), spec)

    recovered = reconcile_compensation(
        recovered_store,
        _identity(),
        spec,
        now=claimed.updated_at + 301,
    )
    assert recovered.state is EffectState.COMPENSATED
    assert recovered.compensation_state is CompensationState.COMPENSATED
    assert recovered.receipt == ProviderReceipt(
        provider_id="create-1",
        status="created",
    )
    assert recovered.compensation_receipt == ProviderReceipt(
        provider_id="undo-remote",
        status="reverted",
    )


def test_retry_safe_write_requires_certified_native_idempotency():
    with pytest.raises(ValueError, match="native idempotency"):
        EffectSpec(
            connector="acme",
            idempotency=IdempotencyMode.KEYED,
            retry_safe=True,
        )
    assert EffectSpec(
        connector="acme",
        idempotency=IdempotencyMode.NATIVE,
        retry_safe=True,
    ).retry_safe is True


def test_governed_effect_replays_same_digest_and_refuses_changed_digest(
    tmp_path,
    monkeypatch,
):
    import maverick.governed_actions as actions
    import maverick.safety.consent as consent

    monkeypatch.setattr(
        consent,
        "require_consent",
        lambda *_args, **_kwargs: SimpleNamespace(
            granted=True,
            source="dashboard",
            actor="user:reviewer",
        ),
    )
    monkeypatch.setattr(actions, "record_tool_lineage", lambda *_a, **_kw: True)
    calls = []

    def execute(args):
        calls.append((dict(args), current_idempotency_key()))
        return f"created:{args['body']['name']}"

    tool = Tool(
        name="acme",
        description="Acme",
        input_schema={
            "type": "object",
            "properties": {
                "op": {"type": "string"},
                "path": {"type": "string"},
                "body": {"type": "object"},
                "confirm": {"type": "boolean"},
            },
        },
        fn=execute,
        effect_spec=EffectSpec(
            connector="acme",
            idempotency=IdempotencyMode.KEYED,
        ),
    )
    store = _SharedEffectStore(tmp_path / "effects.sqlite3")
    wrapped = wrap_connector_tool(
        tool,
        actor="user:alice",
        enterprise=True,
        effect_store=store,
    )
    request = {"op": "post", "path": "/accounts", "body": {"name": "one"}}
    with bind_effect_context(
        tenant="tenant-a",
        principal="user:alice",
        run_id="run-7",
        node_id="node-send",
    ):
        first = wrapped.fn(request)
        replay = wrapped.fn(request)
        conflict = wrapped.fn({
            "op": "post",
            "path": "/accounts",
            "body": {"name": "two"},
        })

    assert first == "created:one"
    assert replay.startswith("COMMITTED (governed)")
    assert "raw provider response is not retained" in replay
    assert hashlib.sha256(first.encode()).hexdigest() in replay
    assert len(calls) == 1
    assert calls[0][1] == _identity().logical_key
    assert conflict.startswith("REFUSED (governed)")
    assert "different request digest" in conflict


def test_strict_annotated_effect_requires_stable_run_identity(tmp_path, monkeypatch):
    import maverick.governed_actions as actions
    import maverick.safety.consent as consent

    monkeypatch.setattr(
        consent,
        "require_consent",
        lambda *_args, **_kwargs: SimpleNamespace(
            granted=True, source="dashboard", actor="user:reviewer",
        ),
    )
    monkeypatch.setattr(actions, "record_tool_lineage", lambda *_a, **_kw: True)
    calls = []
    wrapped = wrap_connector_tool(
        Tool(
            name="acme",
            description="Acme",
            input_schema={
                "type": "object",
                "properties": {"op": {"type": "string"}},
            },
            fn=lambda args: calls.append(args) or "ok",
            effect_spec=EffectSpec(connector="acme"),
        ),
        actor="user:alice",
        enterprise=True,
        effect_store=_SharedEffectStore(tmp_path / "effects.sqlite3"),
    )

    result = wrapped.fn({"op": "post"})

    assert result.startswith("ERROR (governed)")
    assert calls == []


def test_metadata_only_effect_does_not_receive_undeclared_confirm(tmp_path, monkeypatch):
    import maverick.governed_actions as actions
    import maverick.safety.consent as consent

    monkeypatch.setattr(
        consent,
        "require_consent",
        lambda *_args, **_kwargs: SimpleNamespace(
            granted=True, source="dashboard", actor="user:reviewer",
        ),
    )
    monkeypatch.setattr(actions, "record_tool_lineage", lambda *_a, **_kw: True)
    seen = []
    wrapped = wrap_connector_tool(
        Tool(
            name="publisher",
            description="Publisher",
            input_schema={
                "type": "object",
                "properties": {"document": {"type": "string"}},
                "additionalProperties": False,
            },
            fn=lambda args: seen.append(dict(args)) or "published",
            effect_spec=EffectSpec(connector="publisher"),
        ),
        actor="user:alice",
        enterprise=True,
        effect_store=_SharedEffectStore(tmp_path / "effects.sqlite3"),
    )

    with bind_effect_context(
        tenant="tenant-a",
        principal="user:alice",
        run_id="run-publish",
        node_id="node-publish",
    ):
        result = wrapped.fn({"document": "release"})

    assert result == "published"
    assert seen == [{"document": "release"}]


def test_generic_rest_overrides_any_preexisting_idempotency_header(
    monkeypatch,
):
    import maverick.tools._ssrf as ssrf

    captured = {}

    class Response:
        status_code = 200
        text = ""

        @staticmethod
        def json():
            return {"ok": True}

    class Client:
        def request(self, method, url, **kwargs):
            captured.update({"method": method, "url": url, **kwargs})
            return Response()

    @contextlib.contextmanager
    def safe_client(_url, **_kwargs):
        yield Client()

    monkeypatch.setattr(ssrf, "safe_client", safe_client)
    monkeypatch.setenv("ACME_URL", "https://api.example.com")
    monkeypatch.setenv("ACME_TOKEN", "secret-token")
    monkeypatch.setenv("ACME_UNTRUSTED_IDEM", "model-or-env-selected")
    tool = make_rest_tool(
        name="acme",
        base_url_env="ACME_URL",
        token_env="ACME_TOKEN",
        description="Acme",
        extra_headers_env={"idempotency-key": "ACME_UNTRUSTED_IDEM"},
    )
    key = "a" * 64

    with bind_idempotency_key(key):
        result = tool.fn({"op": "post", "path": "/records", "confirm": True})

    assert result == '{"ok": true}'
    idem_headers = {
        name: value
        for name, value in captured["headers"].items()
        if name.casefold() == "idempotency-key"
    }
    assert idem_headers == {"Idempotency-Key": key}
    assert tool.effect_spec is not None
    assert tool.effect_spec.kind is EffectKind.WRITE
    assert tool.effect_spec.idempotency is IdempotencyMode.KEYED
    assert "effect_spec" not in tool.to_anthropic()


@pytest.mark.parametrize("status_code", [202, 302])
def test_generic_rest_ambiguous_http_status_is_durably_indeterminate(
    tmp_path,
    monkeypatch,
    status_code,
):
    import maverick.governed_actions as actions
    import maverick.safety.consent as consent
    import maverick.tools._ssrf as ssrf

    calls = []

    class Response:
        text = "provider body must not decide the state"

        def __init__(self, code):
            self.status_code = code

        @staticmethod
        def json():
            return {"id": "remote-42", "status": "created"}

    class Client:
        def request(self, method, url, **kwargs):
            calls.append((method, url, kwargs))
            return Response(status_code)

    @contextlib.contextmanager
    def safe_client(_url, **_kwargs):
        yield Client()

    monkeypatch.setattr(ssrf, "safe_client", safe_client)
    monkeypatch.setattr(
        consent,
        "require_consent",
        lambda *_a, **_kw: SimpleNamespace(
            granted=True, source="dashboard", actor="user:reviewer",
        ),
    )
    monkeypatch.setattr(actions, "record_tool_lineage", lambda *_a, **_kw: True)
    monkeypatch.setenv("ACME_URL", "https://api.example.com")
    monkeypatch.setenv("ACME_TOKEN", "secret-token")
    store = _SharedEffectStore(tmp_path / "effects.sqlite3")
    wrapped = wrap_connector_tool(
        make_rest_tool(
            name="acme",
            base_url_env="ACME_URL",
            token_env="ACME_TOKEN",
            description="Acme",
        ),
        actor="user:alice",
        enterprise=True,
        effect_store=store,
    )

    with bind_effect_context(
        tenant="tenant-a",
        principal="user:alice",
        run_id="run-http-status",
        node_id=f"node-{status_code}",
    ):
        result = wrapped.fn({"op": "post", "path": "/records"})

    identity = EffectIdentity(
        tenant="tenant-a",
        principal="user:alice",
        run_id="run-http-status",
        node_id=f"node-{status_code}",
        connector="acme",
        operation="post",
    )
    record = store.get(identity)
    assert len(calls) == 1
    assert result.startswith("INDETERMINATE (governed)")
    assert record is not None
    assert record.state is EffectState.INDETERMINATE


def test_secret_bearing_result_is_not_retained_when_encryption_is_disabled(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setenv("MAVERICK_ENCRYPT_AT_REST", "0")
    jwt = (
        "eyJhbGciOiJIUzI1NiJ9."
        "eyJzdWIiOiJ1c2VyLTEiLCJyb2xlIjoiYWRtaW4ifQ."
        "uUFV4LZSWr2N5Tq7qD3AtL5cH8XzP9xwKJ9kL8W7aQ"  # pragma: allowlist secret
    )
    private_key = (
        "-----BEGIN PRIVATE KEY-----\n"  # pragma: allowlist secret
        "cHJpdmF0ZS1rZXktbXVzdC1ub3QtcGVyc2lzdA==\n"  # pragma: allowlist secret
        "-----END PRIVATE KEY-----"
    )
    raw = json.dumps({"access_token": jwt, "private_key": private_key})
    path = tmp_path / "effects.sqlite3"
    store = EffectStore(path)
    claim = store.claim(_identity(), _digest())
    store.mark_sent(claim)
    record = store.commit(claim, raw)

    row = sqlite3.connect(str(path)).execute(
        "SELECT result, result_digest FROM effect_transactions"
    ).fetchone()
    assert row is not None
    serialized = json.dumps(row)
    assert jwt not in serialized
    assert "BEGIN PRIVATE KEY" not in serialized  # pragma: allowlist secret
    assert "cHJpdmF0ZS1rZXk" not in serialized
    assert not row[0].startswith("MVKAR1:")
    assert row[0] == record.result
    assert row[1] == record.result_digest == hashlib.sha256(raw.encode()).hexdigest()


def test_strict_store_scrubs_and_seals_legacy_result_without_losing_receipt(
    tmp_path,
    monkeypatch,
):
    path = tmp_path / "effects.sqlite3"
    monkeypatch.setenv("MAVERICK_ENCRYPT_AT_REST", "0")
    legacy_raw = '{"provider_body":"customer-private-value"}'
    store = EffectStore(path)
    claim = store.claim(_identity(), _digest())
    store.mark_sent(claim)
    store.commit(
        claim,
        "initial",
        ProviderReceipt(provider_id="remote-7", status="created"),
    )
    # Simulate the pre-hardening schema after its digest column was added.
    with sqlite3.connect(str(path)) as conn:
        conn.execute(
            "UPDATE effect_transactions SET result = ?, result_digest = ''",
            (legacy_raw,),
        )

    monkeypatch.setenv("MAVERICK_ENCRYPT_AT_REST", "1")
    strict_store = EffectStore(path, require_encryption=True)
    record = strict_store.get(_identity())

    assert record is not None
    assert record.state is EffectState.COMMITTED
    assert record.result == "committed result content withheld; bytes=42"
    assert record.result_digest == hashlib.sha256(legacy_raw.encode()).hexdigest()
    assert record.receipt == ProviderReceipt(provider_id="remote-7", status="created")
    row = sqlite3.connect(str(path)).execute(
        "SELECT result, receipt_json FROM effect_transactions"
    ).fetchone()
    assert row is not None
    assert row[0].startswith("MVKAR1:")
    assert row[1].startswith("MVKAR1:")
    assert "customer-private-value" not in json.dumps(row)


def test_tools_without_effect_metadata_remain_backward_compatible():
    calls = []
    tool = Tool(
        name="legacy",
        description="legacy",
        input_schema={"type": "object"},
        fn=lambda args: calls.append(args) or "ok",
    )

    assert tool.effect_spec is None
    assert tool.fn({"value": 1}) == "ok"
    assert calls == [{"value": 1}]


def test_rate_limit_wrapper_preserves_effect_contract():
    from maverick.safety.rate_limiter import apply_to_registry

    spec = EffectSpec(connector="acme")
    registry = ToolRegistry()
    registry.register(Tool(
        name="acme",
        description="Acme",
        input_schema={"type": "object"},
        fn=lambda _args: "ok",
        effect_spec=spec,
    ))

    assert apply_to_registry(registry, limits={"acme": (1, 60.0)}) == 1
    assert registry.get("acme").effect_spec is spec


def test_flow_action_binds_stable_run_and_node_context():
    from maverick.flow.execution import _call_action

    seen = []

    def runner(tool, params, data):
        seen.append((tool, params, data, current_effect_context()))
        return "ok", 1.0

    result = _call_action(
        runner,
        "acme",
        {"op": "post"},
        {"input": 1},
        run_id="flow-run-1",
        node_id="publish",
    )

    assert result == ("ok", 1.0)
    assert seen[0][3].run_id == "flow-run-1"
    assert seen[0][3].node_id == "publish"
    assert current_effect_context().run_id == ""


def test_flow_foreach_effect_identity_is_distinct_per_iteration_and_stable(
    tmp_path,
    monkeypatch,
):
    from maverick.flow import execution
    from maverick.flow.ir import NODE_ACTION, NODE_FOREACH, Flow, FlowNode

    monkeypatch.setattr(
        "maverick.paths.data_dir",
        lambda *parts, **_kwargs: tmp_path.joinpath(*parts),
    )
    body = Flow(
        id="publish-body",
        name="publish body",
        start="publish",
        nodes={
            "publish": FlowNode(
                id="publish",
                kind=NODE_ACTION,
                tool="web_search",
                params={"query": "{{item}}"},
            ),
        },
    )
    flow = Flow(
        id="publish-batch",
        name="publish batch",
        start="each",
        nodes={
            "each": FlowNode(
                id="each",
                kind=NODE_FOREACH,
                items="items",
                body=body,
            ),
        },
    )
    seen: list[tuple[str, str]] = []

    def action_runner(_tool, params, _data):
        context = current_effect_context()
        seen.append((context.node_id, params["query"]))
        return "ok", 1.0

    for run_id in ("batch-run-a", "batch-run-b"):
        run = execution.execute(
            flow,
            run_id=run_id,
            agent_runner=lambda *_args: ("ok", 1.0),
            action_runner=action_runner,
            data={"items": ["one", "two", "three"]},
            record_outcomes=False,
        )
        assert run.status == "completed"

    first, second = seen[:3], seen[3:]
    assert [query for _node_id, query in first] == ["one", "two", "three"]
    assert len({node_id for node_id, _query in first}) == 3
    assert first == second
    assert all(node_id.startswith("publish@") for node_id, _query in first)
