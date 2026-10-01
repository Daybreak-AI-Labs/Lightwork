"""Webhook subscriptions, envelope, filter matching, retry schedule,
dead-letter, the delivery log, and the legacy config list."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from maverick import webhook_events, webhooks
from maverick import webhook_subscriptions as subs
from maverick.tools import _ssrf
from maverick.world_model import WorldModel

NOW = 1_800_000_000.0


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    monkeypatch.setenv("MAVERICK_HOME", str(tmp_path))
    monkeypatch.setenv("MAVERICK_CONFIG", str(tmp_path / "config.toml"))
    monkeypatch.delenv("MAVERICK_TENANT", raising=False)
    monkeypatch.delenv("MAVERICK_WEBHOOK_SUBSCRIPTIONS", raising=False)
    from maverick import audit, config
    config.reset_config_cache()
    monkeypatch.setattr(audit, "record", lambda *a, **k: True)
    monkeypatch.setattr(_ssrf, "resolve_pinned_ip", lambda host: "93.184.216.34")
    monkeypatch.setattr(webhooks, "_load_config_outbound", lambda: ([], None))


@pytest.fixture
def world(tmp_path: Path):
    return WorldModel(path=tmp_path / "world.db")


class _Transport:
    """A fake receiver: a queue of results, and a record of every attempt."""

    def __init__(self, *results):
        self.results = list(results)
        self.calls = []

    def __call__(self, url, body, headers, timeout):
        self.calls.append((url, body, headers))
        return self.results.pop(0) if len(self.results) > 1 else self.results[0]


OK = (200, 7, "ok", "")
DOWN = (503, 30, "try later", "HTTP 503")


# ------------------------------------------------------------ subscriptions
def test_create_list_update_never_returns_secret(world):
    sub = subs.create_subscription("https://hooks.example.com/a", events=["dsar.*"],
                                   secret="top-secret", description="CRM", actor="ana", world=world)  # pragma: allowlist secret
    assert sub["secret_set"] is True and "secret" not in sub and sub["events"] == ["dsar.*"]
    listed = subs.list_subscriptions(world=world)
    assert [s["id"] for s in listed] == [sub["id"]]
    assert "top-secret" not in json.dumps(listed)
    off = subs.update_subscription(sub["id"], enabled=False, description="paused", actor="ana", world=world)
    assert off["enabled"] is False and off["description"] == "paused"
    assert subs.list_subscriptions(world=world, include_disabled=False) == []
    assert subs.update_subscription("whs_missing", enabled=True, world=world) is None


def test_url_validation_reuses_the_ssrf_guard(world, monkeypatch):
    with pytest.raises(subs.SubscriptionError):
        subs.validate_url("ftp://example.com/x")
    with pytest.raises(subs.SubscriptionError):
        subs.validate_url("https://user:pw@example.com/x")  # pragma: allowlist secret

    def blocked(host):
        raise _ssrf.BlockedHost("private address")
    monkeypatch.setattr(_ssrf, "resolve_pinned_ip", blocked)
    with pytest.raises(subs.SubscriptionError, match="not allowed"):
        subs.create_subscription("https://10.0.0.1/hook", world=world)
    assert subs.list_subscriptions(world=world) == []


def test_event_filter_normalisation_and_matching():
    assert subs.normalize_events("*") == ["*"] and subs.normalize_events(None) == ["*"]
    assert subs.normalize_events("dsar.*, incident.opened, dsar.*") == ["dsar.*", "incident.opened"]
    with pytest.raises(subs.SubscriptionError):
        subs.normalize_events(["nope.event"])
    with pytest.raises(subs.SubscriptionError):
        subs.normalize_events(["nope.*"])
    assert subs.matches(["*"], "policy.published")
    assert subs.matches(["dsar.*"], "dsar.overdue") and not subs.matches(["dsar.*"], "incident.opened")
    assert subs.matches('["incident.opened"]', "incident.opened")
    assert not subs.matches(["incident.opened"], "incident.closed")


def test_targets_include_matching_subscriptions_and_legacy_list(world, monkeypatch):
    a = subs.create_subscription("https://a.example.com/h", events=["dsar.*"], world=world)
    subs.create_subscription("https://b.example.com/h", events=["incident.*"], world=world)
    subs.create_subscription("https://c.example.com/h", enabled=False, world=world)
    monkeypatch.setattr(webhooks, "_load_config_outbound", lambda: (["https://legacy.example.com/h"], "k"))
    targets = subs.targets_for("dsar.opened", world=world)
    assert [(t["subscription_id"], t["url"]) for t in targets] == [
        (a["id"], "https://a.example.com/h"),
        (subs.LEGACY_SUBSCRIPTION_ID, "https://legacy.example.com/h"),
    ]


# ------------------------------------------------------------------ envelope
@pytest.mark.parametrize("event_type,record_type", [
    ("dsar.opened", "dsar"), ("incident.due_soon", "incident"), ("assessment.decided", "assessment"),
    ("vendor.contract_renewal_due", "vendor"), ("approval.expired", "approval"), ("policy.published", "policy"),
])
def test_envelope_is_stable_per_event_type(world, event_type, record_type, monkeypatch):
    transport = _Transport(OK)
    monkeypatch.setattr(webhooks, "_post_result", transport)
    monkeypatch.setattr(webhooks, "_submit", lambda executor, fn, *a: fn(*a) or True)
    subs.create_subscription("https://a.example.com/h", secret="s", world=world)
    sent = webhook_events.emit(event_type, record_type=record_type, record_id="42", title="thing",
                               summary={"status": "open"}, link="/x/42", payload={"k": 1},
                               occurred_at=NOW, world=world)
    assert sent == 1
    url, body, headers = transport.calls[0]
    env = json.loads(body)
    assert env["v"] == 1 and env["event"] == event_type and env["type"] == event_type
    assert env["id"].startswith("evt_") and env["occurred_at"] == NOW and env["ts"] == NOW
    assert env["workspace"] == "" and env["link"] == "/x/42" and env["payload"] == {"k": 1}
    assert env["record"] == {"type": record_type, "id": "42", "title": "thing", "status": "open"}
    assert headers["X-Maverick-Event"] == event_type and headers["X-Maverick-Delivery"] == env["id"]
    # Signature scheme unchanged: the receiver-side verifier accepts it.
    assert webhooks.verify_signature(body, headers["X-Maverick-Signature"], "s",
                                     timestamp=headers["X-Maverick-Timestamp"])
    assert not webhooks.verify_signature(body, headers["X-Maverick-Signature"], "wrong",
                                         timestamp=headers["X-Maverick-Timestamp"])


def test_unknown_event_type_is_refused():
    assert webhook_events.emit("made.up", record_type="x", record_id="1") == 0
    assert set(webhook_events.EVENT_TYPES) >= {
        "dsar.opened", "dsar.verified", "dsar.due_soon", "dsar.overdue", "dsar.extended", "dsar.closed",
        "incident.opened", "incident.notifiable_decided", "incident.due_soon", "incident.closed",
        "assessment.assigned", "assessment.decided", "assessment.renewal_due",
        "vendor.contract_renewal_due", "vendor.review_due",
        "approval.created", "approval.decided", "approval.expired", "policy.published"}


# ------------------------------------------------------------------- retries
def test_retry_schedule_is_five_attempts_over_about_an_hour():
    delays = [subs.retry_delay(n, rng=lambda: 0.5) for n in range(1, subs.MAX_ATTEMPTS)]
    assert delays == [60, 300, 900, 2400] and 3000 <= sum(delays) <= 4200
    assert subs.retry_delay(subs.MAX_ATTEMPTS) is None
    low, high = subs.retry_delay(2, rng=lambda: 0.0), subs.retry_delay(2, rng=lambda: 1.0)
    assert low == pytest.approx(240) and high == pytest.approx(360)


def test_failed_delivery_retries_then_dead_letters(world, monkeypatch):
    transport = _Transport(DOWN)
    sub = subs.create_subscription("https://a.example.com/h", world=world)
    ids = subs.enqueue("dsar.opened", "evt_1", b'{"x":1}',
                       targets=[{"subscription_id": sub["id"], "url": sub["url"]}], world=world, now=NOW)
    row = subs.attempt_delivery(ids[0], world=world, now=NOW, transport=transport, rng=lambda: 0.5)
    assert row["status"] == "pending" and row["attempts"] == 1
    assert row["next_attempt_at"] == pytest.approx(NOW + 60)
    assert row["last_status_code"] == 503 and row["last_response"] == "try later" and row["last_latency_ms"] == 30
    # Not due yet: the pump leaves it alone. Due: it retries.
    assert subs.process_due(world=world, now=NOW + 30, transport=transport) == 0
    assert subs.process_due(world=world, now=NOW + 61, transport=transport) == 1
    row = subs.get_delivery(ids[0], world=world)
    assert row["attempts"] == 2 and row["next_attempt_at"] == pytest.approx(NOW + 61 + 300)
    for step in range(1, 4):  # each pump well past the previous backoff
        subs.process_due(world=world, now=NOW + step * 10_000, transport=transport)
    row = subs.get_delivery(ids[0], world=world)
    assert row["status"] == "dead" and row["attempts"] == subs.MAX_ATTEMPTS and row["next_attempt_at"] is None
    assert len(row["attempt_log"]) == 5 and row["attempt_log"][-1]["status_code"] == 503
    assert subs.process_due(world=world, now=NOW + 50_000, transport=transport) == 0  # dead stays dead
    assert subs.list_deliveries(world=world, status="dead")[0]["id"] == ids[0]
    # "Retry now" puts it back on the queue and tries immediately.
    transport.results = [OK]
    row = subs.retry_now(ids[0], actor="ana", world=world, now=NOW + 60_000, transport=transport)
    assert row["status"] == "delivered" and row["delivered_at"] == pytest.approx(NOW + 60_000)


def test_delivery_log_records_success(world):
    transport = _Transport(OK)
    sub = subs.create_subscription("https://a.example.com/h", secret="k", world=world)
    row = subs.send_test_event(sub["id"], actor="ana", world=world, now=NOW, transport=transport)
    assert row["status"] == "delivered" and row["event_type"] == "webhook.test" and row["attempts"] == 1
    assert row["last_status_code"] == 200 and row["last_latency_ms"] == 7
    body = json.loads(row["body"])
    assert body["type"] == "webhook.test" and body["record"]["id"] == sub["id"]
    assert transport.calls[0][2]["X-Maverick-Signature"].startswith("sha256=")
    listed = subs.list_deliveries(world=world, subscription_id=sub["id"])
    assert [d["id"] for d in listed] == [row["id"]]


def test_secret_is_sealed_at_rest_when_encryption_is_on(world, monkeypatch):
    from maverick import crypto_at_rest
    if not crypto_at_rest._have_crypto():
        pytest.skip("cryptography not installed")
    monkeypatch.setenv("MAVERICK_ENCRYPT_AT_REST", "1")
    sub = subs.create_subscription("https://a.example.com/h", secret="plain-secret", world=world)  # pragma: allowlist secret
    stored = world._read_one("SELECT secret FROM webhook_subscriptions WHERE id = ?", (sub["id"],))["secret"]
    assert stored != "plain-secret" and stored.startswith("MVKAR1:")
    assert subs._subscription_secret(sub["id"], world) == "plain-secret"  # pragma: allowlist secret


# --------------------------------------------------------------- fire() path
def test_fire_records_a_delivery_for_the_legacy_config_list(world, monkeypatch):
    transport = _Transport(OK)
    monkeypatch.setattr(webhooks, "_post_result", transport)
    monkeypatch.setattr(webhooks, "_submit", lambda executor, fn, *a: fn(*a) or True)
    monkeypatch.setattr(webhooks, "_load_config_outbound", lambda: (["https://legacy.example.com/h"], "k"))
    assert webhooks.fire("goal_created", {"goal_id": 1, "title": "t"}, world=world) == 1
    url, body, headers = transport.calls[0]
    assert url == "https://legacy.example.com/h"
    assert json.loads(body)["payload"] == {"goal_id": 1, "title": "t"}
    assert webhooks.verify_signature(body, headers["X-Maverick-Signature"], "k",
                                     timestamp=headers["X-Maverick-Timestamp"])
    logged = subs.list_deliveries(world=world)
    assert logged[0]["subscription_id"] == subs.LEGACY_SUBSCRIPTION_ID and logged[0]["status"] == "delivered"


def test_fire_with_nothing_configured_is_a_no_op(world):
    assert webhooks.fire("goal_created", {"goal_id": 1}, world=world) == 0
    assert subs.list_deliveries(world=world) == []


def test_fire_falls_back_to_one_shot_when_the_store_is_unavailable(monkeypatch):
    posted = []
    monkeypatch.setattr(webhooks, "_submit", lambda executor, fn, url, *a: posted.append(url) or True)
    monkeypatch.setattr(webhooks, "_load_config_outbound", lambda: (["https://legacy.example.com/h"], None))

    class Broken:
        def _read_all(self, *a):
            raise RuntimeError("no database")
    assert webhooks.fire("goal_created", {"goal_id": 1}, world=Broken()) == 1
    assert posted == ["https://legacy.example.com/h"]


def test_explicit_urls_keep_the_direct_path(monkeypatch):
    posted = []
    monkeypatch.setattr(webhooks, "_submit", lambda executor, fn, url, *a: posted.append(url) or True)
    assert webhooks.fire("deliverable.approved", {"a": 1}, urls=["https://x.example.com/h"], secret=None) == 1
    assert posted == ["https://x.example.com/h"]
