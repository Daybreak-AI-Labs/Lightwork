"""Copilot reads workflow-learning readiness from the native Learning status."""

from maverick_dashboard import product_operations as ops
from maverick_dashboard import product_operations_learning_program as learning


def test_workflow_setup_is_registered_read_only_and_uses_native_readiness(monkeypatch):
    from maverick_dashboard import app

    setup = {
        "available": True,
        "workflows_on": True,
        "examples_on": True,
        "signed_examples_ready": True,
        "signing_enabled": True,
        "workflow_learning_on": True,
        "action_recipes_on": False,
        "action_transforms_on": True,
        "whole_flow_controls_ready": False,
    }
    monkeypatch.setattr(app, "require_page_visible", lambda _request, path: path == "/learning")
    monkeypatch.setattr(app, "_learning_workflow_setup", lambda: setup)

    adapter = ops.adapters()["learning.workflow_setup"]
    assert adapter.execute is None
    assert adapter.direct_ui == "/learning#lrn-path-title"
    assert adapter.pages == ("/learning",)

    snapshot = adapter.snapshot(object(), {})
    assert "needs setup" in snapshot.summary
    assert snapshot.details["learning_setup"]["whole_flow_controls_ready"] is False
    assert {row["name"]: row["ready"] for row in snapshot.details["learning_setup"]["checks"]}[
        "Learn what action comes next"
    ] is False
    assert snapshot.details["open_learning"]["href"] == "/learning#lrn-path-title"

    preview = adapter.prepare(object(), {}, {})
    assert preview.native == snapshot.details


def test_workflow_setup_fails_closed_when_native_status_is_unavailable(monkeypatch):
    from maverick_dashboard import app

    monkeypatch.setattr(app, "require_page_visible", lambda *_args: None)
    monkeypatch.setattr(app, "_learning_workflow_setup", lambda: {"available": False})
    snapshot = learning.workflow_setup_snapshot(object(), {})
    assert snapshot.blocked == "Current learning controls are unavailable."
    assert snapshot.details == {"learning_setup": {"available": False}}
