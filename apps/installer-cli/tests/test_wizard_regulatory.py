"""The wizard step for compiled regulatory obligations (kernel rule 6)."""
from __future__ import annotations

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover -- Python 3.10
    import tomli as tomllib


def _write(cfg_dir, monkeypatch, advanced):
    monkeypatch.setattr("maverick_installer.wizard.CONFIG_DIR", cfg_dir)
    monkeypatch.setattr("maverick_installer.wizard.ENV_FILE", cfg_dir / ".env")
    monkeypatch.setattr("maverick_installer.wizard.CONFIG_FILE", cfg_dir / "config.toml")
    from maverick_installer.wizard import write_config
    write_config(
        providers=["anthropic"], role_models={},
        channels={}, safety={"profile": "balanced"},
        budget={"max_dollars": 5.0, "max_wall_seconds": 600, "max_tool_calls": 30},
        sandbox={"backend": "local", "workdir": "~/ws"},
        keys={"ANTHROPIC_API_KEY": "x"},
        capabilities={}, advanced=advanced,
    )
    return (cfg_dir / "config.toml").read_text()


def test_profiles_are_written_to_the_compliance_table(tmp_path, monkeypatch):
    text = _write(tmp_path, monkeypatch, {"compliance_profiles": ["gdpr", "eu_ai_act"]})
    cfg = tomllib.loads(text)
    assert cfg["compliance"]["profiles"] == ["gdpr", "eu_ai_act"]
    assert text.count("[compliance]") == 1


def test_profiles_and_disclosure_share_one_compliance_table(tmp_path, monkeypatch):
    text = _write(tmp_path, monkeypatch, {
        "compliance_profiles": ["hipaa"],
        "compliance_disclosure_text": "AI-assisted",
    })
    cfg = tomllib.loads(text)
    assert cfg["compliance"] == {"profiles": ["hipaa"], "disclosure_text": "AI-assisted"}
    assert text.count("[compliance]") == 1


def test_no_profiles_means_no_compliance_table(tmp_path, monkeypatch):
    text = _write(tmp_path, monkeypatch, {})
    assert "[compliance]" not in text
