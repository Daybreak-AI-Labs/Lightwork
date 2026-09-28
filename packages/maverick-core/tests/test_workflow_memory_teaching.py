"""Pure supplied-observation qualification; no native/authenticated teaching claim."""
from __future__ import annotations

import copy
import itertools
import json

import pytest
from maverick.flow import workflow_memory as memory
from maverick.flow import workflow_memory_teaching as teaching


def obj(**properties):
    return {"type": "object", "properties": properties, "required": sorted(properties),
            "additionalProperties": False}


INT = {"type": "integer", "minimum": 0, "maximum": 100}
STRING = {"type": "string", "maxLength": 32}
DOMAIN = {"inputs": obj(account=STRING), "tools": {
    "receipt": {"arguments": obj(account=STRING), "result": obj(quantity={"type": "integer", "minimum": 0, "maximum": 4})},
}}


def inputs(account="account-a"):
    return {"account": account}


SEEDS = [(5, [2, 3]), (5, [1, 3, 1]), (5, [3, 1, 1]), (5, [1, 1, 3]), (5, [4, 2]),
         (4, [1, 3]), (6, [2, 1, 4]), (6, [1, 2, 2, 1]), (7, [3, 3, 1]), (8, [3, 1, 4])]


def steps(values, account="account-a"):
    return [{"tool": "receipt", "arguments": {"account": account}, "result": {"quantity": value}}
            for value in values]


@pytest.fixture(scope="module")
def artifact():
    # Complete traces continue after progress 0 and stop after progress 4.
    # Intermediate progress 1/2/3 is intentionally unlabelled. Arguments are
    # independently fixed by account, not supplied as a learned mapping.
    examples = [{"id": account, "inputs": inputs(account), "steps": steps([0, 4], account)}
                for account in ["account-a", "account-b"]]
    return memory.synthesize(examples, allowed_tools=["receipt"], domain=DOMAIN,
        max_operations=2, operators=["if", "le", "lt"], constants=[0, 1, 2, 3, 4])


def choose(artifact, history, *, constraints=None, option="action:receipt", values=None):
    values = inputs() if values is None else values
    question = teaching.question(artifact, values, history, constraints=constraints)
    return teaching.select(artifact, values, history, question, option, constraints=constraints)


@pytest.mark.parametrize("account,values", [("account-a", []), ("account-a", [0]), ("account-a", [0, 4]),
    ("account-a", [0, 1]), ("account-a", [0, 1, 2]), ("account-c", [0] * 8), ("account-c", [0, 2])])
def test_empty_constraints_preserve_baseline_decisions(artifact, account, values):
    original = memory.resolve_next(artifact, inputs(account), steps(values, account))
    assert teaching.resolve_next(artifact, inputs(account), steps(values, account)) == original
    assert teaching.resolve_next(artifact, inputs(account), steps(values, account), constraints=[]) == original


def test_two_successive_choices_are_fixed_across_same_state_and_fresh_reconstruction(artifact):
    original = json.dumps(artifact, sort_keys=True)
    question = teaching.question(artifact, inputs(), steps([0, 1]))
    assert {option["id"] for option in question["options"]} == {"complete", "action:receipt"}
    assert next(option for option in question["options"] if option["kind"] == "action")["arguments"] == {"account": "account-a"}
    first = choose(artifact, steps([0, 1]))
    second = choose(artifact, steps([0, 1, 2]), constraints=[first])
    assert first["question"]["history_sha256"] != second["question"]["history_sha256"]
    assert second["previous_constraint_sha256"] == first["constraint_sha256"]
    persisted = json.loads(json.dumps([first, second]))
    assert memory.resolve_next(artifact, inputs(), steps([0, 1, 2])).reason == "invalid_history"
    decision = teaching.resolve_next(copy.deepcopy(artifact), inputs(), steps([0, 1, 2]), constraints=persisted)
    assert decision.applied and decision.kind == "action" and decision.arguments == {"account": "account-a"}, decision
    third = choose(artifact, steps([0, 1, 2, 3]), constraints=persisted, option="complete")
    final = teaching.resolve_next(artifact, inputs(), steps([0, 1, 2, 3]), constraints=[*persisted, third])
    assert final.applied and final.kind == "complete"
    assert json.dumps(artifact, sort_keys=True) == original
    assert persisted == [first, second]
    checked = teaching.validate_constraints(artifact, inputs(), steps([0, 1, 2]), constraints=persisted)
    assert checked["constraint_chain_sha256"] == second["constraint_sha256"]
    assert checked["conditional_space_sha256"] == second["conditional_space_sha256"]
    assert checked["constraint_count"] == 2


def test_end_selection_is_terminal_not_a_fabricated_tool_result(artifact):
    constraint = choose(artifact, steps([0, 1]), option="complete")
    selected = teaching.resolve_next(artifact, inputs(), steps([0, 1]), constraints=[constraint])
    assert selected.applied and selected.kind == "complete" and selected.arguments is None
    extended = teaching.resolve_next(artifact, inputs(), steps([0, 1, 2]), constraints=[constraint])
    assert not extended.applied and extended.reason == "invalid_history"
    assert artifact["episode_count"] == 2


def test_agreement_does_not_produce_an_unnecessary_question(artifact):
    with pytest.raises(teaching.TeachingUnavailable, match="not_ambiguous"):
        teaching.question(artifact, inputs(), steps([0]))
    constraint = choose(artifact, steps([0, 1]))
    with pytest.raises(teaching.TeachingUnavailable, match="not_ambiguous"):
        teaching.question(artifact, inputs(), steps([0, 1]), constraints=[constraint])


@pytest.mark.parametrize("mutation", ["chain", "space", "artifact", "input", "history", "index",
                                       "question", "option", "authority", "bool_index"])
def test_constraint_rehash_does_not_hide_tampering(artifact, mutation):
    constraint = choose(artifact, steps([0, 1]))
    changed = copy.deepcopy(constraint)
    if mutation in {"chain", "space", "artifact", "input", "history"}:
        key = {"chain": "previous_constraint_sha256", "space": "conditional_space_sha256",
               "artifact": "artifact_sha256", "input": "input_sha256", "history": "history_sha256"}[mutation]
        changed[key] = "0" * 64
    elif mutation == "index":
        changed["step_index"] = 1
    elif mutation == "bool_index":
        changed["step_index"] = True
    elif mutation == "question":
        changed["question"]["options"][0]["arguments"] = {"target": 99}
        changed["question"]["question_sha256"] = memory._digest({key: value for key, value in changed["question"].items()
                                                                 if key != "question_sha256"})
    elif mutation == "option":
        changed["option_id"] = "action:invented"
    else:
        changed["actor"] = "claimed-human"
    changed["constraint_sha256"] = memory._digest({key: value for key, value in changed.items()
                                                  if key != "constraint_sha256"})
    assert not teaching.resolve_next(artifact, inputs(), steps([0, 1]), constraints=[changed]).applied


@pytest.mark.parametrize("case", ["input", "observation", "missing", "duplicate", "prefix", "artifact"])
def test_stale_or_cross_observation_constraints_are_not_authority(artifact, case):
    constraint = choose(artifact, steps([0, 1]))
    values, history, constraints, parent = inputs(), steps([0, 1, 2]), [constraint], artifact
    if case == "input":
        values, history = inputs("account-other"), steps([0, 1, 2], "account-other")
    elif case == "observation":
        history[0]["result"]["quantity"] = 1
    elif case == "missing":
        constraints = []
    elif case == "duplicate":
        constraints = [constraint, copy.deepcopy(constraint)]
    elif case == "prefix":
        history[0]["arguments"]["target"] = True
    else:
        parent = memory.refine(artifact, [{"id": "later", "inputs": inputs(), "steps": steps([0, 1, 2, 4])}])
    assert not teaching.resolve_next(parent, values, history, constraints=constraints).applied


def test_questions_and_constraints_are_snapshots_not_cache_aliases(artifact):
    history = steps([0, 1])
    proposed = teaching.question(artifact, inputs(), history)
    constraint = teaching.select(artifact, inputs(), history, proposed, "action:receipt")
    proposed["options"].clear()
    history[0]["result"]["quantity"] = 99
    assert teaching.resolve_next(artifact, inputs(), steps([0, 1]), constraints=[constraint]).applied
    decision = teaching.resolve_next(artifact, inputs(), steps([0, 1]), constraints=[constraint])
    decision.arguments["account"] = "changed"
    assert teaching.resolve_next(artifact, inputs(), steps([0, 1]), constraints=[constraint]).arguments == {"account": "account-a"}


@pytest.mark.parametrize("cap", ["MAX_PRODUCTIONS", "MAX_TRAINING_EVALUATIONS", "MAX_PACKED_STATES"])
def test_whole_operation_search_limit_cannot_select_only_cheap_models(artifact, monkeypatch, cap):
    monkeypatch.setattr(memory, cap, 1)
    with pytest.raises(teaching.TeachingUnavailable, match="search_limit"):
        teaching.question(artifact, inputs(), steps([0]))
    assert teaching.resolve_next(artifact, inputs(), []).reason == "search_limit"


def test_resolution_budget_is_shared_across_chain_and_replayed_prefix(artifact, monkeypatch):
    constraint = choose(artifact, steps([0, 1]))
    full = teaching._Operation(artifact, inputs(), steps([0, 1, 2]), [constraint])
    full.resolve()
    charge = full.evaluation.used
    before = teaching._Operation(artifact, inputs(), steps([0, 1, 2]), [constraint])
    assert 0 < before.evaluation.used < charge
    monkeypatch.setattr(memory, "MAX_RESOLVE_EVALUATIONS", charge - 1)
    decision = teaching.resolve_next(artifact, inputs(), steps([0, 1, 2]), constraints=[constraint])
    assert not decision.applied and decision.reason == "evaluation_limit"


def test_search_budget_is_not_reset_between_original_and_conditioned_spaces(artifact, monkeypatch):
    constraint = choose(artifact, steps([0, 1]))
    whole = teaching._Operation(artifact, inputs(), steps([0, 1]), [constraint])
    baseline = teaching._Operation(artifact, inputs(), steps([0, 1]), [])
    assert whole.search.evaluations > baseline.search.evaluations
    monkeypatch.setattr(memory, "MAX_TRAINING_EVALUATIONS", whole.search.evaluations - 1)
    assert teaching.resolve_next(artifact, inputs(), steps([0, 1]), constraints=[constraint]).reason == "search_limit"


def test_whole_object_and_constraint_count_bounds_refuse(artifact, monkeypatch):
    constraint = choose(artifact, steps([0, 1]))
    assert not teaching.resolve_next(artifact, inputs(), steps([0, 1]), constraints=[constraint] * 10).applied
    with monkeypatch.context() as tiny:
        tiny.setattr(teaching, "MAX_OPERATION_BYTES", 1)
        assert teaching.resolve_next(artifact, inputs(), []).reason == "operation_limit"
    cyclic = []
    cyclic.append(cyclic)
    assert not teaching.resolve_next(artifact, inputs(), [], constraints=cyclic).applied


@pytest.mark.parametrize("case", ["missing_input", "extra_input", "overlong_input", "boolean_observation",
    "missing_observation", "wrong_tool", "extra_step", "wrong_argument", "too_long", "nonlist"])
def test_invalid_empty_constraints_match_existing_resolver(artifact, case):
    values, history = inputs(), steps([0])
    if case == "missing_input":
        values = {}
    elif case == "extra_input":
        values["extra"] = 1
    elif case == "overlong_input":
        values["account"] = "a" * 33
    elif case == "boolean_observation":
        history[0]["result"]["quantity"] = False
    elif case == "missing_observation":
        history[0]["result"] = {}
    elif case == "wrong_tool":
        history[0]["tool"] = "unavailable"
    elif case == "extra_step":
        history[0]["claimed_authority"] = True
    elif case == "wrong_argument":
        history[0]["arguments"]["account"] = "different"
    elif case == "too_long":
        history = steps([0] * 9)
    else:
        history = {}
    assert teaching.resolve_next(artifact, values, history) == memory.resolve_next(artifact, values, history)


@pytest.mark.parametrize("mutation", ["omit_option", "new_option", "argument", "history", "space", "wrong_option"])
def test_select_reconstructs_complete_question_instead_of_trusting_rehashed_proposal(artifact, mutation):
    proposed = teaching.question(artifact, inputs(), steps([0, 1]))
    option = "action:receipt"
    if mutation == "omit_option":
        proposed["options"].pop()
    elif mutation == "new_option":
        proposed["options"].append({"id": "invented", "kind": "complete", "tool": None,
                                    "arguments": None, "arguments_sha256": memory._digest(None)})
    elif mutation == "argument":
        proposed["options"][0]["arguments"]["account"] = "other"
    elif mutation == "history":
        proposed["history_sha256"] = "0" * 64
    elif mutation == "space":
        proposed["conditional_space_sha256"] = "0" * 64
    else:
        option = "not-an-option"
    proposed["question_sha256"] = memory._digest({key: value for key, value in proposed.items()
                                                  if key != "question_sha256"})
    with pytest.raises(teaching.TeachingUnavailable, match="invalid_question|invalid_option"):
        teaching.select(artifact, inputs(), steps([0, 1]), proposed, option)


def test_later_constraint_does_not_validate_without_earlier_choice(artifact):
    first = choose(artifact, steps([0, 1]))
    second = choose(artifact, steps([0, 1, 2]), constraints=[first], option="complete")
    assert teaching.resolve_next(artifact, inputs(), steps([0, 1, 2]), constraints=[first, second]).applied
    assert teaching.resolve_next(artifact, inputs(), steps([0, 1, 2]), constraints=[first]).reason == "ambiguity"
    for constraints in ([second], [second, first]):
        assert not teaching.resolve_next(artifact, inputs(), steps([0, 1, 2]), constraints=constraints).applied


def test_rehashed_parent_model_omission_is_not_a_smaller_trusted_space(artifact):
    incomplete = copy.deepcopy(artifact)
    incomplete["models"].pop()
    incomplete["model_count"] -= 1
    incomplete["artifact_sha256"] = memory._digest({key: value for key, value in incomplete.items()
                                                   if key != "artifact_sha256"})
    decision = teaching.resolve_next(incomplete, inputs(), [])
    assert not decision.applied and decision.reason == "invalid_artifact" and decision.artifact_digest == ""


def test_original_target_argument_zero_case_remains_unteachable():
    domain = {"inputs": obj(target=INT), "tools": {
        "receipt": {"arguments": obj(target=INT), "result": obj(quantity=INT)}}}
    examples = [{"id": str(index), "inputs": {"target": target}, "steps": [
        {"tool": "receipt", "arguments": {"target": target}, "result": {"quantity": value}} for value in values]}
                for index, (target, values) in enumerate(SEEDS)]
    artifact = memory.synthesize(examples, allowed_tools=["receipt"], domain=domain,
        max_operations=2, operators=["if", "le", "lt"], constants=[0])
    observed = [{"tool": "receipt", "arguments": {"target": 5}, "result": {"quantity": 0}}]
    assert memory.resolve_next(artifact, {"target": 5}, observed).reason == "ambiguity"
    with pytest.raises(teaching.TeachingUnavailable, match="argument_ambiguity"):
        teaching.question(artifact, {"target": 5}, observed)


def test_argument_ambiguity_cannot_be_hidden_by_offering_only_other_labels():
    domain = {"inputs": obj(target=INT, alias=INT, account=STRING), "tools": {
        "receipt": {"arguments": obj(target=INT), "result": obj(quantity=INT)}}}
    examples = [{"id": str(index), "inputs": {**inputs(), "target": target, "alias": target}, "steps": [
        {"tool": "receipt", "arguments": {"target": target}, "result": {"quantity": value}} for value in values]}
                for index, (target, values) in enumerate(SEEDS)]
    artifact = memory.synthesize(examples, allowed_tools=["receipt"], domain=domain,
        max_operations=2, operators=["if", "le", "lt"], constants=[0])
    with pytest.raises(teaching.TeachingUnavailable, match="argument_ambiguity"):
        teaching.question(artifact, {**inputs(), "target": 5, "alias": 6}, [])


def _trees(child):
    memo = {}

    def expand(index):
        if index not in memo:
            result = []
            for production in child["states"][index]["alternatives"]:
                if production[0] in {"field", "literal"}:
                    result.append(tuple(production))
                else:
                    result.extend((production[0], *args) for args in
                                  itertools.product(*(expand(part) for part in production[1:])))
            memo[index] = result
        return memo[index]
    return {tree for root in child["roots"] for tree in expand(root)}


def _eval(tree, values):
    op = tree[0]
    if op == "field":
        return values[tree[1]]
    if op == "literal":
        return tree[1]
    if op == "if":
        return _eval(tree[2] if _eval(tree[1], values) else tree[3], values)
    left, right = _eval(tree[1], values), _eval(tree[2], values)
    return left <= right if op == "le" else left < right


def _values(model, state, quantities):
    summary = model["summary"]
    cell = None
    if summary["kind"] == "integer":
        assert summary["initial"] == {"source": "zero"}
        cell = 0
        if summary["update"] is not None:
            for incoming in quantities:
                op = summary["update"]["op"]
                cell = {"replace": incoming, "add": cell + incoming, "subtract": cell - incoming,
                        "min": min(cell, incoming), "max": max(cell, incoming)}[op]
    return {source["name"]: (cell if source["source"] == "memory" else
            "account-a" if source["source"] == "input" else quantities[-1]) for source in state["catalog"]}


def test_all_constraints_keep_exact_global_tree_subsets_paired_with_each_summary(artifact):
    first = choose(artifact, steps([0, 1]))
    second = choose(artifact, steps([0, 1, 2]), constraints=[first])
    third = choose(artifact, steps([0, 1, 2, 3]), constraints=[first, second], option="complete")
    conditioned = teaching._Operation(artifact, inputs(), steps([0, 1, 2, 3]), [first, second, third]).space
    actual = {json.dumps(model["summary"], sort_keys=True): model for model in conditioned["models"]}
    filtered_counts = []
    for original in artifact["models"]:
        expected = {}
        for state in original["states"]:
            guards = _trees(state["guard"])
            arguments = _trees(state["branches"][0]["bindings"][0]["artifact"])
            if state["last_tool"] is not None:
                counts = [len(guards)]
                for quantities, label in [([0, 1], "action:receipt"), ([0, 1, 2], "action:receipt"),
                                           ([0, 1, 2, 3], "complete")]:
                    values = _values(original, state, quantities)
                    guards = {tree for tree in guards if _eval(tree, values) == label}
                    if label == "action:receipt":
                        arguments = {tree for tree in arguments if _eval(tree, values) == "account-a"}
                    counts.append(len(guards))
                filtered_counts.append(counts)
            expected[state["id"]] = (guards, arguments)
        key = json.dumps(original["summary"], sort_keys=True)
        survives = all(guards and arguments for guards, arguments in expected.values())
        assert (key in actual) == survives
        if survives:
            for state in actual[key]["states"]:
                guards, arguments = expected[state["id"]]
                assert _trees(state["guard"]) == guards
                assert _trees(state["branches"][0]["bindings"][0]["artifact"]) == arguments
    # Both decisions concern the same abstract state, and the second genuinely
    # constrains programs already filtered by the first, not a fresh program.
    assert any(counts[0] > counts[1] > counts[2] > counts[3] > 0 for counts in filtered_counts)
