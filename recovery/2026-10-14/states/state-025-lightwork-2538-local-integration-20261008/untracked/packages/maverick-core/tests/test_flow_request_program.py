from __future__ import annotations

from types import SimpleNamespace

import pytest
from maverick.flow import request_program, runner


def test_sequences_for_result_keeps_every_answer_capable_order():
    all_rows, eligible = request_program.sequences_for_result(
        ["answer", "context"],
        2,
        {"answer": "decision", "context": "note"},
        "decision",
    )

    assert len(all_rows) == 6
    assert eligible == [["answer"], ["answer", "answer"], ["context", "answer"]]


def test_sequences_for_result_rejects_a_result_no_selected_step_can_produce():
    with pytest.raises(ValueError, match="named result"):
        request_program.sequences_for_result(
            ["answer"], 1, {"answer": "decision"}, "missing"
        )


def test_run_skips_sequences_that_cannot_end_with_the_selected_result(monkeypatch):
    steps = [
        {"id": "answer", "label": "Answer", "output": "decision", "alternatives": 1},
        {"id": "context", "label": "Context", "output": "note", "alternatives": 1},
    ]
    monkeypatch.setattr(request_program, "components", lambda _source: steps)
    monkeypatch.setattr(request_program.operator_artifacts, "evaluation_cost", lambda _artifact: 0)

    def compile_flow(_source, sequence, _original, _number):
        nodes = {
            f"step_{index}": SimpleNamespace(
                output=next(row["output"] for row in steps if row["id"] == identity),
                learned_operator={"artifact": identity},
            )
            for index, identity in enumerate(sequence)
        }
        return SimpleNamespace(nodes=nodes)

    monkeypatch.setattr(request_program, "compile_flow", compile_flow)
    executions = []

    def run_flow(flow, *, data, agent_fn, action_fn, run_id, on_node):
        outputs = tuple(node.output for node in flow.nodes.values())
        executions.append((outputs, run_id))
        for node in flow.nodes.values():
            on_node(node, "completed", 0)
        result_key = "decision" if outputs[-1] == "decision" else "note"
        values = {result_key: "approved"}
        return SimpleNamespace(
            status=runner.STATUS_COMPLETED,
            data=values,
            cursor=None,
        )

    monkeypatch.setattr(request_program.runner, "run_flow", run_flow)
    cases = [
        {"inputs": {"case": "one"}, "expected": "approved", "label": "Original one"},
        {"inputs": {"case": "two"}, "expected": "approved", "label": "Original two"},
    ]
    result = request_program.run(
        object(),
        {
            "steps": ["answer", "context"],
            "length": 2,
            "result": "decision",
            "inputs": {"case": "current"},
        },
        cases,
        guard=lambda: None,
        charge=lambda _count=1: None,
        budget=SimpleNamespace(check=lambda: None),
        original="RSP-example",
    )

    assert result["complete"] is True
    assert result["possible_combinations"] == 6
    assert result["eligible_combinations"] == 3
    assert result["skipped_combinations"] == 3
    assert [row["sequence"] for row in result["candidates"]] == [
        ["answer"],
        ["answer", "answer"],
        ["context", "answer"],
    ]
    assert len(executions) == 9  # Two retained cases and the current request per eligible sequence.
    assert all(outputs[-1] == "decision" for outputs, _run_id in executions)
    assert result["passing"] == 3
    assert result["agreement"] is True
    assert result["answer"] == "approved"
