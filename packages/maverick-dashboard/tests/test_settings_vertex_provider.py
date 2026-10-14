"""Claude on Google Cloud Vertex AI can be connected by a workspace owner in Settings.

Vertex signs in with the server's Google credentials, so the guided form asks
for a project, a region and a model ID and never offers a key.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

_VERTEX_ENV = ("ANTHROPIC_VERTEX_PROJECT_ID", "GOOGLE_CLOUD_PROJECT", "CLOUD_ML_REGION",
               "VERTEX_MODEL_ID", "ANTHROPIC_VERTEX_BASE_URL")
_HAIKU = "claude-haiku-4-5@20251001"


@pytest.fixture
def workspace(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("MAVERICK_HOME", str(tmp_path / "maverick"))
    monkeypatch.setenv("MAVERICK_CONFIG", str(tmp_path / "config.toml"))
    (tmp_path / "config.toml").write_text("", encoding="utf-8")
    for name in _VERTEX_ENV:
        monkeypatch.delenv(name, raising=False)
    from maverick import config, world_model
    from maverick_dashboard import app as dashboard

    monkeypatch.setattr(world_model, "DEFAULT_DB", tmp_path / "world.db")
    config.reset_config_cache()
    dashboard._world_cache.clear()
    yield TestClient(dashboard.app, headers={"Origin": "http://testserver"})
    config.reset_config_cache()


def test_vertex_setup_is_saved_without_a_key_and_reaches_the_client(workspace):
    from maverick import config
    from maverick.llm import LLM

    response = workspace.post("/settings/providers", data={
        "provider": "vertex", "project_id": "acme-ai", "region": "us-east5", "model_id": _HAIKU,
    }, follow_redirects=False)
    assert response.status_code == 303
    assert config.get_provider_config("vertex") == {
        "project_id": "acme-ai", "region": "us-east5", "model_id": _HAIKU,
    }
    route = LLM()._provider_client_config("vertex")
    assert route.api_key is None
    assert route.environment_dict()["ANTHROPIC_VERTEX_PROJECT_ID"] == "acme-ai"
    assert route.environment_dict()["CLOUD_ML_REGION"] == "us-east5"
    assert route.environment_dict()["VERTEX_MODEL_ID"] == _HAIKU
    page = workspace.get("/settings").text
    assert 'name="project_id" value="acme-ai"' in page
    assert 'id="provider-key-vertex"' not in page  # no key field: Google sign-in only
    vertex = next(row for row in workspace.get("/api/v1/providers").json()["providers"]
                  if row["name"] == "vertex")
    assert vertex["configured"] is True
    assert vertex["status"] != "connected"


def test_vertex_refuses_a_key_and_values_that_could_reshape_the_request(workspace):
    from maverick import config

    assert workspace.post("/settings/providers", data={
        "provider": "vertex", "api_key": "not-a-vertex-credential",  # pragma: allowlist secret
        "project_id": "acme-ai", "region": "us-east5", "model_id": _HAIKU,
    }, follow_redirects=False).status_code == 400
    for field, value in (("project_id", "acme/../other"), ("region", "US-EAST5"),
                         ("model_id", "claude/../other:rawPredict")):
        values = {"provider": "vertex", "project_id": "acme-ai", "region": "us-east5",
                  "model_id": _HAIKU}
        values[field] = value
        assert workspace.post("/settings/providers", data=values,
                              follow_redirects=False).status_code == 400, field
    assert config.get_provider_config("vertex") == {}


def test_vertex_setup_can_be_removed(workspace):
    from maverick import config

    workspace.post("/settings/providers", data={
        "provider": "vertex", "project_id": "acme-ai", "region": "us-east5", "model_id": _HAIKU,
    }, follow_redirects=False)
    removed = workspace.post("/settings/providers/clear", data={"provider": "vertex"},
                             follow_redirects=False)
    assert removed.status_code == 303
    assert config.get_provider_config("vertex") == {}


def test_vertex_readiness_needs_a_project_region_and_model():
    from maverick_dashboard.provider_readiness import _configured

    complete = {"project_id": "acme-ai", "region": "us-east5", "model_id": _HAIKU}
    assert _configured("vertex", {}, complete) == (True, "Google sign-in")
    assert _configured("vertex", {}, {k: v for k, v in complete.items() if k != "model_id"}) == (False, "")
    assert _configured("vertex", {}, {}) == (False, "")


def test_vertex_picker_offers_the_model_the_operator_saved(monkeypatch):
    from maverick_dashboard import model_pickers

    monkeypatch.delenv("VERTEX_MODEL_ID", raising=False)
    monkeypatch.setattr("maverick.config.get_provider_config",
                        lambda name: {"model_id": _HAIKU} if name == "vertex" else {})
    assert model_pickers._configured_cloud_model("vertex") == f"{_HAIKU}"


def test_vertex_probe_refuses_to_guess_a_model(monkeypatch):
    from maverick.llm import LLM
    from maverick_dashboard.integration_readiness import probe_provider

    class NoModelClient:
        DEFAULT_MODEL = ""

    route = SimpleNamespace(api_key=None, base_url=None,
                            headers_dict=lambda: None, environment_dict=dict)
    monkeypatch.setattr(LLM, "_provider_client_config", lambda self, name: route)
    monkeypatch.setattr("maverick.providers.get_provider_client",
                        lambda *args, **kwargs: NoModelClient())
    result = probe_provider("vertex", {})
    assert result["verified"] is None
    assert result["evidence"]["kind"] == "setup_incomplete"
