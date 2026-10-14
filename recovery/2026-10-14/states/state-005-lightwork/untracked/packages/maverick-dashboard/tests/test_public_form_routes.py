"""Deployment-global public-form routing and anonymous ingress regressions."""
from __future__ import annotations

import copy
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager

import pytest
from fastapi.testclient import TestClient


class _GlobalPlane:
    """Atomic fake matching the private global route-document contract."""

    def __init__(self) -> None:
        self.document: dict = {}
        self.lock = threading.RLock()
        self.clock = 1_000.0
        self.fail_reads = False
        self.fail_mutations = False
        self.fail_after_commit = False
        self.fail_actions_after_commit: set[str] = set()
        self.actions: list[str] = []

    def read(self) -> dict:
        from maverick_dashboard.public_form_routes import PublicFormRouteUnavailable

        if self.fail_reads:
            raise PublicFormRouteUnavailable("route authority read failed")
        with self.lock:
            return copy.deepcopy(self.document)

    def mutate(self, callback, *, action: str):
        from maverick_dashboard.public_form_routes import PublicFormRouteUnavailable

        if self.fail_mutations:
            raise PublicFormRouteUnavailable("route authority update failed")
        with self.lock:
            document = copy.deepcopy(self.document)
            result = callback(document, self.clock)
            self.document = copy.deepcopy(document)
            self.actions.append(action)
            if self.fail_after_commit or action in self.fail_actions_after_commit:
                self.fail_after_commit = False
                self.fail_actions_after_commit.discard(action)
                raise PublicFormRouteUnavailable("route commit response was lost")
            return result

    def mutation_fence(self, callback):
        from maverick.governed_records import GovernedMutationFence

        def _validate(_record: dict, now: float) -> None:
            callback(copy.deepcopy(self.document), now)

        return GovernedMutationFence(
            namespace="public_form_tenant_routing",
            prefix="PFR",
            record_id="PFR-state",
            authority_scope="deployment_global",
            validate=_validate,
        )


class _FencedTenantPlane:
    """Tenant document whose writes share the global fence transaction lock."""

    def __init__(self, routes: _GlobalPlane) -> None:
        self.routes = routes
        self.documents: dict[str, dict] = {}
        self.before_transaction = None
        self.fenced_writes = 0

    @staticmethod
    def _tenant() -> str:
        from maverick.paths import current_tenant_id_strict

        tenant = current_tenant_id_strict()
        assert tenant is not None
        return tenant

    def read(self) -> dict:
        with self.routes.lock:
            return copy.deepcopy(self.documents.get(self._tenant(), {}))

    def mutate(self, callback, *, action: str, fence=None):
        from maverick_dashboard.public_form_routes import PublicFormRouteError
        from maverick_dashboard.trigger_authority import TriggerAuthorityUnavailable

        if self.before_transaction is not None:
            self.before_transaction(action)
        try:
            with self.routes.lock:
                if fence is not None:
                    self.fenced_writes += 1
                    fence.validate({}, self.routes.clock)
                tenant = self._tenant()
                document = copy.deepcopy(self.documents.get(tenant, {}))
                result = callback(document, self.routes.clock)
                self.documents[tenant] = copy.deepcopy(document)
                return result
        except PublicFormRouteError as exc:
            raise TriggerAuthorityUnavailable(
                "tenant trigger mutation lost its global route fence"
            ) from exc


@pytest.fixture
def route_plane(monkeypatch, tmp_path):
    from maverick_dashboard import public_form_routes

    plane = _GlobalPlane()
    monkeypatch.setenv("MAVERICK_HOME", str(tmp_path))
    monkeypatch.setattr(public_form_routes, "_SHARED_DOCUMENT", plane)
    return plane


@contextmanager
def _tenant(tenant: str | None):
    from maverick.paths import reset_tenant, set_tenant

    context = set_tenant(tenant)
    try:
        yield
    finally:
        reset_tenant(context)


def _token(char: str) -> str:
    return char * 32


def test_route_document_is_deployment_global_and_postgres_pinned(monkeypatch):
    from maverick import authority_documents
    from maverick_dashboard import public_form_routes as routes

    captured: dict = {}

    class _Document:
        backend_kind = "postgres"

        def __init__(self, *args, **kwargs):
            captured["args"] = args
            captured["kwargs"] = kwargs

    monkeypatch.setattr(authority_documents, "AuthorityDocumentStore", _Document)
    routes._GlobalRouteDocument()
    assert captured["args"] == (
        "public_form_tenant_routing",
        "PFR",
        "public_form_tenant_routing",
    )
    assert captured["kwargs"] == {
        "required_backend": "postgres",
        "authority_scope": "deployment_global",
    }


def test_route_saga_stores_only_digest_and_tombstones_revocation(route_plane):
    from maverick_dashboard import public_form_routes as routes

    raw = _token("A")
    pending = routes.prepare_route(raw, "acme", "signup")
    assert pending.state == "pending"
    assert routes.resolve_route(raw) is None
    serialized = json.dumps(route_plane.document, sort_keys=True)
    assert raw not in serialized
    assert pending.token_digest in serialized

    active = routes.activate_route(pending)
    assert active.state == "active" and active.epoch == 2
    assert routes.resolve_route(raw) == active
    assert routes.revoke_route(raw, "acme", "signup") is True
    assert routes.resolve_route(raw) is None
    assert routes.has_revoked_trigger("acme", "signup") is True
    with pytest.raises(routes.PublicFormRouteConflict):
        routes.prepare_route(raw, "acme", "signup")


def test_stale_create_cannot_resurrect_trigger_after_takeover_delete(
    route_plane,
    monkeypatch,
    tmp_path,
):
    _isolate_app(monkeypatch, tmp_path)
    target = _install_fenced_target(monkeypatch, tmp_path, route_plane)
    from maverick_dashboard import event_triggers_store, public_form_routes

    checked = threading.Event()
    resume = threading.Event()
    outcome: dict[str, object] = {}

    def _before_transaction(action: str) -> None:
        if (
            action == "event_trigger_set"
            and threading.current_thread().name == "stale-create"
        ):
            checked.set()
            assert resume.wait(5)

    target.before_transaction = _before_transaction
    stale_token = _token("e")
    replacement = _token("h")
    stale_operation = public_form_routes.trigger_mutation_operation(
        "acme",
        "create-aba",
        {"action": "create", "token": stale_token},
    )

    def _stale_create() -> None:
        try:
            with public_form_routes.trigger_mutation_guard(
                "acme",
                "create-aba",
                operation=stale_operation,
            ):
                with _tenant("acme"):
                    event_triggers_store.set_trigger(
                        "create-aba",
                        "triage",
                        "form",
                        config={"token": stale_token},
                        tenant="acme",
                        template_snapshot=_snapshot(),
                        expected_version=0,
                    )
        except BaseException as exc:  # noqa: BLE001 - asserted below
            outcome["error"] = exc

    thread = threading.Thread(
        target=_stale_create,
        name="stale-create",
        daemon=True,
    )
    thread.start()
    assert checked.wait(5)
    route_plane.clock += 10_000

    create_operation = public_form_routes.trigger_mutation_operation(
        "acme",
        "create-aba",
        {"action": "replacement", "token": replacement},
    )
    with public_form_routes.trigger_mutation_guard(
        "acme",
        "create-aba",
        operation=create_operation,
    ):
        with _tenant("acme"):
            created = event_triggers_store.set_trigger(
                "create-aba",
                "triage",
                "form",
                config={"token": replacement},
                tenant="acme",
                template_snapshot=_snapshot(),
                expected_version=0,
            )

    delete_operation = public_form_routes.trigger_mutation_operation(
        "acme",
        "create-aba",
        {"action": "delete", "version": created["version"]},
    )
    with public_form_routes.trigger_mutation_guard(
        "acme",
        "create-aba",
        operation=delete_operation,
    ):
        with _tenant("acme"):
            assert event_triggers_store.delete_trigger(
                "create-aba",
                tenant="acme",
                expected_version=created["version"],
            )

    resume.set()
    thread.join(5)
    assert not thread.is_alive()
    assert isinstance(outcome.get("error"), event_triggers_store.TriggerAuthorityUnavailable)
    with _tenant("acme"):
        assert event_triggers_store.get_trigger("create-aba", tenant="acme") is None
    assert target.fenced_writes == 3


def test_stale_delete_cannot_remove_recreated_version_one_trigger(
    route_plane,
    monkeypatch,
    tmp_path,
):
    _isolate_app(monkeypatch, tmp_path)
    target = _install_fenced_target(monkeypatch, tmp_path, route_plane)
    from maverick_dashboard import event_triggers_store, public_form_routes

    original_token = _token("i")
    replacement_token = _token("j")
    seed_operation = public_form_routes.trigger_mutation_operation(
        "acme",
        "delete-aba",
        {"action": "seed", "token": original_token},
    )
    with public_form_routes.trigger_mutation_guard(
        "acme",
        "delete-aba",
        operation=seed_operation,
    ):
        with _tenant("acme"):
            seeded = event_triggers_store.set_trigger(
                "delete-aba",
                "triage",
                "form",
                config={"token": original_token},
                tenant="acme",
                template_snapshot=_snapshot(),
                expected_version=0,
            )

    checked = threading.Event()
    resume = threading.Event()
    outcome: dict[str, object] = {}

    def _before_transaction(action: str) -> None:
        if (
            action == "event_trigger_delete"
            and threading.current_thread().name == "stale-delete"
        ):
            checked.set()
            assert resume.wait(5)

    target.before_transaction = _before_transaction
    stale_operation = public_form_routes.trigger_mutation_operation(
        "acme",
        "delete-aba",
        {"action": "stale-delete", "version": seeded["version"]},
    )

    def _stale_delete() -> None:
        try:
            with public_form_routes.trigger_mutation_guard(
                "acme",
                "delete-aba",
                operation=stale_operation,
            ):
                with _tenant("acme"):
                    event_triggers_store.delete_trigger(
                        "delete-aba",
                        tenant="acme",
                        expected_version=seeded["version"],
                    )
        except BaseException as exc:  # noqa: BLE001 - asserted below
            outcome["error"] = exc

    thread = threading.Thread(
        target=_stale_delete,
        name="stale-delete",
        daemon=True,
    )
    thread.start()
    assert checked.wait(5)
    route_plane.clock += 10_000

    winning_delete = public_form_routes.trigger_mutation_operation(
        "acme",
        "delete-aba",
        {"action": "winning-delete", "version": seeded["version"]},
    )
    with public_form_routes.trigger_mutation_guard(
        "acme",
        "delete-aba",
        operation=winning_delete,
    ):
        with _tenant("acme"):
            assert event_triggers_store.delete_trigger(
                "delete-aba",
                tenant="acme",
                expected_version=seeded["version"],
            )

    recreate_operation = public_form_routes.trigger_mutation_operation(
        "acme",
        "delete-aba",
        {"action": "recreate", "token": replacement_token},
    )
    with public_form_routes.trigger_mutation_guard(
        "acme",
        "delete-aba",
        operation=recreate_operation,
    ):
        with _tenant("acme"):
            recreated = event_triggers_store.set_trigger(
                "delete-aba",
                "triage",
                "form",
                config={"token": replacement_token},
                tenant="acme",
                template_snapshot=_snapshot(),
                expected_version=0,
            )
    assert recreated["version"] == seeded["version"] == 1

    resume.set()
    thread.join(5)
    assert not thread.is_alive()
    assert isinstance(outcome.get("error"), event_triggers_store.TriggerAuthorityUnavailable)
    with _tenant("acme"):
        stored = event_triggers_store.get_trigger("delete-aba", tenant="acme")
    assert stored is not None
    assert stored["version"] == 1
    assert stored["config"]["token"] == replacement_token
    assert target.fenced_writes == 4


def test_v1_route_registry_reads_and_migrates_under_next_cas(route_plane):
    from maverick_dashboard import public_form_routes as routes

    raw = _token("v")
    active = routes.activate_route(routes.prepare_route(raw, "acme", "cutover"))
    route_plane.document["registry_schema"] = 1
    route_plane.document.pop("transitions")

    assert routes.resolve_route(raw) == active
    assert routes.revoke_route(raw, "acme", "cutover") is True
    assert route_plane.document["registry_schema"] == 2
    assert route_plane.document["transitions"] == {}


def test_route_digest_is_unique_across_tenants_and_triggers(route_plane):
    from maverick_dashboard import public_form_routes as routes

    raw = _token("B")
    first = routes.prepare_route(raw, "acme", "signup")
    assert routes.prepare_route(raw, "acme", "signup") == first
    with pytest.raises(routes.PublicFormRouteConflict):
        routes.prepare_route(raw, "globex", "signup")
    with pytest.raises(routes.PublicFormRouteConflict):
        routes.prepare_route(raw, "acme", "other")
    assert len(route_plane.document["routes"]) == 1


def test_prepare_activate_and_revoke_retries_survive_lost_responses(route_plane):
    from maverick_dashboard import public_form_routes as routes

    raw = _token("C")
    route_plane.fail_after_commit = True
    with pytest.raises(routes.PublicFormRouteUnavailable):
        routes.prepare_route(raw, "acme", "lead")
    pending = routes.prepare_route(raw, "acme", "lead")
    assert pending.state == "pending" and pending.epoch == 1

    route_plane.fail_after_commit = True
    with pytest.raises(routes.PublicFormRouteUnavailable):
        routes.activate_route(pending)
    active = routes.activate_route(pending)
    assert active.state == "active" and active.epoch == 2

    route_plane.fail_after_commit = True
    with pytest.raises(routes.PublicFormRouteUnavailable):
        routes.revoke_route(raw, "acme", "lead")
    assert routes.revoke_route(raw, "acme", "lead") is True
    tombstone = next(iter(route_plane.document["routes"].values()))
    assert tombstone["state"] == "revoked" and tombstone["epoch"] == 3


def test_abort_is_exact_and_never_removes_an_active_claim(route_plane):
    from maverick_dashboard import public_form_routes as routes

    abandoned = routes.prepare_route(_token("D"), "acme", "abandoned")
    assert routes.abort_pending_route(abandoned) is True
    assert route_plane.document["routes"] == {}
    assert routes.abort_pending_route(abandoned) is True

    active = routes.prepare_route(_token("E"), "acme", "live")
    routes.activate_route(active)
    assert routes.abort_pending_route(active) is False
    assert routes.resolve_route(_token("E")) is not None


def test_rotation_suspends_before_save_and_can_restore_or_retire(route_plane):
    from maverick_dashboard import public_form_routes as routes

    old_raw = _token("U")
    new_raw = _token("V")
    old = routes.activate_route(routes.prepare_route(old_raw, "acme", "rotate"))
    new = routes.prepare_route(new_raw, "acme", "rotate")
    assert routes.suspend_other_routes(
        "acme",
        "rotate",
        keep_digest=new.token_digest,
    ) == 1
    assert routes.resolve_route(old_raw) is None
    assert routes.resolve_route(new_raw) is None

    restored = routes.restore_route(old_raw, "acme", "rotate")
    assert restored.state == "active" and restored.epoch == old.epoch + 2
    routes.suspend_other_routes(
        "acme",
        "rotate",
        keep_digest=new.token_digest,
    )
    active_new = routes.activate_route(new)
    assert routes.retire_other_routes(
        "acme",
        "rotate",
        keep_digest=active_new.token_digest,
    ) == 1
    assert routes.resolve_route(old_raw) is None
    assert routes.resolve_route(new_raw) == active_new
    with pytest.raises(routes.PublicFormRouteConflict):
        routes.restore_route(old_raw, "acme", "rotate")


def test_submission_claim_linearizes_revocation_and_is_retryable(route_plane):
    from maverick_dashboard import public_form_routes as routes

    raw = _token("W")
    active = routes.activate_route(routes.prepare_route(raw, "acme", "claim"))
    claim = routes.admit_submission(active, idempotency_key="delivery-1")
    assert claim.status == "admitted" and claim.route_epoch == active.epoch
    assert routes.admit_submission(
        active,
        idempotency_key="delivery-1",
    ) == claim
    # Admission won the race. Revocation disables every later claim but keeps
    # this already-authorized append completable.
    routes.retire_other_routes("acme", "claim", keep_digest=None)
    assert routes.resolve_route(raw) is None
    with pytest.raises(routes.PublicFormRouteConflict):
        routes.admit_submission(active, idempotency_key="delivery-2")
    assert routes.complete_submission(claim, 7) is True
    assert routes.complete_submission(claim, 7) is True
    with pytest.raises(routes.PublicFormRouteConflict):
        routes.complete_submission(claim, 8)


def test_revocation_that_wins_before_admission_rejects_claim(route_plane):
    from maverick_dashboard import public_form_routes as routes

    raw = _token("X")
    active = routes.activate_route(routes.prepare_route(raw, "acme", "race"))
    routes.suspend_other_routes("acme", "race", keep_digest=None)
    with pytest.raises(routes.PublicFormRouteConflict):
        routes.admit_submission(active, idempotency_key="too-late")


def test_concurrent_claims_have_one_global_winner(route_plane):
    from maverick_dashboard import public_form_routes as routes

    raw = _token("F")
    barrier = threading.Barrier(16)

    def _claim(index: int) -> str:
        barrier.wait()
        try:
            routes.prepare_route(raw, f"tenant-{index}", "signup")
            return f"tenant-{index}"
        except routes.PublicFormRouteConflict:
            return "conflict"

    with ThreadPoolExecutor(max_workers=16) as pool:
        results = list(pool.map(_claim, range(16)))
    winners = [result for result in results if result != "conflict"]
    assert len(winners) == 1
    stored = next(iter(route_plane.document["routes"].values()))
    assert stored["tenant"] == winners[0]


def test_malformed_or_unavailable_registry_fails_closed(route_plane):
    from maverick_dashboard import public_form_routes as routes

    route_plane.document = {
        "registry_schema": 1,
        "routes": {"not-a-digest": {"tenant": "attacker"}},
    }
    with pytest.raises(routes.PublicFormRouteUnavailable):
        routes.resolve_route(_token("G"))
    route_plane.fail_reads = True
    with pytest.raises(routes.PublicFormRouteUnavailable):
        routes.resolve_route(_token("G"))
    with pytest.raises(ValueError):
        routes.prepare_route("guessable", "acme", "signup")


def test_trigger_revalidation_is_exact_and_constant_digest_bound(route_plane):
    from maverick_dashboard import public_form_routes as routes

    raw = _token("H")
    route = routes.activate_route(routes.prepare_route(raw, "acme", "signup"))
    trigger = {
        "name": "signup",
        "tenant": "acme",
        "source": "form",
        "config": {"token": raw},
    }
    assert routes.route_matches_trigger(route, trigger, raw)
    for field, value in (
        ("name", "other"),
        ("tenant", "globex"),
        ("source", "http_json"),
    ):
        forged = copy.deepcopy(trigger)
        forged[field] = value
        assert not routes.route_matches_trigger(route, forged, raw)
    forged = copy.deepcopy(trigger)
    forged["config"]["token"] = _token("I")
    assert not routes.route_matches_trigger(route, forged, raw)


def _isolate_app(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("MAVERICK_HOME", str(tmp_path))
    monkeypatch.setenv("MAVERICK_EVENT_TRIGGERS", "1")
    monkeypatch.setenv("MAVERICK_TENANT_BY_USER", "1")
    monkeypatch.delenv("MAVERICK_DASHBOARD_TOKEN", raising=False)
    from maverick import world_model

    monkeypatch.setattr(world_model, "DEFAULT_DB", tmp_path / "world.db")


def _install_template(monkeypatch, tmp_path) -> None:
    import maverick.templates as templates

    directory = tmp_path / ".maverick" / "templates"
    directory.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(templates, "USER_TEMPLATES", directory)
    (directory / "triage.md").write_text(
        "---\ntitle: Triage {{email}}\nparams:\n  - email\n---\nReview {{email}}.\n",
        encoding="utf-8",
    )


def _client() -> TestClient:
    from maverick_dashboard.app import app

    return TestClient(app, headers={"Origin": "http://testserver"})


def _arm_form(tenant: str, name: str, raw: str) -> None:
    from maverick_dashboard import event_triggers_store, public_form_routes

    operation = public_form_routes.trigger_mutation_operation(
        tenant,
        name,
        {"action": "arm-test-form", "token": raw},
    )
    with public_form_routes.trigger_mutation_guard(
        tenant,
        name,
        operation=operation,
    ):
        pending = public_form_routes.prepare_route(raw, tenant, name)
        with _tenant(tenant):
            event_triggers_store.set_trigger(
                name,
                "missing-template",
                "form",
                config={"token": raw},
                tenant=tenant,
            )
        active = public_form_routes.activate_route(pending)
        public_form_routes.retire_other_routes(
            tenant,
            name,
            keep_digest=active.token_digest,
        )


def _install_fenced_target(monkeypatch, tmp_path, route_plane):
    from maverick.paths import current_tenant_id_strict
    from maverick_dashboard import event_triggers_store

    target = _FencedTenantPlane(route_plane)
    monkeypatch.setattr(event_triggers_store, "shared_selected", lambda: True)
    monkeypatch.setattr(
        event_triggers_store,
        "require_active_tenant",
        lambda: str(current_tenant_id_strict() or ""),
    )
    monkeypatch.setattr(event_triggers_store, "_SHARED_DOCUMENT", target)
    monkeypatch.setattr(
        event_triggers_store,
        "_path",
        lambda: tmp_path / "dashboard-event-triggers.toml",
    )
    monkeypatch.setattr(
        "maverick.tenant.registry.assert_tenant_active",
        lambda _tenant: None,
    )
    return target


def _snapshot() -> dict:
    return {
        "name": "triage",
        "title": "Triage submission",
        "body": "Review the submitted item.",
        "params": [],
        "budget_dollars": 1.0,
        "budget_wall_seconds": 60.0,
    }


def test_tenant_trigger_store_rejects_unguarded_route_mutations(
    route_plane,
    monkeypatch,
    tmp_path,
):
    _isolate_app(monkeypatch, tmp_path)
    from maverick_dashboard import event_triggers_store, public_form_routes

    raw = _token("8")
    with pytest.raises(public_form_routes.PublicFormRouteUnavailable):
        public_form_routes.prepare_route(_token("7"), "acme", "unguarded-route")
    with _tenant("acme"):
        with pytest.raises(event_triggers_store.TriggerAuthorityUnavailable):
            event_triggers_store.set_trigger(
                "unguarded",
                "missing-template",
                "form",
                config={"token": raw},
                tenant="acme",
            )
        assert event_triggers_store.get_trigger("unguarded", tenant="acme") is None

    _arm_form("acme", "unguarded", raw)
    with _tenant("acme"):
        with pytest.raises(event_triggers_store.TriggerAuthorityUnavailable):
            event_triggers_store.delete_trigger(
                "unguarded",
                tenant="acme",
                expected_version=1,
            )
        assert event_triggers_store.get_trigger("unguarded", tenant="acme") is not None
    with pytest.raises(public_form_routes.PublicFormRouteUnavailable):
        public_form_routes.retire_other_routes(
            "acme",
            "unguarded",
            keep_digest=None,
        )
    assert public_form_routes.resolve_route(raw) is not None


def test_competing_rotation_cannot_interleave_between_save_and_activation(
    route_plane,
    monkeypatch,
    tmp_path,
):
    _isolate_app(monkeypatch, tmp_path)
    old = _token("a")
    first = _token("b")
    second = _token("c")
    _arm_form("acme", "serialized", old)
    from maverick_dashboard import event_triggers_store, public_form_routes

    operation_a = public_form_routes.trigger_mutation_operation(
        "acme", "serialized", {"action": "rotate", "token": first}
    )
    operation_b = public_form_routes.trigger_mutation_operation(
        "acme", "serialized", {"action": "rotate", "token": second}
    )
    with public_form_routes.trigger_mutation_guard(
        "acme",
        "serialized",
        operation=operation_a,
    ):
        prepared_a = public_form_routes.prepare_route(first, "acme", "serialized")
        with _tenant("acme"):
            prior = event_triggers_store.get_trigger("serialized", tenant="acme")
            event_triggers_store.set_trigger(
                "serialized",
                "missing-template",
                "form",
                config={"token": first},
                tenant="acme",
                expected_version=int(prior["version"]),
            )
        # This is the formerly unsafe A-save -> B-suspend/save -> A-activate
        # window. B cannot acquire a different operation claim while A holds it.
        with pytest.raises(public_form_routes.PublicFormRouteUnavailable):
            with public_form_routes.trigger_mutation_guard(
                "acme",
                "serialized",
                operation=operation_b,
            ):
                raise AssertionError("competing operation unexpectedly acquired")
        active_a = public_form_routes.activate_route(prepared_a)
        public_form_routes.retire_other_routes(
            "acme",
            "serialized",
            keep_digest=active_a.token_digest,
        )

    with public_form_routes.trigger_mutation_guard(
        "acme",
        "serialized",
        operation=operation_b,
    ):
        prepared_b = public_form_routes.prepare_route(second, "acme", "serialized")
        with _tenant("acme"):
            prior = event_triggers_store.get_trigger("serialized", tenant="acme")
            event_triggers_store.set_trigger(
                "serialized",
                "missing-template",
                "form",
                config={"token": second},
                tenant="acme",
                expected_version=int(prior["version"]),
            )
        active_b = public_form_routes.activate_route(prepared_b)
        public_form_routes.retire_other_routes(
            "acme",
            "serialized",
            keep_digest=active_b.token_digest,
        )

    assert public_form_routes.resolve_route(old) is None
    assert public_form_routes.resolve_route(first) is None
    assert public_form_routes.resolve_route(second) == active_b


def test_cross_owner_management_probe_cannot_suspend_public_route(
    route_plane,
    monkeypatch,
    tmp_path,
):
    _isolate_app(monkeypatch, tmp_path)
    _install_template(monkeypatch, tmp_path)
    monkeypatch.setenv("MAVERICK_TENANT", "acme")
    raw = _token("f")
    _arm_form("acme", "owned-form", raw)
    from maverick_dashboard import api, public_form_routes

    monkeypatch.setattr(api, "goal_owner_filter", lambda _request: "other-owner")
    client = _client()
    replacement = _token("g")
    update = client.post(
        "/api/v1/event-triggers",
        json={
            "name": "owned-form",
            "template": "triage",
            "source": "form",
            "config": {"token": replacement},
        },
    )
    assert update.status_code == 409
    assert client.delete("/api/v1/event-triggers/owned-form").status_code == 404
    assert public_form_routes.resolve_route(raw) is not None
    assert public_form_routes.resolve_route(replacement) is None


def test_backend_time_same_operation_takeover_rejects_stale_generation(
    route_plane,
    monkeypatch,
    tmp_path,
):
    _isolate_app(monkeypatch, tmp_path)
    from maverick_dashboard import public_form_routes

    raw = _token("d")
    operation = public_form_routes.trigger_mutation_operation(
        "acme", "lease-race", {"action": "rotate", "token": raw}
    )
    acquired = threading.Event()
    takeover = threading.Event()
    stale_finished = threading.Event()

    def _stale_holder() -> str:
        try:
            with public_form_routes.trigger_mutation_guard(
                "acme",
                "lease-race",
                operation=operation,
            ):
                prepared = public_form_routes.prepare_route(
                    raw,
                    "acme",
                    "lease-race",
                )
                acquired.set()
                assert takeover.wait(timeout=5)
                public_form_routes.activate_route(prepared)
                return "unexpectedly-activated"
        except public_form_routes.PublicFormRouteUnavailable:
            return "fenced"
        finally:
            stale_finished.set()

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(_stale_holder)
        assert acquired.wait(timeout=5)
        # Only the authoritative callback time controls expiry; no process-local
        # time function participates in takeover.
        route_plane.clock += 10_000
        with public_form_routes.trigger_mutation_guard(
            "acme",
            "lease-race",
            operation=operation,
        ):
            prepared = public_form_routes.prepare_route(
                raw,
                "acme",
                "lease-race",
            )
            takeover.set()
            assert stale_finished.wait(timeout=5)
            active = public_form_routes.activate_route(prepared)
            public_form_routes.retire_other_routes(
                "acme",
                "lease-race",
                keep_digest=active.token_digest,
            )
        assert future.result(timeout=5) == "fenced"

    assert public_form_routes.resolve_route(raw) == active
    serialized = json.dumps(route_plane.document, sort_keys=True)
    assert raw not in serialized


def test_anonymous_ingress_binds_tenant_revalidates_and_appends(
    route_plane,
    monkeypatch,
    tmp_path,
):
    _isolate_app(monkeypatch, tmp_path)
    raw = _token("J")
    _arm_form("acme", "signup", raw)

    response = _client().post(f"/form/{raw}", json={"email": "a@example.com"})
    assert response.status_code == 201 and response.json()["seq"] == 1
    from maverick import form_store

    with _tenant("acme"):
        events, cursor = form_store.since(raw, "0")
    with _tenant("globex"):
        foreign, _ = form_store.since(raw, "0")
    assert cursor == "1" and events[0]["email"] == "a@example.com"
    assert foreign == []


def test_unknown_revoked_and_changed_routes_never_append(
    route_plane,
    monkeypatch,
    tmp_path,
):
    _isolate_app(monkeypatch, tmp_path)
    raw = _token("K")
    _arm_form("acme", "lead", raw)
    from maverick_dashboard import public_form_routes

    operation = public_form_routes.trigger_mutation_operation(
        "acme",
        "lead",
        {"action": "test-revoke"},
    )
    with public_form_routes.trigger_mutation_guard(
        "acme",
        "lead",
        operation=operation,
    ):
        assert public_form_routes.revoke_route(raw, "acme", "lead")
    assert _client().post(f"/form/{raw}", json={"x": 1}).status_code == 404
    assert _client().post(f"/form/{_token('L')}", json={"x": 1}).status_code == 404

    replacement = _token("M")
    operation = public_form_routes.trigger_mutation_operation(
        "acme",
        "lead",
        {"action": "test-mismatch", "token": replacement},
    )
    with public_form_routes.trigger_mutation_guard(
        "acme",
        "lead",
        operation=operation,
    ):
        pending = public_form_routes.prepare_route(replacement, "acme", "lead")
        public_form_routes.activate_route(pending)
    # The registry says lead, but the tenant trigger still binds the old token.
    assert _client().post(f"/form/{replacement}", json={"x": 1}).status_code == 404


def test_route_or_trigger_authority_outage_is_503_without_acknowledgement(
    route_plane,
    monkeypatch,
    tmp_path,
):
    _isolate_app(monkeypatch, tmp_path)
    raw = _token("N")
    _arm_form("acme", "outage", raw)
    route_plane.fail_reads = True
    response = _client().post(f"/form/{raw}", json={"x": 1})
    assert response.status_code == 503
    assert response.json()["detail"] == "form-routing authority is unavailable"


def test_route_change_between_checks_is_denied_before_append(
    route_plane,
    monkeypatch,
    tmp_path,
):
    _isolate_app(monkeypatch, tmp_path)
    raw = _token("P")
    _arm_form("acme", "racy", raw)
    from maverick import form_store
    from maverick_dashboard import public_form_routes

    original = public_form_routes.resolve_route(raw)
    calls = iter((original, None))
    monkeypatch.setattr(public_form_routes, "resolve_route", lambda _raw: next(calls))
    appended: list[bool] = []
    monkeypatch.setattr(
        form_store,
        "append",
        lambda *_args, **_kwargs: appended.append(True) or 1,
    )
    response = _client().post(f"/form/{raw}", json={"x": 1})
    assert response.status_code == 404
    assert appended == []


def test_revocation_between_revalidation_and_admission_cannot_append(
    route_plane,
    monkeypatch,
    tmp_path,
):
    _isolate_app(monkeypatch, tmp_path)
    raw = _token("Y")
    _arm_form("acme", "linearized", raw)
    from maverick import form_store
    from maverick_dashboard import public_form_routes

    original = public_form_routes.admit_submission

    def _revoke_then_admit(route, *, idempotency_key=None):
        operation = public_form_routes.trigger_mutation_operation(
            route.tenant,
            route.trigger,
            {"action": "test-revoke-before-admit"},
        )
        with public_form_routes.trigger_mutation_guard(
            route.tenant,
            route.trigger,
            operation=operation,
        ):
            public_form_routes.retire_other_routes(
                route.tenant,
                route.trigger,
                keep_digest=None,
            )
        return original(route, idempotency_key=idempotency_key)

    appended: list[bool] = []
    monkeypatch.setattr(public_form_routes, "admit_submission", _revoke_then_admit)
    monkeypatch.setattr(
        form_store,
        "append",
        lambda *_args, **_kwargs: appended.append(True) or 1,
    )
    response = _client().post(f"/form/{raw}", json={"x": 1})
    assert response.status_code == 404
    assert appended == []


def test_claim_that_wins_before_revocation_remains_completable(
    route_plane,
    monkeypatch,
    tmp_path,
):
    _isolate_app(monkeypatch, tmp_path)
    raw = _token("Z")
    _arm_form("acme", "admitted", raw)
    from maverick import form_store
    from maverick_dashboard import public_form_routes

    original_append = form_store.append

    def _revoke_after_claim(token, fields, **kwargs):
        operation = public_form_routes.trigger_mutation_operation(
            "acme",
            "admitted",
            {"action": "test-revoke-after-admit"},
        )
        with public_form_routes.trigger_mutation_guard(
            "acme",
            "admitted",
            operation=operation,
        ):
            public_form_routes.retire_other_routes(
                "acme",
                "admitted",
                keep_digest=None,
            )
        return original_append(token, fields, **kwargs)

    monkeypatch.setattr(form_store, "append", _revoke_after_claim)
    response = _client().post(
        f"/form/{raw}",
        json={"x": 1},
        headers={"Idempotency-Key": "authorized-before-revoke"},
    )
    assert response.status_code == 201 and response.json()["seq"] == 1
    assert public_form_routes.resolve_route(raw) is None


def test_claim_key_dedupes_retry_and_rejects_different_payload(
    route_plane,
    monkeypatch,
    tmp_path,
):
    _isolate_app(monkeypatch, tmp_path)
    raw = _token("0")
    _arm_form("acme", "idempotent", raw)
    client = _client()
    headers = {"Idempotency-Key": "submission-42"}
    first = client.post(f"/form/{raw}", json={"x": 1}, headers=headers)
    retry = client.post(f"/form/{raw}", json={"x": 1}, headers=headers)
    conflict = client.post(f"/form/{raw}", json={"x": 2}, headers=headers)
    assert first.status_code == retry.status_code == 201
    assert first.json()["seq"] == retry.json()["seq"] == 1
    assert conflict.status_code == 409


def test_submission_commit_response_loss_converges_on_idempotent_retry(
    route_plane,
    monkeypatch,
    tmp_path,
):
    _isolate_app(monkeypatch, tmp_path)
    raw = _token("1")
    _arm_form("acme", "commit-loss", raw)
    route_plane.fail_actions_after_commit.add("public_form_submission_commit")
    client = _client()
    headers = {"Idempotency-Key": "commit-loss-1"}
    first = client.post(f"/form/{raw}", json={"x": 1}, headers=headers)
    assert first.status_code == 503
    retry = client.post(f"/form/{raw}", json={"x": 1}, headers=headers)
    assert retry.status_code == 201 and retry.json()["seq"] == 1


def test_management_api_provisions_rotates_revokes_and_idempotently_deletes(
    route_plane,
    monkeypatch,
    tmp_path,
):
    _isolate_app(monkeypatch, tmp_path)
    _install_template(monkeypatch, tmp_path)
    monkeypatch.setenv("MAVERICK_TENANT", "acme")
    client = _client()
    first = _token("Q")
    second = _token("R")

    created = client.post(
        "/api/v1/event-triggers",
        json={
            "name": "signup",
            "template": "triage",
            "source": "form",
            "config": {"token": first},
        },
    )
    assert created.status_code == 201, created.text
    from maverick_dashboard import event_triggers_store, public_form_routes

    assert public_form_routes.resolve_route(first).tenant == "acme"

    rotated = client.post(
        "/api/v1/event-triggers",
        json={
            "name": "signup",
            "template": "triage",
            "source": "form",
            "config": {"token": second},
        },
    )
    assert rotated.status_code == 201, rotated.text
    assert public_form_routes.resolve_route(first) is None
    assert public_form_routes.resolve_route(second).trigger == "signup"

    deleted = client.delete("/api/v1/event-triggers/signup")
    assert deleted.status_code == 200, deleted.text
    assert public_form_routes.resolve_route(second) is None
    with _tenant("acme"):
        assert event_triggers_store.get_trigger("signup", tenant="acme") is None
    # A lost HTTP response can be retried: the route tombstone proves the
    # already-completed deletion without reactivating or revealing the token.
    assert client.delete("/api/v1/event-triggers/signup").status_code == 200


def test_management_delete_converges_after_tenant_save_response_loss(
    route_plane,
    monkeypatch,
    tmp_path,
):
    _isolate_app(monkeypatch, tmp_path)
    _install_template(monkeypatch, tmp_path)
    monkeypatch.setenv("MAVERICK_TENANT", "acme")
    client = _client()
    raw = _token("9")
    payload = {
        "name": "delete-loss",
        "template": "triage",
        "source": "form",
        "config": {"token": raw},
    }
    assert client.post("/api/v1/event-triggers", json=payload).status_code == 201
    from maverick_dashboard import event_triggers_store, public_form_routes

    original = event_triggers_store.delete_trigger
    lose_once = {"value": True}

    def _delete_then_lose(*args, **kwargs):
        deleted = original(*args, **kwargs)
        if deleted and lose_once["value"]:
            lose_once["value"] = False
            raise event_triggers_store.TriggerAuthorityUnavailable(
                "tenant trigger delete response lost"
            )
        return deleted

    monkeypatch.setattr(event_triggers_store, "delete_trigger", _delete_then_lose)
    assert client.delete("/api/v1/event-triggers/delete-loss").status_code == 503
    assert public_form_routes.resolve_route(raw) is None

    retry = client.delete("/api/v1/event-triggers/delete-loss")
    assert retry.status_code == 200, retry.text
    assert public_form_routes.resolve_route(raw) is None
    with _tenant("acme"):
        assert event_triggers_store.get_trigger("delete-loss", tenant="acme") is None


def test_management_api_recovers_after_activation_response_loss(
    route_plane,
    monkeypatch,
    tmp_path,
):
    _isolate_app(monkeypatch, tmp_path)
    _install_template(monkeypatch, tmp_path)
    monkeypatch.setenv("MAVERICK_TENANT", "acme")
    raw = _token("S")
    route_plane.fail_actions_after_commit.add("public_form_route_activate")
    client = _client()
    payload = {
        "name": "lost-response",
        "template": "triage",
        "source": "form",
        "config": {"token": raw},
    }

    first = client.post("/api/v1/event-triggers", json=payload)
    assert first.status_code == 503
    # The activation committed before its response was lost. Repeating the
    # same provisioning operation converges without a duplicate route.
    retry = client.post("/api/v1/event-triggers", json=payload)
    assert retry.status_code == 201, retry.text
    from maverick_dashboard import public_form_routes

    assert public_form_routes.resolve_route(raw).state == "active"
    assert len(route_plane.document["routes"]) == 1


@pytest.mark.parametrize(
    "checkpoint",
    ["prepare", "fence", "save", "activate", "release"],
)
def test_create_converges_after_each_provisioning_response_loss(
    route_plane,
    monkeypatch,
    tmp_path,
    checkpoint,
):
    _isolate_app(monkeypatch, tmp_path)
    _install_template(monkeypatch, tmp_path)
    monkeypatch.setenv("MAVERICK_TENANT", "acme")
    raw = _token("2")
    from maverick_dashboard import event_triggers_store

    if checkpoint == "save":
        original = event_triggers_store.set_trigger
        lose_once = {"value": True}

        def _save_then_lose(*args, **kwargs):
            record = original(*args, **kwargs)
            if lose_once["value"]:
                lose_once["value"] = False
                raise event_triggers_store.TriggerAuthorityUnavailable(
                    "tenant trigger save response lost"
                )
            return record

        monkeypatch.setattr(event_triggers_store, "set_trigger", _save_then_lose)
    else:
        route_plane.fail_actions_after_commit.add(
            "public_form_route_lease_release"
            if checkpoint == "release"
            else (
                "public_form_route_lease_fence"
                if checkpoint == "fence"
                else f"public_form_route_{checkpoint}"
            )
        )
    payload = {
        "name": f"create-{checkpoint}",
        "template": "triage",
        "source": "form",
        "config": {"token": raw},
    }
    client = _client()
    assert client.post("/api/v1/event-triggers", json=payload).status_code == 503
    retry = client.post("/api/v1/event-triggers", json=payload)
    assert retry.status_code == 201, retry.text
    from maverick_dashboard import public_form_routes

    assert public_form_routes.resolve_route(raw).state == "active"


@pytest.mark.parametrize(
    "checkpoint",
    [
        "prepare",
        "suspend",
        "fence",
        "save",
        "activate",
        "retire",
        "release",
    ],
)
def test_rotation_converges_after_each_saga_response_loss(
    route_plane,
    monkeypatch,
    tmp_path,
    checkpoint,
):
    _isolate_app(monkeypatch, tmp_path)
    _install_template(monkeypatch, tmp_path)
    monkeypatch.setenv("MAVERICK_TENANT", "acme")
    client = _client()
    old = _token("3")
    new = _token("4")
    base = {
        "name": f"rotate-{checkpoint}",
        "template": "triage",
        "source": "form",
    }
    created = client.post(
        "/api/v1/event-triggers",
        json={**base, "config": {"token": old}},
    )
    assert created.status_code == 201, created.text
    from maverick_dashboard import event_triggers_store

    if checkpoint == "save":
        original = event_triggers_store.set_trigger
        lose_once = {"value": True}

        def _save_then_lose(*args, **kwargs):
            record = original(*args, **kwargs)
            if lose_once["value"]:
                lose_once["value"] = False
                raise event_triggers_store.TriggerAuthorityUnavailable(
                    "tenant trigger save response lost"
                )
            return record

        monkeypatch.setattr(event_triggers_store, "set_trigger", _save_then_lose)
    else:
        route_plane.fail_actions_after_commit.add(
            "public_form_route_lease_release"
            if checkpoint == "release"
            else (
                "public_form_route_lease_fence"
                if checkpoint == "fence"
                else f"public_form_route_{checkpoint}"
            )
        )
    updated_payload = {**base, "config": {"token": new}}
    assert client.post(
        "/api/v1/event-triggers",
        json=updated_payload,
    ).status_code == 503
    retry = client.post("/api/v1/event-triggers", json=updated_payload)
    assert retry.status_code == 201, retry.text
    from maverick_dashboard import public_form_routes

    assert public_form_routes.resolve_route(old) is None
    active = public_form_routes.resolve_route(new)
    assert active is not None and active.state == "active"


def test_proven_failed_save_restores_old_route_and_aborts_new_claim(
    route_plane,
    monkeypatch,
    tmp_path,
):
    _isolate_app(monkeypatch, tmp_path)
    _install_template(monkeypatch, tmp_path)
    monkeypatch.setenv("MAVERICK_TENANT", "acme")
    client = _client()
    old = _token("5")
    new = _token("6")
    base = {
        "name": "recover-old",
        "template": "triage",
        "source": "form",
    }
    assert client.post(
        "/api/v1/event-triggers",
        json={**base, "config": {"token": old}},
    ).status_code == 201
    from maverick_dashboard import event_triggers_store, public_form_routes

    monkeypatch.setattr(
        event_triggers_store,
        "set_trigger",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            ValueError("deterministic validation failure")
        ),
    )
    failed = client.post(
        "/api/v1/event-triggers",
        json={**base, "config": {"token": new}},
    )
    assert failed.status_code == 400
    restored = public_form_routes.resolve_route(old)
    assert restored is not None and restored.state == "active"
    assert public_form_routes.resolve_route(new) is None
    assert len(route_plane.document["routes"]) == 1


def test_management_api_rejects_cross_tenant_token_collision_before_mutation(
    route_plane,
    monkeypatch,
    tmp_path,
):
    _isolate_app(monkeypatch, tmp_path)
    _install_template(monkeypatch, tmp_path)
    raw = _token("T")
    from maverick_dashboard import public_form_routes

    operation = public_form_routes.trigger_mutation_operation(
        "globex",
        "signup",
        {"action": "test-collision", "token": raw},
    )
    with public_form_routes.trigger_mutation_guard(
        "globex",
        "signup",
        operation=operation,
    ):
        public_form_routes.prepare_route(raw, "globex", "signup")
    monkeypatch.setenv("MAVERICK_TENANT", "acme")
    response = _client().post(
        "/api/v1/event-triggers",
        json={
            "name": "signup",
            "template": "triage",
            "source": "form",
            "config": {"token": raw},
        },
    )
    assert response.status_code == 409
    from maverick_dashboard import event_triggers_store

    with _tenant("acme"):
        assert event_triggers_store.get_trigger("signup", tenant="acme") is None
