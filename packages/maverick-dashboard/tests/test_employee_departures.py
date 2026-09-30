"""Departure safety contracts; authored for later execution with fake stores."""
from __future__ import annotations

from contextlib import nullcontext
from copy import deepcopy

import pytest
from fastapi import HTTPException

from maverick_dashboard import auth, employee_departures as departures, scim


class Store:
    def __init__(self):
        self.rows = {}

    def get(self, key):
        return deepcopy(self.rows.get(key))

    def create(self, row, **_):
        self.rows[row["id"]] = {**row, "revision": 1}
        return self.get(row["id"])

    def update(self, key, mutate, expected_revision, **_):
        row = self.get(key)
        assert row["revision"] == expected_revision
        mutate(row)
        row["revision"] += 1
        self.rows[key] = row
        return self.get(key)


@pytest.fixture
def reviewed(monkeypatch):
    import maverick.authority_effects
    monkeypatch.setattr(maverick.authority_effects, "authority_effect_fence", nullcontext)
    monkeypatch.setattr(auth, "global_role_for_principal", lambda _: "viewer")
    monkeypatch.setattr(departures, "candidates", lambda: [{"principal": "user:reviewer", "active": True, "admin": True}])
    inventory = {"principal": "user:employee", "snapshot": "reviewed", "truncated": False,
                 "items": [], "active": True}
    monkeypatch.setattr(departures, "preview", lambda _: inventory)
    store = Store()
    monkeypatch.setattr(departures, "STORE", store)
    return inventory, store


def test_changed_review_has_no_deactivation_effect(reviewed, monkeypatch):
    calls = []
    monkeypatch.setattr(scim, "_retire_user_identity", calls.append)
    with pytest.raises(HTTPException) as exc:
        departures.begin("user:employee", "user:reviewer", "old", "user:admin", lambda: None)
    assert exc.value.status_code == 409
    assert calls == []


def test_deactivation_is_not_complete_offboarding(reviewed, monkeypatch):
    calls = []
    monkeypatch.setattr(scim, "_retire_user_identity", calls.append)
    result = departures.begin("user:employee", "user:reviewer", "reviewed", "user:admin", lambda: None)
    assert calls == [{"externalId": "employee"}]
    assert result["status"] == "handoff_required"
    assert result["retained_data_owner"] == "user:reviewer"
    assert result["steps"][-1]["sessions"] == "revoked"


def test_interrupted_connection_has_dispatch_receipt(reviewed, monkeypatch):
    import maverick.connections
    inventory, store = reviewed
    inventory["items"] = [{"kind": "connection", "tenant": None, "id": "mail", "revision": "v1"}]
    monkeypatch.setattr(departures, "_tenant", lambda _: nullcontext())
    monkeypatch.setattr(scim, "_retire_user_identity", lambda _: None)
    def unavailable(*_, **__):
        raise OSError("sealed store unavailable")
    monkeypatch.setattr(maverick.connections, "delete_connection", unavailable)
    with pytest.raises(OSError):
        departures.begin("user:employee", "user:reviewer", "reviewed", "user:admin", lambda: None)
    row = next(iter(store.rows.values()))
    assert row["steps"][-1] == {"kind": "connection", "tenant": None, "id": "mail", "status": "dispatching"}
    assert row["status"] != "completed"
