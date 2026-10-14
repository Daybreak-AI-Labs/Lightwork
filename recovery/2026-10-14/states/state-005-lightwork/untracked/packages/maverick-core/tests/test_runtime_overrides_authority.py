"""Deployment-wide CAS behavior for dashboard runtime policy overrides."""
from __future__ import annotations

import copy
import threading

import pytest
from maverick import runtime_overrides as overrides


class _AtomicDocument:
    def __init__(self, document=None) -> None:
        self.document = copy.deepcopy(document or {})
        self.lock = threading.Lock()

    def read(self):
        with self.lock:
            return copy.deepcopy(self.document)

    def mutate(self, callback, **_kwargs):
        with self.lock:
            working = copy.deepcopy(self.document)
            result = callback(working)
            self.document = working
            return result


@pytest.fixture
def shared_policy(monkeypatch, tmp_path):
    document = _AtomicDocument()
    monkeypatch.setattr(
        overrides,
        "configured_governed_records_backend",
        lambda: "postgres",
    )
    monkeypatch.setattr(overrides, "OVERRIDES_PATH", tmp_path / "legacy.toml")
    monkeypatch.setattr(overrides, "_GOVERNED_STORE", document)
    return document


def test_concurrent_mutators_preserve_unrelated_policy(shared_policy):
    barrier = threading.Barrier(4)

    def disable(name):
        barrier.wait()
        overrides.disable_tool(name)

    def budget():
        barrier.wait()
        overrides.set_budget(7.5)

    def models():
        barrier.wait()
        overrides.set_allowed_models(["anthropic:claude-sonnet-4-6"])

    threads = [
        threading.Thread(target=disable, args=("browser",)),
        threading.Thread(target=disable, args=("shell",)),
        threading.Thread(target=budget),
        threading.Thread(target=models),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert overrides.denied_tools() == {"browser", "shell"}
    assert overrides.budget_override() == 7.5
    assert overrides.allowed_models() == {"anthropic:claude-sonnet-4-6"}


def test_shared_reads_converge_without_local_file(shared_policy):
    overrides.disable_plugin("untrusted-plugin")
    overrides.set_default_model("anthropic:claude-sonnet-4-6")

    assert overrides.plugin_overlay() == (set(), {"untrusted-plugin"})
    assert overrides.default_model_override() == "anthropic:claude-sonnet-4-6"
    assert not overrides.OVERRIDES_PATH.exists()


def test_malformed_shared_policy_fails_closed(shared_policy):
    shared_policy.document = {"security": {"denied_tools": "browser"}}

    with pytest.raises(overrides.RuntimeOverrideAuthorityError, match="security"):
        overrides.denied_tools()


def test_shared_authority_outage_never_falls_back_local(monkeypatch, tmp_path):
    class _Unavailable:
        def read(self):
            raise RuntimeError("database unavailable")

        def mutate(self, *_args, **_kwargs):
            raise RuntimeError("database unavailable")

    local = tmp_path / "legacy.toml"
    monkeypatch.setattr(
        overrides,
        "configured_governed_records_backend",
        lambda: "postgres",
    )
    monkeypatch.setattr(overrides, "OVERRIDES_PATH", local)
    monkeypatch.setattr(overrides, "_GOVERNED_STORE", _Unavailable())

    with pytest.raises(overrides.RuntimeOverrideAuthorityError, match="read failed"):
        overrides.denied_tools()
    with pytest.raises(overrides.RuntimeOverrideAuthorityError, match="update failed"):
        overrides.disable_tool("browser")
    assert not local.exists()


def test_live_legacy_policy_blocks_postgres_cutover(monkeypatch, tmp_path):
    legacy = tmp_path / "legacy.toml"
    legacy.write_text('[security]\ndenied_tools = ["browser"]\n', encoding="utf-8")
    monkeypatch.setattr(
        overrides,
        "configured_governed_records_backend",
        lambda: "postgres",
    )
    monkeypatch.setattr(overrides, "OVERRIDES_PATH", legacy)
    monkeypatch.setattr(overrides, "_GOVERNED_STORE", None)

    with pytest.raises(overrides.RuntimeOverrideAuthorityError, match="cutover"):
        overrides.denied_tools()


def test_security_consumers_fail_closed_on_shared_policy_loss(monkeypatch):
    from maverick import budget, llm, plugins
    from maverick.safety import tool_acl

    def unavailable(*_args, **_kwargs):
        raise overrides.RuntimeOverrideAuthorityError("shared policy unavailable")

    monkeypatch.setattr(overrides, "denied_tools", unavailable)
    monkeypatch.setattr(tool_acl, "_load_lists", lambda: (set(), set()))
    allowed, _denied = tool_acl.resolve_lists()
    assert allowed == set(tool_acl._FAIL_CLOSED)

    monkeypatch.setattr(overrides, "allowed_models", unavailable)
    with pytest.raises(overrides.RuntimeOverrideAuthorityError):
        llm.model_for_role("coder")
    with pytest.raises(overrides.RuntimeOverrideAuthorityError):
        llm._allowlist_filter_fallbacks(["anthropic:claude-sonnet-4-6"])

    monkeypatch.setattr(overrides, "plugin_overlay", unavailable)
    monkeypatch.setattr(plugins, "_config_allowed_plugin_names", lambda: None)
    assert plugins._allowed_plugin_names() == set()

    monkeypatch.setattr(overrides, "budget_override", unavailable)
    with pytest.raises(overrides.RuntimeOverrideAuthorityError):
        budget.budget_from_config()
