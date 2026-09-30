"""Planner is evidence; it cannot bypass protected-state admission."""
import contextlib
from maverick import installation_planner as planner


def test_protected_differences_offer_forward_options(tmp_path, monkeypatch):
    core = planner.recovery
    monkeypatch.setattr(core, "_locked", contextlib.nullcontext)
    monkeypatch.setattr(core, "_scope", contextlib.nullcontext)
    monkeypatch.setattr(core, "_directory", lambda identifier: tmp_path)
    monkeypatch.setattr(core, "_runtime", lambda: tmp_path / "runtime")
    monkeypatch.setattr(core, "blockers", lambda: [])
    monkeypatch.setattr(core, "_read", lambda path: {"status": "upgraded", "before": {"sha256": "sha"}, "new_runtime_sha256": "sha", "old_version": "1.0.0", "new": {"version": "1.2.3"}})
    monkeypatch.setattr(planner.backup, "_sha256", lambda path: "sha")
    monkeypatch.setattr(planner.backup, "compare_backup", lambda path: {"protected_changes": 1, "items": [{"path": "tenant/world.db", "protected": True, "change": "changed"}], "live_state_sha256": "new"})
    result = planner.plan("a" * 32)
    assert result["rollback_candidate"] is False
    assert result["blockers"][0]["code"] == "protected_state_changed"
    assert result["affected"][0]["path"] == "tenant/world.db"
    assert result["authorization_required"] is True
    assert {action["id"] for action in result["actions"]} == {"repair_current", "workspace_recovery", "save_plan"}
