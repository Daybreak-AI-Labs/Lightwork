"""A changed specialist can be tried on a share of new goals first; promote, roll back and refusals go through the draft review."""
from __future__ import annotations

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

TESTS = "/api/v1/workforce-ops/test-suite"
DRAFTS = "/api/v1/specialist-drafts"
PACK = "proc_po_chaser"
NEW = "You chase overdue purchase orders and always name the supplier to chase."
NEWER = NEW + " Keep each chase to two sentences."


@pytest.fixture
def client(monkeypatch, tmp_path):
    from maverick import config, world_model
    from maverick.audit import writer as audit_writer
    from maverick_dashboard import app as dash_app
    from maverick_dashboard import learning_jobs

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("MAVERICK_HOME", str(tmp_path / "mh"))
    monkeypatch.setenv("MAVERICK_CONFIG", str(tmp_path / "config.toml"))
    monkeypatch.setenv("MAVERICK_DOMAINS_DIR", str(tmp_path / "domains"))
    monkeypatch.setenv("MAVERICK_ENCRYPT_AT_REST", "0")
    monkeypatch.delenv("MAVERICK_TENANT", raising=False)
    monkeypatch.delenv("MAVERICK_DASHBOARD_TOKEN", raising=False)
    monkeypatch.setattr(world_model, "DEFAULT_DB", tmp_path / "world.db")
    monkeypatch.setattr("maverick.config.dashboard_overrides_path", lambda: tmp_path / "dashboard.toml")
    monkeypatch.setattr(audit_writer, "_default", None)
    audit_writer._defaults.clear()
    (tmp_path / "config.toml").write_text("[models]\ncapability_tests = true\n", encoding="utf-8")
    config.reset_config_cache()
    dash_app._world_cache.clear()
    learning_jobs.clear_for_tests()
    yield TestClient(dash_app.app, headers={"Origin": "http://testserver"})
    learning_jobs.clear_for_tests()
    config.reset_config_cache()
    dash_app._world_cache.clear()
    audit_writer._defaults.clear()


def _trials_on(monkeypatch, tmp_path, percent):
    from maverick import config

    (tmp_path / "config.toml").write_text(
        f"[models]\ncapability_tests = true\n[agent_tests]\ncanary_percent = {percent}\n", encoding="utf-8")
    config.reset_config_cache()


def _answers(monkeypatch):
    from maverick import llm

    def answer(self, system, messages, **kwargs):
        kwargs["dispatch_guard"]()
        return llm.LLMResponse(text="We should chase the supplier today.", thinking=None, tool_calls=[], stop_reason="end_turn")

    monkeypatch.setattr(llm.LLM, "complete", answer)


def _question(app, index):
    row = app.post(TESTS + "/customer", json={
        "specialist": PACK, "task": f"Purchase order example {index}: which supplier should we chase?",
        "includes": ["chase"], "excludes": [], "refusal": False, "citation": False})
    assert row.status_code == 200, row.text


def _review(app, action, **fields):
    context = app.get(DRAFTS + "/history").json()["context"]
    return app.post(DRAFTS + "/review", json={"name": PACK, "action": action, "context": context, **fields})


def _confirm(app, review):
    assert review.status_code == 200, review.text
    return app.post(DRAFTS + "/apply", json={"token": review.json()["token"], "confirmed": True})


def _saved_draft(app, entered):
    saved = _confirm(app, _review(app, "save", entered=entered))
    assert saved.status_code == 200 and saved.json()["confirmed"], saved.text
    return "SPD-" + saved.json()["operation"]


def _persona(app):
    return app.get("/api/v1/agents/" + PACK).json()["persona"]


def _publish_changed(app, draft):
    published = _confirm(app, _review(app, "publish", selected=draft, reason="Reviewed by the supplier lead"))
    assert published.status_code == 200, published.text
    assert published.json()["confirmed"] is True, published.text
    return published.json()


def test_off_publishing_a_changed_specialist_replaces_it_as_before(client, monkeypatch):
    from maverick import specialist_canary as canary

    app = client
    _question(app, 0)
    _publish_changed(app, _saved_draft(app, {"persona": NEW}))
    assert _persona(app) == NEW
    assert canary.live(PACK) is None


def test_on_publishing_a_changed_specialist_starts_a_trial_and_keeps_the_current_version(client, monkeypatch, tmp_path):
    from maverick import specialist_canary as canary

    app = client
    _trials_on(monkeypatch, tmp_path, 40)
    before = _persona(app)
    _question(app, 0)
    done = _publish_changed(app, _saved_draft(app, {"persona": NEW}))
    assert done["confirmed"] is True
    assert _persona(app) == before
    trial = canary.live(PACK)
    assert trial is not None and trial["percent"] == 40 and trial["patch"]["persona"] == NEW
    listed = app.get(DRAFTS + "/trials").json()["trials"]
    assert [row["name"] for row in listed] == [PACK] and listed[0]["percent"] == 40
    assert listed[0]["versions"]["candidate"]["goals"] == 0 and listed[0]["min_finished"] == 10


def test_a_second_change_is_refused_while_a_trial_is_live(client, monkeypatch, tmp_path):
    app = client
    _trials_on(monkeypatch, tmp_path, 40)
    _question(app, 0)
    _publish_changed(app, _saved_draft(app, {"persona": NEW}))
    other = _saved_draft(app, {"persona": NEWER})
    refused = _review(app, "publish", selected=other, reason="A second urgent change")
    assert refused.status_code == 409 and "trial version of this specialist is live" in refused.json()["detail"]
    # Editing a draft is still allowed; only publishing waits for the trial to end.
    assert _review(app, "save", entered={"persona": NEWER + " Again."}).status_code == 200
    removed = app.request("DELETE", "/api/v1/agents/" + PACK + "/override", params={"expected_generation": "0" * 64}, json={})
    assert removed.status_code == 409 and "trial version" in removed.json()["detail"]


def test_promote_makes_the_trial_the_current_version_for_everyone(client, monkeypatch, tmp_path):
    from maverick import specialist_canary as canary

    app = client
    _trials_on(monkeypatch, tmp_path, 40)
    _question(app, 0)
    _publish_changed(app, _saved_draft(app, {"persona": NEW}))
    review = _review(app, "promote", reason="Reviewed by the supplier lead")
    assert review.status_code == 200, review.text
    assert any(row["label"] == "Trial being decided" for row in review.json()["changes"])
    promoted = _confirm(app, review)
    assert promoted.status_code == 200 and promoted.json()["confirmed"] is True, promoted.text
    assert _persona(app) == NEW
    assert canary.live(PACK) is None
    assert canary.TRIALS.get(canary._trial_id(PACK))["last"]["outcome"] == canary.PROMOTED
    from maverick_dashboard import agent_changelog

    assert any("Changed its instructions" in change for entry in agent_changelog.entries(PACK) for change in entry["changes"])


def test_roll_back_discards_the_trial_and_the_current_version_stays(client, monkeypatch, tmp_path):
    from maverick import specialist_canary as canary

    app = client
    _trials_on(monkeypatch, tmp_path, 40)
    before = _persona(app)
    _question(app, 0)
    _publish_changed(app, _saved_draft(app, {"persona": NEW}))
    rolled = _confirm(app, _review(app, "rollback"))
    assert rolled.status_code == 200 and rolled.json()["confirmed"] is True, rolled.text
    assert _persona(app) == before
    assert canary.live(PACK) is None
    assert canary.TRIALS.get(canary._trial_id(PACK))["last"]["outcome"] == canary.ROLLED_BACK
    # Once the trial is gone a new change can be tried again.
    _publish_changed(app, _saved_draft(app, {"persona": NEWER}))
    assert canary.live(PACK)["patch"]["persona"] == NEWER


def test_promote_or_roll_back_without_a_trial_is_refused_with_a_plain_message(client):
    app = client
    for action in ("promote", "rollback"):
        refused = _review(app, action)
        assert refused.status_code == 409 and "no trial version" in refused.json()["detail"], refused.text


def test_promote_and_roll_back_need_the_same_permission_as_publishing(client, monkeypatch, tmp_path):
    from maverick import specialist_canary as canary
    from maverick_dashboard import auth

    app = client
    _trials_on(monkeypatch, tmp_path, 40)
    _question(app, 0)
    _publish_changed(app, _saved_draft(app, {"persona": NEW}))
    real = auth.require_permission

    def no_admin(*_args, **_kwargs):
        raise HTTPException(403, "Administrator access is required.")

    monkeypatch.setattr(auth, "require_permission", no_admin)
    for action in ("promote", "rollback"):
        refused = app.post(DRAFTS + "/review", json={"name": PACK, "action": action, "context": "0" * 64})
        assert refused.status_code == 403, refused.text
    monkeypatch.setattr(auth, "require_permission", real)
    assert canary.live(PACK) is not None and canary.live(PACK)["patch"]["persona"] == NEW


def test_a_trial_whose_share_changed_later_still_runs_its_own_share(client, monkeypatch, tmp_path):
    from maverick import specialist_canary as canary

    app = client
    _trials_on(monkeypatch, tmp_path, 40)
    _question(app, 0)
    _publish_changed(app, _saved_draft(app, {"persona": NEW}))
    _trials_on(monkeypatch, tmp_path, 10)
    assert canary.live(PACK)["percent"] == 40


def test_publishing_a_brand_new_specialist_is_not_a_trial(client, monkeypatch, tmp_path):
    from maverick import specialist_canary as canary

    app = client
    _trials_on(monkeypatch, tmp_path, 40)
    name = "custom_canary_newcomer"
    entered = {"persona": NEW, "description": "Chases overdue purchase orders for the buyer team.",
               "allow_tools": ["read_file"], "deny_tools": ["shell"], "max_risk": "medium"}
    context = app.get(DRAFTS + "/history").json()["context"]
    review = app.post(DRAFTS + "/review", json={"name": name, "action": "save", "context": context, "entered": entered})
    assert review.status_code == 200, review.text
    saved = app.post(DRAFTS + "/apply", json={"token": review.json()["token"], "confirmed": True})
    assert saved.status_code == 200 and saved.json()["confirmed"] is True, saved.text
    draft = "SPD-" + saved.json()["operation"]
    review = app.post(DRAFTS + "/review", json={"name": name, "action": "publish", "context": context,
                                                 "selected": draft, "reason": "Reviewed by the supplier lead"})
    assert review.status_code == 200, review.text
    published = app.post(DRAFTS + "/apply", json={"token": review.json()["token"], "confirmed": True})
    assert published.status_code == 200 and published.json()["confirmed"] is True, published.text
    assert canary.live(name) is None
    assert app.get("/api/v1/agents/" + name).json()["persona"] == NEW
