"""Shared calibration and sealed-holdout authority regressions."""
from __future__ import annotations

import copy
import json
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from maverick import calibration, governed_records, self_improvement_runner
from maverick import self_harness_holdout as holdout


class _SharedDocuments:
    """Optimistic-CAS document double shared across replica instances."""

    def __init__(self) -> None:
        self.backend_kind = "postgres"
        self.documents: dict[str, dict] = {}
        self.revisions: dict[str, int] = {}
        self.lock = threading.Lock()
        self.first_read_barrier: threading.Barrier | None = None
        self.first_read_slots = 0

    def store(self, key: str):
        shared = self

        class _Store:
            def __init__(self) -> None:
                self.reads = 0

            @property
            def backend_kind(self):
                return shared.backend_kind

            def read(self):
                self.reads += 1
                barrier = None
                with shared.lock:
                    value = copy.deepcopy(shared.documents.get(key, {}))
                    if self.reads == 1:
                        if shared.first_read_slots:
                            shared.first_read_slots -= 1
                            barrier = shared.first_read_barrier
                if barrier is not None:
                    barrier.wait(timeout=10)
                return value

            def mutate(
                self,
                callback,
                *,
                action,
                actor="system",
                max_retries=16,
            ):
                del action, actor
                for _attempt in range(max_retries):
                    with shared.lock:
                        revision = shared.revisions.get(key, 0)
                        document = copy.deepcopy(shared.documents.get(key, {}))
                    result = callback(document)
                    with shared.lock:
                        if shared.revisions.get(key, 0) != revision:
                            continue
                        shared.documents[key] = copy.deepcopy(document)
                        shared.revisions[key] = revision + 1
                        return result
                raise RuntimeError("CAS retries exhausted")

        return _Store()


class _FailingStore:
    backend_kind = "postgres"

    def read(self):
        raise OSError("database unavailable")

    def mutate(self, *_args, **_kwargs):
        raise OSError("database unavailable")


class _DriftAfterRead:
    def __init__(self, inner) -> None:
        self.inner = inner
        self.checks = 0

    @property
    def backend_kind(self):
        self.checks += 1
        return "postgres" if self.checks == 1 else "local"

    def read(self):
        return self.inner.read()

    def mutate(self, callback, **kwargs):
        return self.inner.mutate(callback, **kwargs)


def _calibration_factory(shared: _SharedDocuments):
    return lambda kind: shared.store(f"calibration:{kind}")


def _holdout_factory(shared: _SharedDocuments):
    return lambda name: shared.store(f"holdout:{name}")


def _verdict(*, adequate: bool, ts: float = 100.0) -> dict:
    return {
        "ts": ts,
        "n": 30,
        "n_correct": 15,
        "n_incorrect": 15,
        "discrimination": 0.7 if adequate else 0.01,
        "brier": 0.03,
        "adequate": adequate,
        "reason": "calibrated" if adequate else "drifted",
        "adversarial_discrimination": None,
        "resistance": None,
    }


def _query(digest: str, query_id: str) -> holdout.HoldoutQuery:
    return holdout.HoldoutQuery(
        holdout_sha256=digest,
        query_id=query_id,
        cycle_id=f"cycle-{query_id}",
        signature=f"signature-{query_id}",
        evaluator_epoch="judge-v1",
    )


def _shared_calibration(
    monkeypatch, tmp_path, shared: _SharedDocuments,
):
    verdict_path = tmp_path / "calibration_verdict.json"
    risk_path = tmp_path / "self_harness_calibration_receipt.json"
    monkeypatch.setattr(calibration, "_configured_authority_backend", lambda: "postgres")
    monkeypatch.setattr(calibration, "_active_tenant_id", lambda: "tenant-a")
    monkeypatch.setattr(calibration, "_new_verdict_store", _calibration_factory(shared))
    monkeypatch.setattr(calibration, "_verdict_path", lambda: verdict_path)
    monkeypatch.setattr(calibration, "_risk_verdict_path", lambda: risk_path)
    monkeypatch.setattr(
        calibration,
        "_strict_settings",
        lambda: calibration._validated_settings(calibration._settings()),
    )
    return verdict_path, risk_path


def _shared_holdout(monkeypatch, shared: _SharedDocuments) -> None:
    monkeypatch.setattr(holdout, "_configured_authority_backend", lambda: "postgres")
    monkeypatch.setattr(
        holdout, "_configured_holdout_authority_id", lambda: "test-study",
    )
    monkeypatch.setattr(holdout, "_active_tenant_id", lambda: "tenant-a")
    monkeypatch.setattr(holdout, "_new_holdout_store", _holdout_factory(shared))


def test_calibration_verdict_is_shared_and_local_file_never_appears(
    tmp_path, monkeypatch,
):
    shared = _SharedDocuments()
    verdict_path, _risk = _shared_calibration(monkeypatch, tmp_path, shared)
    samples = tmp_path / "calibration.ndjson"
    monkeypatch.setattr(calibration.time, "time", lambda: 200.0)
    monkeypatch.setattr(calibration, "_settings", lambda: {
        **calibration._DEFAULTS,
        "enforce": True,
        "min_samples": 20,
        "min_discrimination": 0.15,
    })
    for _index in range(15):
        calibration.record_sample(0.9, True, path=samples)
        calibration.record_sample(0.2, False, path=samples)

    report = calibration.run_assessment(samples_path=samples)

    assert report.adequate is True
    assert not verdict_path.exists()
    assert calibration._load_verdict()["adequate"] is True
    assert calibration.learning_frozen() is False
    document = shared.documents["calibration:general"]
    assert document["generation"] == 1
    assert document["verdict"]["n"] == 30


def test_calibration_gate_freezes_on_missing_outage_corruption_and_local_unfreeze(
    tmp_path, monkeypatch,
):
    shared = _SharedDocuments()
    _shared_calibration(monkeypatch, tmp_path, shared)
    monkeypatch.setattr(calibration, "_settings", lambda: {
        **calibration._DEFAULTS, "enforce": True,
    })
    monkeypatch.setenv("MAVERICK_LEARNING_FROZEN", "0")

    assert calibration.learning_frozen() is True


def test_shared_calibration_freezes_stale_and_future_verdicts(
    tmp_path,
    monkeypatch,
):
    shared = _SharedDocuments()
    _shared_calibration(monkeypatch, tmp_path, shared)
    monkeypatch.setattr(calibration, "_settings", lambda: {
        **calibration._DEFAULTS,
        "max_age_hours": 24.0,
    })
    monkeypatch.setattr(calibration.time, "time", lambda: 200_000.0)

    def install(ts: float) -> None:
        shared.documents["calibration:general"] = {
            "schema": calibration._AUTHORITY_SCHEMA,
            "kind": "general",
            "generation": 1,
            "verdict": _verdict(adequate=True, ts=ts),
        }

    install(200_000.0)
    assert calibration.learning_frozen() is False
    install(100_000.0)
    assert calibration.learning_frozen() is True
    install(201_000.0)
    assert calibration.learning_frozen() is True

    monkeypatch.setattr(calibration, "_new_verdict_store", lambda _kind: _FailingStore())
    assert calibration.learning_frozen() is True

    monkeypatch.setattr(calibration, "_new_verdict_store", _calibration_factory(shared))
    shared.documents["calibration:general"] = {
        "schema": calibration._AUTHORITY_SCHEMA,
        "kind": "general",
        "generation": 1,
        "verdict": {**_verdict(adequate=True), "unexpected": True},
    }
    assert calibration.learning_frozen() is True


@pytest.mark.parametrize(
    "policy_override",
    [
        {"min_samples": 31},
        {"min_discrimination": 0.8},
        {"min_resistance": 0.5},
    ],
)
def test_shared_calibration_requalifies_verdict_after_policy_tightening(
    tmp_path,
    monkeypatch,
    policy_override,
):
    shared = _SharedDocuments()
    _shared_calibration(monkeypatch, tmp_path, shared)
    monkeypatch.setattr(calibration.time, "time", lambda: 200.0)
    shared.documents["calibration:general"] = {
        "schema": calibration._AUTHORITY_SCHEMA,
        "kind": "general",
        "generation": 1,
        "verdict": _verdict(adequate=True, ts=200.0),
    }
    policy = {**calibration._DEFAULTS, **policy_override}
    monkeypatch.setattr(
        calibration,
        "_strict_settings",
        lambda: calibration._validated_settings(policy),
    )

    assert calibration.learning_frozen() is True


def test_risk_promotion_receipt_reads_shared_and_outage_denies(
    tmp_path, monkeypatch,
):
    shared = _SharedDocuments()
    _verdict_path, risk_path = _shared_calibration(monkeypatch, tmp_path, shared)
    samples = tmp_path / "calibration.ndjson"
    monkeypatch.setattr(calibration, "_samples_path", lambda: samples)
    monkeypatch.setattr(calibration.time, "time", lambda: 200.0)
    monkeypatch.setattr(calibration, "_settings", lambda: {
        **calibration._DEFAULTS,
        "min_samples": 20,
        "min_discrimination": 0.15,
    })
    for _index in range(15):
        calibration.record_sample(
            0.9, True, evaluator_id="judge:A", path=samples,
        )
        calibration.record_sample(
            0.2, False, evaluator_id="judge:A", path=samples,
        )
    calibration.run_assessment(
        verdict_path=risk_path,
        evaluator_id="judge:A",
        since=100.0,
    )
    settings = {"risk_limited": True, "calibration_max_age_hours": 24.0}

    assert self_improvement_runner._fresh_calibration_receipt(
        settings,
        now=200.0,
        evaluator_id="judge:A",
        evidence_since=100.0,
    ) is True
    assert not risk_path.exists()

    monkeypatch.setattr(calibration, "_new_verdict_store", lambda _kind: _FailingStore())
    assert self_improvement_runner._fresh_calibration_receipt(
        settings,
        now=200.0,
        evaluator_id="judge:A",
        evidence_since=100.0,
    ) is False


def test_risk_receipt_uses_current_resistance_and_stricter_age_policy(
    tmp_path,
    monkeypatch,
):
    shared = _SharedDocuments()
    _verdict_path, _risk_path = _shared_calibration(monkeypatch, tmp_path, shared)
    shared.documents["calibration:risk"] = {
        "schema": calibration._AUTHORITY_SCHEMA,
        "kind": "risk",
        "generation": 1,
        "verdict": {
            **_verdict(adequate=True, ts=100.0),
            "schema": "maverick-calibration-receipt-v2",
            "evaluator_id": "judge:A",
            "evidence_since": 50.0,
            "sample_min_ts": 50.0,
            "sample_max_ts": 100.0,
        },
    }
    runner_settings = {
        "risk_limited": True,
        "calibration_max_age_hours": 24.0,
    }

    policy = {**calibration._DEFAULTS, "min_resistance": 0.5}
    monkeypatch.setattr(
        calibration,
        "_strict_settings",
        lambda: calibration._validated_settings(policy),
    )
    assert self_improvement_runner._fresh_calibration_receipt(
        runner_settings,
        now=100.0,
        evaluator_id="judge:A",
        evidence_since=50.0,
    ) is False

    policy = {**calibration._DEFAULTS, "max_age_hours": 0.01}
    assert self_improvement_runner._fresh_calibration_receipt(
        runner_settings,
        now=200.0,
        evaluator_id="judge:A",
        evidence_since=50.0,
    ) is False


def test_calibration_stale_writer_conflict_is_explicit(tmp_path, monkeypatch):
    shared = _SharedDocuments()
    verdict_path, _risk = _shared_calibration(monkeypatch, tmp_path, shared)
    shared.first_read_barrier = threading.Barrier(2)
    shared.first_read_slots = 2

    def write(adequate: bool):
        try:
            return calibration._write_shared_verdict(
                "general", verdict_path, _verdict(adequate=adequate),
            )
        except calibration.CalibrationAuthorityConflict:
            return "stale"

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(write, value) for value in (True, False)]
        outcomes = [future.result(timeout=15) for future in futures]

    assert sorted(outcomes, key=str) == [1, "stale"]
    assert shared.documents["calibration:general"]["generation"] == 1


def test_calibration_generation_rejects_an_aba_writer(tmp_path, monkeypatch):
    shared = _SharedDocuments()
    verdict_path, _risk = _shared_calibration(monkeypatch, tmp_path, shared)
    payload = _verdict(adequate=True)
    stale = {
        "schema": calibration._AUTHORITY_SCHEMA,
        "kind": "general",
        "generation": 1,
        "verdict": payload,
    }
    current = {**copy.deepcopy(stale), "generation": 3}

    class _ABAStore:
        backend_kind = "postgres"

        def __init__(self):
            self.reads = 0

        def read(self):
            self.reads += 1
            return copy.deepcopy(stale if self.reads == 1 else current)

        def mutate(self, callback, **_kwargs):
            document = copy.deepcopy(current)
            return callback(document)

    store = _ABAStore()
    monkeypatch.setattr(calibration, "_new_verdict_store", lambda _kind: store)

    with pytest.raises(calibration.CalibrationAuthorityConflict, match="stale"):
        calibration._write_shared_verdict(
            "general", verdict_path, _verdict(adequate=False),
        )


def test_calibration_rejects_backend_drift_after_read(tmp_path, monkeypatch):
    shared = _SharedDocuments()
    _shared_calibration(monkeypatch, tmp_path, shared)
    shared.documents["calibration:general"] = {
        "schema": calibration._AUTHORITY_SCHEMA,
        "kind": "general",
        "generation": 1,
        "verdict": _verdict(adequate=True),
    }
    inner = shared.store("calibration:general")
    monkeypatch.setattr(
        calibration,
        "_new_verdict_store",
        lambda _kind: _DriftAfterRead(inner),
    )

    with pytest.raises(calibration.CalibrationAuthorityError, match="drifted"):
        calibration._load_verdict()


def test_calibration_selector_cannot_downgrade_after_shared_admission(
    tmp_path, monkeypatch,
):
    shared = _SharedDocuments()
    _shared_calibration(monkeypatch, tmp_path, shared)
    selected = {
        "value": governed_records._BackendSelection(
            "postgres",
            "postgresql://runtime:secret@db.internal/lightwork",  # pragma: allowlist secret
            False,
        ),
    }
    monkeypatch.setattr(
        governed_records, "_selection", lambda: selected["value"],
    )
    monkeypatch.setattr(
        calibration,
        "_configured_authority_backend",
        governed_records.configured_governed_records_backend,
    )
    monkeypatch.setattr(calibration.time, "time", lambda: 200.0)
    shared.documents["calibration:general"] = {
        "schema": calibration._AUTHORITY_SCHEMA,
        "kind": "general",
        "generation": 1,
        "verdict": _verdict(adequate=True, ts=200.0),
    }

    assert calibration.learning_frozen() is False
    selected["value"] = governed_records._BackendSelection("local", "", False)

    assert calibration.learning_frozen() is True
    with pytest.raises(
        calibration.CalibrationAuthorityError,
        match="selection is unavailable",
    ):
        calibration._load_verdict()


def test_shared_calibration_policy_outage_freezes_and_refuses_assessment(
    tmp_path, monkeypatch,
):
    shared = _SharedDocuments()
    _verdict_path, risk_path = _shared_calibration(monkeypatch, tmp_path, shared)
    monkeypatch.setattr(calibration.time, "time", lambda: 200.0)
    shared.documents["calibration:general"] = {
        "schema": calibration._AUTHORITY_SCHEMA,
        "kind": "general",
        "generation": 1,
        "verdict": _verdict(adequate=True, ts=200.0),
    }
    shared.documents["calibration:risk"] = {
        "schema": calibration._AUTHORITY_SCHEMA,
        "kind": "risk",
        "generation": 1,
        "verdict": {
            **_verdict(adequate=True, ts=200.0),
            "schema": "maverick-calibration-receipt-v2",
            "evaluator_id": "judge:A",
            "evidence_since": 100.0,
            "sample_min_ts": 100.0,
            "sample_max_ts": 200.0,
        },
    }
    before = copy.deepcopy(shared.documents)

    def unavailable():
        raise calibration.CalibrationAuthorityError(
            "shared calibration authority policy is unavailable"
        )

    monkeypatch.setattr(calibration, "_strict_settings", unavailable)

    assert calibration.learning_frozen() is True
    with pytest.raises(
        calibration.CalibrationAuthorityError,
        match="policy is unavailable",
    ):
        calibration.run_assessment(samples_path=tmp_path / "samples.ndjson")
    assert shared.documents == before
    assert self_improvement_runner._fresh_calibration_receipt(
        {"risk_limited": True, "calibration_max_age_hours": 24.0},
        now=200.0,
        evaluator_id="judge:A",
        evidence_since=100.0,
    ) is False
    assert not risk_path.exists()


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("enforce", "false"),
        ("collect_from_coding", 1),
        ("min_samples", "20"),
        ("min_discrimination", float("nan")),
        ("min_resistance", -0.1),
        ("max_age_hours", 0.0),
    ],
)
def test_shared_calibration_policy_rejects_coercion_and_clamping(
    monkeypatch, key, value,
):
    from maverick import config

    monkeypatch.setattr(config, "load_config", lambda: {
        "calibration": {key: value},
    })
    monkeypatch.setattr(config, "config_source_errors", dict)

    with pytest.raises(calibration.CalibrationAuthorityError):
        calibration._strict_settings()


def test_shared_calibration_policy_rejects_unreadable_source(monkeypatch):
    from maverick import config

    monkeypatch.setattr(config, "load_config", dict)
    monkeypatch.setattr(
        config,
        "config_source_errors",
        lambda: {"config.toml": "malformed"},
    )

    with pytest.raises(
        calibration.CalibrationAuthorityError,
        match="source is unreadable",
    ):
        calibration._strict_settings()


def test_calibration_cutover_blocks_then_explicitly_migrates(tmp_path, monkeypatch):
    shared = _SharedDocuments()
    verdict_path, _risk = _shared_calibration(monkeypatch, tmp_path, shared)
    verdict_path.write_text(json.dumps(_verdict(adequate=False)), encoding="utf-8")
    monkeypatch.setattr(calibration, "_settings", lambda: {
        **calibration._DEFAULTS, "enforce": False,
    })

    assert calibration.learning_frozen() is True
    assert "calibration:general" not in shared.documents

    archive = calibration.migrate_legacy_verdict()

    assert archive.exists() and not verdict_path.exists()
    assert calibration._load_verdict()["adequate"] is False
    assert calibration.learning_frozen() is True


def test_calibration_cutover_rejects_leaf_replacement_before_commit(
    tmp_path, monkeypatch,
):
    from maverick import file_lock

    shared = _SharedDocuments()
    verdict_path, _risk = _shared_calibration(monkeypatch, tmp_path, shared)
    verdict_path.write_text(json.dumps(_verdict(adequate=False)), encoding="utf-8")
    replacement = tmp_path / "replacement-verdict.json"
    replacement.write_text(json.dumps(_verdict(adequate=True)), encoding="utf-8")
    displaced = tmp_path / "displaced-verdict.json"
    original_read = file_lock._read_snapshot_descriptor

    def replace_after_read(fd, *, max_bytes):
        raw = original_read(fd, max_bytes=max_bytes)
        os.replace(verdict_path, displaced)
        os.replace(replacement, verdict_path)
        return raw

    monkeypatch.setattr(file_lock, "_read_snapshot_descriptor", replace_after_read)

    with pytest.raises(calibration.CalibrationAuthorityError, match="missing or invalid"):
        calibration.migrate_legacy_verdict()

    assert verdict_path.exists()
    assert not verdict_path.with_name(verdict_path.name + ".migrated").exists()
    assert shared.documents == {}


def test_calibration_migration_aba_uses_one_bound_source_snapshot(
    tmp_path, monkeypatch,
):
    from maverick import file_lock

    shared = _SharedDocuments()
    verdict_path, _risk = _shared_calibration(monkeypatch, tmp_path, shared)
    original = json.dumps(_verdict(adequate=False)).encode("utf-8")
    replacement_bytes = json.dumps(_verdict(adequate=True)).encode("utf-8")
    verdict_path.write_bytes(original)
    replacement = tmp_path / "replacement-verdict.json"
    replacement.write_bytes(replacement_bytes)
    displaced = tmp_path / "displaced-verdict.json"
    original_read = file_lock._read_snapshot_descriptor
    raced = False

    def read_during_aba(fd, *, max_bytes):
        nonlocal raced
        if not raced:
            raced = True
            os.replace(verdict_path, displaced)   # A -> away
            os.replace(replacement, verdict_path)  # B -> source
            raw = original_read(fd, max_bytes=max_bytes)  # still reads A
            os.replace(verdict_path, replacement)  # B -> its original name
            os.replace(displaced, verdict_path)   # A -> source
            return raw
        return original_read(fd, max_bytes=max_bytes)

    monkeypatch.setattr(file_lock, "_read_snapshot_descriptor", read_during_aba)

    archive = calibration.migrate_legacy_verdict()

    assert raced is True
    assert archive.read_bytes() == original
    assert replacement.read_bytes() == replacement_bytes
    assert not verdict_path.exists()
    retired = list(tmp_path.glob(f".{verdict_path.name}-migration-retired-*.source"))
    assert len(retired) == 1 and retired[0].read_bytes() == original
    assert shared.documents["calibration:general"]["verdict"]["adequate"] is False


def test_calibration_migration_never_overwrites_racing_archive(
    tmp_path, monkeypatch,
):
    from maverick import file_lock

    shared = _SharedDocuments()
    verdict_path, _risk = _shared_calibration(monkeypatch, tmp_path, shared)
    verdict_path.write_text(json.dumps(_verdict(adequate=False)), encoding="utf-8")
    archive = verdict_path.with_name(verdict_path.name + ".migrated")
    original_create = file_lock.atomic_create_bytes

    def plant_before_create(path, data, **kwargs):
        if Path(path) == archive and not archive.exists():
            archive.write_bytes(b"pre-existing archive")
        return original_create(path, data, **kwargs)

    monkeypatch.setattr(file_lock, "atomic_create_bytes", plant_before_create)

    with pytest.raises(calibration.CalibrationAuthorityError, match="archival failed"):
        calibration.migrate_legacy_verdict()

    assert archive.read_bytes() == b"pre-existing archive"
    assert verdict_path.exists()
    assert shared.documents["calibration:general"]["verdict"]["adequate"] is False


def test_retirement_preserves_post_verification_leaf_replacement(
    tmp_path, monkeypatch,
):
    """A latest-seam leaf swap must not redirect retirement deletion."""
    from maverick import file_lock

    source = tmp_path / "legacy-authority.bin"
    replacement = tmp_path / "replacement-authority.bin"
    displaced = tmp_path / "displaced-retirement-source.bin"
    original_bytes = b"original-authority" * 8
    replacement_bytes = b"replacement-authority" * 8
    file_lock.atomic_create_bytes(source, original_bytes)
    file_lock.atomic_create_bytes(replacement, replacement_bytes)
    snapshot = file_lock.read_private_file_snapshot(source, max_bytes=4096)
    original_read = file_lock.read_private_file_snapshot
    raced = False

    def replace_after_verified_read(path, **kwargs):
        nonlocal raced
        observed = original_read(path, **kwargs)
        candidate = Path(path)
        if "migration-retired" in candidate.name and not raced:
            raced = True
            os.replace(candidate, displaced)
            os.replace(replacement, candidate)
        return observed

    monkeypatch.setattr(
        file_lock,
        "read_private_file_snapshot",
        replace_after_verified_read,
    )

    with pytest.raises(PermissionError, match="changed after verification"):
        file_lock.retire_private_file_snapshot(source, snapshot)

    assert raced is True
    # The exact validated bytes are recreated at the active path, keeping every
    # shared-authority reader blocked after the ambiguous retirement.
    assert source.read_bytes() == original_bytes
    # Neither object observed in the race was deleted.
    assert displaced.read_bytes() == original_bytes
    retired = list(tmp_path.glob(".*-migration-retired-*.source"))
    assert len(retired) == 1
    assert retired[0].read_bytes() == replacement_bytes


def test_retirement_move_never_overwrites_target_published_at_syscall(
    tmp_path, monkeypatch,
):
    """The native move must reject a destination planted after selection."""
    from maverick import file_lock

    source = tmp_path / "legacy-authority.bin"
    original_bytes = b"validated-legacy-authority"
    collision_bytes = b"pre-existing-retirement-target"
    file_lock.atomic_create_bytes(source, original_bytes)
    snapshot = file_lock.read_private_file_snapshot(source, max_bytes=4096)
    original_move = file_lock._move_private_file_no_replace
    collisions: list[Path] = []

    def plant_inside_move(source_path, target_path):
        target = Path(target_path)
        if not collisions:
            file_lock.atomic_create_bytes(target, collision_bytes)
            collisions.append(target)
        return original_move(Path(source_path), target)

    monkeypatch.setattr(
        file_lock,
        "_move_private_file_no_replace",
        plant_inside_move,
    )

    retired = file_lock.retire_private_file_snapshot(source, snapshot)

    assert len(collisions) == 1
    assert collisions[0].read_bytes() == collision_bytes
    assert retired != collisions[0]
    assert retired.read_bytes() == original_bytes
    assert not source.exists()


def test_shared_authorities_require_an_active_tenant(tmp_path, monkeypatch):
    shared = _SharedDocuments()
    _shared_calibration(monkeypatch, tmp_path, shared)
    monkeypatch.setattr(calibration, "_active_tenant_id", lambda: None)
    assert calibration.learning_frozen() is True

    _shared_holdout(monkeypatch, shared)
    monkeypatch.setattr(holdout, "_active_tenant_id", lambda: None)
    with pytest.raises(holdout.HoldoutLedgerError, match="active tenant"):
        holdout.HoldoutQueryLedger.provision(tmp_path / "holdout.db")
    assert not shared.documents


def test_holdout_stable_authority_id_unifies_different_replica_paths(
    tmp_path, monkeypatch,
):
    shared = _SharedDocuments()
    _shared_holdout(monkeypatch, shared)
    first = holdout.HoldoutQueryLedger.provision(
        tmp_path / "replica-a" / "holdout.db",
        authority_id="primary-study",
        now=lambda: 10.0,
    )
    second = holdout.HoldoutQueryLedger(
        tmp_path / "replica-b" / "different.db",
        authority_id="primary-study",
        now=lambda: 20.0,
    )
    other = holdout.HoldoutQueryLedger(
        tmp_path / "replica-c" / "holdout.db",
        authority_id="other-study",
    )
    digest = holdout.fingerprint_manifest(["sealed"])

    second.authorize(_query(digest, "q-1"), holdout.AlphaBudget())

    assert first.path != second.path
    assert first._authority_document_name == second._authority_document_name
    assert first._authority_document_name != other._authority_document_name
    assert first.status(digest).queries == 1
    assert len(shared.documents) == 1


@pytest.mark.parametrize("authority_id", [None, " unsafe", "", "a/b"])
def test_shared_holdout_refuses_missing_or_invalid_authority_id(
    tmp_path, monkeypatch, authority_id,
):
    shared = _SharedDocuments()
    _shared_holdout(monkeypatch, shared)
    monkeypatch.setattr(
        holdout,
        "_configured_holdout_authority_id",
        lambda: authority_id,
    )

    with pytest.raises(holdout.HoldoutLedgerError, match="authority_id"):
        holdout.HoldoutQueryLedger.provision(tmp_path / "holdout.db")

    assert shared.documents == {}


def test_dangling_legacy_links_block_shared_learning_cutover(
    tmp_path,
    monkeypatch,
):
    verdict_link = tmp_path / "calibration_verdict.json"
    holdout_link = tmp_path / "holdout.db"
    try:
        verdict_link.symlink_to(tmp_path / "missing-verdict.json")
        holdout_link.symlink_to(tmp_path / "missing-holdout.db")
    except OSError:
        pytest.skip("symlink creation is unavailable")

    shared = _SharedDocuments()
    _shared_calibration(monkeypatch, tmp_path, shared)
    assert calibration.learning_frozen() is True
    _shared_holdout(monkeypatch, shared)
    with pytest.raises(holdout.HoldoutLedgerError, match="legacy"):
        holdout.HoldoutQueryLedger.provision(holdout_link)
    assert not shared.documents


def test_holdout_shared_replicas_cannot_double_spend(tmp_path, monkeypatch):
    shared = _SharedDocuments()
    _shared_holdout(monkeypatch, shared)
    path = tmp_path / "logical-holdout.db"
    holdout.HoldoutQueryLedger.provision(path, now=lambda: 10.0)
    digest = holdout.fingerprint_manifest(["sealed-a", "sealed-b"])
    policy = holdout.AlphaBudget(
        family_alpha=0.04, query_alpha=0.01, max_queries=4,
    )

    def spend(index: int):
        ledger = holdout.HoldoutQueryLedger(path, now=lambda: 20.0 + index)
        try:
            return ledger.authorize(_query(digest, f"q-{index}"), policy)
        except holdout.HoldoutBudgetExhausted:
            return "exhausted"

    with ThreadPoolExecutor(max_workers=10) as pool:
        outcomes = list(pool.map(spend, range(10), timeout=20))

    permits = [item for item in outcomes if item != "exhausted"]
    assert len(permits) == 4
    assert sorted(item.ordinal for item in permits) == [1, 2, 3, 4]
    status = holdout.HoldoutQueryLedger(path).status(digest)
    assert status.queries == 4
    assert status.alpha_spent == pytest.approx(0.04)
    assert status.ledger_events == 5
    assert not path.exists()


def test_holdout_rejected_transition_rolls_back_document(tmp_path, monkeypatch):
    shared = _SharedDocuments()
    _shared_holdout(monkeypatch, shared)
    path = tmp_path / "logical-holdout.db"
    ledger = holdout.HoldoutQueryLedger.provision(path)
    digest = holdout.fingerprint_manifest(["sealed"])
    original = holdout.AlphaBudget(
        family_alpha=0.02, query_alpha=0.01, max_queries=2,
    )
    ledger.authorize(_query(digest, "q-1"), original)
    before = copy.deepcopy(shared.documents)

    with pytest.raises(holdout.HoldoutLedgerError, match="immutable"):
        holdout.HoldoutQueryLedger(path).authorize(
            _query(digest, "q-2"),
            holdout.AlphaBudget(
                family_alpha=0.03, query_alpha=0.01, max_queries=3,
            ),
        )

    assert shared.documents == before
    assert holdout.HoldoutQueryLedger(path).status(digest).queries == 1


def test_holdout_outage_backend_drift_and_corruption_fail_closed(
    tmp_path, monkeypatch,
):
    shared = _SharedDocuments()
    _shared_holdout(monkeypatch, shared)
    path = tmp_path / "logical-holdout.db"
    holdout.HoldoutQueryLedger.provision(path)

    monkeypatch.setattr(holdout, "_new_holdout_store", lambda _name: _FailingStore())
    with pytest.raises(holdout.HoldoutLedgerError, match="unavailable"):
        holdout.HoldoutQueryLedger(path).verify()

    monkeypatch.setattr(holdout, "_new_holdout_store", _holdout_factory(shared))
    shared.backend_kind = "local"
    with pytest.raises(holdout.HoldoutLedgerError, match="drifted"):
        holdout.HoldoutQueryLedger(path).verify()

    shared.backend_kind = "postgres"
    key = next(iter(shared.documents))
    shared.documents[key]["generation"] = 2
    with pytest.raises(holdout.HoldoutLedgerTampered, match="generation"):
        holdout.HoldoutQueryLedger(path).verify()


def test_holdout_rejects_backend_drift_after_read(tmp_path, monkeypatch):
    shared = _SharedDocuments()
    _shared_holdout(monkeypatch, shared)
    path = tmp_path / "logical-holdout.db"
    holdout.HoldoutQueryLedger.provision(path)

    def drifting(name: str):
        return _DriftAfterRead(shared.store(f"holdout:{name}"))

    monkeypatch.setattr(holdout, "_new_holdout_store", drifting)
    with pytest.raises(holdout.HoldoutLedgerError, match="drifted"):
        holdout.HoldoutQueryLedger(path).verify()


def test_holdout_selector_cannot_downgrade_after_shared_admission(
    tmp_path, monkeypatch,
):
    shared = _SharedDocuments()
    _shared_holdout(monkeypatch, shared)
    selected = {
        "value": governed_records._BackendSelection(
            "postgres",
            "postgresql://runtime:secret@db.internal/lightwork",  # pragma: allowlist secret
            False,
        ),
    }
    monkeypatch.setattr(
        governed_records, "_selection", lambda: selected["value"],
    )
    monkeypatch.setattr(
        holdout,
        "_configured_authority_backend",
        governed_records.configured_governed_records_backend,
    )
    path = tmp_path / "logical-holdout.db"
    holdout.HoldoutQueryLedger.provision(path)

    selected["value"] = governed_records._BackendSelection("local", "", False)

    with pytest.raises(holdout.HoldoutLedgerError, match="selection is unavailable"):
        holdout.HoldoutQueryLedger(path).verify()


def test_holdout_cutover_blocks_then_migrates_exact_chain(tmp_path, monkeypatch):
    path = tmp_path / "holdout.db"
    monkeypatch.setattr(holdout, "_configured_authority_backend", lambda: "local")
    local = holdout.HoldoutQueryLedger.provision(path, now=lambda: 10.0)
    digest = holdout.fingerprint_manifest(["sealed"])
    local.authorize(_query(digest, "q-1"), holdout.AlphaBudget())

    shared = _SharedDocuments()
    _shared_holdout(monkeypatch, shared)
    with pytest.raises(holdout.HoldoutLedgerError, match="legacy"):
        holdout.HoldoutQueryLedger(path).verify()
    assert not shared.documents

    archive = holdout.HoldoutQueryLedger.migrate_legacy(path)

    assert archive.exists() and not path.exists()
    migrated = holdout.HoldoutQueryLedger(path)
    assert migrated.verify().ledger_events == 2
    assert migrated.status(digest).queries == 1


def test_holdout_cutover_rejects_leaf_replacement_before_commit(
    tmp_path, monkeypatch,
):
    from maverick import file_lock

    source = tmp_path / "holdout.db"
    replacement = tmp_path / "replacement.db"
    monkeypatch.setattr(holdout, "_configured_authority_backend", lambda: "local")
    holdout.HoldoutQueryLedger.provision(source, now=lambda: 10.0)
    holdout.HoldoutQueryLedger.provision(replacement, now=lambda: 11.0)

    shared = _SharedDocuments()
    _shared_holdout(monkeypatch, shared)
    displaced = tmp_path / "displaced.db"
    original_read = file_lock._read_snapshot_descriptor

    def replace_after_read(fd, *, max_bytes):
        raw = original_read(fd, max_bytes=max_bytes)
        os.replace(source, displaced)
        os.replace(replacement, source)
        return raw

    monkeypatch.setattr(file_lock, "_read_snapshot_descriptor", replace_after_read)

    with pytest.raises(holdout.HoldoutLedgerError, match="snapshotted"):
        holdout.HoldoutQueryLedger.migrate_legacy(source)

    assert source.exists()
    assert not source.with_name(source.name + ".migrated").exists()
    assert shared.documents == {}


def test_holdout_migration_aba_uses_one_bound_sqlite_snapshot(
    tmp_path, monkeypatch,
):
    from maverick import file_lock

    source = tmp_path / "holdout.db"
    replacement = tmp_path / "replacement.db"
    monkeypatch.setattr(holdout, "_configured_authority_backend", lambda: "local")
    original_ledger = holdout.HoldoutQueryLedger.provision(source, now=lambda: 10.0)
    original_digest = holdout.fingerprint_manifest(["original"])
    original_ledger.authorize(_query(original_digest, "q-original"), holdout.AlphaBudget())
    replacement_ledger = holdout.HoldoutQueryLedger.provision(
        replacement,
        now=lambda: 11.0,
    )
    replacement_digest = holdout.fingerprint_manifest(["replacement"])
    replacement_ledger.authorize(
        _query(replacement_digest, "q-replacement"),
        holdout.AlphaBudget(),
    )
    original_bytes = source.read_bytes()
    replacement_bytes = replacement.read_bytes()

    shared = _SharedDocuments()
    _shared_holdout(monkeypatch, shared)
    displaced = tmp_path / "displaced.db"
    original_read = file_lock._read_snapshot_descriptor
    raced = False

    def read_during_aba(fd, *, max_bytes):
        nonlocal raced
        if not raced:
            raced = True
            os.replace(source, displaced)          # A -> away
            os.replace(replacement, source)        # B -> source
            raw = original_read(fd, max_bytes=max_bytes)  # still reads A
            os.replace(source, replacement)        # B -> its original name
            os.replace(displaced, source)          # A -> source
            return raw
        return original_read(fd, max_bytes=max_bytes)

    monkeypatch.setattr(file_lock, "_read_snapshot_descriptor", read_during_aba)

    archive = holdout.HoldoutQueryLedger.migrate_legacy(source)

    assert raced is True
    assert archive.read_bytes() == original_bytes
    assert replacement.read_bytes() == replacement_bytes
    assert not source.exists()
    retired = list(tmp_path.glob(f".{source.name}-migration-retired-*.source"))
    assert len(retired) == 1 and retired[0].read_bytes() == original_bytes
    migrated = holdout.HoldoutQueryLedger(source)
    assert migrated.status(original_digest).queries == 1
    assert migrated.status(replacement_digest).queries == 0


def test_holdout_migration_never_overwrites_racing_archive(
    tmp_path, monkeypatch,
):
    from maverick import file_lock

    source = tmp_path / "holdout.db"
    monkeypatch.setattr(holdout, "_configured_authority_backend", lambda: "local")
    holdout.HoldoutQueryLedger.provision(source, now=lambda: 10.0)
    archive = source.with_name(source.name + ".migrated")
    shared = _SharedDocuments()
    _shared_holdout(monkeypatch, shared)
    original_create = file_lock.atomic_create_bytes

    def plant_before_create(path, data, **kwargs):
        if Path(path) == archive and not archive.exists():
            archive.write_bytes(b"pre-existing archive")
        return original_create(path, data, **kwargs)

    monkeypatch.setattr(file_lock, "atomic_create_bytes", plant_before_create)

    with pytest.raises(holdout.HoldoutLedgerError, match="archival failed"):
        holdout.HoldoutQueryLedger.migrate_legacy(source)

    assert archive.read_bytes() == b"pre-existing archive"
    assert source.exists()
    assert len(shared.documents) == 1
