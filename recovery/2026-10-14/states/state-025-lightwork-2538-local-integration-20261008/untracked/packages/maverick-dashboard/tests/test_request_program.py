from __future__ import annotations

from contextlib import nullcontext

from maverick_dashboard import request_program


def test_review_lists_only_combinations_that_can_produce_the_selected_result(monkeypatch):
    value = {
        "arguments": {
            "description": "Prepare this decision",
            "steps": ["answer", "context"],
            "length": 2,
            "result": "decision",
            "inputs": {"case": "current"},
        },
        "name": "Current workflow",
        "root": "flow-digest",
        "publication": {"subflow_digests": {}},
        "draft": [],
        "policy": {},
        "cases": [
            {
                "label": "Original case 1",
                "inputs": {"case": "one"},
                "expected": "approved",
            }
        ],
        "fields": [],
        "steps": [
            {"id": "answer", "label": "Answer", "output": "decision"},
            {"id": "context", "label": "Context", "output": "note"},
        ],
    }
    monkeypatch.setattr(request_program, "authority", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(request_program, "authority_effect_fence", nullcontext)
    monkeypatch.setattr(request_program.store, "_definitions_lock", nullcontext)
    monkeypatch.setattr(request_program, "material", lambda *_args: value.copy())
    monkeypatch.setattr(request_program.store, "load_flow_snapshot", lambda _digest: object())
    monkeypatch.setattr(request_program.source, "_source_review", lambda *_args: [])

    _prepared, changes = request_program.prepare(object(), {}, "current-context")

    combinations = next(row["after"] for row in changes if row["label"] == "Possible combinations")
    assert combinations.startswith("3 of 6 possible combinations")
    assert "The other 3 end in a different result" in combinations
    assert "1. Answer\n2. Answer → Answer\n3. Context → Answer" in combinations
    assert "Answer → Context" not in combinations
    assert "Context → Context" not in combinations
