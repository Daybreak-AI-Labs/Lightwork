"""Settings > All settings: the runtime rows (models, tools, work area, safety).

Every row in ``settings_catalog_runtime`` saves and reads back through the
catalog API into what the kernel's ``load_config`` returns; wrong values are
refused; secrets are sealed and never returned; loosening a protection needs a
confirmation; each change leaves an audit row. The readers this package had to
fix are exercised directly: the saved value changes what the kernel does.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from maverick_dashboard import settings_catalog as catalog
from maverick_dashboard import settings_catalog_runtime as runtime
from maverick_dashboard.app import app

client = TestClient(app, headers={"Origin": "http://testserver"})
API = "/api/v1/settings/catalog"
ROWS = runtime.entries(catalog.S, catalog.C, catalog.Column)
_ENV = sorted({name for s in ROWS for name in s.env} | {
    "MAVERICK_DASHBOARD_TOKEN", "MAVERICK_CONFIG", "MAVERICK_CONFIG_OVERLAY",
    "MAVERICK_GATEWAY_TOKEN", "LLM_GATEWAY_UPSTREAM_TOKEN", "AZURE_OPENAI_DEPLOYMENT",
    "AZURE_OPENAI_API_VERSION", "AWS_REGION", "AWS_DEFAULT_REGION", "MAVERICK_ENTERPRISE",
})


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    from maverick import config, world_model

    monkeypatch.setattr(world_model, "DEFAULT_DB", tmp_path / "world.db")
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("MAVERICK_HOME", str(tmp_path / "home"))
    for name in _ENV:
        monkeypatch.delenv(name, raising=False)
    config.reset_config_cache()
    yield
    config.reset_config_cache()


@pytest.fixture()
def audit_rows(monkeypatch):
    import maverick.audit as audit

    rows: list[dict] = []

    def capture(kind, **payload):
        rows.append({"kind": kind, **payload})
        return True

    monkeypatch.setattr(audit, "audit_event", capture)
    return rows


def _loaded():
    from maverick import config

    config.reset_config_cache()
    return config.load_config()


def _save(changes, confirm=False):
    return client.post(API, json={"changes": changes, "confirm": confirm})


def _row(setting_id):
    for group in client.get(API).json()["groups"]:
        for row in group["settings"]:
            if row["id"] == setting_id:
                return row
    raise AssertionError(setting_id)


def _live(source: str) -> list[str]:
    from maverick_dashboard import settings_all_page

    return [v for v, _label in settings_all_page._source_choices(source) if v]


# Values sent for rows whose control is not a single switch, choice or number,
# with the value the kernel then reads.
_WORDS = {
    "persona.addendum": "Always cite your sources.",
    "providers.anthropic.base_url": "https://gateway.example.com/anthropic",
    "providers.openai_compatible.base_url": "http://localhost:8000/v1",
    "providers.azure.base_url": "https://acme.openai.azure.com",
    "providers.azure.deployment": "gpt-deploy",
    "providers.azure.api_version": "2024-10-21",
    "providers.bedrock.region": "us-east-1",
    "llm_gateway.anthropic_version": "2023-06-01",
    "sandbox.workdir": "/srv/lightwork/work",
    "sandbox.runtime": "runsc-debug",
    "sandbox.context": "prod-cluster",
    "sandbox.kubeconfig": "/etc/lightwork/kubeconfig",
    "sandbox.project_dir": "/srv/project",
    "plugins.ca_root_pubkey": "ab" * 32,
    "attachments.s3_bucket": "acme-attachments",
    "attachments.s3_prefix": "lightwork/",
    "knowledge.path": "/srv/lightwork/index.db",
    "knowledge.revision": "v2",
    "screening.sdn_path": "/srv/lists/sdn.txt",
}
_LISTS = {
    "models.catalog": ["vllm:my-model"],
    "sandbox.ssh_args": ["-p2222"],
    "sandbox.extra_kubectl_args": ["--request-timeout=30s"],
    "egress.deny": ["169.254.169.254"],
    "egress.allow": ["api.example.com"],
    "plugins.ca_revoked": ["0123456789abcdef"],
    "attachments.extra_mime_prefixes": ["application/vnd.visio"],
    "containment.deny_tools": ["github_issues"],
}


def _sample(setting) -> tuple[object, object]:  # noqa: C901 - one branch per control
    """(value sent, value the kernel reads) for a change away from the default."""
    sid = setting.id
    if sid in _WORDS:
        return _WORDS[sid], _WORDS[sid]
    if sid in _LISTS:
        return _LISTS[sid], _LISTS[sid]
    if setting.control == "toggle":
        return (not setting.default,) * 2
    if setting.control == "secret":
        return "s3cret-value-for-" + sid, "s3cret-value-for-" + sid
    if setting.control == "select":
        options = [v for v, _ in setting.choices] or _live(setting.choices_from)
        value = next(v for v in options if v != setting.default)
        return value, value
    if setting.control == "multiselect":
        value = [setting.choices[0][0]]
        return value, value
    if setting.control == "number":
        shown = setting.maximum if setting.minimum is None else setting.minimum
        if shown * setting.scale == setting.default:
            shown = setting.maximum
        stored = shown * setting.scale
        if setting.integer or float(stored).is_integer():
            stored = int(round(stored)) if setting.integer or isinstance(setting.default, int) \
                else stored
        return shown, stored
    if sid == "context.model_windows":
        model = _live("models")[0]
        return [{"name": model, "window": 64000}], {model: 64000}
    if sid == "routing.roles":
        return ([{"name": "coder", "tier": "cheap"}],
                {"coder": {"providers": [], "deny_providers": [], "tier": "cheap"}})
    if sid == "sandbox.tool":
        return ([{"name": "http_fetch", "allow_egress": ["api.example.com"]}],
                {"http_fetch": {"allow_egress": ["api.example.com"], "deny_egress": []}})
    if sid == "security.tool_risk":
        return [{"name": "mcp_*", "risk": "medium"}], {"mcp_*": "medium"}
    if sid == "lsp.servers":
        return [{"name": "python", "command": "pylsp"}], {"python": ["pylsp"]}
    if sid == "safety.constitution":
        return ([{"phrases": "launch codes, secret plan", "severity": "critical"}],
                [{"phrases": ["launch codes", "secret plan"], "severity": "critical"}])
    raise AssertionError(f"no sample for {sid}")


def _bad(setting) -> object:
    """A value the catalog must refuse for this row."""
    control = setting.control
    if control == "toggle":
        return "yes"
    if control == "select":
        return "not-an-option"
    if control == "number":
        return setting.minimum - 1 if setting.minimum is not None else "lots"
    if control in {"text"}:
        return "<bad>"
    if control == "secret":
        return ""
    if control == "list":
        return ["<bad>"]
    if control == "multiselect":
        return ["nope"]
    if control == "table":
        return [{"name": "no-such-choice"}]
    if control == "rules":
        return [{"phrases": "", "severity": "high"}]
    raise AssertionError(setting.id)


def _stored(tree, setting):
    present, value = catalog.lookup(tree, setting.path)
    assert present, setting.id
    return value


def test_every_runtime_row_is_in_the_catalog():
    ids = {s.id for s in ROWS}
    assert ids <= set(catalog.BY_ID)
    assert len(ids) == len(ROWS)
    assert all(s.group in catalog.GROUPS for s in ROWS)


@pytest.mark.parametrize("setting", ROWS, ids=lambda s: s.id)
def test_row_saves_reads_back_and_resets(setting, audit_rows):
    sent, stored = _sample(setting)
    r = _save([{"id": setting.id, "value": sent}])
    if r.status_code == 409:
        assert catalog.weakens(setting, setting.default, stored) or setting.warning
        assert r.json()["confirm"][0]["id"] == setting.id
        r = _save([{"id": setting.id, "value": sent}], confirm=True)
    assert r.status_code == 200, r.text
    assert _stored(_loaded(), setting) == stored
    row = _row(setting.id)
    assert row["source"] == "here"
    if setting.secret:
        assert row["value"] is None and row["is_set"] is True
        assert stored not in r.text and stored not in client.get(API).text
        assert stored not in client.get("/settings/all").text
        text = __import__("maverick.config", fromlist=["x"]).dashboard_overrides_path() \
            .read_text(encoding="utf-8")
        assert stored not in text and "MVKAR1:" in text
    kinds = [a["kind"] for a in audit_rows]
    assert kinds == ["setting_changed"]
    assert (audit_rows[0]["section"], audit_rows[0]["key"]) == (setting.section, setting.key)
    if setting.secret:
        assert audit_rows[0]["new"] == "set" and stored not in repr(audit_rows)
    r = _save([{"id": setting.id, "reset": True}], confirm=True)
    assert r.status_code == 200, r.text
    assert _row(setting.id)["source"] == "default"


@pytest.mark.parametrize("setting", ROWS, ids=lambda s: s.id)
def test_row_refuses_wrong_values(setting, audit_rows):
    r = _save([{"id": setting.id, "value": _bad(setting)}], confirm=True)
    assert r.status_code == 400, r.text
    assert r.json()["message"]
    assert audit_rows == []
    present, _ = catalog.lookup(_loaded(), setting.path)
    assert not present


_LOOSENING = [s for s in ROWS if s.warning]


@pytest.mark.parametrize("setting", _LOOSENING, ids=lambda s: s.id)
def test_loosening_needs_confirmation(setting, audit_rows):
    sent, _stored_value = _sample(setting)
    setup = None
    if setting.weaker == "shrink":
        setup, sent = sent, []
    elif setting.control == "toggle":
        setup = (not setting.weaker) if setting.default is setting.weaker else None
        sent = setting.weaker
    elif setting.control == "select" and setting.order:
        setup = setting.order[0] if setting.default == setting.order[-1] else None
        sent = setting.order[-1]
    elif setting.weaker == "higher":
        setup, sent = setting.minimum, setting.maximum
    elif setting.weaker == "lower":
        setup, sent = setting.maximum, setting.minimum
    if setup is not None:
        assert _save([{"id": setting.id, "value": setup}], confirm=True).status_code == 200
    r = _save([{"id": setting.id, "value": sent}])
    assert r.status_code == 409, r.text
    assert r.json()["confirm"][0]["warning"] == setting.warning
    r = _save([{"id": setting.id, "value": sent}], confirm=True)
    assert r.status_code == 200, r.text
    assert audit_rows[-1]["confirmed"] is True


def test_environment_value_locks_the_row(monkeypatch, audit_rows):
    monkeypatch.setenv("MAVERICK_CONSENT_MODE", "ask")
    row = _row("safety.consent_mode")
    assert row["locked"] is True and row["source"] == "deployment"
    r = _save([{"id": "safety.consent_mode", "value": "auto-approve"}], confirm=True)
    assert r.status_code == 409
    assert "safety" not in _loaded()


# ------------------------------------------------------------ fixed readers
def test_consent_mode_saved_here_gates_actions(monkeypatch, audit_rows):
    from maverick.safety import consent

    assert _save([{"id": "safety.consent_mode", "value": "auto-deny"}]).status_code == 200
    assert consent._resolve_mode("low") == "auto-deny"
    assert consent._resolve_mode("critical") == "auto-deny"
    r = _save([{"id": "safety.consent_mode", "value": "auto-approve"}], confirm=True)
    assert r.status_code == 200
    assert consent._resolve_mode("critical") == "auto-approve"
    monkeypatch.setenv("MAVERICK_CONSENT_MODE", "dashboard")
    assert consent._resolve_mode("low") == "dashboard"  # the environment still wins


def test_unknown_consent_mode_in_config_fails_closed(tmp_path):
    from maverick import config
    from maverick.safety import consent

    config.config_path().parent.mkdir(parents=True, exist_ok=True)
    config.config_path().write_text('[safety]\nconsent_mode = "maybe"\n', encoding="utf-8")
    config.reset_config_cache()
    assert consent._resolve_mode("low") == "ask"


def test_blocked_phrases_reach_the_safety_checks(audit_rows):
    from maverick import config
    from maverick_shield.constitutional import parse_rules, scan

    row = {"phrases": "launch codes, Secret (plan)", "severity": "critical"}
    r = _save([{"id": "safety.constitution", "value": [row]}], confirm=True)
    assert r.status_code == 200, r.text
    rules = parse_rules(config.get_safety()["constitution"])
    assert len(rules) == 1 and rules[0].name == "launch codes, Secret (plan)"
    assert scan("share the LAUNCH CODES now", rules) == (True, "critical", [rules[0].name])
    # Phrases are matched as written, never as patterns.
    assert scan("secret plan", rules)[0] is False
    assert scan("the secret (plan) is", rules)[0] is True


def test_model_reading_limit_saved_with_the_service_name_applies():
    from maverick import preflight

    model = next(m for m in _live("models") if ":" in m)
    bare = model.split(":", 1)[1]
    r = _save([{"id": "context.model_windows", "value": [{"name": model, "window": 12345}]}])
    assert r.status_code == 200, r.text
    assert preflight.context_limit(bare) == 12345


def test_gateway_credentials_saved_here_are_used_and_env_wins(monkeypatch, audit_rows):
    from maverick import llm_gateway

    r = _save([{"id": "llm_gateway.upstream_url", "value": "https://api.example.com"},
               {"id": "llm_gateway.enable", "value": True},
               {"id": "llm_gateway.gateway_token", "value": "caller-pass-123"},
               {"id": "llm_gateway.upstream_token", "value": "upstream-key-456"}],
              confirm=True)
    assert r.status_code == 200, r.text
    assert llm_gateway.verify_gateway_bearer("caller-pass-123") is True
    assert llm_gateway.verify_gateway_bearer("wrong") is False
    settings = llm_gateway.get_llm_gateway_config()
    assert "caller-pass-123" not in repr(settings) and "upstream-key-456" not in repr(settings)
    _url, headers = llm_gateway._provider_upstream("openai", settings)
    assert headers["Authorization"] == "Bearer upstream-key-456"
    monkeypatch.setenv("MAVERICK_GATEWAY_TOKEN", "from-the-server")
    assert llm_gateway.verify_gateway_bearer("from-the-server") is True
    assert llm_gateway.verify_gateway_bearer("caller-pass-123") is False


def test_azure_and_bedrock_details_saved_here_reach_the_client(monkeypatch, audit_rows):
    from maverick import config
    from maverick.llm import LLM

    r = _save([{"id": "providers.azure.base_url", "value": "https://acme.openai.azure.com"},
               {"id": "providers.azure.api_key", "value": "azure-key-1"},
               {"id": "providers.azure.deployment", "value": "gpt-deploy"},
               {"id": "providers.bedrock.region", "value": "eu-west-1"},
               {"id": "providers.bedrock.endpoint", "value": "legacy"}], confirm=True)
    assert r.status_code == 200, r.text
    assert "deployment" not in config.azure_provider_configuration_missing(
        config.get_provider_config("azure"))
    env = dict(LLM()._provider_client_config("azure").provider_environment)
    assert env["AZURE_OPENAI_DEPLOYMENT"] == "gpt-deploy"
    env = dict(LLM()._provider_client_config("bedrock").provider_environment)
    assert env["AWS_REGION"] == "eu-west-1" and env["BEDROCK_ENDPOINT"] == "legacy"
    monkeypatch.setenv("AZURE_OPENAI_DEPLOYMENT", "from-env")
    monkeypatch.setenv("AWS_REGION", "us-west-2")
    assert dict(LLM()._provider_client_config("azure").provider_environment)[
        "AZURE_OPENAI_DEPLOYMENT"] == "from-env"
    assert dict(LLM()._provider_client_config("bedrock").provider_environment)[
        "AWS_REGION"] == "us-west-2"


def test_provider_switches_and_keys_share_the_settings_file(audit_rows):
    from maverick import config
    from maverick_dashboard import settings_store

    settings_store.set_provider("anthropic", api_key="sk-ant-keep")  # pragma: allowlist secret
    r = _save([{"id": "providers.per_tenant_keys", "value": True},
               {"id": "providers.bedrock.endpoint", "value": "legacy"}], confirm=True)
    assert r.status_code == 200, r.text
    assert config.providers_per_tenant_keys() is True
    assert config.get_provider_config("anthropic")["api_key"] == "sk-ant-keep"  # pragma: allowlist secret
    assert config.get_provider_config("bedrock")["endpoint"] == "legacy"
    assert config.config_source_errors() == {}


def test_deferred_connectors_switch_reaches_the_agent(audit_rows):
    from maverick import config

    assert config.get_capabilities()["deferred_tools"] is True
    assert _save([{"id": "capabilities.deferred_tools", "value": False}]).status_code == 200
    assert config.get_capabilities()["deferred_tools"] is False


def test_plugin_settings_survive_other_plugin_writes(audit_rows):
    from maverick import plugins
    from maverick_dashboard import settings_store

    settings_store.set_plugin_install_allowed(True)
    settings_store.set_installable_plugins(["weather-plugin"])
    key = "cd" * 32
    r = _save([{"id": "plugins.ca_root_pubkey", "value": key},
               {"id": "plugins.ca_revoked", "value": ["0123456789abcdef"]},
               {"id": "plugins.isolation", "value": "subprocess"}], confirm=True)
    assert r.status_code == 200, r.text
    settings_store.set_plugin_install_allowed(False)
    loaded = _loaded()["plugins"]
    assert loaded["allow_install"] is False and loaded["installable"] == ["weather-plugin"]
    assert loaded["isolation"] == "subprocess"
    root, required, revoked = plugins._plugin_signing_policy()
    assert root == key and required is True and "0123456789abcdef" in revoked
    from maverick.plugin_isolation import isolation_mode

    assert isolation_mode() == "subprocess"


def test_sandbox_details_survive_the_work_area_type_selector(audit_rows):
    from maverick_dashboard import settings_store

    r = _save([{"id": "sandbox.api_key", "value": "e2b-key-1"},
               {"id": "sandbox.provider", "value": "e2b"}], confirm=True)
    assert r.status_code == 200, r.text
    settings_store.set_sandbox_backend("docker")
    loaded = _loaded()["sandbox"]
    expected = {"backend": "docker", "api_key": "e2b-key-1",  # pragma: allowlist secret
                "provider": "e2b"}
    assert loaded == expected
    stored = __import__("maverick.config", fromlist=["x"]).dashboard_overrides_path() \
        .read_text(encoding="utf-8")
    assert "e2b-key-1" not in stored


def test_locked_down_mode_applies_at_every_seam(tmp_path, audit_rows):
    from maverick.budget import resolve_budget_caps
    from maverick.sandbox import LocalBackend, build_sandbox
    from maverick.tools import base_registry
    from maverick.world_model import open_world

    r = _save([{"id": "containment.enabled", "value": True},
               {"id": "containment.max_wall_seconds", "value": 10},
               {"id": "containment.deny_tools", "value": ["read_file"]},
               {"id": "sandbox.workdir", "value": str(tmp_path / "work")}], confirm=True)
    assert r.status_code == 200, r.text
    # Tools that send data out, and the extra ones listed, are removed.
    world = open_world(None)
    reg = base_registry(world, LocalBackend(workdir=tmp_path))
    assert not reg._acl_allows("http_fetch") and not reg._acl_allows("read_file")
    assert "read_file" not in {t.name for t in reg.exposed()}
    assert reg._acl_allows("shell")
    # Every task is capped at the locked-down time limit.
    assert resolve_budget_caps(max_wall_seconds=7200)["max_wall_seconds"] == 600
    assert resolve_budget_caps(max_wall_seconds=60)["max_wall_seconds"] == 60
    # Commands run in a throwaway folder with the proxies pointed at a dead end.
    box = build_sandbox()
    folder = box.workdir
    assert folder != tmp_path / "work" and folder.is_dir()
    result = box.exec("echo $HTTPS_PROXY")
    assert result.stdout.strip() == "http://127.0.0.1:9"
    del box
    import gc

    gc.collect()
    assert not folder.exists()


def test_locked_down_mode_turns_container_internet_off():
    from maverick.containment import ContainmentProfile
    from maverick.sandbox import _contain

    held: list = []
    cfg, wd = _contain(ContainmentProfile(ephemeral_workspace=False), "docker",
                       {"allow_network": True}, __import__("pathlib").Path("/w"), held)
    assert cfg["allow_network"] is False and cfg["network"] == "egress-deny"
    assert wd == __import__("pathlib").Path("/w") and held == []
    cfg, wd = _contain(None, "docker", {"allow_network": True},
                       __import__("pathlib").Path("/w"), held)
    assert cfg == {"allow_network": True}


def test_locked_down_mode_off_changes_nothing(tmp_path):
    from maverick.budget import resolve_budget_caps
    from maverick.sandbox import build_sandbox

    assert resolve_budget_caps(max_wall_seconds=7200)["max_wall_seconds"] == 7200
    from maverick.sandbox.local import scrub_env

    box = build_sandbox(workdir=tmp_path)
    assert box.workdir == tmp_path
    expected = scrub_env().get("HTTPS_PROXY") or "none"
    assert box.exec("echo ${HTTPS_PROXY:-none}").stdout.strip() == expected


def test_request_fit_check_follows_the_setting(monkeypatch, audit_rows):
    from maverick import llm
    from maverick.preflight import PreflightFailed

    huge = [{"role": "user", "content": "word " * 400_000}]
    llm._run_preflight("claude-haiku-4-5", "", huge, None, 1000)  # default: warn only
    assert _save([{"id": "context.preflight", "value": "strict"}]).status_code == 200
    with pytest.raises(PreflightFailed):
        llm._run_preflight("claude-haiku-4-5", "", huge, None, 1000)
    monkeypatch.setenv("MAVERICK_PREFLIGHT", "off")  # the environment still wins
    llm._run_preflight("claude-haiku-4-5", "", huge, None, 1000)
    monkeypatch.delenv("MAVERICK_PREFLIGHT")
    r = _save([{"id": "context.preflight", "value": "off"}])
    assert r.status_code == 409
    assert _save([{"id": "context.preflight", "value": "off"}], confirm=True).status_code == 200
    llm._run_preflight("claude-haiku-4-5", "", huge, None, 1000)
