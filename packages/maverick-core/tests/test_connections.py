"""Named, sealed SaaS connections + the connector env fallback."""
from __future__ import annotations

import pytest
from maverick import connections


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    monkeypatch.setattr("maverick.paths.data_dir", lambda *p, **k: tmp_path.joinpath(*p))
    monkeypatch.setenv("MAVERICK_CONNECTIONS", "1")
    # a plaintext seal so the test needs no KMS backend
    monkeypatch.setattr("maverick.connections.seal_text_for_tenant",
                        lambda t, s: s.encode("utf-8"))
    monkeypatch.setattr("maverick.connections.unseal_text_for_tenant",
                        lambda t, b: b.decode("utf-8"))


def test_set_list_get_delete_roundtrip():
    rec = connections.set_connection("salesforce-eu", connector="salesforce",
                                     base_url="https://eu.salesforce.com/", token="sekret")
    assert rec == {"name": "salesforce-eu", "connector": "salesforce",
                   "base_url": "https://eu.salesforce.com", "has_token": True,
                   "created": rec["created"], "owner": ""}
    listed = connections.list_connections()
    assert listed[0]["name"] == "salesforce-eu" and "token" not in listed[0]
    full = connections.get_connection("salesforce-eu")
    assert full["token"] == "sekret"                 # internal read keeps the token
    assert connections.delete_connection("salesforce-eu") is True
    assert connections.list_connections() == []


def test_invalid_name_rejected():
    with pytest.raises(ValueError):
        connections.set_connection("!!!", token="x")


def test_resolve_prefers_exact_name_then_connector_field():
    connections.set_connection("acme", connector="acme", base_url="https://a", token="t1")
    connections.set_connection("acme-staging", connector="acme", base_url="https://s", token="t2")
    # exact name wins
    assert connections.resolve("acme") == ("https://a", "t1")
    # unknown name falls through to the first connection whose connector matches
    connections.delete_connection("acme")
    assert connections.resolve("acme")[1] in ("t2",)


def test_resolve_returns_none_when_disabled(monkeypatch):
    connections.set_connection("x", base_url="https://x", token="t")
    monkeypatch.delenv("MAVERICK_CONNECTIONS", raising=False)
    monkeypatch.setattr("maverick.config.load_config", dict)
    assert connections.resolve("x") is None


def test_connector_env_fallback_uses_a_connection(monkeypatch):
    # env unset -> the REST connector's _env_config falls back to a connection
    from maverick.tools import _rest_connector
    monkeypatch.delenv("ACME_BASE_URL", raising=False)
    monkeypatch.delenv("ACME_TOKEN", raising=False)
    connections.set_connection("acme", connector="acme",
                               base_url="https://api.acme.test", token="conn-tok")
    base, tok = _rest_connector._env_config("acme", "ACME_BASE_URL", "ACME_TOKEN")
    assert base == "https://api.acme.test" and tok == "conn-tok"


def test_env_still_wins_over_connection(monkeypatch):
    from maverick.tools import _rest_connector
    monkeypatch.setenv("ACME_BASE_URL", "https://env.acme")
    monkeypatch.setenv("ACME_TOKEN", "env-tok")
    connections.set_connection("acme", connector="acme",
                               base_url="https://conn.acme", token="conn-tok")
    base, tok = _rest_connector._env_config("acme", "ACME_BASE_URL", "ACME_TOKEN")
    assert base == "https://env.acme" and tok == "env-tok"    # env precedence
