"""``maverick immune`` renders the kernel surface and fails loudly on bad evidence."""
from __future__ import annotations

import json

from click.testing import CliRunner
from maverick import config
from maverick.cli import main


def _run(*args):
    return CliRunner().invoke(main, ["immune", *args])


def test_network_on_a_graph_file(tmp_path):
    g = tmp_path / "g.json"
    g.write_text(json.dumps({"agents": {"r": ["knowledge_search"], "s": ["web_search"]},
                             "flows": [["r", "s"]]}))
    out = _run("network", "--graph", str(g))
    assert out.exit_code == 0, out.output
    assert "2 agents, 1 findings" in out.output and "r -> s" in out.output
    rows = json.loads(_run("network", "--graph", str(g), "--json").output)
    assert rows[0]["kind"] == "exfiltration"


def test_title_check_seal_verify_roundtrip(tmp_path):
    d = {"problem": {"statement": "x", "signed_off_by": "h"}, "inputs": [], "priority": [],
         "gates": [{"id": "g", "human": "h", "decision": "framed", "elements": ["e"], "at": 1}],
         "claims": [{"id": "c", "elements": ["e"]}],
         "verification": [{"checker": "lean", "passed": True}], "coverage": {"examined": 1, "total": 1}}
    src, sealed = tmp_path / "d.json", tmp_path / "s.json"
    src.write_text(json.dumps(d))
    assert _run("title", "check", str(src)).exit_code == 0
    assert _run("title", "seal", str(src), "-o", str(sealed)).exit_code == 0
    assert "verifies" in _run("title", "verify", str(sealed)).output
    doc = json.loads(sealed.read_text())
    doc["dossier"]["claims"][0]["elements"].append("unconceived")
    sealed.write_text(json.dumps(doc))
    assert _run("title", "verify", str(sealed)).exit_code != 0
    d["claims"][0]["elements"].append("unconceived")
    src.write_text(json.dumps(d))
    bad = _run("title", "check", str(src))
    assert bad.exit_code == 1 and "unconceived" in bad.output


def test_rules_reports_contradictions_and_replays(tmp_path):
    rules, cand, events = (tmp_path / n for n in ("r.json", "c.json", "e.json"))
    rules.write_text(json.dumps({"rules": [
        {"id": "a", "action": "pay", "modality": "forbid", "when": {"new": True}},
        {"id": "b", "action": "pay", "modality": "require", "when": {}}]}))
    cand.write_text(json.dumps({"rules": [{"id": "c", "action": "pay", "modality": "forbid", "when": {}}]}))
    events.write_text(json.dumps({"events": [{"action": "pay", "incident": True}]}))
    out = _run("rules", str(rules), "--candidate", str(cand), "--events", str(events))
    assert out.exit_code == 0, out.output
    assert "a forbids what b requires" in out.output and '"promote": true' in out.output


def test_canary_refused_until_enabled(monkeypatch, tmp_path):
    from maverick import world_model
    monkeypatch.setattr(world_model, "DEFAULT_DB", tmp_path / "w.db")
    monkeypatch.setattr(config, "get_immune", lambda: {"canaries": False})
    out = _run("canary", "--action", "a", "--detail", "d", "--class", "c", "--flaw", "f")
    assert out.exit_code != 0 and "canaries are off" in out.output


def test_twin_replay_list_and_awareness(tmp_path, monkeypatch):
    import asyncio

    import maverick.cli as cli_mod
    import maverick.cli._immune_groups as groups
    import maverick.runner as runner
    from maverick.immune import twin
    from maverick.world_model import WorldModel

    monkeypatch.setenv("MAVERICK_HOME", str(tmp_path / ".maverick"))
    monkeypatch.setenv("MAVERICK_ENCRYPT_AT_REST", "0")
    monkeypatch.setattr(config, "get_immune", lambda: {"canaries": False, "twin_record": True})
    db = tmp_path / "w.db"
    world = WorldModel(db)
    gid = world.create_goal("close the books", "")

    async def live(tool, args):
        return "rows=12"
    asyncio.run(_d(twin, gid, "sql_query", {"q": "gl"}, live))
    world.close()

    monkeypatch.setattr(cli_mod, "_require_llm_key", lambda *a, **k: "k")
    monkeypatch.setattr(groups, "open_world", lambda *a, **k: WorldModel(db))
    seen = []

    def fake_run(goal_id, max_dollars=None):
        async def agent():  # stands in for the swarm's tool call
            async def never(tool, args):
                raise AssertionError("a twin must not execute tools")
            return await _d(twin, goal_id, "sql_query", {"q": "gl"}, never)
        seen.append(asyncio.run(agent()))
        return "done"
    monkeypatch.setattr(runner, "run_goal_in_thread", fake_run)

    out = _run("twin", "replay", str(gid), "--max-dollars", "0.5")
    assert out.exit_code == 0, out.output
    assert seen == ["rows=12"] and json.loads(out.output)["served"] == 1
    assert "served 1/1, unmatched 0" in _run("twin", "list").output
    assert _run("twin", "replay", "999").exit_code != 0

    s = tmp_path / "s.json"
    s.write_text(json.dumps({"watched": [False] * 40, "unwatched": [True] * 20 + [False] * 20}))
    gap = json.loads(_run("awareness", str(s)).output)
    assert gap["significant"] and gap["gap"] == 0.5


def _d(twin, goal_id, tool, args, run):
    return twin.dispatch(goal_id, tool, args, lambda: run(tool, args))
