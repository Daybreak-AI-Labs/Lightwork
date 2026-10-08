"""Creating a reviewed report schedule: what must still hold from the review."""
from __future__ import annotations

import copy
import time
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from maverick_dashboard import automation_queue
from maverick_dashboard import executive_report_schedules as schedules

REVIEWED = {"context": "ctx", "owner": "user:alice", "tenant": "acme", "role": "operator",
            "permissions": ["operate", "view"], "suites": None, "scopes": None, "key": "",
            "workspace_source": "operator_floor", "workspace_binding": "binding",
            "mfa": True, "address": "203.0.113.5"}


@pytest.fixture
def created(monkeypatch, tmp_path):
    """``create`` against a fake queue; ``now`` is what authority() reports on this request."""
    monkeypatch.setenv("MAVERICK_HOME", str(tmp_path))
    recipe = {"layout": "layout-1", "layout_revision": "rev", "reviewed_sources": []}
    value = {"purpose": schedules.PURPOSE, "operation": "op-1", "authority": copy.deepcopy(REVIEWED),
             "expires": time.time() + 600, "body": {"report": "layout-1"}, "runs": [time.time() + 300],
             "payload": {"report_recipe": "recipe-token"}}
    sealed = {"review-token": value, "recipe-token": recipe}
    now = {"authority": copy.deepcopy(REVIEWED)}
    enqueued = []

    class Queue:
        def enqueue_once(self, kind, payload, *, idempotency_key, run_at, effect_guard):
            effect_guard()
            enqueued.append(idempotency_key)

    monkeypatch.setattr(schedules, "unseal", lambda reference: sealed[reference])
    monkeypatch.setattr(schedules, "authority", lambda request, expected=None, *, write=False: dict(now["authority"]))
    monkeypatch.setattr(schedules, "original", lambda request, reference: {"found": bool(enqueued)})
    monkeypatch.setattr(schedules, "_build_runtime", lambda *args, **kwargs: SimpleNamespace())
    monkeypatch.setattr(schedules, "_layout", lambda *args, **kwargs: SimpleNamespace())
    monkeypatch.setattr(schedules, "_selectors", lambda layout: ())
    monkeypatch.setattr(schedules, "_snapshot", lambda *args: SimpleNamespace(sources=[]))
    monkeypatch.setattr(automation_queue, "queue", lambda: Queue())
    monkeypatch.setattr(automation_queue, "start", lambda: None)

    def create():
        return schedules.create(SimpleNamespace(app=None), "review-token", {"report": "layout-1"})

    return SimpleNamespace(now=now, reviewed=value["authority"], create=create, enqueued=enqueued)


def test_a_changed_source_address_does_not_void_the_review(created):
    # NAT rotation, a VPN or load-balanced egress between the two requests: the
    # workspace allowlist is checked on this request by authority() itself.
    created.now["authority"]["address"] = "198.51.100.77"
    created.create()
    assert created.enqueued == ["report-schedule:op-1"]


def test_a_stronger_sign_in_does_not_void_the_review(created):
    created.reviewed["mfa"] = False  # reviewed without a second factor, created with one
    created.create()
    assert created.enqueued == ["report-schedule:op-1"]


def test_a_weaker_sign_in_or_changed_access_still_voids_the_review(created):
    created.now["authority"]["mfa"] = False
    with pytest.raises(HTTPException) as weaker:
        created.create()
    assert weaker.value.status_code == 409
    for key, value in (("role", "viewer"), ("tenant", "other"), ("permissions", ["view"]),
                       ("workspace_binding", "changed"), ("key", "api-key-1")):
        created.now["authority"] = {**REVIEWED, key: value}
        with pytest.raises(HTTPException) as changed:
            created.create()
        assert changed.value.status_code == 409, key
    assert created.enqueued == []
