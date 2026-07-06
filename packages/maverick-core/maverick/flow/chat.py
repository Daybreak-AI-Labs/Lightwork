"""Conversational flow copilot -- chat that BUILDS and REPAIRS a flow.

One turn in, one turn out: the caller sends the user's message plus grounding
(the current flow graph, recent conversation turns, optionally a run trace to
diagnose), and gets back a short reply and -- when the user asked for a change
-- a list of :mod:`.patch` operations. Patches are applied and validated here,
so the model can never leave the canvas in an invalid state: a bad patch list
degrades to a reply with a note, never a broken flow.

Why patches instead of regenerating the graph: each edit is small, auditable,
and animatable on the canvas; the saved result is a normal new flow version, so
"undo what the AI did" is the existing rollback. On an EMPTY canvas the model
may instead return a full flow (the :mod:`.draft` shape).

The ``complete`` callable is injected (the ``maverick.llm.LLM.complete`` seam),
so this is unit-testable with a fake and never hard-codes a model.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field

from .draft import FLOW_SCHEMA_BLOCK, _extract_json, _sanitize
from .ir import Flow
from .patch import PatchError, apply_patches, describe_patch

_CHAT_MAX_DOLLARS = 0.50
_CHAT_MAX_TOKENS = 2500
_MAX_HISTORY_TURNS = 12
_MAX_TURN_CHARS = 2000

FLOW_CHAT_SYSTEM = """You are a workflow copilot inside a visual flow designer.
You answer questions about the user's flow and edit it on request. Output STRICT JSON only:

{"reply": "<1-3 short sentences for the user>", "patches": [ ...zero or more patch ops ]}

Patch ops (use ONLY these):
- {"op":"add_node","node":{<a node object>},"after":"<existing node id>"}   (omit "after" to add unlinked)
- {"op":"remove_node","id":"<node id>"}
- {"op":"set_field","id":"<node id>","field":"<node field>","value":<new value>}
- {"op":"set_flow_field","field":"name|start|notify|max_seconds|max_dollars|schedule","value":<new value>}
- {"op":"rewire","id":"<node id>","field":"next|if_true|if_false|on_error","to":"<node id or null>"}

Node objects use the flow schema below. New node ids must be short unique slugs not already in the flow.

Rules:
- If the user only asked a question (explain / why did it fail), return "patches": [] and answer in "reply".
- If the current flow is EMPTY and the user describes an automation, you may instead return
  {"reply": "...", "flow": {<a complete flow object in the schema below>}}.
- When a run trace is provided and the user asks about a failure, read the failed node's status/error
  and either explain it or propose the smallest patch that fixes it.
- Prefer the smallest set of patches that satisfies the request. Never invent tool names: only use
  tools from the provided list (or use an "agent" node with a clear brief).
- Insert an "approval" node before anything irreversible (sending, paying, deleting).
- Output the JSON object ONLY -- no prose, no code fence.

Flow schema:
""" + FLOW_SCHEMA_BLOCK


@dataclass
class FlowChatResult:
    """One copilot turn: what to tell the user, and (if the flow changed) the
    patched flow plus the human-readable patch lines that produced it."""

    reply: str = ""
    flow: Flow | None = None            # the NEW flow when this turn changed it
    patches: list = field(default_factory=list)
    applied: list[str] = field(default_factory=list)   # describe_patch() lines
    notes: list[str] = field(default_factory=list)


def _tool_hint(tools, tool_docs) -> str:
    if tool_docs:
        lines = [f"- {n}: {str(d)[:100]}" for n, d in list(tool_docs.items())[:60]]
        return "\nAvailable action tools (use the exact name):\n" + "\n".join(lines)
    if tools:
        return "\nAvailable action tools (use exact names): " + ", ".join(sorted(tools)[:60])
    return ""


def _run_context(flow: Flow | None, run: dict | None) -> str:
    parts = []
    if flow is not None and flow.nodes:
        parts.append("Current flow:\n" + json.dumps(flow.to_dict(), sort_keys=True))
    else:
        parts.append("Current flow: (empty canvas)")
    if run:
        slim = {k: run.get(k) for k in
                ("status", "error", "cursor", "nodes", "prompt") if run.get(k)}
        data = run.get("data")
        if isinstance(data, dict):
            slim["data_keys"] = sorted(data.keys())[:40]
        parts.append("Most recent run:\n" + json.dumps(slim, sort_keys=True, default=str)[:4000])
    return "\n\n".join(parts)


def _messages(message: str, history) -> list[dict]:
    msgs: list[dict] = []
    for turn in list(history or [])[-_MAX_HISTORY_TURNS:]:
        role = str(turn.get("role", ""))
        content = str(turn.get("content", ""))[:_MAX_TURN_CHARS]
        if role in ("user", "assistant") and content:
            msgs.append({"role": role, "content": content})
    msgs.append({"role": "user", "content": str(message)[:_MAX_TURN_CHARS]})
    return msgs


def chat_flow(message: str, *, flow: Flow | None = None, history=(),
              run: dict | None = None, tools=(), tool_docs=None,
              complete=None) -> FlowChatResult:
    """One copilot turn. Returns a :class:`FlowChatResult`; ``result.flow`` is
    set only when the turn produced a valid changed flow. Never raises on model
    misbehavior -- a bad patch list or unparsable output degrades to a plain
    reply with a note."""
    message = (message or "").strip()
    if not message:
        raise ValueError("a message is required")
    if complete is None:  # pragma: no cover -- exercised via the real endpoint
        from ..llm import LLM, model_for_role
        complete = LLM(model=model_for_role("orchestrator")).complete
    from ..budget import Budget
    resp = complete(
        system=FLOW_CHAT_SYSTEM + _tool_hint(tools, tool_docs)
        + "\n\n" + _run_context(flow, run),
        messages=_messages(message, history),
        budget=Budget(max_dollars=_CHAT_MAX_DOLLARS),
        max_tokens=_CHAT_MAX_TOKENS,
    )
    raw = _extract_json(getattr(resp, "text", "") or "")
    out = FlowChatResult()
    if raw is None:
        out.reply = "I couldn't produce a structured answer for that -- try rephrasing."
        out.notes.append("model did not return JSON")
        return out
    out.reply = str(raw.get("reply") or "").strip() or "Done."
    patches = raw.get("patches")
    full = raw.get("flow")
    if isinstance(patches, list) and patches:
        if flow is None or not flow.nodes:
            out.notes.append("patches ignored: there is no flow to edit yet")
            return out
        try:
            out.flow = apply_patches(flow, patches)
            out.patches = patches
            out.applied = [describe_patch(p) for p in patches]
        except PatchError as e:
            out.notes.append(f"suggested edit was not applied: {e}")
        return out
    if isinstance(full, dict) and (flow is None or not flow.nodes):
        try:
            candidate = Flow.from_dict(_sanitize(full, message))
            if flow is not None and flow.id:
                candidate.id = flow.id
            errs = candidate.validate()
            if errs:
                raise ValueError("; ".join(errs[:3]))
            out.flow = candidate
            out.applied = [f"drafted flow with {len(candidate.nodes)} nodes"]
        except (ValueError, KeyError, TypeError) as e:
            out.notes.append(f"drafted flow was invalid and was not applied: {e}")
    return out


__all__ = ["chat_flow", "FlowChatResult", "FLOW_CHAT_SYSTEM"]
