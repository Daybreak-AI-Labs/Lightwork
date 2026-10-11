"""Config loader tests."""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest
from maverick.config import _interp, config_path, get_safety, load_config


def test_default_config_path_honors_maverick_home(tmp_path, monkeypatch):
    home = tmp_path / "isolated-home"
    monkeypatch.setenv("MAVERICK_HOME", str(home))
    monkeypatch.delenv("MAVERICK_CONFIG", raising=False)

    assert config_path() == home / "config.toml"


def test_missing_config_returns_empty_dict():
    cfg = load_config(Path("/this/path/does/not/exist.toml"))
    assert cfg == {}


def test_corrupt_config_fails_soft_to_empty_dict():
    """A corrupt/unparseable config.toml must not crash the agent loop; it
    fails soft to {} like a missing file. Regression: load_config raised
    TOMLDecodeError, which propagated through every get_role_model/get_safety
    caller. The common real-world trigger is a Windows backslash path that
    TOML reads as an invalid \\U escape."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".toml", delete=False) as f:
        f.write('workdir = "C:\\Users\\x\\ws"\n[unterminated\n')  # invalid TOML
        path = Path(f.name)
    try:
        assert load_config(path) == {}
    finally:
        path.unlink()


def test_corrupt_active_config_exposes_security_health_error(tmp_path, monkeypatch):
    import maverick.config as cfg_mod

    path = tmp_path / "config.toml"
    path.write_text("[agent_trust\nenforce = true\n", encoding="utf-8")
    monkeypatch.setenv("MAVERICK_CONFIG", str(path))
    cfg_mod.reset_config_cache()
    try:
        assert cfg_mod.load_config() == {}  # ordinary consumers remain fail-soft
        assert str(path) in cfg_mod.config_source_errors()
    finally:
        cfg_mod.reset_config_cache()


def test_deleted_corrupt_optional_overlay_clears_security_health_error(
    tmp_path, monkeypatch,
):
    import maverick.config as cfg_mod

    path = tmp_path / "config.toml"
    path.write_text("", encoding="utf-8")
    overlay = tmp_path / cfg_mod.DASHBOARD_OVERRIDES_BASENAME
    overlay.write_text("[agent_trust\nenforce = true\n", encoding="utf-8")
    monkeypatch.setenv("MAVERICK_CONFIG", str(path))
    cfg_mod.reset_config_cache()
    try:
        cfg_mod.load_config()
        assert str(overlay) in cfg_mod.config_source_errors()

        overlay.unlink()
        cfg_mod.load_config()  # optional overlay is now skipped

        assert str(overlay) not in cfg_mod.config_source_errors()
    finally:
        cfg_mod.reset_config_cache()


def test_unreadable_optional_overlay_retains_security_health_error(
    tmp_path, monkeypatch,
):
    import maverick.config as cfg_mod

    path = tmp_path / "config.toml"
    path.write_text("", encoding="utf-8")
    overlay = tmp_path / cfg_mod.DASHBOARD_OVERRIDES_BASENAME
    overlay.write_text("[agent_trust\nenforce = true\n", encoding="utf-8")
    monkeypatch.setenv("MAVERICK_CONFIG", str(path))
    cfg_mod.reset_config_cache()
    try:
        cfg_mod.load_config()
        assert str(overlay) in cfg_mod.config_source_errors()

        real_stat = Path.stat

        def _deny_overlay_stat(self, *args, **kwargs):
            if self == overlay:
                raise PermissionError("policy source is not readable")
            return real_stat(self, *args, **kwargs)

        monkeypatch.setattr(Path, "stat", _deny_overlay_stat)

        assert str(overlay) in cfg_mod.config_source_errors()
    finally:
        cfg_mod.reset_config_cache()


def test_env_var_interpolation(monkeypatch):
    monkeypatch.setenv("MAVERICK_TEST_KEY", "hello")
    assert _interp("${MAVERICK_TEST_KEY}") == "hello"
    assert _interp("prefix-${MAVERICK_TEST_KEY}-suffix") == "prefix-hello-suffix"


def test_unset_env_var_becomes_empty(monkeypatch):
    monkeypatch.delenv("MAVERICK_NEVER_SET", raising=False)
    assert _interp("${MAVERICK_NEVER_SET}") == ""


def test_config_cache_avoids_reparse_but_keeps_interp_live(tmp_path, monkeypatch):
    # Uses a benign [models] key so the literal isn't flagged by detect-secrets.
    import maverick.config as cfg_mod
    cfg_mod.reset_config_cache()
    path = tmp_path / "c.toml"
    path.write_text('[models]\nsummarizer = "${MAVERICK_CFG_TEST_VAL}"\n')

    calls = {"n": 0}
    real_load = cfg_mod.tomllib.load

    def _counting_load(f):
        calls["n"] += 1
        return real_load(f)

    monkeypatch.setattr(cfg_mod.tomllib, "load", _counting_load)

    monkeypatch.setenv("MAVERICK_CFG_TEST_VAL", "first")
    assert cfg_mod.load_config(path)["models"]["summarizer"] == "first"
    # Second read: parse is cached (no new tomllib.load) ...
    monkeypatch.setenv("MAVERICK_CFG_TEST_VAL", "second")
    assert cfg_mod.load_config(path)["models"]["summarizer"] == "second"
    assert calls["n"] == 1  # parsed once, interpolation re-ran live

    # Editing the file (mtime/size changes) invalidates the cache.
    path.write_text(
        '[models]\nsummarizer = "${MAVERICK_CFG_TEST_VAL}"\norchestrator = "x"\n')
    cfg = cfg_mod.load_config(path)
    assert cfg["models"]["orchestrator"] == "x"
    assert calls["n"] == 2
    cfg_mod.reset_config_cache()


def test_load_config_with_models_section():
    with tempfile.NamedTemporaryFile(mode="w", suffix=".toml", delete=False) as f:
        f.write(
            '[models]\n'
            'orchestrator = "anthropic:claude-opus-4-7"\n'
            'summarizer = "ollama:phi3:14b"\n'
        )
        path = Path(f.name)
    try:
        cfg = load_config(path)
        assert cfg["models"]["orchestrator"] == "anthropic:claude-opus-4-7"
        assert cfg["models"]["summarizer"] == "ollama:phi3:14b"
    finally:
        path.unlink()


def test_get_governed_connectors_defaults_off(monkeypatch):
    import maverick.config as cfg_mod
    monkeypatch.delenv("MAVERICK_GOVERNED_RESTORE_POINTS", raising=False)
    monkeypatch.setattr(cfg_mod, "load_config", lambda *a, **k: {})
    gc = cfg_mod.get_governed_connectors()
    # restore_points defaults ON: it only takes effect once connectors are
    # enabled, and without it every in-place update is irreversible.
    assert gc == {"enable": False, "connectors": [], "approver": "",
                  "restore_points": True}


def test_get_governed_connectors_parses_list_and_csv(monkeypatch):
    import maverick.config as cfg_mod
    monkeypatch.delenv("MAVERICK_GOVERNED_RESTORE_POINTS", raising=False)
    monkeypatch.setattr(cfg_mod, "load_config", lambda *a, **k: {
        "governed_connectors": {"enable": True, "connectors": ["salesforce", " servicenow "],
                                "approver": "ops@corp"}})
    assert cfg_mod.get_governed_connectors() == {
        "enable": True, "connectors": ["salesforce", "servicenow"],
        "approver": "ops@corp", "restore_points": True}
    # A CSV string (an operator hand-edit) is tolerated and split.
    monkeypatch.setattr(cfg_mod, "load_config", lambda *a, **k: {
        "governed_connectors": {"enable": True, "connectors": "salesforce, servicenow"}})
    assert cfg_mod.get_governed_connectors()["connectors"] == ["salesforce", "servicenow"]


def test_automation_import_rejects_string_truthiness(monkeypatch):
    import maverick.config as cfg_mod

    monkeypatch.setattr(cfg_mod, "load_config", lambda: {
        "automation_import": {
            "enable": "false",
            "create_schedules": "true",
        }
    })
    assert cfg_mod.get_automation_import() == {
        "enable": False,
        "create_schedules": False,
    }


def test_nested_dict_interpolation(monkeypatch):
    monkeypatch.setenv("X", "42")
    data = {"a": "${X}", "b": ["${X}", "plain"], "c": {"inner": "${X}"}}
    out = _interp(data)
    assert out["a"] == "42"
    assert out["b"] == ["42", "plain"]
    assert out["c"]["inner"] == "42"


def test_get_safety_preserves_constitution_rules(monkeypatch):
    with tempfile.NamedTemporaryFile(mode="w", suffix=".toml", delete=False) as f:
        f.write(
            '[safety]\n'
            'block_threshold = "high"\n'
            '[[safety.constitution]]\n'
            'name = "no_zephyr"\n'
            'pattern = "zephyr-token"\n'
            'severity = "high"\n'
        )
        path = Path(f.name)
    monkeypatch.setenv("MAVERICK_CONFIG", str(path))
    try:
        safety = get_safety()
        assert safety["constitution"] == [
            {"name": "no_zephyr", "pattern": "zephyr-token", "severity": "high"}
        ]
    finally:
        path.unlink()


def test_toml_cache_is_bounded(tmp_path, monkeypatch):
    """The parsed-TOML cache must not grow without bound across many distinct
    config paths (e.g. one per tenant). Oldest entries are evicted past the cap."""
    from maverick import config as cfg
    cfg.reset_config_cache()
    monkeypatch.setattr(cfg, "_TOML_CACHE_MAX", 8)
    try:
        for i in range(40):
            p = tmp_path / f"t{i}.toml"
            p.write_text(f'[budget]\nmax_dollars = {i}\n', encoding="utf-8")
            assert cfg._read_toml_raw(p) == {"budget": {"max_dollars": i}}
        assert len(cfg._toml_cache) <= 8     # bounded, not 40
    finally:
        cfg.reset_config_cache()


def test_env_flag_tristate(monkeypatch):
    from maverick.config import env_flag

    for val in ("1", "true", "TRUE", "Yes", "on", " on "):
        monkeypatch.setenv("MAVERICK_X", val)
        assert env_flag("MAVERICK_X") is True, val
    for val in ("0", "false", "No", "off", "OFF"):
        monkeypatch.setenv("MAVERICK_X", val)
        assert env_flag("MAVERICK_X") is False, val
    for val in ("", "maybe", "2"):
        monkeypatch.setenv("MAVERICK_X", val)
        assert env_flag("MAVERICK_X") is None, val
    monkeypatch.delenv("MAVERICK_X", raising=False)
    assert env_flag("MAVERICK_X") is None


def _write_wizard_files(home, env_line: str):
    """A wizard-shaped install: config.toml referencing ${VAR} + secrets in .env."""
    mav = home / ".maverick"
    mav.mkdir(exist_ok=True)
    (mav / "config.toml").write_text(
        '[providers.anthropic]\napi_key = "${MAVERICK_ENVFILE_TEST_KEY}"\n',
        encoding="utf-8",
    )
    (mav / ".env").write_text(f"# wizard-written secrets\n{env_line}\n", encoding="utf-8")


def test_wizard_env_file_is_loaded_for_interpolation(tmp_path, monkeypatch):
    """The installer persists every collected secret ONLY to ~/.maverick/.env
    and writes config values as "${VAR}". Compose/systemd inject that file, but
    a bare pip/CLI install had NO injector, so a freshly validated key
    interpolated to "" and `maverick start` exited 2 right after `maverick
    init` said "Setup complete". load_config() must export the file itself."""
    import os

    import maverick.config as cfg_mod

    cfg_mod.reset_config_cache()
    _write_wizard_files(tmp_path, "MAVERICK_ENVFILE_TEST_KEY=tok-123")  # pragma: allowlist secret
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))  # Windows Path.home()
    monkeypatch.delenv("MAVERICK_CONFIG", raising=False)
    monkeypatch.delenv("MAVERICK_ENVFILE_TEST_KEY", raising=False)
    try:
        cfg = cfg_mod.load_config()
        assert cfg["providers"]["anthropic"]["api_key"] == "tok-123"  # pragma: allowlist secret
        # ...and direct env readers (llm._provider_api_key) see it too.
        assert os.environ["MAVERICK_ENVFILE_TEST_KEY"] == "tok-123"
    finally:
        os.environ.pop("MAVERICK_ENVFILE_TEST_KEY", None)
        cfg_mod.reset_config_cache()


def test_env_file_never_overrides_exported_var(tmp_path, monkeypatch):
    """An explicitly exported variable wins over a stale ~/.maverick/.env value
    (setdefault semantics, matching compose env_file / systemd EnvironmentFile)."""
    import os

    import maverick.config as cfg_mod

    cfg_mod.reset_config_cache()
    _write_wizard_files(tmp_path, "MAVERICK_ENVFILE_TEST_KEY=stale-file-value")  # pragma: allowlist secret
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.delenv("MAVERICK_CONFIG", raising=False)
    monkeypatch.setenv("MAVERICK_ENVFILE_TEST_KEY", "from-shell")
    try:
        cfg = cfg_mod.load_config()
        assert cfg["providers"]["anthropic"]["api_key"] == "from-shell"  # pragma: allowlist secret
        assert os.environ["MAVERICK_ENVFILE_TEST_KEY"] == "from-shell"
    finally:
        cfg_mod.reset_config_cache()


def test_env_file_key_satisfies_any_provider_configured(tmp_path, monkeypatch):
    """The CLI preflight path: with the key present ONLY in ~/.maverick/.env,
    any_provider_configured() must be True (was False -> exit 2)."""
    import os

    import maverick.config as cfg_mod

    cfg_mod.reset_config_cache()
    mav = tmp_path / ".maverick"
    mav.mkdir()
    (mav / "config.toml").write_text(
        '[providers.anthropic]\napi_key = "${ANTHROPIC_API_KEY}"\n', encoding="utf-8",
    )
    (mav / ".env").write_text("ANTHROPIC_API_KEY=tok-e2e\n", encoding="utf-8")  # pragma: allowlist secret
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.delenv("MAVERICK_CONFIG", raising=False)
    for var in (
        cfg_mod.PROVIDER_CREDENTIAL_ENV_VARS
        + cfg_mod.PROVIDER_BASE_URL_ENV_VARS
    ):
        monkeypatch.delenv(var, raising=False)
    try:
        assert cfg_mod.any_provider_configured() is True
    finally:
        os.environ.pop("ANTHROPIC_API_KEY", None)
        cfg_mod.reset_config_cache()


def test_incomplete_azure_credential_does_not_satisfy_configured_probe(monkeypatch):
    import maverick.config as cfg_mod

    monkeypatch.setattr(cfg_mod, "load_config", dict)
    for var in (
        cfg_mod.PROVIDER_CREDENTIAL_ENV_VARS
        + cfg_mod.PROVIDER_BASE_URL_ENV_VARS
    ):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("AZURE_OPENAI_AD_TOKEN", "static-entra-token")

    assert cfg_mod.any_provider_configured() is False


@pytest.mark.parametrize(
    ("auth_name", "auth_value"),
    [
        ("AZURE_OPENAI_AD_TOKEN", "static-entra-token"),
        ("AZURE_OPENAI_AUTH", "entra_id"),
        ("AZURE_OPENAI_API_KEY", "azure-api-key"),
    ],
)
def test_complete_azure_route_satisfies_configured_probe(
    monkeypatch, auth_name, auth_value,
):
    import maverick.config as cfg_mod

    monkeypatch.setattr(cfg_mod, "load_config", dict)
    for var in (
        cfg_mod.PROVIDER_CREDENTIAL_ENV_VARS
        + cfg_mod.PROVIDER_BASE_URL_ENV_VARS
        + ("AZURE_OPENAI_AUTH", "AZURE_OPENAI_ENDPOINT", "AZURE_OPENAI_DEPLOYMENT")
    ):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", "https://res.openai.azure.com")
    monkeypatch.setenv("AZURE_OPENAI_DEPLOYMENT", "gpt5")
    monkeypatch.setenv(auth_name, auth_value)

    assert cfg_mod.any_provider_configured() is True


def test_azure_config_auth_mode_satisfies_configured_probe(monkeypatch):
    import maverick.config as cfg_mod

    table = {
        "base_url": "https://res.openai.azure.com",
        "auth_mode": "entra_id",
    }
    monkeypatch.setattr(
        cfg_mod,
        "load_config",
        lambda: {"providers": {"azure": table}},
    )
    for var in (
        cfg_mod.PROVIDER_CREDENTIAL_ENV_VARS
        + cfg_mod.PROVIDER_BASE_URL_ENV_VARS
        + ("AZURE_OPENAI_AUTH", "AZURE_OPENAI_ENDPOINT", "AZURE_OPENAI_DEPLOYMENT")
    ):
        monkeypatch.delenv(var, raising=False)
    # A conflicting environment mode must not override the admitted table.
    monkeypatch.setenv("AZURE_OPENAI_AUTH", "api_key")
    monkeypatch.setenv("AZURE_OPENAI_DEPLOYMENT", "gpt5")

    assert cfg_mod.azure_provider_configuration_missing(table) == ()
    assert cfg_mod.any_provider_configured() is True


def test_codex_login_file_satisfies_configured_probe(tmp_path, monkeypatch):
    import maverick.config as cfg_mod

    monkeypatch.setattr(cfg_mod, "load_config", dict)
    for var in (
        cfg_mod.PROVIDER_CREDENTIAL_ENV_VARS
        + cfg_mod.PROVIDER_BASE_URL_ENV_VARS
        + ("AZURE_OPENAI_AUTH", "AZURE_OPENAI_ENDPOINT", "AZURE_OPENAI_DEPLOYMENT")
    ):
        monkeypatch.delenv(var, raising=False)
    codex_home = tmp_path / "codex-home"
    codex_home.mkdir()
    monkeypatch.setenv("CODEX_HOME", str(codex_home))

    assert cfg_mod.any_provider_configured() is False
    (codex_home / "auth.json").write_text("{}", encoding="utf-8")
    assert cfg_mod.any_provider_configured() is True


def test_env_file_recheck_is_throttled(monkeypatch):
    """load_config() sits on per-row hot paths (_dec_field on every sealed
    column read); the .env freshness re-check must NOT stat the file on every
    call or the world_read perf SLA breaches. One check per throttle window."""
    import maverick.config as cfg_mod

    cfg_mod.reset_config_cache()
    calls = []
    monkeypatch.setattr(cfg_mod, "_load_env_file", lambda p: calls.append(p))
    try:
        for _ in range(100):
            cfg_mod.load_config()
        assert len(calls) == 1
        # reset_config_cache (the test hook) re-arms an immediate re-check.
        cfg_mod.reset_config_cache()
        cfg_mod.load_config()
        assert len(calls) == 2
    finally:
        cfg_mod.reset_config_cache()


def test_data_use_governance_enabled_env_first_then_config(monkeypatch):
    """MAVERICK_DATA_USE_GOVERNANCE wins; otherwise [data_use_governance] enable."""
    from maverick.config import data_use_governance_enabled

    monkeypatch.delenv("MAVERICK_DATA_USE_GOVERNANCE", raising=False)
    assert data_use_governance_enabled({}) is False
    assert data_use_governance_enabled({"data_use_governance": {"enable": True}}) is True
    assert data_use_governance_enabled({"data_use_governance": {"enable": "yes"}}) is False
    monkeypatch.setenv("MAVERICK_DATA_USE_GOVERNANCE", "1")
    assert data_use_governance_enabled({}) is True
    monkeypatch.setenv("MAVERICK_DATA_USE_GOVERNANCE", "0")
    assert data_use_governance_enabled({"data_use_governance": {"enable": True}}) is False


# ---- boot-time import cycle through a sealed dashboard overlay --------------
#
# Production outage, 2026-09-10: the dashboard Settings page had sealed two
# provider keys into ~/.maverick/dashboard-config.toml. On the next process
# start, the FIRST thing the CLI does is resolve the data directory
# (Workspace.current().db_path -> paths.data_dir()), which resolves the tenant
# through the client binding, which loads config, which sees the sealed token
# and imports crypto_at_rest to open it -- and crypto_at_rest, at IMPORT time,
# called data_dir() for its key path, re-entering the same chain and hitting its
# own half-initialised module:
#
#   ImportError: cannot import name 'deployment_key_scope' from partially
#   initialized module 'maverick.crypto_at_rest' (most likely due to a
#   circular import)
#
# Every task exited 1 before listening; the load balancer answered 503 until
# the file was moved aside. Both the running image and the previous one were
# affected: the bug was latent since the overlay learned to seal values, and
# only a reboot with a sealed value present triggers it.


def _cold_boot_env(home: Path) -> dict:
    import os

    env = {
        k: v for k, v in os.environ.items()
        if k not in {"MAVERICK_CLIENT_ID", "MAVERICK_TENANT", "MAVERICK_CONFIG",
                     "MAVERICK_CONFIG_OVERLAY", "MAVERICK_ENCRYPT_AT_REST"}
    }
    env["MAVERICK_HOME"] = str(home)
    # A deployment-injected key, exactly as the ECS task definition provides it.
    env["MAVERICK_ENCRYPTION_KEY"] = "7f" * 32
    env["MAVERICK_ENCRYPT_AT_REST"] = "1"
    return env


def test_sealed_dashboard_overlay_survives_a_cold_boot(tmp_path):
    """A sealed value in dashboard-config.toml must not break the very first
    data_dir() call of a fresh process -- the exact boot order of `maverick
    dashboard` -- and load_config() must still open the value afterwards."""
    import subprocess
    import sys

    home = tmp_path / "home"
    home.mkdir()
    env = _cold_boot_env(home)

    # Seal the value the way the dashboard Settings page does
    # (settings_store: deployment_key_scope() + seal_to_str()), in its own
    # interpreter so this test process's import state plays no part.
    seal = (
        "import sys\n"
        "from maverick.crypto_at_rest import deployment_key_scope, seal_to_str\n"
        "with deployment_key_scope():\n"
        "    sys.stdout.write(seal_to_str('sk-test-openai'))\n"
    )
    sealed = subprocess.run(
        [sys.executable, "-c", seal], env=env, capture_output=True, text=True,
    )
    assert sealed.returncode == 0, sealed.stderr
    token = sealed.stdout.strip()
    assert token.startswith("MVKAR1:")
    (home / "dashboard-config.toml").write_text(
        f'[providers.openai]\napi_key = "{token}"\n', encoding="utf-8",
    )

    # Cold boot. The assertion mirrors production: nothing has imported the
    # encryption module when the data directory is first resolved.
    boot = (
        "import sys\n"
        "from maverick.paths import data_dir\n"
        "assert 'maverick.crypto_at_rest' not in sys.modules, 'test precondition'\n"
        "data_dir()\n"
        "from maverick.config import load_config\n"
        "sys.stdout.write(load_config()['providers']['openai']['api_key'])\n"
    )
    booted = subprocess.run(
        [sys.executable, "-c", boot], env=env, capture_output=True, text=True,
    )
    assert booted.returncode == 0, booted.stderr
    assert booted.stdout.strip() == "sk-test-openai"


def test_unseal_config_tree_does_not_reenter_itself():
    """While the overlay is being opened, any nested config load (the tenant /
    client-id resolution the encryption module triggers) gets the overlay
    with its sealed tokens left opaque instead of recursing into another
    unseal -- the tokens are never needed to resolve an identity."""
    import maverick.config as cfg_mod

    tree = {"providers": {"openai": {"api_key": "MVKAR1:AAAA"}}}  # pragma: allowlist secret
    token = cfg_mod._UNSEALING.set(True)
    try:
        assert cfg_mod._unseal_config_tree(tree) is tree
    finally:
        cfg_mod._UNSEALING.reset(token)


def _sealed_overlay(home, secret="sk-test-not-a-real-key"):  # pragma: allowlist secret
    """A config with one sealed dashboard secret, as Settings saves it."""
    from maverick import config, crypto_at_rest

    (home / "config.toml").write_text('[budget]\nmax_dollars = 5.0\n', encoding="utf-8")
    with crypto_at_rest.deployment_key_scope():
        token = crypto_at_rest.seal_to_str(secret)
    config.dashboard_overrides_path().write_text(f'[providers.anthropic]\napi_key = "{token}"\n', encoding="utf-8")


@pytest.fixture
def sealed_home(tmp_path, monkeypatch):
    from maverick import config

    monkeypatch.setenv("MAVERICK_HOME", str(tmp_path))
    monkeypatch.setenv("MAVERICK_CONFIG", str(tmp_path / "config.toml"))
    monkeypatch.delenv("MAVERICK_ENCRYPTION_KEY", raising=False)
    config.reset_config_cache()
    _sealed_overlay(tmp_path)
    yield tmp_path
    config.reset_config_cache()


def _key_resolutions(monkeypatch):
    from maverick import crypto_at_rest

    resolved = []
    real = crypto_at_rest._load_or_create_key
    monkeypatch.setattr(crypto_at_rest, "_load_or_create_key", lambda: resolved.append(1) or real())
    return resolved


def _api_key():
    from maverick import config

    return config.load_config().get("providers", {}).get("anthropic", {}).get("api_key")


def test_a_key_scope_resolves_the_at_rest_key_once_and_still_decrypts_every_read(sealed_home, monkeypatch):
    # A dashboard page reads the configuration about 350 times; each read
    # re-secured and re-read the key file before opening the saved secrets.
    from maverick import config, crypto_at_rest

    resolved = _key_resolutions(monkeypatch)
    opened = []
    real = crypto_at_rest.unseal_many_from_str
    monkeypatch.setattr(
        crypto_at_rest, "unseal_many_from_str", lambda values, **kw: opened.append(1) or real(values, **kw),
    )
    with config.at_rest_key_scope():
        for _ in range(50):
            assert _api_key() == "sk-test-not-a-real-key"  # pragma: allowlist secret
    assert (len(resolved), len(opened)) == (1, 50)
    _api_key()
    _api_key()
    assert len(resolved) == 3  # outside a scope every read resolves, as before


@pytest.mark.skipif(os.name == "nt", reason="POSIX modes")
def test_a_key_scope_resolves_the_key_again_when_the_key_files_change(sealed_home, monkeypatch):
    import secrets
    import stat

    from maverick import config, crypto_at_rest

    resolved = _key_resolutions(monkeypatch)
    key = crypto_at_rest._key_path()
    with config.at_rest_key_scope():
        _api_key()
        os.chmod(key, 0o644)
        assert _api_key() == "sk-test-not-a-real-key"  # pragma: allowlist secret
        assert stat.S_IMODE(key.stat().st_mode) == 0o600  # re-secured at once
        _api_key()
        assert len(resolved) == 2
        # A rotated key, with the saved secret sealed again under it.
        replacement = key.with_name("at_rest.key.new")
        replacement.write_text(secrets.token_bytes(32).hex(), encoding="utf-8")
        os.chmod(replacement, 0o600)
        os.replace(replacement, key)
        _sealed_overlay(sealed_home, secret="sk-test-rotated")  # pragma: allowlist secret
        assert _api_key() == "sk-test-rotated"  # pragma: allowlist secret
        assert not config.config_source_errors()


def test_a_failed_key_read_is_retried_in_the_same_scope(sealed_home, monkeypatch):
    from maverick import config, crypto_at_rest

    real = crypto_at_rest._load_or_create_key
    calls = []

    def flaky():
        calls.append(1)
        if len(calls) == 1:
            raise crypto_at_rest.EncryptionUnavailable("the key file is held by its writer")
        return real()

    monkeypatch.setattr(crypto_at_rest, "_load_or_create_key", flaky)
    with config.at_rest_key_scope():
        assert _api_key() is None
        assert config.config_source_errors()
        assert _api_key() == "sk-test-not-a-real-key"  # pragma: allowlist secret
        assert not config.config_source_errors()


def test_work_that_copied_a_closed_or_discarded_key_scope_resolves_every_read(sealed_home, monkeypatch):
    import contextvars

    from maverick import config

    resolved = _key_resolutions(monkeypatch)
    with config.at_rest_key_scope():
        _api_key()
        copied = contextvars.copy_context()
        _api_key()
    copied.run(_api_key)
    copied.run(_api_key)
    assert len(resolved) == 3
    with config.at_rest_key_scope():
        _api_key()
        config.discard_at_rest_key_scope()
        _api_key()
        _api_key()
    assert len(resolved) == 6
