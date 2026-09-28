"""Learned page: suggested instruction changes and self-coaching read-outs.
Reads need view (or audit for the activity log and hold-out check); every
change needs a global administrator and runs the CLI's kernel function."""
from __future__ import annotations

import os
import re

import pytest
from fastapi.testclient import TestClient

ORIGIN = {"Origin": "http://testserver"}
API = "/api/v1/learning-loop"
_BANNED = re.compile(
    r"\b(tenant|principal|payload|endpoint|schema|uuid|sha-?256|digest|json|revision|"
    r"idempoten\w*|harness|refinement|corpus|holdout|efficacy|canary)\b|/api/v1|HTTP \d{3}|maverick ",
    re.IGNORECASE,
)


@pytest.fixture
def client(monkeypatch, tmp_path):
    from maverick import config, world_model
    from maverick.audit import writer as audit_writer
    from maverick_dashboard import app as dash_app
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("MAVERICK_HOME", str(tmp_path / "mh"))
    monkeypatch.setenv("MAVERICK_CONFIG", str(tmp_path / "config.toml"))
    monkeypatch.setenv("MAVERICK_ENCRYPT_AT_REST", "0")
    monkeypatch.setattr(world_model, "DEFAULT_DB", tmp_path / "world.db")
    monkeypatch.setattr(audit_writer, "_default", None)
    audit_writer._defaults.clear()
    for name in ("MAVERICK_DASHBOARD_TOKEN", "MAVERICK_TENANT", "MAVERICK_HARNESS_REFINE"):
        monkeypatch.delenv(name, raising=False)
    config.reset_config_cache()
    dash_app._world_cache.clear()
    yield TestClient(dash_app.app, headers=ORIGIN)
    config.reset_config_cache()
    dash_app._world_cache.clear()
    audit_writer._defaults.clear()


def _configure(body: str) -> None:
    from maverick import config
    with open(os.environ["MAVERICK_CONFIG"], "w", encoding="utf-8") as fh:
        fh.write(body + "\n")
    config.reset_config_cache()


def _as(monkeypatch, role: str, who: str = "user:someone"):
    from maverick_dashboard import auth, rbac
    rbac.set_role(who, role)
    monkeypatch.setattr(auth, "caller_principal", lambda request: who)


SUGGESTION = {
    "target": "prompt",
    "title": "Check quote approval",
    "failure": "The agent emailed the customer before the quote was approved.",
    "change": "Confirm the quote is approved before contacting the customer.",
    "rationale": "Three blocked tasks in a row carried an unapproved quote.",
}


def test_every_change_needs_a_global_admin(client, monkeypatch):
    _as(monkeypatch, "operator")
    for path, body in (
        ("/refinements", SUGGESTION),
        ("/refinements/0123456789abcdef/apply", {}),  # pragma: allowlist secret
        ("/refinements/0123456789abcdef/revert", {}),
        ("/run", {"scope": "main"}),
        ("/corpus-quality", {}),
    ):
        assert client.post(API + path, json=body).status_code == 403, path


def test_reads_need_view_and_audit_reads_need_audit(client, monkeypatch):
    _as(monkeypatch, "viewer")
    assert client.get(API + "/refinements").status_code == 200
    assert client.get(API + "/models").status_code == 200
    assert client.get(API + "/conflicts").status_code == 200
    for path in ("/log", "/holdout", "/preview"):
        assert client.get(API + path).status_code == 403, path
    _as(monkeypatch, "auditor", "user:auditor")
    assert client.get(API + "/log").status_code == 200
    assert client.get(API + "/holdout").json() == {"configured": False}


def test_suggest_approve_apply_and_undo_through_the_kernel(client):
    from maverick import harness_refine
    from maverick.world_model import open_world

    off = client.get(API + "/refinements").json()
    assert off["enabled"] is False and off["proposals"] == []
    refused = client.post(API + "/refinements", json=SUGGESTION)
    assert refused.status_code == 409 and "turned off" in refused.json()["detail"]

    _configure("[harness_refine]\nenable = true")
    made = client.post(API + "/refinements", json=SUGGESTION).json()
    assert made["needs_approval"] is True
    proposal = made["proposal"]
    assert proposal["title"] == "Check quote approval"
    assert proposal["target"] == "Standing instructions"
    listed = client.get(API + "/refinements").json()["proposals"][0]
    assert listed["approval"] == "Waiting for approval"
    assert listed["can_apply"] is False

    early = client.post(API + f"/refinements/{proposal['id']}/apply")
    assert early.status_code == 409 and "waiting for approval" in early.json()["detail"]
    assert harness_refine.refinements() == []

    approval_id = harness_refine.list_proposals()[0]["approval_id"]
    world = open_world()
    world.decide_approval(approval_id, "approved", decided_by="security@corp.test")
    assert client.get(API + "/refinements").json()["proposals"][0]["can_apply"] is True

    applied = client.post(API + f"/refinements/{proposal['id']}/apply")
    assert applied.status_code == 200, applied.text
    live = client.get(API + "/refinements").json()
    assert live["in_force"][0]["change"] == SUGGESTION["change"]
    assert live["proposals"][0]["can_revert"] is True

    assert client.post(API + f"/refinements/{proposal['id']}/revert").json() == {"reverted": True}
    assert harness_refine.refinements() == []
    again = client.post(API + f"/refinements/{proposal['id']}/revert")
    assert again.status_code == 409


def test_refinements_are_workspace_scoped(client, monkeypatch):
    _configure("[harness_refine]\nenable = true\nrequire_approval = false")
    monkeypatch.setenv("MAVERICK_TENANT", "alpha")
    assert client.post(API + "/refinements", json=SUGGESTION).status_code == 200
    assert len(client.get(API + "/refinements").json()["proposals"]) == 1
    monkeypatch.setenv("MAVERICK_TENANT", "beta")
    assert client.get(API + "/refinements").json()["proposals"] == []


def test_suggestion_text_is_screened_and_ids_are_checked(client):
    _configure("[harness_refine]\nenable = true")
    bad = dict(SUGGESTION, title="!!!")
    r = client.post(API + "/refinements", json=bad)
    assert r.status_code == 422
    assert client.post(API + "/refinements/../etc/apply").status_code in (404, 405)
    assert client.post(API + "/refinements/not-an-id/apply").status_code == 404


def test_read_outs_use_offered_models_only(client, monkeypatch):
    from maverick import self_harness as sh
    store = os.path.join(os.environ["HOME"], "addenda.json")
    from pathlib import Path
    monkeypatch.setattr(sh, "_store_path", lambda: Path(store))
    sh._write_addenda({"claude-x": "Operating guidance learned for this model:\n"
                                   "- always cite the source\n- never cite the source"}, Path(store))
    models = client.get(API + "/models").json()["models"]
    assert "claude-x" in [m["value"] for m in models]
    eff = client.get(API + "/efficacy", params={"model_id": "claude-x"}).json()
    assert [row["text"] for row in eff["lessons"]] == ["always cite the source", "never cite the source"]
    assert client.get(API + "/efficacy", params={"model_id": "made-up"}).status_code == 422


def test_run_now_respects_the_switch_and_the_learning_stop(client, monkeypatch):
    from maverick import learning_guard, self_harness
    from maverick import self_improvement_runner as runner
    _configure("[self_harness]\nenable = false")
    r = client.post(API + "/run", json={"scope": "main"})
    assert r.status_code == 409 and "Self-coaching is off" in r.json()["detail"]

    monkeypatch.setattr(self_harness, "enabled", lambda: True)
    report = self_harness.SelfHarnessReport(model_id="claude-x", mined=2, proposed=1, validated=1,
                                            promoted=1, applied_lines=["check totals"])
    monkeypatch.setattr(runner, "run_self_harness_cycle", lambda **kw: (report, 3))
    out = client.post(API + "/run", json={"scope": "main"}).json()
    assert out["reports"][0]["adopted"] == 1 and out["reports"][0]["retired"] == 3
    assert out["reports"][0]["new_lessons"] == ["check totals"]

    def halted(job, phase):
        raise learning_guard.Halted("stop", "operator")
    monkeypatch.setattr(learning_guard, "check_learning_halt", halted)
    stopped = client.post(API + "/run", json={"scope": "main"})
    assert stopped.status_code == 409 and "stopped" in stopped.json()["detail"]


def test_learned_page_shows_the_new_sections_in_plain_words(client):
    page = client.get("/learned").text
    assert 'id="llp-root"' in page and 'data-admin="1"' in page
    assert 'id="llp-propose"' in page and 'id="llp-run"' in page
    assert "/static/lightwork-learning-loop.js" in page
    own = page.split('id="llp-root"', 1)[1].split(">", 1)[1].split("<style>", 1)[0]
    visible = re.sub(r"<script[\s\S]*?</script>|<[^>]+>", " ", own)
    assert "Suggested instruction changes" in visible
    assert not _BANNED.search(visible), visible


def test_learned_page_hides_controls_from_non_admins(client, monkeypatch):
    _as(monkeypatch, "viewer")
    page = client.get("/learned").text
    assert 'id="llp-root"' in page and 'data-admin="0"' in page
    assert 'id="llp-propose"' not in page and 'id="llp-run"' not in page


def test_learning_loop_script_is_served(client):
    r = client.get("/static/lightwork-learning-loop.js")
    assert r.status_code == 200 and "javascript" in r.headers["content-type"]
    assert "window.confirm(" not in r.text and "mvConfirm" in r.text
