"""Pure, bounded conditioning of fixed memory programs by explicit decisions.

This module authenticates nobody and dispatches nothing. A structurally valid
constraint is only an assertion supplied by its caller. A future native layer
must establish actual observations, human identity, durable decision authority
and suitability for global learning separately. This is not a learning artifact
or a route around signed evidence admission.

Questions project the complete possible next labels. Every possible action must
have agreed arguments across precisely the models which can select that action.
Unknown states, argument disagreement or incomplete evaluation refuse the whole
question. Selected sparse rows condition the same fixed programs jointly; a
partial prefix never becomes a fabricated complete training episode.
"""
from __future__ import annotations

import copy
import json

from . import composed_operator as composed
from . import workflow_memory as memory
from . import workflow_transducer as workflow
from .workflow_transducer import WorkflowDecision

QUESTION_SCHEMA = "lightwork.workflow-memory-teaching-question.v1"
CONSTRAINT_SCHEMA = "lightwork.workflow-memory-constraint.v1"
MAX_CONSTRAINTS = 9  # At most one choice per position, including bounded END.
MAX_OPERATION_BYTES = 1536 * 1024
_CONSTRAINT_FIELDS = {"schema", "artifact_sha256", "input_sha256", "history_sha256", "step_index",
                      "previous_constraint_sha256", "question", "option_id", "conditional_space_sha256",
                      "constraint_sha256"}
_INVALID = (ValueError, TypeError, KeyError, IndexError, RecursionError, UnicodeError)


class TeachingUnavailable(ValueError):
    """No complete structurally valid conditional decision is available."""

    def __init__(self, reason):
        self.reason = reason
        super().__init__(f"memory teaching unavailable: {reason}")


def _guard(operation):
    try:
        return operation()
    except TeachingUnavailable:
        raise
    except composed.SynthesisLimit as exc:
        raise TeachingUnavailable("search_limit") from exc
    except (workflow._Limit, composed._PredictionLimit) as exc:
        raise TeachingUnavailable("evaluation_limit") from exc
    except memory._MemoryOverflow as exc:
        raise TeachingUnavailable("memory_overflow") from exc
    except _INVALID as exc:
        raise TeachingUnavailable("invalid_constraints") from exc


def _base(artifact, budget):
    try:
        raw = memory._canonical(artifact)
        snapshot = json.loads(raw)
        if (type(snapshot) is not dict or set(snapshot) != memory._FIELDS
                or snapshot["schema"] != memory.SCHEMA
                or type(snapshot["grammar"]) is not dict
                or set(snapshot["grammar"]) != memory._GRAMMAR_FIELDS):
            raise ValueError("invalid memory artifact")
        grammar = snapshot["grammar"]
        rebuilt = memory._synthesize(snapshot["episodes"], allowed_tools=snapshot["allowed_tools"],
            domain=snapshot["domain"], max_operations=grammar["max_operations"], operators=grammar["operators"],
            constants=grammar["constants"], max_steps=snapshot["max_steps"], budget=budget)
        if memory._canonical(rebuilt) != raw:
            raise ValueError("incomplete memory artifact")
        return rebuilt, len(raw.encode("utf-8"))
    except composed.SynthesisLimit:
        raise
    except _INVALID as exc:
        raise TeachingUnavailable("invalid_artifact") from exc


def _runtime(base, inputs, history):
    try:
        snapshot = workflow._snapshot({"inputs": inputs, "history": history})
        inputs, history = snapshot["inputs"], snapshot["history"]
        memory.transforms._matches(inputs, base["domain"]["inputs"])
    except _INVALID as exc:
        raise TeachingUnavailable("outside_domain") from exc
    if type(history) is not list or len(history) > base["max_steps"]:
        raise TeachingUnavailable("invalid_history")
    try:
        for step in history:
            workflow._step(base["domain"], step)
    except _INVALID as exc:
        raise TeachingUnavailable("invalid_history") from exc
    return inputs, history, len(memory.transforms._canonical(snapshot).encode("utf-8"))


def _replay(space, inputs, history, budget):
    memories = [memory._initial(model["summary"], inputs) for model in space["models"]]
    prefix = []
    for step in history:
        expected = memory._next_all(space, inputs, prefix, memories, budget)
        if (not expected.applied or expected.kind != "action" or expected.tool != step["tool"]
                or memory._canonical(expected.arguments) != memory._canonical(step["arguments"])):
            raise TeachingUnavailable("invalid_history")
        memories = [memory._advance(model["summary"], value, step, budget)
                    for model, value in zip(space["models"], memories, strict=True)]
        prefix.append(step)
    return memories


def _space_digest(space):
    return memory._digest({"schema": "lightwork.workflow-memory-conditional-space.v1",
                           "artifact_sha256": space["artifact_sha256"], "models": space["models"]})


def _condition(base, rows, budget):
    """Rebuild ALL jointly consistent programs; rows have no invented suffix."""
    grammar, domain = base["grammar"], base["domain"]
    labels = {memory._canonical(value): value for value in
              [*grammar["constants"], workflow._END, *("action:" + tool for tool in base["allowed_tools"])]}
    guard_constants, models = [labels[key] for key in sorted(labels)], []
    for original in base["models"]:
        summary = original["summary"]
        try:
            grouped = memory._samples(base["episodes"], summary, budget)
            for row in rows:
                state = workflow._state(row["history"])
                key = workflow._state_id(state)
                if key not in grouped:
                    raise TeachingUnavailable("unknown_state")
                cell = memory._initial(summary, row["inputs"])
                for step in row["history"]:
                    cell = memory._advance(summary, cell, step, budget)
                selected = row["option"]
                following = (None if selected["kind"] == "complete" else
                             {"tool": selected["tool"], "arguments": selected["arguments"]})
                grouped[key][1].append({"inputs": row["inputs"], "prior": row["history"],
                                       "memory": cell, "next": following})
            states = [memory._learn_state(state, samples, domain, summary, grammar, guard_constants, budget)
                      for _, (state, samples) in sorted(grouped.items())]
        except (memory._Contradiction, memory._MemoryOverflow):
            # These concrete added observations contradict this fixed program.
            # Limits and unrelated builder errors are never caught here.
            continue
        models.append({"summary": copy.deepcopy(summary), "states": states,
            "guard_candidate_count": sum(state["guard"]["candidate_count"] for state in states),
            "candidate_binding_count": sum(binding["candidate_count"] for state in states
                for branch in state["branches"] for binding in branch["bindings"])})
    if not models:
        raise TeachingUnavailable("inconsistent_constraints")
    space = {**base, "models": models, "model_count": len(models),
             "guard_candidate_count": sum(model["guard_candidate_count"] for model in models),
             "candidate_binding_count": sum(model["candidate_binding_count"] for model in models)}
    memory._canonical(space)  # Still obey the complete memory representation cap.
    return space


def _project(space, inputs, history, memories, budget):
    """Keep model/guard/argument association through the complete projection."""
    identity = workflow._state_id(workflow._state(history))
    options = {}
    for model, cell in zip(space["models"], memories, strict=True):
        state = next((item for item in model["states"] if item["id"] == identity), None)
        if state is None:
            raise TeachingUnavailable("unknown_state")
        values = memory._values(state["catalog"], inputs, history, cell)
        outputs = workflow._Distribution(state["guard"], values, budget).roots(state["guard"]["roots"])
        if ("invalid",) in outputs:
            raise TeachingUnavailable("outside_domain")
        for label, _count in outputs.values():
            if label == workflow._END:
                option = {"id": "complete", "kind": "complete", "tool": None, "arguments": None}
            else:
                branch = next((item for item in state["branches"] if label == "action:" + item["tool"]), None)
                if branch is None:
                    raise TeachingUnavailable("unobserved_transition")
                if len(history) >= space["max_steps"]:
                    raise TeachingUnavailable("step_limit")
                agreed, arguments, reason = workflow._arguments(branch, values,
                    space["domain"]["tools"][branch["tool"]]["arguments"], budget)
                if not agreed:
                    raise TeachingUnavailable("argument_" + reason)
                option = {"id": label, "kind": "action", "tool": branch["tool"], "arguments": arguments}
            if label in options and memory._canonical(options[label]) != memory._canonical(option):
                raise TeachingUnavailable("argument_ambiguity")
            options[label] = option
    if len(options) < 2:
        raise TeachingUnavailable("not_ambiguous")
    return [{**options[key], "arguments_sha256": memory._digest(options[key]["arguments"])}
            for key in sorted(options)]


class _Operation:
    def __init__(self, artifact, inputs, history, constraints):
        self.search, self.evaluation = memory._TrainingBudget(), memory._ResolveBudget()
        self.base, base_bytes = _base(artifact, self.search)
        self.inputs, self.history, input_bytes = _runtime(self.base, inputs, history)
        self.constraints = workflow._snapshot([] if constraints is None else constraints)
        if type(self.constraints) is not list or len(self.constraints) > MAX_CONSTRAINTS:
            raise TeachingUnavailable("invalid_constraints")
        constraint_bytes = len(memory.transforms._canonical(self.constraints).encode("utf-8"))
        self.encoded_bytes = base_bytes + input_bytes + constraint_bytes
        if self.encoded_bytes > MAX_OPERATION_BYTES:
            raise TeachingUnavailable("operation_limit")
        self.space, self.rows = self.base, []
        self.input_digest = memory._digest(self.inputs)
        self.chain = memory._digest({"schema": "lightwork.workflow-memory-constraint-root.v1",
            "artifact_sha256": self.base["artifact_sha256"], "input_sha256": self.input_digest})
        previous = -1
        for constraint in self.constraints:
            if (type(constraint) is not dict or set(constraint) != _CONSTRAINT_FIELDS
                    or type(constraint["step_index"]) is not int
                    or not previous < constraint["step_index"] <= len(self.history)):
                raise TeachingUnavailable("invalid_constraints")
            prefix = self.history[:constraint["step_index"]]
            proposed = self.question(prefix)
            if memory._canonical(proposed) != memory._canonical(constraint["question"]):
                raise TeachingUnavailable("invalid_constraints")
            expected = self.select(proposed, constraint["option_id"], prefix)
            if memory._canonical(expected) != memory._canonical(constraint):
                raise TeachingUnavailable("invalid_constraints")
            previous = constraint["step_index"]

    def question(self, history):
        memories = _replay(self.space, self.inputs, history, self.evaluation)
        options = _project(self.space, self.inputs, history, memories, self.evaluation)
        value = {"schema": QUESTION_SCHEMA, "artifact_sha256": self.base["artifact_sha256"],
            "input_sha256": self.input_digest, "history_sha256": memory._digest(history),
            "step_index": len(history), "previous_constraint_sha256": self.chain,
            "conditional_space_sha256": _space_digest(self.space), "model_count": self.space["model_count"],
            "options": options}
        value["question_sha256"] = memory._digest(value)
        return workflow._snapshot(value)

    def select(self, proposed, option_id, history):
        if type(option_id) is not str or len(option_id) > 135:
            raise TeachingUnavailable("invalid_option")
        selected = next((option for option in proposed["options"] if option["id"] == option_id), None)
        if selected is None:
            raise TeachingUnavailable("invalid_option")
        rows = [*self.rows, {"inputs": self.inputs, "history": history, "option": selected}]
        space = _condition(self.base, rows, self.search)
        memories = _replay(space, self.inputs, history, self.evaluation)
        resolved = memory._next_all(space, self.inputs, history, memories, self.evaluation)
        if (not resolved.applied or resolved.kind != selected["kind"] or resolved.tool != selected["tool"]
                or memory._canonical(resolved.arguments) != memory._canonical(selected["arguments"])):
            raise TeachingUnavailable("inconsistent_constraints")
        value = {"schema": CONSTRAINT_SCHEMA, "artifact_sha256": self.base["artifact_sha256"],
            "input_sha256": self.input_digest, "history_sha256": memory._digest(history),
            "step_index": len(history), "previous_constraint_sha256": self.chain,
            "question": proposed, "option_id": option_id, "conditional_space_sha256": _space_digest(space)}
        value["constraint_sha256"] = memory._digest(value)
        self.space, self.rows, self.chain = space, rows, value["constraint_sha256"]
        return workflow._snapshot(value)

    def resolve(self):
        memories = _replay(self.space, self.inputs, self.history, self.evaluation)
        return memory._next_all(self.space, self.inputs, self.history, memories, self.evaluation)


def question(artifact, inputs, history, *, constraints=None):
    """Return every resolved action/END option, or raise TeachingUnavailable.

    The observation and constraint chain are caller assertions, not evidence of
    real execution or human authority. No question exists for mere agreement.
    """
    def operation():
        current = _Operation(artifact, inputs, history, constraints)
        return current.question(current.history)
    return _guard(operation)


def select(artifact, inputs, history, proposed, option_id, *, constraints=None):
    """Return one constraint to append; authenticate/retain it outside this module."""
    def operation():
        snapshot = workflow._snapshot(proposed)
        current = _Operation(artifact, inputs, history, constraints)
        if (current.encoded_bytes + len(memory.transforms._canonical(snapshot).encode("utf-8"))
                > MAX_OPERATION_BYTES):
            raise TeachingUnavailable("operation_limit")
        expected = current.question(current.history)
        if memory._canonical(snapshot) != memory._canonical(expected):
            raise TeachingUnavailable("invalid_question")
        if len(current.constraints) >= MAX_CONSTRAINTS:
            raise TeachingUnavailable("constraint_limit")
        return current.select(expected, option_id, current.history)
    return _guard(operation)


def resolve_next(artifact, inputs, history, *, constraints=None):
    """Resolve under every supplied constraint, without dispatch or authentication."""
    identity = artifact.get("artifact_sha256", "") if type(artifact) is dict else ""
    try:
        return _guard(lambda: _Operation(artifact, inputs, history, constraints).resolve())
    except TeachingUnavailable as exc:
        return WorkflowDecision(False, reason=exc.reason,
                                artifact_digest=identity if exc.reason != "invalid_artifact"
                                and type(identity) is str and len(identity) == 64 else "")


def validate_constraints(artifact, inputs, history, *, constraints=None):
    """Return reconstructed commitments only; these confer no runtime authority."""
    def operation():
        current = _Operation(artifact, inputs, history, constraints)
        _replay(current.space, current.inputs, current.history, current.evaluation)
        return {"artifact_sha256": current.base["artifact_sha256"], "input_sha256": current.input_digest,
                "history_sha256": memory._digest(current.history), "constraint_count": len(current.constraints),
                "constraint_chain_sha256": current.chain, "conditional_space_sha256": _space_digest(current.space),
                "model_count": current.space["model_count"]}
    return _guard(operation)
