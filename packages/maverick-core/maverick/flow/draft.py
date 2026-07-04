"""Draft a :class:`~.ir.Flow` from a plain-English description.

The visual designer's "✨ Draft" button and ``POST /flows/draft`` call this: an
operator types "when a new GitHub issue is opened, summarize it and post to
Slack, but ask me before posting" and gets a first-pass flow graph to edit. The
model output is strict JSON in the flow schema, parsed forgivingly and validated;
anything unusable degrades to a single agent node running the description (so the
user always gets *something* runnable to refine).

The ``complete`` callable is injected (the ``maverick.llm.LLM.complete`` seam),
so this is unit-testable with a fake and never hard-codes a model.
"""
from __future__ import annotations

import json
import re

from .ir import Flow, single_agent_flow

_DRAFT_MAX_DOLLARS = 0.50
_DRAFT_MAX_TOKENS = 2500

FLOW_DRAFT_SYSTEM = """You design automation FLOWS and output STRICT JSON only.

A flow is a graph of typed nodes:
{"id": "<slug>", "name": "<title>", "start": "<node id>", "nodes": [ ...nodes ]}

Each node is one of these kinds (include only the fields for its kind):
- {"id","kind":"agent","brief":"<instruction for an AI agent>","next":"<id|null>"}
- {"id","kind":"action","tool":"<tool name>","params":{...},"next":"<id|null>"}
- {"id","kind":"branch","condition":"<key op value>","if_true":"<id>","if_false":"<id>"}
- {"id","kind":"foreach","items":"<data key holding a list>","var":"item","body":{<a nested flow>},"next":"<id|null>"}
- {"id","kind":"parallel","branches":[{<nested flow>},...],"next":"<id|null>"}
- {"id","kind":"approval","prompt":"<what a human approves>","next":"<id|null>"}
- {"id","kind":"delay","seconds":<number>,"next":"<id|null>"}
- {"id","kind":"subflow","flow_ref":"<a saved flow id to run here>","next":"<id|null>"}

Any "agent" or "action" node may also set "retries":<n> (re-try on failure) and "on_error":"<id>" (route there if it fails).

Rules:
- Prefer an "agent" node when a step needs judgment/writing; an "action" node when it maps to a known tool.
- Use a "subflow" node to reuse an existing saved flow instead of rebuilding its steps.
- For a step that can fail (an external call), set "retries" and/or an "on_error" handler node.
- Reference earlier results with {{key}} where a node's "output" set that key. Set "output" on nodes whose result a later node needs. You may transform a value: {{upper(key)}}, {{lower(key)}}, {{trim(key)}}, {{title(key)}}, {{length(key)}}, {{concat(a," ",b)}}, {{replace(key,"x","y")}}, {{default(key,"fallback")}}.
- A "branch" condition is a simple "<key> <op> <value>" (ops: == != > < >= <= contains) over flow data.
- Insert an "approval" node before anything a human should review (sending, paying, deleting).
- Node ids are short slugs (n0, n1, ...). "next":null (or omit) ends a path. "start" is the first node's id.
- Output the JSON object ONLY — no prose, no code fence."""


def _strip_fence(text: str) -> str:
    t = (text or "").strip()
    if t.startswith("```"):
        t = re.sub(r"^```[a-zA-Z0-9]*\n?", "", t)
        t = re.sub(r"\n?```$", "", t.strip())
    return t.strip()


def _extract_json(text: str) -> dict | None:
    t = _strip_fence(text)
    try:
        obj = json.loads(t)
        return obj if isinstance(obj, dict) else None
    except ValueError:
        # tolerate leading/trailing prose: grab the outermost {...}
        start, end = t.find("{"), t.rfind("}")
        if 0 <= start < end:
            try:
                obj = json.loads(t[start:end + 1])
                return obj if isinstance(obj, dict) else None
            except ValueError:
                return None
        return None


def _sanitize(raw: dict, description: str) -> dict:
    """Coerce a model's flow dict into a valid shape: ensure node ids, a real
    start, and that routing only points at existing nodes."""
    nodes = raw.get("nodes")
    if not isinstance(nodes, list) or not nodes:
        raise ValueError("no nodes")
    clean = []
    used: set[str] = set()
    for i, n in enumerate(nodes):
        if not isinstance(n, dict) or not n.get("kind"):
            continue
        n = dict(n)
        nid = str(n.get("id") or f"n{i}")
        while nid in used:      # keep every node on an id collision (rename, don't drop)
            nid = f"{nid}_{i}"
        n["id"] = nid
        used.add(nid)
        clean.append(n)
    if not clean:
        raise ValueError("no usable nodes")
    ids = {n["id"] for n in clean}
    for n in clean:                       # drop routing that points nowhere
        for key in ("next", "if_true", "if_false"):
            if n.get(key) is not None and n.get(key) not in ids:
                n[key] = None
    start = str(raw.get("start") or "")
    if start not in ids:
        start = clean[0]["id"]
    return {
        "id": str(raw.get("id") or "").strip(),
        "name": str(raw.get("name") or description[:60]).strip() or "Imported flow",
        "start": start,
        "nodes": clean,
    }


def _tool_hint(tools, tool_docs) -> str:
    """Ground the model in the ACTION tools it may reference. With ``tool_docs``
    (name -> one-line description) it lists 'name: what it does' so the draft
    picks a real connector for the right step; else just the names."""
    if tool_docs:
        lines = [f"- {n}: {str(d)[:100]}" for n, d in list(tool_docs.items())[:60]]
        return "\nAvailable action tools (use the exact name):\n" + "\n".join(lines)
    if tools:
        return "\nAvailable action tools (use exact names): " + ", ".join(sorted(tools)[:60])
    return ""


def _run(complete, tools, description: str, tool_docs=None) -> str:
    from ..budget import Budget
    resp = complete(
        system=FLOW_DRAFT_SYSTEM + _tool_hint(tools, tool_docs),
        messages=[{"role": "user", "content": f"Design a flow for: {description}"}],
        budget=Budget(max_dollars=_DRAFT_MAX_DOLLARS),
        max_tokens=_DRAFT_MAX_TOKENS,
    )
    return getattr(resp, "text", "") or ""


def draft_flow(description: str, *, tools=(), tool_docs=None, complete=None,
               flow_id: str = "") -> tuple[Flow, list[str]]:
    """Draft a flow from ``description``. Returns ``(flow, notes)``. Falls back to
    a single agent node (never fails to return something runnable). ``complete``
    defaults to a real LLM call (orchestrator model) when not injected. ``tool_docs``
    (name -> description) grounds the drafter so action nodes name a real tool."""
    description = (description or "").strip()
    if not description:
        raise ValueError("a description is required")
    notes: list[str] = []
    if complete is None:  # pragma: no cover -- exercised via the real endpoint
        from ..llm import LLM, model_for_role
        complete = LLM(model=model_for_role("orchestrator")).complete
    try:
        raw = _extract_json(_run(complete, tools, description, tool_docs))
        if raw is None:
            raise ValueError("model did not return JSON")
        flow = Flow.from_dict(_sanitize(raw, description))
        if flow_id:
            flow.id = flow_id
        errs = flow.validate()
        if errs:
            notes.append("draft had structural issues: " + "; ".join(errs[:3]))
            raise ValueError("invalid draft")
        return flow, notes
    except Exception as e:
        notes.append(f"couldn't build a full graph ({type(e).__name__}); "
                     "starting from a single agent step you can expand")
        fallback = single_agent_flow(flow_id or "", description[:60] or "New flow", description)
        return fallback, notes


__all__ = ["draft_flow", "FLOW_DRAFT_SYSTEM"]
