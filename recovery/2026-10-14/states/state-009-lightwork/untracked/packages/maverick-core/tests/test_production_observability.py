"""Production-assurance signals are fail-closed, fixed, and payload-free."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

import pytest
from maverick import production_observability as assurance

_REAL_KMS_KEY_CUSTODY_HEALTHY = assurance._kms_key_custody_healthy
_REAL_POSTGRES_RLS_VERIFIED = assurance._postgres_rls_verified
_REAL_TRANSPORT_TLS_VERIFIED = assurance._transport_tls_verified


@pytest.fixture(autouse=True)
def _reset_assurance(monkeypatch):
    assurance.reset_for_testing()
    monkeypatch.setattr(assurance, "production_assurance_required", lambda: True)
    monkeypatch.setattr(assurance, "_shield_enforcing", lambda: True)
    monkeypatch.setattr(assurance, "_audit_forwarder_backlog", lambda _world: 0)
    monkeypatch.setattr(assurance, "_kms_key_custody_healthy", lambda: True)
    monkeypatch.setattr(assurance, "_control_plane_lease_owned", lambda: True)
    monkeypatch.setattr(assurance, "_postgres_rls_verified", lambda _world: True)
    monkeypatch.setattr(assurance, "_transport_tls_verified", lambda _world: True)
    monkeypatch.setattr(
        assurance,
        "_queue_snapshot",
        lambda: {"pending": 0, "failed": 0, "stale_running": 0},
    )
    monkeypatch.setattr(assurance, "_kill_switch_clear", lambda: True)
    monkeypatch.setattr(assurance, "_policy_integrity_healthy", lambda: True)
    monkeypatch.setattr(
        assurance,
        "_audit_anchor_signal",
        lambda: assurance.AssuranceSignal(
            assurance.AUDIT_ANCHOR_LAG_SECONDS,
            True,
            0,
            assurance.AUDIT_ANCHOR_LAG_MAX_SECONDS,
            "anchors current and intact",
        ),
    )
    yield
    assurance.reset_for_testing()


def _signal(report, name: str):
    return next(signal for signal in report.signals if signal.name == name)


def test_nonproduction_does_not_publish_assurance(monkeypatch):
    monkeypatch.setattr(assurance, "production_assurance_required", lambda: False)

    report = assurance.collect_assurance(object())

    assert report.enabled is False
    assert report.healthy is True
    assert report.signals == ()
    assert assurance.prometheus_lines(report) == []
    assert assurance.health_checks(report) == {}


def test_exact_fixed_signal_inventory_has_no_sensitive_labels():
    report = assurance.collect_assurance(object())
    rendered = "\n".join(assurance.prometheus_lines(report))

    assert report.healthy is True
    assert tuple(signal.name for signal in report.signals) == assurance.SIGNAL_NAMES
    for signal in assurance.SIGNAL_NAMES:
        assert f'signal="{signal}"' in rendered
    assert "tenant=" not in rendered
    assert "principal=" not in rendered
    assert "path=" not in rendered
    assert "destination=" not in rendered
    assert "exception=" not in rendered


def test_unknown_event_cannot_create_metric_cardinality():
    assert assurance.record_event("tenant-controlled-value") is False
    assert assurance.event_counts() == dict.fromkeys(assurance.EVENT_SIGNALS, 0)


@pytest.mark.parametrize(
    ("probe", "signal"),
    [
        ("_shield_enforcing", assurance.SHIELD_ENFORCING),
        ("_audit_forwarder_backlog", assurance.AUDIT_FORWARDER_BACKLOG),
        ("_kms_key_custody_healthy", assurance.KMS_KEY_CUSTODY),
        ("_control_plane_lease_owned", assurance.CONTROL_PLANE_LEASE_OWNED),
        ("_postgres_rls_verified", assurance.POSTGRES_RLS_VERIFIED),
        ("_transport_tls_verified", assurance.TRANSPORT_TLS_VERIFIED),
        ("_queue_snapshot", assurance.QUEUE_PENDING),
        ("_kill_switch_clear", assurance.KILL_SWITCH_CLEAR),
        ("_policy_integrity_healthy", assurance.POLICY_INTEGRITY),
    ],
)
def test_unavailable_collectors_fail_their_signal_closed(monkeypatch, probe, signal):
    def unavailable(*_args, **_kwargs):
        raise RuntimeError("secret infrastructure detail")

    monkeypatch.setattr(assurance, probe, unavailable)
    assurance.reset_for_testing()

    report = assurance.collect_assurance(object())

    assert report.healthy is False
    observed = _signal(report, signal)
    assert observed.healthy is False
    assert "secret infrastructure detail" not in observed.detail
    assert "secret infrastructure detail" not in "\n".join(
        assurance.prometheus_lines(report)
    )


def test_events_and_queue_thresholds_make_readiness_unhealthy(monkeypatch):
    assurance.record_event(assurance.SHIELD_ENFORCEMENT_FAILURE)
    assurance.record_event(assurance.DURABLE_AUDIT_REFUSAL)
    assurance.record_event(assurance.AUDIT_FORWARDER_FAILURE)
    assurance.record_event(assurance.INDETERMINATE_SIDE_EFFECT)
    assurance.record_event(assurance.POLICY_FALLBACK)
    monkeypatch.setattr(
        assurance,
        "_queue_snapshot",
        lambda: {"pending": 101, "failed": 1, "stale_running": 1},
    )

    report = assurance.collect_assurance(object())

    assert report.healthy is False
    failed = {signal.name for signal in report.signals if not signal.healthy}
    assert {
        assurance.SHIELD_FAILURES,
        assurance.DURABLE_AUDIT_REFUSALS,
        assurance.AUDIT_FORWARDER_FAILURES,
        assurance.INDETERMINATE_SIDE_EFFECTS,
        assurance.POLICY_FALLBACKS,
        assurance.QUEUE_PENDING,
        assurance.QUEUE_DEAD_LETTER,
        assurance.QUEUE_STALE_LEASES,
    } <= failed


def test_expensive_probe_cache_is_tenant_scoped(monkeypatch):
    from maverick.paths import tenant_scope

    calls = 0

    def kms_healthy():
        nonlocal calls
        calls += 1
        return True

    monkeypatch.setattr(assurance, "_kms_key_custody_healthy", kms_healthy)
    world = object()

    with tenant_scope(tenant="alpha"):
        assert assurance.collect_assurance(world).healthy is True
        assert assurance.collect_assurance(world).healthy is True
    with tenant_scope(tenant="beta"):
        assert assurance.collect_assurance(world).healthy is True

    assert calls == 2


def test_kms_custody_requires_remote_roundtrip_and_offhost_signer(monkeypatch):
    from maverick.audit import signing
    from maverick.tenant import kms as tenant_kms

    class _RemoteKMS:
        def wrap(self, plaintext, *, context=None):
            return b"wrapped:" + bytes(context or b"") + b":" + plaintext

        def unwrap(self, wrapped, *, context=None):
            prefix = b"wrapped:" + bytes(context or b"") + b":"
            assert wrapped.startswith(prefix)
            return wrapped[len(prefix) :]

    monkeypatch.setattr(tenant_kms, "get_kms", lambda _tenant: _RemoteKMS())
    monkeypatch.setattr(signing, "active_key_is_offhost", lambda: True)

    assert _REAL_KMS_KEY_CUSTODY_HEALTHY() is True

    monkeypatch.setattr(signing, "active_key_is_offhost", lambda: False)
    assert _REAL_KMS_KEY_CUSTODY_HEALTHY() is False

    local = object.__new__(tenant_kms.LocalKMS)
    monkeypatch.setattr(tenant_kms, "get_kms", lambda _tenant: local)
    assert _REAL_KMS_KEY_CUSTODY_HEALTHY() is False


def test_rls_probe_requires_every_exact_tenant_policy():
    from maverick.world_model_backends.postgres import _RLS_POLICY_TABLES

    checked: list[str] = []

    class _World:
        _rls = True

        @staticmethod
        def _rls_policy_is_active(table):
            checked.append(table)
            return table != _RLS_POLICY_TABLES[-1]

    assert _REAL_POSTGRES_RLS_VERIFIED(_World()) is False
    assert checked == list(_RLS_POLICY_TABLES)


def test_postgres_tls_probe_requires_verify_full_and_negotiated_tls():
    class _Info:
        sslmode = "verify-full"

        def get_parameters(self):
            return {"sslmode": self.sslmode}

    class _Connection:
        info = _Info()

    class _Cursor:
        connection = _Connection()
        ssl = True

        def execute(self, statement):
            assert "pg_stat_ssl" in statement

        def fetchone(self):
            return (self.ssl,)

    class _World:
        cursor = _Cursor()

        @contextmanager
        def _tx(self):
            yield self.cursor

    world = _World()
    assert assurance._postgres_tls_verified(world) is True

    world.cursor.connection.info.sslmode = "require"
    assert assurance._postgres_tls_verified(world) is False

    world.cursor.connection.info.sslmode = "verify-full"
    world.cursor.ssl = False
    assert assurance._postgres_tls_verified(world) is False


def test_transport_tls_requires_ingress_assertion(monkeypatch):
    monkeypatch.setattr(assurance, "_postgres_tls_verified", lambda _world: True)
    monkeypatch.setattr(assurance, "_queue_tls_verified", lambda: True)

    monkeypatch.delenv("MAVERICK_TLS_TERMINATED", raising=False)
    assert _REAL_TRANSPORT_TLS_VERIFIED(object()) is False

    monkeypatch.setenv("MAVERICK_TLS_TERMINATED", "1")
    assert _REAL_TRANSPORT_TLS_VERIFIED(object()) is True


def test_stale_audit_anchor_exceeds_readiness_threshold(monkeypatch, tmp_path):
    from maverick import paths

    audit_dir = tmp_path / "audit"
    audit_dir.mkdir()
    old_day = (datetime.now(timezone.utc) - timedelta(days=4)).strftime("%Y-%m-%d")
    (audit_dir / f"{old_day}.ndjson").write_text("", encoding="utf-8")
    monkeypatch.setattr(paths, "data_dir", lambda *_args, **_kwargs: audit_dir)

    healthy, lag = assurance._audit_anchor_probe()

    assert healthy is False
    assert lag > assurance.AUDIT_ANCHOR_LAG_MAX_SECONDS


def test_missing_required_anchor_ledger_is_immediate_integrity_failure(
    monkeypatch,
    tmp_path,
):
    from maverick import paths
    from maverick.audit.signing import _anchor_marker_path

    audit_dir = tmp_path / "audit"
    audit_dir.mkdir()
    _anchor_marker_path(audit_dir).touch()
    monkeypatch.setattr(paths, "data_dir", lambda *_args, **_kwargs: audit_dir)

    healthy, lag = assurance._audit_anchor_probe()

    assert healthy is False
    assert lag == 0


def test_job_queue_snapshot_reports_stale_worker_lease(tmp_path):
    from maverick.job_queue import JobQueue

    queue = JobQueue(tmp_path / "jobs.db")
    for name in ("stale", "fresh", "pending", "failed", "done"):
        queue.enqueue(name, run_at=0)
    stale = queue.claim(now=100, ready_at=1)
    fresh = queue.claim(now=190, ready_at=1)
    failed = queue.claim(now=190, ready_at=1)
    done = queue.claim(now=190, ready_at=1)
    assert stale and fresh and failed and done
    assert queue.fail(failed.id, "no", retry_after=None)
    assert queue.complete(done.id)

    snapshot = queue.health_snapshot(lease_seconds=50, now=200)

    assert snapshot == {
        "pending": 1,
        "running": 2,
        "failed": 1,
        "done": 1,
        "stale_running": 1,
    }
    with pytest.raises(ValueError, match="positive finite"):
        queue.health_snapshot(lease_seconds=0)
