"""Natural-language -> flow draft: JSON parsing/sanitising, validation, and the
always-runnable single-agent fallback. The LLM ``complete`` is injected."""
from __future__ import annotations

from maverick.flow.draft import draft_flow


class _Resp:
    def __init__(self, text):
        self.text = text


def _complete(text):
    def c(system, messages, budget=None, max_tokens=0, **k):
        return _Resp(text)
    return c


def test_drafts_a_valid_graph():
    js = ('{"id":"x","name":"Triage","start":"n0","nodes":['
          '{"id":"n0","kind":"agent","brief":"triage {{issue}}","next":"n1"},'
          '{"id":"n1","kind":"approval","prompt":"post it?"}]}')
    flow, notes = draft_flow("triage an issue then ask me", complete=_complete(js), flow_id="mine")
    assert flow.id == "mine"                       # flow_id override wins
    assert [n.kind for n in flow.nodes.values()] == ["agent", "approval"]
    assert flow.validate() == [] and notes == []


def test_sanitizer_prunes_dangling_switch_cases_and_on_error():
    # A drafted switch case.to / on_error pointing at a hallucinated id must be
    # pruned so the graph validates, instead of being discarded wholesale for a
    # single-agent fallback.
    js = ('{"id":"x","name":"Route","start":"s","nodes":['
          '{"id":"s","kind":"switch","condition":"tier","cases":['
          '{"value":"gold","to":"g"},{"value":"ghost","to":"nowhere"}],"next":"g"},'
          '{"id":"g","kind":"action","tool":"slack_bot","params":{},"on_error":"missing"}]}')
    flow, notes = draft_flow("route by tier", complete=_complete(js), flow_id="r")
    assert flow.validate() == []                    # not discarded to a fallback
    sw = flow.nodes["s"]
    assert [c["value"] for c in sw.cases] == ["gold"]   # the dangling case dropped
    assert flow.nodes["g"].on_error is None             # dangling on_error nulled
    assert not any("single agent" in n for n in notes)


def test_tolerates_code_fence_and_prose():
    js = 'Sure!\n```json\n{"name":"F","start":"a","nodes":[{"id":"a","kind":"agent","brief":"do"}]}\n```'
    flow, notes = draft_flow("do a thing", complete=_complete(js))
    assert flow.validate() == [] and flow.nodes["a"].brief == "do"


def test_dangling_routing_is_dropped():
    js = ('{"start":"n0","nodes":[{"id":"n0","kind":"agent","brief":"x","next":"ghost"}]}')
    flow, _ = draft_flow("x", complete=_complete(js))
    assert flow.nodes["n0"].next is None           # points nowhere -> dropped, still valid
    assert flow.validate() == []


def test_tool_docs_ground_the_prompt_with_descriptions():
    seen = {}

    def capture(system, messages, budget=None, max_tokens=0, **k):
        seen["system"] = system
        return _Resp('{"start":"a","nodes":[{"id":"a","kind":"agent","brief":"x"}]}')
    draft_flow("do it", tool_docs={"slack_bot": "Post a message to a Slack channel"},
               complete=capture)
    assert "slack_bot: Post a message to a Slack channel" in seen["system"]


def test_missing_start_defaults_to_first_node():
    js = '{"nodes":[{"id":"a","kind":"agent","brief":"x"},{"id":"b","kind":"agent","brief":"y"}]}'
    flow, _ = draft_flow("x", complete=_complete(js))
    assert flow.start == "a" and flow.validate() == []


def test_non_json_falls_back_to_single_agent():
    flow, notes = draft_flow("summarize the news", complete=_complete("I cannot help with JSON"),
                             flow_id="fb")
    assert flow.is_single_agent() and flow.id == "fb"
    assert flow.nodes["n0"].brief == "summarize the news"
    assert notes and "single agent" in notes[0]


def test_invalid_graph_falls_back():
    # an action node with no tool is structurally invalid -> fallback
    js = '{"start":"n0","nodes":[{"id":"n0","kind":"action"}]}'
    flow, notes = draft_flow("call a tool", complete=_complete(js))
    assert flow.is_single_agent() and notes


def test_empty_description_rejected():
    import pytest
    with pytest.raises(ValueError):
        draft_flow("   ", complete=_complete("{}"))
