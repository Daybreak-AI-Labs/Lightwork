"""Real local deletion adapters and durable dispatch behavior, no provider calls."""
import copy
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from maverick_dashboard import personal_data_requests as deletion
from maverick_dashboard.draft_store import DraftConflict, DraftStore


def test_draft_erasure_fences_old_autosave_and_other_owner(tmp_path, monkeypatch):
    from maverick import crypto_at_rest
    from maverick_dashboard import draft_store
    monkeypatch.setattr(draft_store, "seal", lambda value: b"sealed:" + value)
    monkeypatch.setattr(draft_store, "unseal", lambda value: value.removeprefix(b"sealed:"))
    monkeypatch.setattr(draft_store, "is_sealed", lambda value: value.startswith(b"sealed:"))
    del crypto_at_rest
    own = DraftStore("a" * 64, directory=tmp_path / "own")
    other = DraftStore("b" * 64, directory=tmp_path / "other")
    saved = own.save("c" * 64, {"text": "private"}, expected_revision=0, operation_id="first")
    other.save("c" * 64, {"text": "other"}, expected_revision=0, operation_id="other")
    result = own.erase_all(expected_version=own.inventory()["version"])
    assert result["state"] == "purged"
    assert own.read("c" * 64)["payload"] is None
    with pytest.raises(DraftConflict):
        own.save("c" * 64, {"text": "stale"}, expected_revision=saved["revision"], operation_id="late")
    assert other.read("c" * 64)["payload"] == {"text": "other"}


def test_conversation_erasure_refuses_cross_owner_descendant():
    world = SimpleNamespace(list_conversations=lambda channel: [SimpleNamespace(id=1, user_id="alice")],
        plan_conversation_erasure=lambda ids: {"conversation_ids": [1], "goal_ids": [3], "episode_ids": [], "attachment_paths": ["private-path"]},
        get_goal=lambda gid: SimpleNamespace(owner="user:bob", status="done"))
    with pytest.raises(ValueError, match="outside this verified owner"):
        deletion._conversation_plan(world, "user:alice", "dashboard", "alice")


def test_conversation_adapter_deletes_only_reviewed_owned_closure(tmp_path, monkeypatch):
    from maverick import erasure_receipts
    from maverick.world_model import WorldModel
    world = WorldModel(tmp_path / "world.db")
    try:
        conversation = world.get_or_create_conversation("dashboard", "alice")
        goal = world.create_goal("private", owner="user:alice")
        world.set_goal_status(goal, "done")
        world.append_turn(conversation.id, "user", "private turn", goal_id=goal)
        other = world.create_goal("other", owner="user:bob")
        plan = deletion._conversation_plan(world, "user:alice", "dashboard", "alice")
        monkeypatch.setattr(erasure_receipts, "persist_receipt", lambda world, manifest: manifest)
        receipt = deletion._erase_conversations(world, {"owner": "user:alice"}, {"scope": plan})
        assert receipt["state"] == "purged"
        assert world.get_goal(goal) is None
        assert world.get_goal(other) is not None
        assert receipt["stores"]["conversations"]["remaining"] == 0
        assert "global caches" in receipt["retained"]
    finally:
        world.close()


class Store:
    def __init__(self, row):
        self.row = copy.deepcopy(row)

    def get(self, identifier):
        return copy.deepcopy(self.row)

    def update(self, identifier, mutate, *, expected_revision, **kwargs):
        assert self.row["revision"] == expected_revision
        mutate(self.row)
        self.row["revision"] += 1
        return copy.deepcopy(self.row)


@pytest.fixture
def request_store(monkeypatch):
    store = Store({"id": "PDR-case", "revision": 1, "owner": "user:alice", "channel": "dashboard", "user_id": "alice",
                   "status": "requested", "targets": [{"store": "preference_notes", "state": "requested", "count": 2}], "exceptions": deletion.EXCEPTIONS})
    monkeypatch.setattr(deletion, "STORE", store)
    monkeypatch.setattr(deletion, "preservation_snapshot", lambda: {"blocked": False})
    monkeypatch.setattr(deletion.personal_memory, "require_scope", lambda *args: None)
    monkeypatch.setattr(deletion, "preview", lambda *args: {"targets": copy.deepcopy(store.row["targets"])})
    return store


def test_known_hold_never_dispatches_or_claims_deletion(request_store, monkeypatch):
    monkeypatch.setattr(deletion, "preservation_snapshot", lambda: {"blocked": True})
    monkeypatch.setattr(deletion, "_erase_target", lambda *a: pytest.fail("hold must prevent dispatch"))
    receipt = deletion.apply("PDR-case", 1, "user:reviewer", lambda: None, None, external_holds_clear=True)
    assert receipt["status"] == "held"
    assert receipt["targets"][0]["state"] == "requested"


def test_own_request_requires_distinct_current_reviewer(request_store):
    with pytest.raises(HTTPException) as error:
        deletion.apply("PDR-case", 1, "user:alice", lambda: None, None, external_holds_clear=True)
    assert error.value.status_code == 403


def test_effect_receipt_precedes_dispatch_failure_stays_unknown(request_store, monkeypatch):
    def erase(*args):
        assert request_store.row["targets"][0]["state"] == "dispatching"
        raise OSError("interrupted deletion")
    monkeypatch.setattr(deletion, "_erase_target", erase)
    receipt = deletion.apply("PDR-case", 1, "user:reviewer", lambda: None, None, external_holds_clear=True)
    assert receipt["status"] == "partial"
    assert receipt["targets"][0]["state"] == "unconfirmed"
    with pytest.raises(HTTPException):
        deletion.apply("PDR-case", receipt["revision"], "user:reviewer", lambda: None, None, external_holds_clear=True)


def test_purged_scope_keeps_explicit_retained_exceptions(request_store, monkeypatch):
    monkeypatch.setattr(deletion, "_erase_target", lambda *args: {"state": "purged", "removed": 2})
    receipt = deletion.apply("PDR-case", 1, "user:reviewer", lambda: None, None, external_holds_clear=True)
    assert receipt["status"] == "purged-with-retained-exceptions"
    assert any(row["state"] == "external-unverified" for row in receipt["exceptions"])


def test_reviewer_revocation_prevents_next_store(request_store, monkeypatch):
    calls = 0
    def authorize():
        nonlocal calls
        calls += 1
        if calls > 1:
            raise HTTPException(403, "revoked")
    monkeypatch.setattr(deletion, "_erase_target", lambda *args: pytest.fail("revoked reviewer must not delete"))
    with pytest.raises(HTTPException):
        deletion.apply("PDR-case", 1, "user:reviewer", authorize, None, external_holds_clear=True)
    assert request_store.row["targets"][0]["state"] == "requested"
