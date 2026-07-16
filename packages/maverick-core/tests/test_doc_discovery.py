"""Doc discovery: find the SOW/contract/DPA in connected sources, fail-open."""
from __future__ import annotations

import pytest
from maverick import doc_discovery
from maverick.config import reset_config_cache

GRAPH_SEARCH_PAYLOAD = {
    "value": [{"hitsContainers": [{"hits": [
        {"summary": "…Acme CRM DPA…",
         "resource": {"id": "d1", "name": "Acme CRM — DPA (signed).pdf",
                      "webUrl": "https://sp/x", "size": 1234,
                      "file": {"mimeType": "application/pdf"},
                      "parentReference": {"driveId": "drv"}}},
        {"summary": "",
         "resource": {"id": "d2", "name": "Holiday calendar.pdf",
                      "webUrl": "https://sp/y", "size": 99,
                      "file": {"mimeType": "application/pdf"},
                      "parentReference": {"driveId": "drv"}}},
    ]}]}],
}

SLACK_SEARCH_PAYLOAD = {
    "ok": True,
    "files": {"matches": [
        {"id": "F1", "name": "Acme CRM contract.docx",
         "permalink": "https://slack/f1", "mimetype": "application/msword",
         "size": 10, "url_private_download": "https://slack/dl/f1"},
    ]},
}


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch, tmp_path):
    for var in ("MSGRAPH_ACCESS_TOKEN", "MSGRAPH_BASE_URL",
                "SLACK_SEARCH_TOKEN", "SLACK_BOT_TOKEN", "SLACK_BASE_URL",
                "GDRIVE_ACCESS_TOKEN", "GDRIVE_BASE_URL",
                "MAVERICK_ASSESS_DISCOVERY"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("MAVERICK_CONFIG", str(tmp_path / "config.toml"))
    monkeypatch.setenv("MAVERICK_HOME", str(tmp_path))
    reset_config_cache()
    yield
    reset_config_cache()


class TestCreds:
    def test_unconfigured_source_is_none(self):
        assert doc_discovery._creds("msgraph") is None

    def test_env_token_with_default_base(self, monkeypatch):
        monkeypatch.setenv("MSGRAPH_ACCESS_TOKEN", "tok")
        base, token = doc_discovery._creds("msgraph")
        assert token == "tok"
        assert base == "https://graph.microsoft.com/v1.0"

    def test_env_base_override(self, monkeypatch):
        monkeypatch.setenv("MSGRAPH_ACCESS_TOKEN", "tok")
        monkeypatch.setenv("MSGRAPH_BASE_URL", "http://127.0.0.1:9/graph-sim/")
        base, _ = doc_discovery._creds("msgraph")
        assert base == "http://127.0.0.1:9/graph-sim"

    def test_configured_sources_respects_config_order(self, monkeypatch):
        monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb")  # pragma: allowlist secret
        assert doc_discovery.configured_sources() == ["slack"]


class TestDiscover:
    def test_parses_ranks_and_dedupes_msgraph(self, monkeypatch):
        monkeypatch.setenv("MSGRAPH_ACCESS_TOKEN", "tok")
        monkeypatch.setattr(doc_discovery, "_post",
                            lambda url, token, body: (200, GRAPH_SEARCH_PAYLOAD))
        hits = doc_discovery.discover("Acme CRM")
        assert [h.doc_id for h in hits] == ["d1", "d2"]
        top = hits[0]
        assert top.source == "msgraph"
        assert top.ref == {"drive_id": "drv"}
        # DPA + subject tokens outrank the unrelated calendar.
        assert top.score > hits[1].score

    def test_multiple_sources_merge(self, monkeypatch):
        monkeypatch.setenv("MSGRAPH_ACCESS_TOKEN", "t1")
        monkeypatch.setenv("SLACK_BOT_TOKEN", "t2")  # pragma: allowlist secret
        monkeypatch.setattr(doc_discovery, "_post",
                            lambda url, token, body: (200, GRAPH_SEARCH_PAYLOAD))
        monkeypatch.setattr(doc_discovery, "_get",
                            lambda url, token, params=None, **kw:
                            (200, SLACK_SEARCH_PAYLOAD))
        hits = doc_discovery.discover("Acme CRM")
        assert {h.source for h in hits} == {"msgraph", "slack"}

    def test_erroring_source_contributes_nothing(self, monkeypatch):
        monkeypatch.setenv("MSGRAPH_ACCESS_TOKEN", "t1")
        monkeypatch.setenv("SLACK_BOT_TOKEN", "t2")  # pragma: allowlist secret

        def boom(url, token, body):
            raise RuntimeError("graph is down")

        monkeypatch.setattr(doc_discovery, "_post", boom)
        monkeypatch.setattr(doc_discovery, "_get",
                            lambda url, token, params=None, **kw:
                            (200, SLACK_SEARCH_PAYLOAD))
        hits = doc_discovery.discover("Acme CRM")
        assert [h.source for h in hits] == ["slack"]

    def test_no_sources_returns_empty(self):
        assert doc_discovery.discover("Acme CRM") == []

    def test_empty_subject_returns_empty(self, monkeypatch):
        monkeypatch.setenv("MSGRAPH_ACCESS_TOKEN", "tok")
        assert doc_discovery.discover("   ") == []

    def test_env_kill_switch(self, monkeypatch):
        monkeypatch.setenv("MSGRAPH_ACCESS_TOKEN", "tok")
        monkeypatch.setenv("MAVERICK_ASSESS_DISCOVERY", "0")
        reset_config_cache()
        assert doc_discovery.discover("Acme CRM") == []


class TestFetch:
    def test_unconfigured_source_raises(self):
        with pytest.raises(ValueError):
            doc_discovery.fetch("msgraph", "d1")

    def test_size_cap_enforced(self, monkeypatch):
        monkeypatch.setenv("MSGRAPH_ACCESS_TOKEN", "tok")
        monkeypatch.setitem(doc_discovery._FETCHERS, "msgraph",
                            lambda base, token, ref, doc_id:
                            (b"x" * 100, "application/pdf"))
        data, mime = doc_discovery.fetch("msgraph", "d1", max_bytes=100)
        assert (len(data), mime) == (100, "application/pdf")
        with pytest.raises(ValueError):
            doc_discovery.fetch("msgraph", "d1", max_bytes=99)
