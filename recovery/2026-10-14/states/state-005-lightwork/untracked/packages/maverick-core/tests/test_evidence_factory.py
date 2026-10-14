"""Production evidence campaigns stay provenance-bound and non-synthetic."""
from __future__ import annotations

import copy
import threading
import time
from types import SimpleNamespace

import pytest
from maverick import evidence_factory
from maverick.privacy_ops import RecordConflict


@pytest.fixture(autouse=True)
def _isolate_state(tmp_path, monkeypatch):
    monkeypatch.setenv("MAVERICK_HOME", str(tmp_path))
    config_path = tmp_path / "config.toml"
    monkeypatch.setenv("MAVERICK_CONFIG", str(config_path))
    config_path.write_text(
        "[evidence_factory]\nenable = true\n\n"
        "[evidence_graph]\nenable = true\n",
        encoding="utf-8",
    )
    from maverick import config

    config._toml_cache.clear()
    config._toml_errors.clear()
    import maverick.audit as audit

    monkeypatch.setattr(audit, "record", lambda *_args, **_kwargs: True)


def _provenance() -> evidence_factory.Provenance:
    return evidence_factory.Provenance(
        commit="a" * 40,
        image_digest="sha256:" + "b" * 64,
        config_sha256=evidence_factory.config_fingerprint(
            {"evidence_factory": {"enable": True}, "provider": {"api_key": "hidden"}}  # pragma: allowlist secret
        ),
        deployment_id="prod-us-east-1/acme",
        migrations={"heads": {"sqlite": 25, "postgres": 25}},
        dependencies={"maverick-core": "0.1.6", "python": "3.12.9"},
    )


def _objectives() -> evidence_factory.ServiceObjectives:
    return evidence_factory.ServiceObjectives(
        slo={"availability_percent": 99.9, "p95_latency_ms": 500},
        rto_seconds=900,
        rpo_seconds=60,
    )


def _start(*, controls=None):
    return evidence_factory.start_campaign(
        title="Production readiness campaign",
        provenance=_provenance(),
        objectives=_objectives(),
        controls=controls,
        actor="release-operator",
        now=1_700_000_000,
    )


def test_config_gate_fails_closed(tmp_path):
    from maverick import config

    (tmp_path / "config.toml").write_text(
        "[evidence_factory]\nenable = false\n",
        encoding="utf-8",
    )
    config._toml_cache.clear()
    assert evidence_factory.enabled() is False
    with pytest.raises(evidence_factory.EvidenceFactoryDisabled):
        _start()


def test_config_fingerprint_redacts_secret_values_but_binds_policy():
    first = evidence_factory.config_fingerprint(
        {"provider": {"api_key": "sk-first-secret", "model": "local-a"}}  # pragma: allowlist secret
    )
    rotated = evidence_factory.config_fingerprint(
        {"provider": {"api_key": "sk-second-secret", "model": "local-a"}}  # pragma: allowlist secret
    )
    changed_policy = evidence_factory.config_fingerprint(
        {"provider": {"api_key": "sk-second-secret", "model": "local-b"}}  # pragma: allowlist secret
    )
    assert first == rotated
    assert first != changed_policy
    assert len(first) == 64


def test_campaign_redacts_results_hashes_artifacts_and_uses_cas():
    campaign = _start(controls=["RES-01"])

    def _probe(_context):
        return evidence_factory.CollectorOutcome(
            evidence_factory.PASS,
            "OPENAI_API_KEY=sk-test-abcdefghijklmnopqrstuvwxyz123456",  # pragma: allowlist secret
            data={
                "access_token": "bearer-secret",
                "nested": {"result": "passed"},
            },
            artifacts=(evidence_factory.ArtifactInput(
                "readiness.json",
                "OPENAI_API_KEY=sk-test-abcdefghijklmnopqrstuvwxyz123456",  # pragma: allowlist secret
                "application/json",
            ),),
        )

    collector = evidence_factory.CollectorSpec("readiness", "integration", _probe)
    collected = evidence_factory.collect_campaign(
        campaign["id"],
        expected_revision=campaign["revision"],
        collectors=[collector],
        actor="release-operator",
    )
    result = collected["results"][0]
    assert result["status"] == evidence_factory.PASS
    assert "sk-test" not in result["summary"]
    assert result["data"]["access_token"] == "[REDACTED:sensitive-key]"
    assert result["artifacts"][0]["redactions_applied"] >= 1
    assert len(result["artifacts"][0]["sha256"]) == 64

    with pytest.raises(RecordConflict):
        evidence_factory.collect_campaign(
            campaign["id"],
            expected_revision=campaign["revision"],
            collectors=[collector],
            actor="stale-operator",
        )


def test_missing_witness_does_not_execute_drill_and_is_not_run():
    campaign = _start()
    called = False

    def _drill(_context):
        nonlocal called
        called = True
        return evidence_factory.CollectorOutcome(
            evidence_factory.PASS, "restore met the declared RTO"
        )

    collected = evidence_factory.collect_campaign(
        campaign["id"],
        expected_revision=campaign["revision"],
        collectors=[evidence_factory.CollectorSpec(
            "restore_drill", "operator_attested", _drill, drill=True
        )],
        actor="release-operator",
    )
    result = collected["results"][0]
    assert called is False
    assert result["status"] == evidence_factory.NOT_RUN
    assert result["drill_id"].startswith("EDR-")
    drill = evidence_factory.get_drill(result["drill_id"])
    assert drill is not None
    assert drill["status"] == evidence_factory.NOT_RUN
    assert drill["revision"] == 2


def test_witnessed_drill_is_bound_to_campaign_and_manifest():
    campaign = _start()
    witness = evidence_factory.OperatorWitness(
        identity="incident-commander@example.com",
        role="recovery witness",
        statement="Observed an isolated restore; no production target was modified.",
        witnessed_at=1_700_000_100,
    )
    collected = evidence_factory.collect_campaign(
        campaign["id"],
        expected_revision=campaign["revision"],
        collectors=[evidence_factory.CollectorSpec(
            "restore_drill",
            "operator_attested",
            lambda _context: evidence_factory.CollectorOutcome(
                evidence_factory.PASS,
                "isolated restore completed inside the declared RTO",
                data={"rto_seconds_observed": 812},
            ),
            drill=True,
        )],
        witnesses={"restore_drill": witness},
        actor="release-operator",
    )
    result = collected["results"][0]
    assert result["status"] == evidence_factory.PASS
    assert result["witness"]["identity"] == witness.identity
    assert evidence_factory.list_drills(campaign_id=campaign["id"])[0][
        "campaign_id"
    ] == campaign["id"]

    finalized = evidence_factory.finalize_campaign(
        campaign["id"],
        expected_revision=collected["revision"],
        actor="release-operator",
        now=1_700_000_200,
        ingest_graph=False,
    )
    assert finalized["manifest"]["witnessed_drills"] == 1
    assert finalized["manifest"]["overall_status"] == evidence_factory.PASS


def test_timeout_and_malformed_results_are_error_not_pass():
    campaign = _start()

    def _slow(_context):
        time.sleep(3.0)
        return evidence_factory.CollectorOutcome(evidence_factory.PASS, "too late")

    started = time.perf_counter()
    collected = evidence_factory.collect_campaign(
        campaign["id"],
        expected_revision=campaign["revision"],
        collectors=[
            evidence_factory.CollectorSpec(
                "slow_live_read", "live", _slow, timeout_seconds=0.01
            ),
            evidence_factory.CollectorSpec(
                "malformed", "offline", lambda _context: True
            ),
        ],
        actor="release-operator",
    )
    # Spawn startup is intentionally included. The 3-second callback itself
    # must not be awaited to completion.
    assert time.perf_counter() - started < 2.5
    assert [row["status"] for row in collected["results"]] == [
        evidence_factory.ERROR,
        evidence_factory.ERROR,
    ]


def test_timed_out_collector_is_killed_before_delayed_side_effect(tmp_path):
    campaign = _start()
    side_effect = tmp_path / "late-side-effect.txt"

    def _slow_write(_context):
        time.sleep(0.5)
        side_effect.write_text("collector survived timeout", encoding="utf-8")
        return evidence_factory.CollectorOutcome(evidence_factory.PASS, "too late")

    collected = evidence_factory.collect_campaign(
        campaign["id"],
        expected_revision=campaign["revision"],
        collectors=[evidence_factory.CollectorSpec(
            "killable_timeout", "live", _slow_write, timeout_seconds=0.05
        )],
        actor="release-operator",
    )
    assert collected["results"][0]["status"] == evidence_factory.ERROR
    time.sleep(0.7)
    assert not side_effect.exists()


def test_campaign_reservation_prevents_duplicate_concurrent_callbacks(tmp_path):
    campaign = _start()
    invocations = tmp_path / "collector-invocations.txt"

    def _count_call(_context):
        with invocations.open("a", encoding="utf-8") as handle:
            handle.write("called\n")
        time.sleep(0.1)
        return evidence_factory.CollectorOutcome(evidence_factory.PASS, "once")

    collector = evidence_factory.CollectorSpec(
        "single_owner", "integration", _count_call, timeout_seconds=5
    )
    outcomes: list[object] = []
    start = threading.Barrier(3)

    def _race() -> None:
        start.wait(timeout=5)
        try:
            outcomes.append(evidence_factory.collect_campaign(
                campaign["id"],
                expected_revision=campaign["revision"],
                collectors=[collector],
                actor="release-operator",
            ))
        except BaseException as exc:  # noqa: BLE001 - asserted below
            outcomes.append(exc)

    racers = [threading.Thread(target=_race) for _ in range(2)]
    for racer in racers:
        racer.start()
    start.wait(timeout=5)
    for racer in racers:
        racer.join(timeout=10)
        assert not racer.is_alive()

    assert sum(isinstance(item, dict) for item in outcomes) == 1
    assert sum(isinstance(item, RecordConflict) for item in outcomes) == 1
    assert invocations.read_text(encoding="utf-8").splitlines() == ["called"]


def test_crashed_collection_and_drill_are_stale_recoverable_and_fenced(
    monkeypatch,
):
    campaign = _start()
    witness = evidence_factory.OperatorWitness(
        identity="recovery-operator@example.com",
        role="recovery witness",
        statement="Observed the isolated crash-recovery exercise.",
        witnessed_at=1_700_000_100,
    )

    def _crash_after_drill_start(*_args, **_kwargs):
        raise SystemExit("simulated collector host crash")

    monkeypatch.setattr(evidence_factory, "_run_callback", _crash_after_drill_start)
    spec = evidence_factory.CollectorSpec(
        "crash_recovery_drill",
        "operator_attested",
        lambda _context: evidence_factory.CollectorOutcome(
            evidence_factory.PASS,
            "unreachable",
        ),
        timeout_seconds=0.01,
        drill=True,
    )
    with pytest.raises(SystemExit, match="simulated collector host crash"):
        evidence_factory.collect_campaign(
            campaign["id"],
            expected_revision=campaign["revision"],
            collectors=[spec],
            witnesses={spec.name: witness},
            actor="release-operator",
        )

    stuck = evidence_factory.get_campaign(campaign["id"])
    assert stuck is not None
    assert stuck["status"] == "collection_running"
    attempt = stuck["collection_attempt"]
    deadline = attempt["lease_expires_at"]
    drills = evidence_factory.list_drills(campaign_id=campaign["id"])
    assert len(drills) == 1 and drills[0]["status"] == "running"
    assert drills[0]["collection_attempt_id"] == attempt["id"]

    with pytest.raises(ValueError, match="not stale"):
        evidence_factory.recover_stale_campaign(
            campaign["id"],
            expected_revision=stuck["revision"],
            actor="recovery-operator",
            reason="collector host stopped before committing its result",
            now=deadline - 0.001,
        )

    recovered = evidence_factory.recover_stale_campaign(
        campaign["id"],
        expected_revision=stuck["revision"],
        actor="recovery-operator",
        reason="collector host stopped before committing its result",
        now=deadline,
    )
    assert recovered["status"] == "collecting"
    assert recovered["collection_attempt"] is None
    abandoned = recovered["collection_attempt_history"][-1]
    assert abandoned["id"] == attempt["id"]
    assert abandoned["status"] == "aborted"
    assert abandoned["abort"]["recovery"] is True
    recovered_drill = evidence_factory.get_drill(drills[0]["id"])
    assert recovered_drill is not None
    assert recovered_drill["status"] == "aborted"
    assert recovered_drill["abort"]["recovery"] is True

    # The expired owner still holds the reservation revision. Recovery changed
    # that revision and removed its attempt id, so a late commit/retry is fenced.
    with pytest.raises(RecordConflict):
        evidence_factory.collect_campaign(
            campaign["id"],
            expected_revision=stuck["revision"],
            collectors=[],
            actor="expired-owner",
        )
    retried = evidence_factory.collect_campaign(
        campaign["id"],
        expected_revision=recovered["revision"],
        collectors=[],
        actor="recovery-operator",
    )
    assert retried["status"] == "collected"


def test_explicit_campaign_and_drill_abort_are_terminal_cas_fences(monkeypatch):
    campaign = _start()

    def _crash_after_drill_start(*_args, **_kwargs):
        raise SystemExit("simulated interruption")

    monkeypatch.setattr(evidence_factory, "_run_callback", _crash_after_drill_start)
    witness = evidence_factory.OperatorWitness(
        identity="incident-commander@example.com",
        role="incident commander",
        statement="Ordered the evidence attempt stopped.",
        witnessed_at=1_700_000_100,
    )
    spec = evidence_factory.CollectorSpec(
        "abortable_drill",
        "operator_attested",
        lambda _context: evidence_factory.CollectorOutcome(
            evidence_factory.PASS,
            "unreachable",
        ),
        drill=True,
    )
    with pytest.raises(SystemExit, match="simulated interruption"):
        evidence_factory.collect_campaign(
            campaign["id"],
            expected_revision=campaign["revision"],
            collectors=[spec],
            witnesses={spec.name: witness},
            actor="release-operator",
        )
    running_campaign = evidence_factory.get_campaign(campaign["id"])
    running_drill = evidence_factory.list_drills(campaign_id=campaign["id"])[0]
    assert running_campaign is not None

    aborted = evidence_factory.abort_campaign(
        campaign["id"],
        expected_revision=running_campaign["revision"],
        actor="incident-commander",
        reason="operator cancelled the collection after host interruption",
        now=running_campaign["collection_attempt"]["started_at"] + 1,
    )
    assert aborted["status"] == "aborted"
    assert aborted["collection_attempt"]["status"] == "aborted"
    aborted_drill = evidence_factory.get_drill(running_drill["id"])
    assert aborted_drill is not None
    assert aborted_drill["status"] == "aborted"
    assert aborted_drill["abort"]["recovery"] is False

    with pytest.raises(RecordConflict):
        evidence_factory._complete_drill(
            running_drill,
            {
                "name": spec.name,
                "mode": spec.mode,
                "status": evidence_factory.PASS,
                "summary": "late owner result",
            },
            actor="expired-owner",
        )
    with pytest.raises(ValueError, match="not collecting"):
        evidence_factory.collect_campaign(
            campaign["id"],
            expected_revision=aborted["revision"],
            collectors=[],
            actor="release-operator",
        )

    independent = evidence_factory._start_drill(
        spec,
        evidence_factory.CollectorContext(
            campaign_id=campaign["id"],
            provenance={},
            objectives={},
            started_at=1_700_000_000,
            collection_attempt_id="manual-attempt",
        ),
        actor="release-operator",
        witness_payload=witness.to_dict(),
        started_at=1_700_000_000,
    )
    explicitly_aborted = evidence_factory.abort_drill(
        independent["id"],
        expected_revision=independent["revision"],
        actor="incident-commander",
        reason="operator cancelled the standalone drill",
        now=1_700_000_001,
    )
    assert explicitly_aborted["status"] == "aborted"
    assert explicitly_aborted["abort"]["recovery"] is False


def test_abort_sweep_accepts_only_a_terminal_same_attempt_race(monkeypatch):
    campaign = _start()
    attempt_id = "a" * 32
    spec = evidence_factory.CollectorSpec(
        "racing_drill",
        "operator_attested",
        lambda _context: evidence_factory.CollectorOutcome(
            evidence_factory.PASS,
            "completed",
        ),
        drill=True,
    )
    drill = evidence_factory._start_drill(
        spec,
        evidence_factory.CollectorContext(
            campaign_id=campaign["id"],
            provenance={},
            objectives={},
            started_at=1_700_000_000,
            collection_attempt_id=attempt_id,
        ),
        actor="release-operator",
        witness_payload=None,
        started_at=1_700_000_000,
    )
    original_abort = evidence_factory._abort_drill_claim

    def _owner_completes_before_abort(drill_id, **_kwargs):
        evidence_factory._complete_drill(
            drill,
            {
                "name": spec.name,
                "mode": spec.mode,
                "status": evidence_factory.PASS,
                "summary": "owner completed before recovery CAS",
                "data": {},
                "artifacts": [],
                "started_at": 1_700_000_000,
                "finished_at": 1_700_000_001,
                "duration_ms": 1000,
                "witness": None,
                "drill_id": "",
            },
            actor="release-operator",
        )
        raise ValueError("drill is not running")

    monkeypatch.setattr(
        evidence_factory,
        "_abort_drill_claim",
        _owner_completes_before_abort,
    )
    evidence_factory._abort_attempt_drills(
        campaign["id"],
        attempt_id,
        actor="recovery-operator",
        reason="stale attempt",
        now=1_700_004_000,
        recovery=True,
    )
    terminal = evidence_factory.get_drill(drill["id"])
    assert terminal is not None and terminal["status"] == evidence_factory.PASS
    monkeypatch.setattr(evidence_factory, "_abort_drill_claim", original_abort)


def test_shared_claim_recovery_uses_authority_clock_and_rejects_override():
    class _SharedClock:
        backend_kind = "postgres"

        @staticmethod
        def authoritative_time():
            return 1_700_000_321.5

    clock = _SharedClock()
    assert evidence_factory._authority_now(clock, None, "recovery time") == (
        1_700_000_321.5
    )
    with pytest.raises(ValueError, match="override is not allowed"):
        evidence_factory._authority_now(clock, 1_700_000_999, "recovery time")


def test_drill_is_running_and_id_bound_before_callback_executes():
    campaign = _start()
    witness = evidence_factory.OperatorWitness(
        identity="witness@example.com",
        role="recovery witness",
        statement="Observed the isolated drill.",
        witnessed_at=1_700_000_100,
    )

    def _observe_start(context):
        drills = evidence_factory.list_drills(campaign_id=context.campaign_id)
        running = [row for row in drills if row["status"] == "running"]
        return evidence_factory.CollectorOutcome(
            evidence_factory.PASS,
            "running record observed",
            data={"running_count": len(running)},
        )

    collected = evidence_factory.collect_campaign(
        campaign["id"],
        expected_revision=campaign["revision"],
        collectors=[evidence_factory.CollectorSpec(
            "durable_drill", "operator_attested", _observe_start, drill=True
        )],
        witnesses={"durable_drill": witness},
        actor="release-operator",
    )
    result = collected["results"][0]
    assert result["data"]["running_count"] == 1
    drill = evidence_factory.get_drill(result["drill_id"])
    assert drill is not None
    assert drill["result"]["drill_id"] == drill["id"]
    assert drill["result"]["drill_revision"] == drill["revision"]


def test_destructive_collectors_are_refused_before_execution():
    with pytest.raises(ValueError, match="destructive"):
        evidence_factory.CollectorSpec(
            "prod_failover",
            "live",
            lambda _context: evidence_factory.CollectorOutcome(
                evidence_factory.PASS, "must never run"
            ),
            drill=True,
            destructive=True,
        )


def test_nonpassing_collector_prevents_synthetic_campaign_pass():
    campaign = _start()
    collected = evidence_factory.collect_campaign(
        campaign["id"],
        expected_revision=campaign["revision"],
        collectors=[
            evidence_factory.CollectorSpec(
                "ready",
                "offline",
                lambda _context: evidence_factory.CollectorOutcome(
                    evidence_factory.PASS, "ready"
                ),
            ),
            evidence_factory.CollectorSpec(
                "managed_restore",
                "live",
                lambda _context: evidence_factory.CollectorOutcome(
                    evidence_factory.NOT_RUN, "protected target was not available"
                ),
            ),
        ],
        actor="release-operator",
    )
    finalized = evidence_factory.finalize_campaign(
        campaign["id"],
        expected_revision=collected["revision"],
        actor="release-operator",
        ingest_graph=False,
    )
    assert finalized["manifest"]["overall_status"] == evidence_factory.NOT_RUN
    assert finalized["manifest"]["passed"] is False


def test_unsigned_finalization_and_graph_projection_are_labeled_honestly(
    monkeypatch,
):
    from maverick import evidence_graph, proof_pack

    monkeypatch.setattr(proof_pack, "sign", lambda manifest: copy.deepcopy(manifest))
    campaign = _start()
    collected = evidence_factory.collect_campaign(
        campaign["id"],
        expected_revision=campaign["revision"],
        collectors=[evidence_factory.CollectorSpec(
            "ready",
            "offline",
            lambda _context: evidence_factory.CollectorOutcome(
                evidence_factory.PASS, "ready"
            ),
        )],
        actor="release-operator",
    )
    finalized = evidence_factory.finalize_campaign(
        campaign["id"],
        expected_revision=collected["revision"],
        actor="release-operator",
        ingest_graph=False,
    )
    assert finalized["signature_status"] == "unsigned"
    assert "signature" not in finalized["manifest"]

    captured = {}

    def _capture(**kwargs):
        captured.update(kwargs)
        return {"id": "captured-node"}

    monkeypatch.setattr(evidence_graph, "ingest", _capture)
    evidence_factory.ingest_campaign(finalized, actor="release-operator")
    assert captured["summary"].startswith("Unsigned production evidence campaign")
    assert captured["attributes"]["signed"] is False


def test_final_manifest_roundtrip_requires_anchor_and_rejects_tamper():
    pytest.importorskip("cryptography")
    campaign = _start(controls=["RES-01"])
    collected = evidence_factory.collect_campaign(
        campaign["id"],
        expected_revision=campaign["revision"],
        collectors=[evidence_factory.CollectorSpec(
            "ready",
            "offline",
            lambda _context: evidence_factory.CollectorOutcome(
                evidence_factory.PASS, "all safe checks passed"
            ),
        )],
        actor="release-operator",
    )
    finalized = evidence_factory.finalize_campaign(
        campaign["id"],
        expected_revision=collected["revision"],
        actor="release-operator",
        ingest_graph=False,
    )
    manifest = finalized["manifest"]
    assert manifest["signature"]
    anchor = manifest["signature"]["pubkey"]
    ok, reason = evidence_factory.verify_manifest(
        manifest, trusted_pubkey_hex=anchor
    )
    assert ok, reason
    ok, reason = evidence_factory.verify_manifest(manifest, trusted_pubkey_hex=None)
    assert not ok and "trusted pubkey" in reason

    tampered = copy.deepcopy(manifest)
    tampered["results"][0]["summary"] = "changed after signing"
    ok, reason = evidence_factory.verify_manifest(
        tampered, trusted_pubkey_hex=anchor
    )
    assert not ok and "payload hash" in reason


def test_finalize_projects_pending_review_node_into_evidence_graph():
    campaign = _start(controls=["RES-01"])
    collected = evidence_factory.collect_campaign(
        campaign["id"],
        expected_revision=campaign["revision"],
        collectors=[evidence_factory.CollectorSpec(
            "ready",
            "offline",
            lambda _context: evidence_factory.CollectorOutcome(
                evidence_factory.PASS, "safe check passed"
            ),
        )],
        actor="release-operator",
    )
    finalized = evidence_factory.finalize_campaign(
        campaign["id"],
        expected_revision=collected["revision"],
        actor="release-operator",
    )
    assert finalized["evidence_graph_node_id"].startswith("EGN-")
    from maverick import evidence_graph

    node = evidence_graph.get(finalized["evidence_graph_node_id"])
    assert node is not None
    assert node["status"] == "pending_review"
    assert node["attributes"]["manifest_sha256"] == finalized["manifest_sha256"]


def test_default_collectors_use_injected_safe_checks_and_explicit_modes():
    campaign = _start()
    collectors = evidence_factory.default_collectors(
        readiness_probe=lambda: [
            SimpleNamespace(name="DB", passed=True, detail="writable"),
            SimpleNamespace(name="Shield", passed=True, detail="enforcing"),
        ],
        audit_probe=lambda: {"status": "ok", "files_checked": 2},
        migration_probe=list,
        reliability_probe=lambda: {
            "checks": {
                "wal": {"passed": True, "detail": "clean"},
                "chaos": {"passed": True, "detail": "absorbed"},
            },
            "passed": True,
        },
        timeout_seconds=1,
    )
    assert [collector.mode for collector in collectors] == [
        "integration",
        "integration",
        "offline",
        "integration",
    ]
    collected = evidence_factory.collect_campaign(
        campaign["id"],
        expected_revision=campaign["revision"],
        collectors=collectors,
        actor="release-operator",
    )
    assert {row["status"] for row in collected["results"]} == {
        evidence_factory.PASS
    }
    assert all(row["artifacts"] for row in collected["results"])
