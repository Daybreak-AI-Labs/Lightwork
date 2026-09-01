"""Agent Control Plane: one roster, verbs that land in real enforcement, and
outcomes that report only what was confirmed."""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from maverick import agent_control_plane as acp


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    from maverick import config, killswitch, revocation
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("MAVERICK_HOME", str(home))
    monkeypatch.setenv("MAVERICK_HALT_FILE", str(tmp_path / "HALT"))
    monkeypatch.setenv("MAVERICK_CONTROL_PLANE", "1")
    monkeypatch.delenv("MAVERICK_CONFIG", raising=False)
    monkeypatch.delenv("MAVERICK_TENANT", raising=False)
    config.reset_config_cache()
    revocation.reset_shared()
    # No world by default: the native roster comes only from revocations.
    monkeypatch.setattr("maverick.world_model.open_world",
                        lambda *a, **k: SimpleNamespace(list_goals=lambda **kw: []))
    yield
    try:
        killswitch.clear()
    except Exception:
        pass
    config.reset_config_cache()
    revocation.reset_shared()


def _capture(monkeypatch):
    seen: list[tuple[str, dict]] = []

    def _record(kind, **kw):
        seen.append((str(kind), kw))
        return True
    monkeypatch.setattr("maverick.audit.record", _record)
    return seen


def _world(*owners):
    goals = [SimpleNamespace(owner=o) for o in owners]
    return SimpleNamespace(list_goals=lambda **kw: goals if kw.get("status") == "running" else [])


def _enroll(agent_id="vega", **fields):
    from maverick.agent_trust import put_agent
    entry = {"id": agent_id, "direction": "both", "max_risk": "medium",
             "max_dollars": 5.0, "deny_tools": ["shell"]}
    entry.update(fields)
    return put_agent(entry)


# ---- gate ---------------------------------------------------------------------

def test_the_plane_is_off_by_default_and_refuses_verbs_but_serves_reads(monkeypatch):
    from maverick import config
    monkeypatch.delenv("MAVERICK_CONTROL_PLANE")
    config.reset_config_cache()
    assert acp.enabled() is False
    assert acp.roster() == []
    assert acp.status()["enabled"] is False
    with pytest.raises(acp.ControlPlaneDisabled):
        acp.suspend("user:local", reason="x")
    with pytest.raises(acp.ControlPlaneDisabled):
        acp.halt_all("x")
    from maverick.revocation import is_revoked
    assert not is_revoked("user:local")


def test_the_config_knob_arms_the_plane(monkeypatch, tmp_path):
    from maverick import config
    monkeypatch.delenv("MAVERICK_CONTROL_PLANE")
    cfg = tmp_path / "config.toml"
    cfg.write_text("[control_plane]\nenable = true\n")
    monkeypatch.setenv("MAVERICK_CONFIG", str(cfg))
    config.reset_config_cache()
    assert acp.enabled() is True


# ---- roster -------------------------------------------------------------------

def test_roster_joins_native_runs_and_external_trust_entries(monkeypatch):
    monkeypatch.setattr("maverick.world_model.open_world",
                        lambda *a, **k: _world("user:alice", "user:alice", ""))
    _enroll("vega")
    rows = {r.principal: r for r in acp.roster()}
    assert rows["user:alice"].kind == acp.KIND_NATIVE
    assert rows["user:alice"].running_goals == 2
    assert rows["user:alice"].state == "running"
    # An unowned goal runs under the root grant's principal.
    assert rows["user:local"].running_goals == 1
    assert rows["agent:vega"].kind == acp.KIND_EXTERNAL
    assert rows["agent:vega"].state == "active"
    assert rows["agent:vega"].max_risk == "medium"
    assert rows["agent:vega"].deny_tools == ("shell",)


def test_an_unreadable_world_does_not_hide_the_external_roster(monkeypatch):
    def _boom(*a, **k):
        raise OSError("db locked")
    monkeypatch.setattr("maverick.world_model.open_world", _boom)
    _enroll("vega")
    assert [r.principal for r in acp.roster()] == ["agent:vega"]


def test_a_revoked_external_principal_without_a_trust_entry_stays_on_the_roster():
    """Found by driving the console: containing `agent:ghost` (no registry
    entry) revoked it, then the roster dropped it, so nothing could release
    it. The revocation is live either way; the roster must say so."""
    from maverick.revocation import is_revoked
    assert acp.contain("agent:ghost", reason="honeytoken").ok
    assert is_revoked("agent:ghost")
    row = acp.lookup("agent:ghost")
    assert row is not None and row.kind == acp.KIND_EXTERNAL
    assert row.state == "suspended" and row.revoked
    assert row.platform == "not in trust registry"
    assert acp.release("agent:ghost").ok
    assert not is_revoked("agent:ghost") and acp.lookup("agent:ghost") is None


# ---- suspend / resume ---------------------------------------------------------

def test_suspend_lands_in_the_revocation_registry_and_resume_lifts_it(monkeypatch):
    from maverick.revocation import is_revoked
    seen = _capture(monkeypatch)
    result = acp.suspend("user:local", reason="runaway spend", actor="ops@x")
    assert result.ok and result.verb == "suspend"
    assert is_revoked("user:local")
    row = acp.lookup("user:local")
    assert row is not None and row.state == "suspended" and row.revoked
    assert "runaway spend" in row.revoked_reason
    assert acp.status()["native"]["suspended"] == 1

    back = acp.resume("user:local", actor="ops@x")
    assert back.ok
    assert not is_revoked("user:local")
    assert acp.lookup("user:local") is None  # nothing running, nothing revoked
    kinds = [(k, kw["verb"], kw["ok"]) for k, kw in seen if k == "control_plane_action"]
    assert kinds == [("control_plane_action", "suspend", True),
                     ("control_plane_action", "resume", True)]
    assert all(kw["actor"] == "ops@x" for k, kw in seen if k == "control_plane_action")


def test_suspending_an_external_agent_flips_its_trust_entry_too():
    from maverick.agent_trust import load_registry
    from maverick.revocation import is_revoked
    _enroll("vega")
    assert acp.suspend("agent:vega", reason="key leaked").ok
    assert is_revoked("agent:vega")
    assert load_registry()["vega"].revoked is True
    assert load_registry()["vega"].is_active() == (False, "revoked")
    assert acp.lookup("agent:vega").state == "suspended"

    assert acp.resume("agent:vega").ok
    assert not is_revoked("agent:vega")
    assert load_registry()["vega"].revoked is False


def test_a_config_only_trust_entry_gets_a_managed_overlay(monkeypatch, tmp_path):
    """The TOML entry cannot be edited; the managed overlay overrides it by id."""
    from maverick import config
    from maverick.agent_trust import load_registry
    cfg = tmp_path / "config.toml"
    cfg.write_text('[control_plane]\nenable = true\n[agent_trust]\nagents = [\n'
                   '  { id = "toml-only", direction = "both", max_risk = "low" },\n]\n')
    monkeypatch.setenv("MAVERICK_CONFIG", str(cfg))
    config.reset_config_cache()
    assert load_registry()["toml-only"].revoked is False
    result = acp.suspend("agent:toml-only", reason="x")
    assert result.ok
    assert any("managed overlay" in n for n in result.notes)
    assert load_registry()["toml-only"].revoked is True
    assert load_registry()["toml-only"].max_risk == "low"  # nothing else changed


def test_a_revocation_that_does_not_persist_is_reported_as_failure(monkeypatch):
    import maverick.revocation as rev

    class _Fake:
        def revoke(self, principal, *, reason="", now=None):
            return None

        def is_revoked(self, principal):
            return False

    monkeypatch.setattr(rev, "shared", lambda: _Fake())
    result = acp.suspend("user:local", reason="x")
    assert not result.ok
    assert "did not persist" in result.detail


# ---- contain / release --------------------------------------------------------

def test_contain_uses_the_security_plane_and_release_undoes_it(monkeypatch):
    from maverick.revocation import is_revoked
    seen = _capture(monkeypatch)
    _enroll("vega")
    result = acp.contain("agent:vega", reason="honeytoken tripped", actor="soc")
    assert result.ok
    assert is_revoked("agent:vega")
    assert any("not sealed" in n for n in result.notes)  # out-of-band: no live swarm
    assert acp.lookup("agent:vega").revoked
    # The security plane's own containment row is written alongside ours.
    assert any(k == "agent_edr_containment" for k, _ in seen)
    assert [kw["verb"] for k, kw in seen if k == "control_plane_action"] == ["contain"]

    back = acp.release("agent:vega", actor="soc")
    assert back.ok
    assert not is_revoked("agent:vega")
    assert any("not enrolled in the gateway" in n for n in back.notes)


def test_contain_revokes_the_whole_subtree():
    from maverick.revocation import is_revoked
    result = acp.contain("user:alice", reason="x",
                         edges={"user:alice": ["agent:alice-child"]})
    assert result.ok
    assert is_revoked("user:alice") and is_revoked("agent:alice-child")


# ---- attenuate ----------------------------------------------------------------

def test_attenuate_narrows_and_never_widens():
    from maverick.agent_trust import load_registry
    _enroll("vega")  # medium risk, $5, deny shell
    ok = acp.attenuate("agent:vega", deny_tools=["email"], max_risk="low", max_dollars=2)
    assert ok.ok
    agent = load_registry()["vega"]
    assert agent.deny_tools == frozenset({"shell", "email"})
    assert agent.max_risk == "low" and agent.max_dollars == 2.0
    assert agent.direction == "both"  # untouched fields survive
    for widen in ({"max_risk": "high"}, {"max_dollars": 3}, {"max_dollars": 0}):
        with pytest.raises(ValueError):
            acp.attenuate("agent:vega", **widen)
    assert load_registry()["vega"].max_risk == "low"  # refused whole, nothing moved


def test_attenuate_is_external_only_and_needs_a_trust_entry():
    with pytest.raises(ValueError, match="external"):
        acp.attenuate("user:local", max_risk="low")
    with pytest.raises(LookupError):
        acp.attenuate("agent:ghost", max_risk="low")


# ---- halt ---------------------------------------------------------------------

def test_halt_and_clear_drive_the_fleet_killswitch(monkeypatch):
    from maverick import killswitch
    seen = _capture(monkeypatch)
    result = acp.halt_all("prompt-injection wave", actor="ops")
    assert result.ok and killswitch.is_active()
    assert acp.status()["halted"] is True
    with pytest.raises(killswitch.Halted):
        killswitch.check()
    cleared = acp.clear_halt(actor="ops")
    assert cleared.ok and not killswitch.is_active()
    assert [kw["verb"] for k, kw in seen if k == "control_plane_action"] == ["halt", "clear-halt"]
    assert any(k == "halt" and "control_plane:ops" in str(kw.get("source")) for k, kw in seen)


# ---- validation ---------------------------------------------------------------

@pytest.mark.parametrize("bad", ["", " user:x", "user:x ", "..", "a/b", "x" * 129, None, 3])
def test_invalid_principals_are_refused_before_anything_lands(bad):
    with pytest.raises(ValueError):
        acp.suspend(bad, reason="x")
    assert not acp.valid_principal(bad)


def test_a_reason_is_required_and_bounded():
    with pytest.raises(ValueError):
        acp.suspend("user:local", reason="  ")
    with pytest.raises(ValueError):
        acp.suspend("user:local", reason="r" * 501)
    with pytest.raises(ValueError):
        acp.halt_all("")


def test_ledger_reads_the_principals_timeline(monkeypatch):
    from maverick import agent_edr
    monkeypatch.setattr(agent_edr, "timeline", lambda subject, since=None: [
        agent_edr.Detection(ts=1.0, kind="k", severity="low", subject=subject,
                            detail="d", source_event="e"),
        agent_edr.Detection(ts=2.0, kind="k", severity="low", subject=subject,
                            detail="d2", source_event="e")])
    rows = acp.ledger("user:local", limit=1)
    assert rows == [{"ts": 2.0, "kind": "k", "severity": "low", "subject": "user:local",
                     "detail": "d2", "source_event": "e", "day": "", "evidence": {}}]


# ---- CLI ----------------------------------------------------------------------

def test_the_cli_group_drives_the_plane(monkeypatch):
    from click.testing import CliRunner
    from maverick.cli import main
    from maverick.revocation import is_revoked
    _capture(monkeypatch)
    runner = CliRunner()
    out = runner.invoke(main, ["control-plane", "status", "--json"])
    assert out.exit_code == 0, out.output
    assert '"enabled": true' in out.output
    out = runner.invoke(main, ["control-plane", "suspend", "user:local", "--reason", "cli test"])
    assert out.exit_code == 0, out.output
    assert is_revoked("user:local")
    out = runner.invoke(main, ["control-plane", "list"])
    assert out.exit_code == 0 and "user:local" in out.output and "suspended" in out.output
    out = runner.invoke(main, ["control-plane", "resume", "user:local"])
    assert out.exit_code == 0 and not is_revoked("user:local")
    out = runner.invoke(main, ["control-plane", "attenuate", "user:local", "--max-risk", "low"])
    assert out.exit_code != 0 and "external" in out.output
    monkeypatch.delenv("MAVERICK_CONTROL_PLANE")
    from maverick import config
    config.reset_config_cache()
    out = runner.invoke(main, ["control-plane", "halt", "--reason", "x"])
    assert out.exit_code != 0 and "control plane is off" in out.output
