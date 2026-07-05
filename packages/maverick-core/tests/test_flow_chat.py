"""Conversational flow copilot: patch-emitting chat turns over a flow."""
from __future__ import annotations

import json

import pytest
from maverick.flow.chat import FLOW_CHAT_SYSTEM, chat_flow
from maverick.flow.ir import Flow


class _Resp:
    def __init__(self, text: str):
        self.text = text


def _fake(payload) -> tuple[list, object]:
    """A fake complete() that records its call and returns ``payload`` as JSON."""
    calls = []

    def complete(*, system, messages, budget, max_tokens):
        calls.append({"system": system, "messages": messages})
        return _Resp(payload if isinstance(payload, str) else json.dumps(payload))
    return calls, complete


def _flow() -> Flow:
    return Flow.from_dict({
        "id": "f1", "name": "Triage", "start": "n0",
        "nodes": [
            {"id": "n0", "kind": "agent", "brief": "triage {{issue}}", "next": "n1"},
            {"id": "n1", "kind": "action", "tool": "slack_post", "params": {"text": "hi"}},
        ],
    })


def test_question_turn_returns_reply_without_changes():
    calls, complete = _fake({"reply": "It triages then posts.", "patches": []})
    res = chat_flow("what does this flow do?", flow=_flow(), complete=complete)
    assert res.reply == "It triages then posts."
    assert res.flow is None and res.patches == []
    # the current graph was in the model's context
    assert "triage {{issue}}" in calls[0]["system"]


def test_edit_turn_applies_patches_and_returns_new_flow():
    _, complete = _fake({"reply": "Added an approval before the post.", "patches": [
        {"op": "add_node", "after": "n0",
         "node": {"id": "ap", "kind": "approval", "prompt": "post it?"}},
    ]})
    res = chat_flow("ask me before posting", flow=_flow(), complete=complete)
    assert res.flow is not None
    assert res.flow.nodes["n0"].next == "ap"
    assert res.flow.nodes["ap"].next == "n1"
    assert res.applied == ["add approval node ap after n0"]


def test_bad_patch_degrades_to_reply_with_note():
    _, complete = _fake({"reply": "ok", "patches": [
        {"op": "remove_node", "id": "nope"},
    ]})
    original = _flow()
    res = chat_flow("remove that node", flow=original, complete=complete)
    assert res.flow is None
    assert any("not applied" in n for n in res.notes)
    assert set(original.nodes) == {"n0", "n1"}     # untouched


def test_empty_canvas_accepts_full_flow_draft():
    _, complete = _fake({"reply": "Drafted it.", "flow": {
        "id": "", "name": "Digest", "start": "a",
        "nodes": [{"id": "a", "kind": "agent", "brief": "summarize the feed"}],
    }})
    res = chat_flow("build me an rss digest", flow=Flow(id="fx", name="", start=""),
                    complete=complete)
    assert res.flow is not None and res.flow.id == "fx"
    assert res.flow.nodes["a"].kind == "agent"


def test_full_flow_ignored_when_canvas_not_empty():
    _, complete = _fake({"reply": "here", "flow": {
        "id": "", "name": "X", "start": "a",
        "nodes": [{"id": "a", "kind": "agent", "brief": "b"}]}})
    res = chat_flow("rewrite everything", flow=_flow(), complete=complete)
    assert res.flow is None       # a populated canvas is only edited via patches


def test_run_trace_and_history_reach_the_model():
    calls, complete = _fake({"reply": "n1 failed: connector down.", "patches": []})
    run = {"status": "failed", "cursor": "n1", "error": "node 'n1': connector down",
           "nodes": {"n0": {"status": "done", "outcome": 1.0}}, "data": {"x": 1}}
    res = chat_flow("why did the last run fail?", flow=_flow(), run=run,
                    history=[{"role": "user", "content": "hi"},
                             {"role": "assistant", "content": "hello"}],
                    complete=complete)
    assert "connector down" in calls[0]["system"]
    assert calls[0]["messages"][0]["content"] == "hi"
    assert calls[0]["messages"][-1]["content"] == "why did the last run fail?"
    assert res.reply.startswith("n1 failed")


def test_non_json_output_degrades():
    _, complete = _fake("Sorry, I can't help with that.")
    res = chat_flow("do something", flow=_flow(), complete=complete)
    assert res.flow is None and res.notes


def test_empty_message_rejected():
    _, complete = _fake({"reply": "x"})
    with pytest.raises(ValueError):
        chat_flow("   ", flow=_flow(), complete=complete)


def test_system_prompt_documents_ops_and_schema():
    for needle in ("add_node", "remove_node", "set_field", "rewire",
                   '"kind":"agent"', '"kind":"branch"'):
        assert needle in FLOW_CHAT_SYSTEM
