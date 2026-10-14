"""Cross-replica contracts for learned budget-cap authority."""
from __future__ import annotations

import copy
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from maverick import self_tuning_budget as budget_tuning


class _Plane:
    def __init__(self) -> None:
        self.backend_kind = "postgres"
        self.document: dict = {}
        self.lock = threading.Lock()
        self.fail_after_commit = False
        self.fail_reads = False

    def store(self):
        plane = self

        class _Store:
            @property
            def backend_kind(self):
                return plane.backend_kind

            def read(self):
                if plane.fail_reads:
                    raise OSError("shared authority unavailable")
                with plane.lock:
                    return copy.deepcopy(plane.document)

            def mutate(self, callback, **_kwargs):
                with plane.lock:
                    document = copy.deepcopy(plane.document)
                    result = callback(document)
                    plane.document = copy.deepcopy(document)
                    if plane.fail_after_commit:
                        plane.fail_after_commit = False
                        raise OSError("response lost after commit")
                    return result

        return _Store()


def _learner(tmp_path, plane: _Plane | None = None, **kwargs):
    plane = plane or _Plane()
    learner = budget_tuning.GovernedSelfTuningBudget(
        store=plane.store(),
        legacy_path=tmp_path / "budget_tuning.json",
        **kwargs,
    )
    return learner, plane


def test_shared_concurrent_observations_have_no_lost_updates(tmp_path):
    learner, _plane = _learner(tmp_path, min_samples=8)

    def observe(index: int) -> None:
        learner.observe("research", float(index), observation_id=f"episode:{index}")

    with ThreadPoolExecutor(max_workers=16) as pool:
        list(pool.map(observe, range(64)))

    assert learner.stats("research")["count"] == 64
    assert learner.suggest("research") is not None


def test_shared_lost_response_retry_is_idempotent(tmp_path):
    learner, plane = _learner(tmp_path, min_samples=1)
    plane.fail_after_commit = True

    with pytest.raises(budget_tuning.SelfTuningBudgetAuthorityError):
        learner.observe("research", 3.5, observation_id="episode:7")
    learner.observe("research", 3.5, observation_id="episode:7")

    assert learner.stats("research") == {
        "count": 1,
        "q": 3.5,
        "suggested": 4.55,
    }


def test_shared_replay_with_different_cost_is_rejected(tmp_path):
    learner, _plane = _learner(tmp_path, min_samples=1)
    learner.observe("research", 1.0, observation_id="episode:9")

    with pytest.raises(budget_tuning.SelfTuningBudgetAuthorityError):
        learner.observe("research", 2.0, observation_id="episode:9")

    assert learner.stats("research")["q"] == 1.0


def test_shared_reservoir_is_order_independent_and_bounded(tmp_path):
    observations = [
        (f"episode:{index}", float(index))
        for index in range(budget_tuning._RESERVOIR + 75)
    ]
    forward, forward_plane = _learner(tmp_path / "a", min_samples=1)
    reverse, reverse_plane = _learner(tmp_path / "b", min_samples=1)

    for observation_id, cost in observations:
        forward.observe("research", cost, observation_id=observation_id)
    for observation_id, cost in reversed(observations):
        reverse.observe("research", cost, observation_id=observation_id)

    assert forward_plane.document == reverse_plane.document
    assert forward.stats("research")["count"] == budget_tuning._RESERVOIR
    assert forward.suggest("research") == reverse.suggest("research")


def test_shared_requires_stable_observation_id(tmp_path):
    learner, _plane = _learner(tmp_path)

    with pytest.raises(budget_tuning.SelfTuningBudgetAuthorityError):
        learner.observe("research", 1.0)


def test_shared_cutover_backend_drift_and_corruption_fail_closed(tmp_path):
    learner, plane = _learner(tmp_path, min_samples=1)
    learner.legacy_path.write_text("{}", encoding="utf-8")
    with pytest.raises(budget_tuning.SelfTuningBudgetAuthorityError):
        learner.suggest("research")

    learner.legacy_path.unlink()
    plane.backend_kind = "local"
    with pytest.raises(budget_tuning.SelfTuningBudgetAuthorityError):
        learner.observe("research", 1.0, observation_id="episode:1")

    plane.backend_kind = "postgres"
    plane.document = {"schema_version": 1, "classes": {"research": "forged"}}
    with pytest.raises(budget_tuning.SelfTuningBudgetAuthorityError):
        learner.suggest("research")


def test_shared_read_outage_is_not_downgraded_to_static_cap(
    tmp_path, monkeypatch,
):
    learner, plane = _learner(tmp_path)
    plane.fail_reads = True
    monkeypatch.setattr(budget_tuning, "enabled", lambda: True)

    with pytest.raises(budget_tuning.SelfTuningBudgetAuthorityError):
        budget_tuning.suggested_max_dollars("research", learner=learner)


def test_budget_constructor_propagates_shared_authority_outage(monkeypatch):
    from maverick.budget import budget_from_config

    def unavailable(_task_class):
        raise budget_tuning.SelfTuningBudgetAuthorityError("offline")

    monkeypatch.setattr(budget_tuning, "suggested_max_dollars", unavailable)
    with pytest.raises(budget_tuning.SelfTuningBudgetAuthorityError):
        budget_from_config(task_class="research")


def test_existing_shared_cap_does_not_disappear_when_policy_becomes_unreadable(
    tmp_path, monkeypatch,
):
    from maverick import config

    learner, _plane = _learner(tmp_path, min_samples=1)
    learner.observe("research", 3.0, observation_id="episode:trusted")
    monkeypatch.delenv("MAVERICK_BUDGET_SELF_TUNING", raising=False)
    monkeypatch.setattr(
        config,
        "load_config",
        lambda: {"budget": {"self_tuning": True}},
    )
    monkeypatch.setattr(
        config,
        "config_source_errors",
        lambda: {"config.toml": "permission denied"},
    )
    monkeypatch.setattr(
        budget_tuning,
        "_compatibility_fail_soft_allowed",
        lambda: False,
    )

    with pytest.raises(
        budget_tuning.SelfTuningBudgetAuthorityError,
        match="policy source",
    ):
        budget_tuning.suggested_max_dollars("research", learner=learner)


def test_single_replica_postgres_does_not_get_local_fail_soft_policy(
    monkeypatch,
):
    from maverick import authority_policy, authority_state, config, governed_records

    monkeypatch.setenv("MAVERICK_REPLICA_COUNT", "1")
    monkeypatch.setattr(authority_policy, "strict_authority", lambda: False)
    monkeypatch.setattr(
        governed_records,
        "_selection",
        lambda: governed_records._BackendSelection("postgres", "dsn", False),
    )
    monkeypatch.setattr(
        config,
        "load_config",
        lambda: {"budget": {"self_tuning": True}},
    )
    monkeypatch.setattr(
        config,
        "config_source_errors",
        lambda: {"config.toml": "permission denied"},
    )

    assert authority_state.compatibility_local_authority_allowed() is False
    with pytest.raises(
        budget_tuning.SelfTuningBudgetAuthorityError,
        match="policy source",
    ):
        budget_tuning.enabled()


def test_explicit_self_tuning_disable_remains_safe_during_config_outage(
    monkeypatch,
):
    from maverick import config

    monkeypatch.setenv("MAVERICK_BUDGET_SELF_TUNING", "0")
    monkeypatch.setattr(
        config,
        "load_config",
        lambda: (_ for _ in ()).throw(OSError("unreadable")),
    )
    monkeypatch.setattr(
        budget_tuning,
        "_compatibility_fail_soft_allowed",
        lambda: False,
    )

    assert budget_tuning.enabled() is False


def test_invalid_self_tuning_env_fails_closed_in_shared_mode(monkeypatch):
    from maverick import config

    monkeypatch.setenv("MAVERICK_BUDGET_SELF_TUNING", "maybe")
    monkeypatch.setattr(config, "load_config", dict)
    monkeypatch.setattr(config, "config_source_errors", dict)
    monkeypatch.setattr(
        budget_tuning,
        "_compatibility_fail_soft_allowed",
        lambda: False,
    )

    with pytest.raises(
        budget_tuning.SelfTuningBudgetAuthorityError,
        match="environment policy",
    ):
        budget_tuning.enabled()


def test_budget_config_read_failure_is_authoritative_in_shared_mode(
    monkeypatch,
):
    from maverick import authority_state, config
    from maverick.budget import budget_from_config

    monkeypatch.setattr(
        config,
        "get_budget_overrides",
        lambda: (_ for _ in ()).throw(OSError("unreadable")),
    )
    monkeypatch.setattr(
        authority_state,
        "compatibility_local_authority_allowed",
        lambda: False,
    )

    with pytest.raises(
        budget_tuning.SelfTuningBudgetAuthorityError,
        match="budget policy",
    ):
        budget_from_config(defaults={"max_dollars": 10.0})


def test_budget_config_parse_failure_signal_is_authoritative_in_shared_mode(
    monkeypatch,
):
    from maverick import authority_state, config
    from maverick.budget import budget_from_config

    monkeypatch.setattr(config, "get_budget_overrides", dict)
    monkeypatch.setattr(
        config,
        "config_source_errors",
        lambda: {"config.toml": "malformed TOML"},
    )
    monkeypatch.setattr(
        authority_state,
        "compatibility_local_authority_allowed",
        lambda: False,
    )

    with pytest.raises(
        budget_tuning.SelfTuningBudgetAuthorityError,
        match="budget policy",
    ):
        budget_from_config(defaults={"max_dollars": 10.0})


def test_shared_selector_refuses_legacy_file_and_strict_local(
    tmp_path, monkeypatch,
):
    monkeypatch.setenv("MAVERICK_HOME", str(tmp_path))
    budget_tuning.reset_shared()
    legacy = tmp_path / "budget_tuning.json"
    legacy.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(budget_tuning, "_authority_backend", lambda: "postgres")
    monkeypatch.setattr(budget_tuning, "_new_shared_store", _Plane().store)

    with pytest.raises(budget_tuning.SelfTuningBudgetAuthorityError):
        budget_tuning.shared()

    legacy.unlink()
    budget_tuning.reset_shared()
    monkeypatch.setattr(budget_tuning, "_authority_backend", lambda: "local")
    monkeypatch.setattr(
        "maverick.authority_policy.strict_authority", lambda: True,
    )
    with pytest.raises(budget_tuning.SelfTuningBudgetAuthorityError):
        budget_tuning.shared()


def test_local_learner_accepts_optional_observation_id_compatibly(tmp_path):
    learner = budget_tuning.SelfTuningBudget(
        path=tmp_path / "budget_tuning.json",
        min_samples=1,
    )
    learner.observe("research", 2.0, observation_id="ignored-locally")
    assert learner.suggest("research") == 2.6


def test_orchestrator_observation_identity_is_stable_and_run_scoped(tmp_path):
    from maverick.orchestrator import _budget_observation_id

    class _World:
        path = tmp_path / "world.db"

    first = _budget_observation_id(_World(), 3, 11, "reservation-a")
    assert first == _budget_observation_id(_World(), 3, 11, "reservation-a")
    assert first != _budget_observation_id(_World(), 3, 11, "reservation-b")
    assert str(tmp_path) not in first
