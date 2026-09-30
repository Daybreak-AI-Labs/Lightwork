from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from maverick.personal_notifications import validate_preferences
from maverick_dashboard import product_operations_personal_notifications as operations


def test_one_task_mute_keeps_other_preferences_and_does_not_mutate_input():
    current = validate_preferences({})
    current["muted_tasks"] = {"8": 2_000_000_000}
    changed = operations._change(current, {"action": "mute", "value": "7", "enabled": True, "minutes": 60}, now=100)
    assert changed["muted_tasks"] == {"8": 2_000_000_000, "7": 3700}
    assert current["muted_tasks"] == {"8": 2_000_000_000}
    assert changed["events"] == current["events"]


def test_revoked_critical_exception_is_refused_before_save(monkeypatch):
    monkeypatch.setattr(operations.native, "_state", lambda request: {"critical_allowed": False})
    with pytest.raises(HTTPException) as caught:
        operations.prepare(object(), {}, {"action": "critical_exceptions", "enabled": True})
    assert caught.value.status_code == 403


def test_execution_uses_reviewed_cas_and_native_operation_receipt(monkeypatch):
    calls = []
    prepared = {"context": "account", "revision": "a" * 64, "preferences": {"mode": "off"}}
    def save(request, response, body):
        calls.append(body)
        return {"preferences": body.preferences, "revision": "b" * 64}
    monkeypatch.setattr(operations.native, "save_preferences", save)
    result = operations.execute(object(), {}, {}, prepared, "c" * 32)
    assert result.status == "confirmed"
    assert calls[0].expected_revision == "a" * 64
    assert calls[0].operation_id == "c" * 32
    assert calls[0].preferences == {"mode": "off"}


def test_recovery_uses_native_receipt_without_new_save(monkeypatch):
    import sys
    monkeypatch.setattr(operations.native, "_identity", lambda request: "user:alice")
    checks = []
    def confirmed(actor, **kwargs):
        checks.append((actor, kwargs))
        return True
    monkeypatch.setitem(sys.modules, "maverick.experience_preferences", SimpleNamespace(operation_confirmed=confirmed))
    result = operations.recover(object(), {}, {}, {"revision": "a" * 64, "preferences": {"mode": "digest"}}, "b" * 32)
    assert result.status == "replayed"
    assert checks[0][0] == "user:alice"
    assert checks[0][1]["changes"] == {"notification_preferences": {"mode": "digest"}}


def test_private_choices_use_current_verified_channel_records():
    state = {"projects": [], "tasks": [], "private_delivery": {"channels": [{"id": "a" * 32, "channel": "slack"}]}}
    assert operations._choices(state)["channel"] == [{"value": "a" * 32, "label": "Slack verified private identity"}]
