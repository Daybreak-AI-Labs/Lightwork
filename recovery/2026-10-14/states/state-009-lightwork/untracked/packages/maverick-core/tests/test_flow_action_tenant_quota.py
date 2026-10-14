"""Tenant-quota regressions for direct flow action dispatch."""
from __future__ import annotations

from types import SimpleNamespace

from maverick.connections import bind_principal
from maverick.failure_taxonomy import FailureKind
from maverick.flow import execution
from maverick.flow.ir import NODE_ACTION, Flow, FlowNode
from maverick.paths import reset_tenant, set_tenant
from maverick.tenant_quota_authority import (
    TenantQuotaAuthorityUnavailable,
    TenantQuotaReservation,
)


class _Registry:
    def __init__(self, *results: object) -> None:
        self.results = list(results or ("ok",))
        self.calls: list[tuple[str, dict]] = []

    def all(self):
        return [SimpleNamespace(name="web_search")]

    async def run(self, tool: str, params: dict):
        self.calls.append((tool, dict(params)))
        result = self.results.pop(0)
        if isinstance(result, BaseException):
            raise result
        return result


class _Quota:
    def __init__(self) -> None:
        self.reserves: list[dict] = []
        self.settles: list[dict] = []
        self.releases: list[str] = []
        self.reserve_error: Exception | None = None
        self.settle_error: Exception | None = None
        self.state = "ACTIVE"

    def reserve(
        self,
        tenant_id,
        principal_id,
        resources,
        request_id,
        ttl,
        *,
        world=None,
        **_kwargs,
    ):
        if self.reserve_error is not None:
            raise self.reserve_error
        self.reserves.append({
            "tenant_id": tenant_id,
            "principal_id": principal_id,
            "resources": dict(resources),
            "request_id": request_id,
            "ttl": ttl,
            "world": world,
        })
        return TenantQuotaReservation(
            tenant_id=tenant_id,
            principal_id=principal_id,
            request_id=request_id,
            resources={"tool_calls": 1},
            period_key="2026-08-04",
            expires_at=1_000_000.0,
            policy_sha256="policy",
            state=self.state,
        )

    def settle(self, reservation, resources, *, world=None, **_kwargs):
        self.settles.append({
            "request_id": reservation.request_id,
            "resources": dict(resources),
            "world": world,
        })
        if self.settle_error is not None:
            raise self.settle_error
        return {"state": "SETTLED"}

    def release(self, reservation, *, world=None, **_kwargs):
        self.releases.append(reservation.request_id)
        return {"state": "RELEASED", "world": world}


def _install(monkeypatch, registry: _Registry, quota: _Quota) -> None:
    monkeypatch.setattr("maverick.tools.base_registry", lambda *a, **k: registry)
    monkeypatch.setattr("maverick.tool_authz.authorize", lambda *a, **k: None)
    monkeypatch.setattr("maverick.tool_authz.audit_result", lambda *a, **k: True)
    monkeypatch.setattr(
        "maverick.tenant_quota_authority.authority_required", lambda: True,
    )
    monkeypatch.setattr("maverick.tenant_quota_authority.reserve", quota.reserve)
    monkeypatch.setattr("maverick.tenant_quota_authority.settle", quota.settle)
    monkeypatch.setattr("maverick.tenant_quota_authority.release", quota.release)
    monkeypatch.setattr(execution, "record_state_audit", lambda *a, **k: True)
    monkeypatch.setattr(execution, "record_state_telemetry", lambda *a, **k: None)


def _flow(*, retries: int = 0, timeout: float = 0.0) -> Flow:
    return Flow(
        id="quota-flow",
        name="quota flow",
        start="action",
        nodes={
            "action": FlowNode(
                id="action",
                kind=NODE_ACTION,
                tool="web_search",
                params={"query": "production"},
                output="result",
                retries=retries,
                timeout=timeout,
            ),
        },
    )


def _execute(flow: Flow, registry: _Registry, *, run_id: str, world: object):
    action_runner = execution.default_action_runner(
        world,
        sandbox=object(),
        user_id="alice",
    )
    tenant_token = set_tenant("tenant-a")
    try:
        with bind_principal("user:alice"):
            return execution.execute(
                flow,
                agent_runner=lambda *_args: ("unused", 1.0),
                action_runner=action_runner,
                run_id=run_id,
                record_outcomes=False,
            )
    finally:
        reset_tenant(tenant_token)


def test_action_reserves_and_settles_once_inside_timeout_worker(
    tmp_path, monkeypatch,
):
    monkeypatch.setattr(
        "maverick.paths.data_dir", lambda *p, **k: tmp_path.joinpath(*p),
    )
    registry = _Registry("done")
    quota = _Quota()
    _install(monkeypatch, registry, quota)
    world = object()

    run = _execute(
        _flow(timeout=1.0),
        registry,
        run_id="quota-timeout-run",
        world=world,
    )

    assert run.status == "completed"
    assert registry.calls == [("web_search", {"query": "production"})]
    assert len(quota.reserves) == len(quota.settles) == 1
    assert quota.releases == []
    admission = quota.reserves[0]
    assert admission["tenant_id"] == "tenant-a"
    assert admission["principal_id"] == "user:alice"
    assert admission["resources"] == {"tool_calls": 1}
    assert admission["request_id"].startswith("flow-action-v1:")
    assert admission["ttl"] == 301.0
    assert admission["world"] is world
    assert quota.settles[0]["request_id"] == admission["request_id"]
    assert quota.settles[0]["resources"] == {"tool_calls": 1}


def test_each_retry_attempt_gets_one_distinct_charge(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "maverick.paths.data_dir", lambda *p, **k: tmp_path.joinpath(*p),
    )
    registry = _Registry(ConnectionError("lost response"), "done")
    quota = _Quota()
    _install(monkeypatch, registry, quota)

    run = _execute(
        _flow(retries=1),
        registry,
        run_id="quota-retry-run",
        world=object(),
    )

    assert run.status == "completed"
    assert len(registry.calls) == 2
    assert len(quota.reserves) == len(quota.settles) == 2
    request_ids = [item["request_id"] for item in quota.reserves]
    assert len(set(request_ids)) == 2
    assert [item["request_id"] for item in quota.settles] == request_ids
    assert quota.releases == []


def test_failed_run_resume_uses_a_new_durable_action_segment(
    tmp_path, monkeypatch,
):
    monkeypatch.setattr(
        "maverick.paths.data_dir", lambda *p, **k: tmp_path.joinpath(*p),
    )
    registry = _Registry(ConnectionError("first attempt failed"), "done")
    quota = _Quota()
    _install(monkeypatch, registry, quota)
    world = object()
    flow = _flow()

    first = _execute(
        flow,
        registry,
        run_id="quota-resume-run",
        world=world,
    )
    assert first.status == "failed"

    action_runner = execution.default_action_runner(
        world,
        sandbox=object(),
        user_id="alice",
    )
    tenant_token = set_tenant("tenant-a")
    try:
        with bind_principal("user:alice"):
            resumed = execution.execute(
                flow,
                agent_runner=lambda *_args: ("unused", 1.0),
                action_runner=action_runner,
                resume_run_id=first.run_id,
                from_failure=True,
                record_outcomes=False,
            )
    finally:
        reset_tenant(tenant_token)

    assert resumed.status == "completed"
    assert len(quota.reserves) == len(quota.settles) == 2
    assert quota.reserves[0]["request_id"] != quota.reserves[1]["request_id"]


def test_settlement_failure_quarantines_without_action_retry(
    tmp_path, monkeypatch,
):
    monkeypatch.setattr(
        "maverick.paths.data_dir", lambda *p, **k: tmp_path.joinpath(*p),
    )
    registry = _Registry("remote side effect committed")
    quota = _Quota()
    quota.settle_error = TenantQuotaAuthorityUnavailable("database unavailable")
    _install(monkeypatch, registry, quota)

    run = _execute(
        _flow(retries=3),
        registry,
        run_id="quota-settle-failure-run",
        world=object(),
    )

    assert run.status == "indeterminate"
    assert run.failure is not None
    assert run.failure.kind is FailureKind.INDETERMINATE_EFFECT
    assert len(registry.calls) == 1
    assert len(quota.reserves) == len(quota.settles) == 1
    assert quota.releases == []


def test_admission_failure_never_reaches_registry(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "maverick.paths.data_dir", lambda *p, **k: tmp_path.joinpath(*p),
    )
    registry = _Registry("must not run")
    quota = _Quota()
    quota.reserve_error = TenantQuotaAuthorityUnavailable("database unavailable")
    _install(monkeypatch, registry, quota)

    run = _execute(
        _flow(retries=2),
        registry,
        run_id="quota-admission-failure-run",
        world=object(),
    )

    assert run.status == "failed"
    assert registry.calls == []
    assert quota.settles == []
    assert quota.releases == []


def test_action_timeout_cannot_outlive_quota_hold(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "maverick.paths.data_dir", lambda *p, **k: tmp_path.joinpath(*p),
    )
    registry = _Registry("must not run")
    quota = _Quota()
    _install(monkeypatch, registry, quota)

    run = _execute(
        _flow(timeout=7 * 24 * 60 * 60.0),
        registry,
        run_id="quota-timeout-horizon-run",
        world=object(),
    )

    assert run.status == "failed"
    assert registry.calls == []
    assert quota.reserves == []


def test_unused_reservation_is_released_when_stop_wins_before_dispatch(
    tmp_path, monkeypatch,
):
    from maverick.killswitch import Halted

    monkeypatch.setattr(
        "maverick.paths.data_dir", lambda *p, **k: tmp_path.joinpath(*p),
    )
    registry = _Registry("must not run")
    quota = _Quota()
    _install(monkeypatch, registry, quota)
    # The interpreter's admission checks pass; the helper's lazy import sees
    # this stop after quota admission and immediately before registry.run.
    monkeypatch.setattr("maverick.flow.runner.critical_check", lambda: None)

    def _halt():
        raise Halted("stopped")

    monkeypatch.setattr("maverick.killswitch.critical_check", _halt)

    run = _execute(
        _flow(),
        registry,
        run_id="quota-release-run",
        world=object(),
    )

    assert run.status == "failed"
    assert registry.calls == []
    assert quota.settles == []
    assert quota.releases == [quota.reserves[0]["request_id"]]
