"""Real earlier results of every bounded value type can guide example setup."""

from types import SimpleNamespace
import json

import pytest
from maverick.flow import Flow, FlowNode
from maverick.flow.capture_schema import describe, suggest_contract


@pytest.mark.parametrize("value", [
    "ordinary text",
    42,
    True,
    ["first", "second"],
    {"approved": True, "count": 2},
    None,
])
@pytest.mark.parametrize("json_encoded", [False, True])
def test_contract_suggestion_uses_bounded_scalar_and_collection_results(value, json_encoded):
    flow = Flow(id="f", name="Example", start="step", nodes={
        "step": FlowNode(id="step", kind="action", tool="lookup", output="lookup_result"),
    })
    current_run = SimpleNamespace(input_data={})
    result = json.dumps(value) if json_encoded else value
    earlier_run = SimpleNamespace(data={"lookup_result": result})

    suggestion = suggest_contract(flow, current_run, allowed_tools=["lookup"],
                                  earlier_runs=[earlier_run])

    assert suggestion["unknown_results"] == []
    assert suggestion["tool_data_schemas"]["lookup"]["result"] == describe(value)


def test_contract_suggestion_keeps_result_unknown_when_no_earlier_value_exists():
    flow = Flow(id="f", name="Example", start="step", nodes={
        "step": FlowNode(id="step", kind="action", tool="lookup", output="lookup_result"),
    })

    suggestion = suggest_contract(flow, SimpleNamespace(input_data={}), allowed_tools=["lookup"],
                                  earlier_runs=[SimpleNamespace(data={})])

    assert suggestion["unknown_results"] == ["lookup"]


@pytest.mark.parametrize("result", [[1, "one"], float("inf"), {1: "value"}])
def test_contract_suggestion_does_not_guess_unsupported_result_shapes(result):
    flow = Flow(id="f", name="Example", start="step", nodes={
        "step": FlowNode(id="step", kind="action", tool="lookup", output="lookup_result"),
    })

    suggestion = suggest_contract(flow, SimpleNamespace(input_data={}), allowed_tools=["lookup"],
                                  earlier_runs=[SimpleNamespace(data={"lookup_result": result})])

    assert suggestion["unknown_results"] == ["lookup"]


def test_contract_suggestion_skips_unsupported_earlier_result_for_a_safe_later_one():
    flow = Flow(id="f", name="Example", start="step", nodes={
        "step": FlowNode(id="step", kind="action", tool="lookup", output="lookup_result"),
    })
    later = {"status": "found", "count": 3}

    suggestion = suggest_contract(flow, SimpleNamespace(input_data={}), allowed_tools=["lookup"],
                                  earlier_runs=[
                                      SimpleNamespace(data={"lookup_result": [1, "unknown"]}),
                                      SimpleNamespace(data={"lookup_result": later}),
                                  ])

    assert suggestion["unknown_results"] == []
    assert suggestion["tool_data_schemas"]["lookup"]["result"] == describe(later)
