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
