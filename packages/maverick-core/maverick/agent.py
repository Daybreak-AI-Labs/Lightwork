"""Recursive async agent.

v0.1.4: appends ``persona.render_persona_prompt()`` to the system
prompt of every agent so users can give the swarm a name and voice
without patching the kernel.
"""
from __future__ import annotations

import asyncio
import contextlib
import hashlib
import inspect
import itertools
import json
import logging
import os
import re
import secrets as _secrets
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import urlsplit

from . import killswitch
from ._envparse import env_float, env_int
from .audit.errors import AuditRefused
from .budget import BudgetExceeded, UnpricedModelError, UsageAccountingError
from .env_setting import env_or_config as _env_or_config
from .llm import model_for_role
from .run_outcome import GoalCancelled, cancellation_message, owned_task, task_stop
from .swarm import SwarmContext
from .tools import ToolRegistry, base_registry
from .tools.agent_bus_tool import delegate_to_agent, recv_from_agent, send_to_agent
from .tools.spawn import (
    list_specialists_tool,
    spawn_specialist_tool,
    spawn_subagent_tool,
    spawn_swarm_tool,
)

log = logging.getLogger(__name__)

WORKER_SYSTEM_TEMPLATE = """You are a specialist agent in Lightwork, a long-horizon multi-agent swarm.

Your role: {role}
Your depth in the swarm: {depth} (root = 0, max = {max_depth})

You have a single sub-goal. Plan briefly, then act.

Tools you can call include:
  - File / shell / read / write for the sandbox.
  - `ask_user` to queue a question for the user (async). Use sparingly, batch.
  - `spawn_subagent` to delegate a focused sub-task to a child specialist.
  - `spawn_swarm` to fan out INDEPENDENT sub-tasks in PARALLEL.
  - `memory` for durable, cross-session notes: consult it for long-horizon work and record lasting learnings (conventions, decisions, dead ends) — not scratch.
  - `mcp_<server>__<tool>` for any external MCP servers wired into config.

Rules:
1. Do the work YOURSELF by default. Only `spawn_swarm` when a sub-task is genuinely heavy AND independent AND needs its own context window — not merely because your task "has several aspects". Over-decomposing front-loads the whole budget on fan-out so nobody is left to synthesize the answer. The deeper you are, the more you should be a leaf: at depth ≥ 1, prefer doing the research yourself over spawning more children.
2. If a sub-task needs its own context window or a different specialty, use `spawn_subagent`.
3. When done, respond in plain text starting with `FINAL:` followed by your answer. No tool call.
4. Be precise. Cite exact paths, commands, results, and findings from your children.
5. Budget is enforced globally; spend wisely. Stop spawning if results so far are sufficient — reaching a synthesized answer matters more than breadth of research."""


ORCHESTRATOR_SYSTEM_TEMPLATE = """You are the orchestrator of a Lightwork swarm.

You own a top-level goal. You do not execute work yourself; you decompose, delegate, and verify.

Standard playbook:
1. Plan: think through the goal. Identify which sub-tasks are independent (parallelizable) vs. sequential.
2. Spawn: use `spawn_swarm` to fan out independent sub-tasks in parallel. Use `spawn_subagent` for sequential dependencies.
3. Synthesize: aggregate findings from your children into a coherent answer.
4. Verify: before finalizing, check that the answer satisfies the original goal.
5. If you are blocked on info only the user can give, use `ask_user` (batched).
6. End with `FINAL:` followed by your synthesized answer.

Consult your `memory` (durable cross-session notes) when planning a long-horizon goal, and record lasting learnings there as the run progresses.

You have a maximum spawn depth of {max_depth}. Use it wisely.

Available roles for children: researcher, coder, writer, analyst, summarizer, revisor.

External MCP tools (if any) appear as `mcp_<server>__<tool>`."""


def select_base_template(
    *, role: str, depth: int, max_depth: int, coding_enabled: bool,
    is_root: bool = False,
) -> str:
    """Pick the base system template for an agent. Pure: no env/config reads.

    Coding mode, when enabled, overrides role: the orchestrator emits the FINAL
    and must speak unified diffs, not prose, or the patch validator and
    test-driven verifier operate on prose and reject every output (Wave 9). With
    coding mode off it is the orchestrator template for the planner and the
    worker template for every other role. First side-effect-free seam extracted
    from ``Agent._build_system`` (god-module decomposition: PromptBuilder).

    ``is_root`` (W1-24): a domain-routed goal's depth-0 agent has
    ``role=profile.name`` (e.g. ``"finance_controller"``), not
    ``"orchestrator"`` -- it still emits the goal's FINAL and needs the
    orchestrator template, not the "specialist worker" one. The domain
    persona is layered on separately via ``apply_role_overlays``, so this
    does not lose any pack flavor.
    """
    if coding_enabled:
        from .coding_mode import CODER_CODING_MODE_TEMPLATE
        return CODER_CODING_MODE_TEMPLATE.format(
            role=role, depth=depth, max_depth=max_depth,
        )
    if role == "orchestrator" or is_root:
        return ORCHESTRATOR_SYSTEM_TEMPLATE.format(max_depth=max_depth)
    return WORKER_SYSTEM_TEMPLATE.format(role=role, depth=depth, max_depth=max_depth)


def apply_global_overlays(base: str) -> str:
    """Append the swarm-wide additive prompt overlays to ``base``.

    Persona, output style, and the learned-habits prior are global (config /
    data-engine state, not agent-specific), each optional and fail-open. Order
    is preserved from the original inline assembly. Second PromptBuilder
    collaborator extracted from ``Agent._build_system``; self-independent, so it
    is a free function rather than a method.
    """
    # Persona (optional, additive).
    try:
        from .persona import render_persona_prompt
        persona = render_persona_prompt()
        if persona:
            base = base + persona
    except Exception:
        pass

    # Output style (optional, additive): the user-selected response style
    # (dashboard runtime overlay). Tone/format only, like the persona block.
    try:
        from .styles import render_active_style_prompt
        style = render_active_style_prompt()
        if style:
            base = base + style
    except Exception:
        pass

    # Learned-habits prior (the Hippocampus, additive): when the data engine
    # is on, surface the strongest causally-beneficial habits it has
    # consolidated so the agent prefers what has worked. No-op unless
    # [data_engine] is enabled and procedural memory exists; fail-open.
    try:
        from . import data_engine
        if data_engine.enabled():
            from .procedural_memory import recall_prompt
            base = base + recall_prompt()
    except Exception:
        pass

    return base


def apply_role_overlays(base: str, *, role: str, domain_persona: str | None) -> str:
    """Append the per-agent role overlays to ``base``.

    The tenant's per-role client addendum (dashboard roles editor; empty unless
    customized, fail-open) and the domain-pack persona (factory spawn-from-
    profile). Both are additive and blank-line separated, matching the original
    inline assembly. Third PromptBuilder collaborator extracted from
    ``Agent._build_system``; depends only on the role name and the resolved
    domain persona, so it is a free function.
    """
    # Per-role client addendum (optional, additive): a tenant's custom
    # instructions for this role, edited via the dashboard roles editor.
    # Empty for any role the client hasn't customized, so behavior is
    # unchanged by default. Specialist roles (domain-pack names) never
    # match a known role, so this is a no-op for them.
    try:
        from .role_edit import role_addendum
        addendum = role_addendum(role)
        if addendum:
            base = base + "\n\n" + addendum
    except Exception:
        pass

    # Domain-pack persona (factory spawn-from-profile): specialist
    # instructions for this agent's domain, additive to the base template.
    if domain_persona:
        base = base + "\n\n" + domain_persona

    return base


def apply_skill_overlays(
    base: str, *, brief: str, use_skills: bool, depth: int = 0,
) -> tuple[str, list]:
    """Append relevant prior-run skills to ``base``; return ``(base, skills)``.

    Returns the recalled skill objects so the caller can record their use and
    attribute this run's outcome to them — the stats + ``skills_used``
    bookkeeping stays in the caller to preserve the original all-or-nothing
    semantics (names are read inside the caller's fail-safe block). No-op when
    skills are disabled or none are relevant; fail-open on a missing/invalid
    skill store. Fourth PromptBuilder collaborator from ``Agent._build_system``.
    """
    if not use_skills:
        return base, []
    try:
        from .skills import available_skills, relevant_skills, render_for_prompt
        # Deep workers get the single most relevant skill: overlays ride every
        # node's system prompt, so a swarm re-pays the full render per child.
        # The root keeps the standard top-3 recall.
        skills = relevant_skills(brief, available_skills(),
                                 max_n=3 if depth == 0 else 1)
        if skills:
            delivered = []
            overlay = render_for_prompt(skills, rendered_skills=delivered)
            if overlay:
                return base + "\n\n" + overlay, delivered
    except (ImportError, FileNotFoundError, ValueError):
        pass
    return base, []


def apply_memory_brief(base: str, *, depth: int) -> str:
    """Append the cross-session memory presence hint (root agent only).

    Depth-gated to the root agent (``depth == 0``) so deep workers keep lean,
    focused context (they can still use the ``memory`` tool directly). Surfaces
    only a safe presence hint — memory filenames/contents are model-writable and
    re-enter through the tool's scanned, redacted path, never directly here.
    Empty memory -> no change; fail-open. Fifth PromptBuilder collaborator from
    ``Agent._build_system``.
    """
    if depth != 0:
        return base
    try:
        from .tools.memory import memory_brief
        brief = memory_brief()
        if brief:
            base = base + "\n\n" + brief
    except Exception:  # pragma: no cover -- never block a run
        pass
    return base


# #611: fraction of the budget reserved for the TOP-level goal's synthesis /
# write step. A deeper worker (depth > 0) stops once cumulative spend crosses
# (1 - this) of the cap, so a recursive research swarm can't burn the budget
# the orchestrator needs to actually produce the answer (the dogfooded failure:
# 35 agents, $7.85 spent, zero report). 0 disables it.
_SYNTHESIS_RESERVE = env_float("MAVERICK_SYNTHESIS_RESERVE", 0.25)

# #614: how often (seconds) the root agent mirrors running spend onto its
# open episode row so `maverick runs` / `maverick budget` show mid-run spend.
# Throttled so we don't write the row on every step of a fast loop.
_SPEND_MIRROR_INTERVAL = env_float("MAVERICK_SPEND_MIRROR_INTERVAL", 5.0)

# Loop guard: a long-horizon failure mode is the model re-issuing the SAME
# tool call that keeps failing the same way, silently burning budget/steps. We
# track a per-(tool,args) consecutive-failure streak and, once it hits the
# threshold, append a one-line nudge to the tool result so the model breaks the
# loop (change args, switch tools, rethink). Pure advice -- it never blocks a
# call. Default on; MAVERICK_LOOP_GUARD=0 disables it. Each knob is also a
# setting ([agent] loop_guard / loop_guard_threshold / loop_guard_block /
# step_budget_warning) read once here at startup; the environment still wins.
def _loop_guard_settings() -> tuple[bool, int, int]:
    """(enabled, nudge threshold, suppression threshold) as read at startup."""
    from .env_setting import env_or_config_int

    enabled = (_env_or_config("MAVERICK_LOOP_GUARD", "agent", "loop_guard", "1")
               or "1").strip().lower() not in {"0", "false", "no", "off"}
    threshold = max(2, env_or_config_int(
        "MAVERICK_LOOP_GUARD_THRESHOLD", "agent", "loop_guard_threshold", 3))
    block = env_or_config_int("MAVERICK_LOOP_GUARD_BLOCK", "agent", "loop_guard_block", 6)
    return enabled, threshold, (max(block, threshold + 1) if block > 0 else 0)


def _step_budget_warning_setting() -> int:
    from .env_setting import env_or_config_int

    return max(0, env_or_config_int(
        "MAVERICK_STEP_BUDGET_WARNING", "agent", "step_budget_warning", 3))


_LOOP_GUARD_ENABLED, _LOOP_GUARD_THRESHOLD, _LOOP_GUARD_BLOCK = _loop_guard_settings()
# Suppression tier above the nudge: once the SAME call has produced the
# byte-identical result this many times in a row — success or failure — the
# next identical attempt is refused WITHOUT executing. Identical output N
# times is definitionally zero new information, and the smaller open-weight
# models the ollama/vllm/tgi lanes serve are the ones that ignore the nudge
# and grind a polling/retry loop through the whole tool budget. Per-call
# only, never a run abort: changed arguments or a different tool clear it
# immediately, so a legitimate poll loop that varies anything keeps working.
# Clamped above the nudge threshold so advice always precedes enforcement,
# whatever the two env knobs say. 0 disables suppression (nudges continue).
# MAVERICK_LOOP_GUARD=0 disables the whole guard as before. (Read above, in
# _loop_guard_settings.)

# Step-budget awareness: when only this many tool-using turns remain before
# max_steps force-stops the run, the loop nudges the agent to synthesize a
# FINAL now -- otherwise a long run can get cut off mid-work with no answer.
# 0 disables the nudge. Tune via MAVERICK_STEP_BUDGET_WARNING.
_STEP_BUDGET_WARNING = _step_budget_warning_setting()

# How many of an agent's recent tool calls speculative reads predict from.
_SPECULATION_HISTORY = 20
# AI-3: one plan decision per task run, shared by every agent in it.
_PLAN_DECISIONS: dict[tuple[int, int], bool] = {}
_PLAN_DECISIONS_LOCK = threading.Lock()


def _plan_facts(agent) -> tuple[list[str], list[str], float | None]:
    """The plan so far (newest eight briefs), the agent's tool names and the
    task's spending cap (a cap, not an estimate). Unreadable parts are empty."""
    steps: list[str] = []
    try:
        for entry in agent.ctx.blackboard.by_kind("plan")[-8:]:
            brief = str(entry.content).split("brief=", 1)[-1].strip()
            # The planner's top-level brief repeats the request as its description.
            brief = brief.removeprefix("Top-level goal:").split(" Description:", 1)[0].strip()
            if brief and brief not in steps:
                steps.append(brief[:200])
    except Exception:  # an unreadable plan is described as missing
        steps = []
    try:
        tools = [t.name for t in agent.tools.all()]
    except Exception:  # no tool list: only the first action is described
        tools = []
    cap = getattr(getattr(agent.ctx, "budget", None), "max_dollars", None)
    return steps, tools, (float(cap) if isinstance(cap, int | float) else None)

# W1-44: how many blackboard entries posted by OTHER agents since this agent's
# last tool-running turn are appended to that turn's tool_results message, so
# a running orchestrator sees fan-out caps, seals, denials and sibling findings
# instead of only what the blackboard held on its first turn. 0 disables.
# Tune via MAVERICK_BB_TURN_DELTA_ENTRIES; per-entry size follows
# MAVERICK_BB_RENDER_ENTRY_CHARS.
_BB_TURN_DELTA_ENTRIES = max(0, env_int("MAVERICK_BB_TURN_DELTA_ENTRIES", 12))

# P0 filesystem-resource layer. In-process tools run in the Lightwork process,
# outside Docker's read-only bind mounts, so every workspace path they read or
# write is described here and enforced at the common dispatch chokepoint.
#
# Path-level access matters: image_edit reads input_path but writes output_path;
# wasm_run reads its module but grants write-capable preopens in dirs. A
# tool-level "mutates" flag cannot express either safely. Conditional tools
# declare their operation selector and complete known operation set. A new or
# misspelled operation is treated as write-capable for every supplied path,
# forcing this registry to be updated before a new write mode can bypass the
# policy.
@dataclass(frozen=True)
class _WorkspacePathRule:
    argument: str
    operations: frozenset[Any] | None = None
    mutates: bool = False
    mutates_subtree: bool = False
    many: bool = False
    default: str | None = None
    fixed_default: bool = False
    skip_remote_references: bool = False


@dataclass(frozen=True)
class _FileToolPolicy:
    paths: tuple[_WorkspacePathRule, ...]
    operation_argument: str | None = None
    known_operations: frozenset[Any] = frozenset()
    unknown_operation_mutates: bool = True


@dataclass(frozen=True)
class _ResolvedWorkspacePath:
    path: str
    mutates_subtree: bool = False


def _rule(
    argument: str,
    *,
    operations: set[Any] | frozenset[Any] | None = None,
    mutates: bool = False,
    mutates_subtree: bool = False,
    many: bool = False,
    default: str | None = None,
    fixed_default: bool = False,
    skip_remote_references: bool = False,
) -> _WorkspacePathRule:
    return _WorkspacePathRule(
        argument=argument,
        operations=None if operations is None else frozenset(operations),
        mutates=mutates,
        mutates_subtree=mutates_subtree,
        many=many,
        default=default,
        fixed_default=fixed_default,
        skip_remote_references=skip_remote_references,
    )


_FILE_TOOL_POLICIES: dict[str, _FileToolPolicy] = {
    "read_file": _FileToolPolicy((_rule("path"),)),
    "spreadsheet_read": _FileToolPolicy((_rule("path"),)),
    "sql_query_read": _FileToolPolicy((_rule("database"),)),
    "write_file": _FileToolPolicy((_rule("path", mutates=True),)),
    "list_dir": _FileToolPolicy((_rule("path", default="."),)),
    "str_replace_editor": _FileToolPolicy(
        (
            _rule("path", operations={"view"}),
            _rule(
                "path",
                operations={"create", "str_replace", "insert"},
                mutates=True,
            ),
        ),
        operation_argument="command",
        known_operations=frozenset({"view", "create", "str_replace", "insert"}),
    ),
    "ast_edit": _FileToolPolicy((_rule("path", mutates=True),)),
    "spreadsheet": _FileToolPolicy(
        (
            _rule("path", operations={"info", "read"}),
            _rule("path", operations={"write", "set_cell"}, mutates=True),
        ),
        operation_argument="op",
        known_operations=frozenset({"info", "read", "write", "set_cell"}),
    ),
    "sql_query": _FileToolPolicy(
        (
            # The runtime defaults None to read-only, then applies bool(value).
            # Schema-valid booleans are represented precisely here; malformed
            # direct calls are handled by the custom extractor below.
            _rule("database", operations={None, True}),
            _rule("database", operations={False}, mutates=True),
        ),
        operation_argument="read_only",
        known_operations=frozenset({None, True, False}),
    ),
    "image_content_classifier": _FileToolPolicy((_rule("file"),)),
    "ocr": _FileToolPolicy(
        (
            _rule("path", operations={"extract"}),
            # extract_url materialises a bounded temporary file directly in the
            # workspace root before sandboxed OCR, then removes it. Mutating the
            # root directory entry must still honor a root read-only overlay.
            _rule(
                ".",
                operations={"extract_url"},
                mutates=True,
                default=".",
                fixed_default=True,
            ),
        ),
        operation_argument="op",
        known_operations=frozenset({"extract", "extract_url"}),
    ),
    "transcribe_audio": _FileToolPolicy((_rule("source"),)),
    "wasm_run": _FileToolPolicy(
        (
            _rule("module", operations={"run"}),
            _rule(
                "dirs",
                operations={"run"},
                mutates=True,
                mutates_subtree=True,
                many=True,
            ),
        ),
        operation_argument="op",
        known_operations=frozenset({"run", "version"}),
    ),
    "diagram": _FileToolPolicy((_rule("out", mutates=True),)),
    "latex": _FileToolPolicy(
        (_rule("out", operations={"render"}, mutates=True, default="doc.pdf"),),
        operation_argument="op",
        known_operations=frozenset({"mathml", "render"}),
    ),
    "image_edit": _FileToolPolicy(
        (
            _rule(
                "image",
                operations={"inpaint", "variation", "upscale"},
                skip_remote_references=True,
            ),
            _rule(
                "mask",
                operations={"inpaint"},
                skip_remote_references=True,
            ),
            _rule("input_path", operations={"crop", "resize", "rotate"}),
            _rule(
                "output_path",
                operations={"crop", "resize", "rotate"},
                mutates=True,
            ),
        ),
        operation_argument="op",
        known_operations=frozenset(
            {"inpaint", "variation", "upscale", "crop", "resize", "rotate"}
        ),
    ),
    "speak": _FileToolPolicy((_rule("output", mutates=True),)),
    "html_to_app": _FileToolPolicy(
        (
            _rule(
                "dest",
                operations={"scaffold"},
                mutates=True,
                mutates_subtree=True,
            ),
        ),
        operation_argument="op",
        known_operations=frozenset({"analyze", "scaffold"}),
    ),
    "workspace_snapshot": _FileToolPolicy(
        (
            _rule("path", operations={"snapshot"}, default="."),
            _rule(
                "dest",
                operations={"restore"},
                mutates=True,
                mutates_subtree=True,
            ),
        ),
        operation_argument="op",
        known_operations=frozenset({"snapshot", "list", "restore"}),
    ),
    "android": _FileToolPolicy(
        (
            _rule("apk_path", operations={"install"}),
            _rule("out_path", operations={"screenshot"}, mutates=True),
        ),
        operation_argument="op",
        known_operations=frozenset(
            {
                "devices",
                "shell",
                "install",
                "uninstall",
                "screenshot",
                "tap",
                "input_text",
                "launch",
                "logcat",
            }
        ),
    ),
    "ios_sim": _FileToolPolicy(
        (
            _rule("app_path", operations={"install"}),
            _rule("out_path", operations={"screenshot"}, mutates=True),
        ),
        operation_argument="op",
        known_operations=frozenset(
            {
                "list_devices",
                "boot",
                "shutdown",
                "install",
                "uninstall",
                "launch",
                "terminate",
                "screenshot",
                "open_url",
            }
        ),
    ),
    # Obsidian paths are resolved against its configured vault by the custom
    # extractor below. They cannot be treated as workdir-relative raw notes.
    "obsidian": _FileToolPolicy(
        (),
        operation_argument="op",
        known_operations=frozenset({"list", "read", "create", "append", "search"}),
    ),
    # Like Obsidian, memory paths are rooted by operator configuration rather
    # than relative to workdir and need a custom extractor below.
    "memory": _FileToolPolicy(
        (),
        operation_argument="command",
        known_operations=frozenset(
            {"view", "create", "str_replace", "insert", "delete", "rename"}
        ),
    ),
    # Persistence is opt-in, but when configured the browser can checkpoint
    # after navigation, explicit save/close, or interpreter exit. The target is
    # operator-selected and therefore resolved by a custom extractor.
    "browser": _FileToolPolicy(
        (),
        operation_argument="action",
        known_operations=frozenset(
            {
                "navigate",
                "click",
                "type",
                "fill_form",
                "press",
                "scroll",
                "screenshot",
                "observe",
                "extract_text",
                "extract_html",
                "find_text",
                "wait_for",
                "go_back",
                "go_forward",
                "current_url",
                "list_links",
                "save_session",
                "close",
            }
        ),
    ),
    "oauth_helper": _FileToolPolicy(
        (),
        operation_argument="op",
        known_operations=frozenset({"authorize_url", "exchange", "refresh"}),
    ),
}

_IN_PROCESS_WORKSPACE_WRITERS = frozenset(
    {
        "write_file",
        "str_replace_editor",
        "ast_edit",
        "apply_patch",
        "spreadsheet",
        "sql_query",
        "ocr",
        "wasm_run",
        "diagram",
        "latex",
        "image_edit",
        "speak",
        "html_to_app",
        "workspace_snapshot",
        "android",
        "ios_sim",
        "obsidian",
        "memory",
        "browser",
        "oauth_helper",
    }
)

# P0 capability layer (host resource-scopes): the network tools whose URL
# argument a capability's allow_hosts globs gate at the _run_tool chokepoint,
# mapped to the arg name that carries that URL. Conservative on purpose --
# only tools whose URL arg is verified against its real input_schema appear
# here. `web_search` is intentionally absent: it takes a query/site, not a URL
# to reach. The host is parsed from the URL; a URL without a host (or a missing
# arg) skips the check. Empty allow_hosts == all (the capability's "empty ==
# allow-all" convention), so this is a no-op unless capability enforcement was
# opted in AND the active grant restricts hosts.
_NET_TOOL_URL_ARGS: dict[str, str] = {
    "http_fetch": "url",
    "browser": "url",
    "oidc": "token_url",
    "oauth_helper": "token_url",
}


def _workspace_relative_path(sandbox: Any, raw_path: str) -> str:
    """Return the canonical workspace-relative path a file tool will touch.

    Filesystem tools resolve paths against ``sandbox.workdir`` before touching
    them, collapsing ``..`` components and following symlinks. Capability path
    scopes must be checked against the same canonical workspace-relative path,
    not the raw model-supplied string.
    """
    workdir = Path(sandbox.workdir).resolve()
    target = (workdir / raw_path).resolve()
    rel = target.relative_to(workdir).as_posix()
    return rel or "."


def _default_workspace_path(
    name: str,
    rule: _WorkspacePathRule,
    args: dict[str, Any],
    sandbox: Any,
) -> str | None:
    """Resolve a tool's real default destination without executing it."""
    if name == "diagram" and rule.argument == "out":
        return f"diagram.{str(args.get('format') or 'svg').strip().lower()}"
    if name == "speak" and rule.argument == "output":
        base = Path(sandbox.workdir)
        index = 1
        while (base / f"speech-{index}.mp3").exists():
            index += 1
        return f"speech-{index}.mp3"
    return rule.default


def _normalize_file_tool_args(
    name: str,
    args: dict[str, Any],
    sandbox: Any,
) -> dict[str, Any]:
    """Freeze dynamic path defaults once for policy, audit, hooks, and dispatch.

    ``speak`` historically searched for the next free ``speech-N.mp3`` both in
    the path gate and again inside the tool. A file appearing between those two
    searches could shift the actual destination onto a protected next name.
    Supplying the admitted name explicitly makes the checked and written path
    identical.
    """
    if name != "speak" or args.get("output"):
        return args
    normalized = dict(args)
    rule = _FILE_TOOL_POLICIES["speak"].paths[0]
    output = _default_workspace_path(name, rule, normalized, sandbox)
    if output:
        normalized["output"] = output
    return normalized


def _one_workspace_access(
    sandbox: Any,
    target: str | Path,
    *,
    mutates_subtree: bool = False,
) -> _ResolvedWorkspacePath:
    return _ResolvedWorkspacePath(
        _workspace_relative_path(sandbox, str(target)),
        mutates_subtree=mutates_subtree,
    )


def _obsidian_path_accesses(
    args: dict[str, Any],
    sandbox: Any,
    *,
    mutating_only: bool,
) -> list[_ResolvedWorkspacePath] | None:
    """Resolve vault-relative notes to their actual workspace identity."""
    operation = args.get("op")
    known_operations = (
        "list",
        "read",
        "create",
        "append",
        "search",
    )
    mutates = (
        operation in ("create", "append")
        or operation not in known_operations
    )
    if mutating_only and not mutates:
        return []
    try:
        from .tools.obsidian import _resolve, _vault

        vault = _vault().resolve()
        if operation in ("read", "create", "append"):
            target = _resolve(vault, args.get("note") or "")
        else:
            target = vault
    except (OSError, RuntimeError, ValueError):
        # The tool will return its own actionable validation/config error.
        return None
    return [
        _one_workspace_access(
            sandbox,
            target,
            mutates_subtree=mutates and operation not in ("create", "append"),
        )
    ]


def _memory_path_accesses(
    args: dict[str, Any],
    sandbox: Any,
    *,
    mutating_only: bool,
) -> list[_ResolvedWorkspacePath] | None:
    """Resolve configured long-term-memory paths without granting its root."""
    command = str(args.get("command") or "").strip()
    mutating = {"create", "str_replace", "insert", "delete", "rename"}
    if mutating_only and command not in mutating:
        return []
    try:
        from .tools.memory import _memory_root, _resolve

        root = _memory_root().resolve()
        if command == "rename":
            targets = [
                _resolve(root, args.get("old_path") or args.get("path") or ""),
                _resolve(root, args.get("new_path") or ""),
            ]
        elif command in {
            "view",
            "create",
            "str_replace",
            "insert",
            "delete",
        }:
            targets = [_resolve(root, args.get("path") or "")]
        else:
            return []
    except (OSError, RuntimeError, ValueError):
        return None
    recursive = command in {"delete", "rename"}
    return [
        _one_workspace_access(
            sandbox,
            target,
            mutates_subtree=recursive,
        )
        for target in targets
    ]


def _sql_query_path_accesses(
    args: dict[str, Any],
    sandbox: Any,
    *,
    mutating_only: bool,
) -> list[_ResolvedWorkspacePath] | None:
    """Mirror sql_query's coercion and include SQLite's writable sidecars."""
    raw = args.get("database")
    if not isinstance(raw, str) or not raw:
        return None
    read_only_arg = args.get("read_only")
    read_only = True if read_only_arg is None else bool(read_only_arg)
    if mutating_only and read_only:
        return []
    path = _workspace_relative_path(sandbox, raw)
    accesses = [_ResolvedWorkspacePath(path)]
    if not read_only:
        # A writable connection may create rollback/WAL coordination files
        # beside the main DB. Protecting one of those exact evidence paths must
        # be as effective as protecting the main database itself.
        accesses.extend(
            _ResolvedWorkspacePath(path + suffix)
            for suffix in ("-journal", "-wal", "-shm")
        )
    return accesses


def _browser_path_accesses(
    args: dict[str, Any],
    sandbox: Any,
    *,
    mutating_only: bool,
) -> list[_ResolvedWorkspacePath] | None:
    """Resolve the opt-in browser state file that any live session may save."""
    del mutating_only  # every started persistent session may save at exit
    if not args.get("action"):
        return []
    from .tools.browser import _persist_enabled

    if not _persist_enabled():
        return []
    state = os.environ.get("MAVERICK_BROWSER_STATE", "")
    if not state:
        return []
    return [_one_workspace_access(sandbox, Path(state).expanduser())]


def _oauth_path_accesses(
    args: dict[str, Any],
    sandbox: Any,
    *,
    mutating_only: bool,
) -> list[_ResolvedWorkspacePath] | None:
    """Resolve the legacy plaintext OAuth sink when the sealed vault is off."""
    if args.get("op") not in ("exchange", "refresh"):
        return []
    try:
        from .oauth_vault import enabled as oauth_vault_enabled

        if oauth_vault_enabled():
            return []
    except Exception:
        # The tool will fail before persisting if vault policy cannot resolve.
        return None
    out = os.environ.get("MAVERICK_OAUTH_OUT", "").strip()
    if not out:
        return []
    del mutating_only  # exchange/refresh always overwrite the configured sink
    return [_one_workspace_access(sandbox, Path(out).expanduser())]


def _operation_in(operation: Any, options: frozenset[Any]) -> bool:
    """Membership that treats malformed/unhashable direct-call values as unknown."""
    try:
        return operation in options
    except TypeError:
        return False


def _workspace_path_accesses_for_tool(
    name: str,
    args: Any,
    sandbox: Any,
    *,
    mutating_only: bool = False,
) -> list[_ResolvedWorkspacePath] | None:
    """Canonical workspace identities and recursive-write semantics for a call."""
    if not isinstance(args, dict):
        return None

    if name == "apply_patch":
        patch_text = args.get("patch")
        if not isinstance(patch_text, str):
            return None
        from .tools.apply_patch import _files_in_patch

        return [
            _one_workspace_access(sandbox, raw)
            for raw in _files_in_patch(patch_text)
        ]

    custom = {
        "obsidian": _obsidian_path_accesses,
        "memory": _memory_path_accesses,
        "sql_query": _sql_query_path_accesses,
        "browser": _browser_path_accesses,
        "oauth_helper": _oauth_path_accesses,
    }.get(name)
    if custom is not None:
        return custom(args, sandbox, mutating_only=mutating_only)

    policy = _FILE_TOOL_POLICIES.get(name)
    if policy is None:
        return None

    operation = (
        args.get(policy.operation_argument)
        if policy.operation_argument is not None
        else None
    )
    unknown_operation = (
        policy.operation_argument is not None
        and not _operation_in(operation, policy.known_operations)
    )
    accesses: list[_ResolvedWorkspacePath] = []
    for rule in policy.paths:
        active = rule.operations is None or _operation_in(
            operation,
            rule.operations,
        )
        if not active and not unknown_operation:
            continue
        mutates = (
            policy.unknown_operation_mutates
            if unknown_operation and not active
            else rule.mutates
        )
        if mutating_only and not mutates:
            continue

        raw: Any = None if rule.fixed_default else args.get(rule.argument)
        if raw in (None, ""):
            # Unknown operations do not get another operation's synthesized
            # default. They can only be denied against paths they supplied.
            raw = None if unknown_operation else _default_workspace_path(
                name,
                rule,
                args,
                sandbox,
            )
        values = raw if rule.many and isinstance(raw, list) else [raw]
        if rule.many and raw is not None and not isinstance(raw, list):
            return None
        for value in values:
            if not isinstance(value, str) or not value:
                continue
            if rule.skip_remote_references:
                scheme = urlsplit(value).scheme.lower()
                if scheme in {"http", "https", "data"}:
                    continue
            accesses.append(
                _one_workspace_access(
                    sandbox,
                    value,
                    mutates_subtree=mutates and rule.mutates_subtree,
                )
            )

    if name == "workspace_snapshot" and operation == "snapshot":
        # Snapshot archives and their temporary sibling are writes too. The
        # store is operator-configurable and can legitimately overlap workdir.
        from .workspace_snapshot import store_dir

        accesses.append(
            _one_workspace_access(
                sandbox,
                store_dir().resolve(),
                mutates_subtree=True,
            )
        )

    # Preserve order for audit messages while merging duplicate input/output
    # identities (for example an in-place image edit).
    deduplicated: dict[str, bool] = {}
    for access in accesses:
        deduplicated[access.path] = (
            deduplicated.get(access.path, False) or access.mutates_subtree
        )
    return [
        _ResolvedWorkspacePath(path, mutates_subtree=recursive)
        for path, recursive in deduplicated.items()
    ]


def _capability_paths_for_tool(
    name: str,
    args: Any,
    sandbox: Any,
    *,
    mutating_only: bool = False,
) -> list[str] | None:
    """Canonicalize the workspace paths a tool call will touch.

    ``None`` preserves the legacy fail-soft behavior for malformed calls whose
    path cannot be located confidently; those continue to the tool's own
    validation. When ``mutating_only`` is true, read inputs are excluded so a
    protected evidence file remains readable while every declared destination
    stays immutable. ``apply_patch`` is special because its diff can name
    multiple files.
    """
    accesses = _workspace_path_accesses_for_tool(
        name,
        args,
        sandbox,
        mutating_only=mutating_only,
    )
    return None if accesses is None else [access.path for access in accesses]


def _governance_amount(args: Any) -> float | None:
    """Extract a transaction ``amount`` from tool args for the governance gate.

    The org policy's dollar-tier thresholds (``deny_above`` /
    ``require_human_above`` -- the finance delegation-of-authority gate) compare
    a transaction value, so the chokepoint passes the tool's conventional
    ``amount`` arg through to :func:`maverick.governance.evaluate`. Accepts a
    number or a numeric string; anything else (missing, non-numeric, bool)
    yields ``None`` so the gate stays inert -- exactly as before for tools that
    carry no amount.
    """
    if not isinstance(args, dict):
        return None
    v = args.get("amount")
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        try:
            return float(v.strip())
        except ValueError:
            return None
    return None


def _tool_call_failed(output: str) -> bool:
    """Did a tool result represent a failure? Used for the is_error flag and the
    per-step success score.

    Looks PAST the ``<tool_output …>`` security frame to the raw content: the
    frame begins with ``<tool_output``, so a naive leading-``ERROR`` check on the
    framed string was always False -- silently never setting ``is_error`` and
    scoring every failed tool as a success. Tool-execution errors are prefixed
    ``ERROR``; shield / hook blocks (which return UNframed) start with ``⚠``.
    """
    from .tool_results import tool_result_failed

    return tool_result_failed(output)


class _NotExecutedToolResult(str):
    """A pre-dispatch refusal returned to the model, never an executed step.

    Keeping this a string preserves the tool-result protocol while letting the
    learner distinguish a held call from a tool that ran and failed.
    """


def _cascade_draft(role: str, model: str) -> str | None:
    """The cascade's cheap first model for ``role`` (None when off)."""
    try:
        from .routing import cascade_draft_model

        return cascade_draft_model(role, model)
    except Exception:  # pragma: no cover -- routing never breaks agent setup
        return None


def _budget_error_label(e: BudgetExceeded) -> str:
    """W1-30: distinguish a genuinely unpriced model from a real cap trip.

    ``UnpricedModelError`` is a ``BudgetExceeded`` subclass (so it still
    aborts the run and skips billing), but it is not a spending/time cap
    being reached -- it is Lightwork refusing to bill a model with no
    verified price. Labelling both alike as "budget exceeded" made the
    orchestrator's "raise your cap and resume" message the reply for a
    problem raising the cap can never fix, and fed the budget-cap failure
    class -- poisoning the self-tuning budget learner with a failure that
    had nothing to do with spend.
    """
    from .tenant.provider_admission import OrganizationBudgetExceeded

    if isinstance(e, OrganizationBudgetExceeded):
        return "organization spending limit"
    return "unpriced model" if isinstance(e, UnpricedModelError) else "budget exceeded"


def _budget_stop_kind(e: BudgetExceeded) -> str:
    """The :attr:`AgentResult.stop_kind` for a cap trip / unpriced model."""
    from .tenant.provider_admission import OrganizationBudgetExceeded

    if isinstance(e, OrganizationBudgetExceeded):
        return "organization_budget"
    return "unpriced_model" if isinstance(e, UnpricedModelError) else "budget"


def _audit_summary(value: Any, limit: int = 200) -> str:
    """Short, whitespace-collapsed, length-bounded text for the audit log.

    Used for the ``input_summary`` / ``output_summary`` of TOOL_CALL /
    TOOL_RESULT events. The audit writer scrubs secrets before signing, so this
    only needs to bound size, not redact."""
    try:
        s = value if isinstance(value, str) else json.dumps(value, default=str)
    except Exception:  # pragma: no cover -- unserializable arg
        s = repr(value)
    s = " ".join((s or "").split())
    return s[:limit] + ("…" if len(s) > limit else "")


def _tool_status(output: str) -> str:
    """'error' | 'ok' for a tool result -- the TOOL_RESULT audit status."""
    return "error" if _tool_call_failed(output) else "ok"


# A single runaway tool result (multi-MB shell stdout, a giant query/file dump)
# would otherwise enter the CURRENT context window uncapped -- compaction only
# trims results behind the recent window -- blowing tokens/budget in one turn.
# Cap any single result; the model keeps the head + tail and is told how to get
# the rest. ~32 KB chars (~8k tokens) — the old 100 KB default let one result
# inject ~25k tokens that compaction never touches while it sits in the recent
# window. Scales up with the driving model's context window (32 KB is the
# 200k-window baseline; see _tool_result_limit). Tune via
# MAVERICK_MAX_TOOL_RESULT_BYTES.
_DEFAULT_TOOL_RESULT_BYTES = 32_000
_MAX_TOOL_RESULT_BYTES = max(
    2_000, env_int("MAVERICK_MAX_TOOL_RESULT_BYTES", _DEFAULT_TOOL_RESULT_BYTES))

# W1-27: cap on total "please revise your patch" round-trips per goal, across
# BOTH the apply-check block and the ground-truth test-verifier block (they
# share `_patch_revision_attempts`, which -- unlike `_patch_validated` -- is
# never reset per FINAL). 1 matches the "one retry max" every reject site
# already documented in its own comments before the #612 fix's per-FINAL
# reset silently defeated it.
_MAX_PATCH_REVISION_ATTEMPTS = max(
    1, env_int("MAVERICK_MAX_PATCH_REVISION_ATTEMPTS", 1))


def unframe_tool_output(framed: str) -> str:
    """Recover the raw tool output from the ``<tool_output …>`` frame _run_tool
    adds. Block/error messages (shield, hooks) are returned UNframed by
    _run_tool, so they pass through unchanged. Used by code_exec to feed real
    data -- not the model-facing frame -- into a sandboxed script."""
    text = framed or ""
    if not text.startswith("<tool_output "):
        return text
    nl = text.find("\n")
    if nl == -1:
        return text
    inner = text[nl + 1:]
    close = inner.rfind("\n</tool_output ")
    if close != -1:
        inner = inner[:close]  # drop the close tag (and any loop-guard note after it)
    return inner


def _tool_result_limit(model: str | None = None) -> int:
    """Effective per-result byte cap for ``model``.

    An explicit cap (MAVERICK_MAX_TOOL_RESULT_BYTES, or a test patching
    ``_MAX_TOOL_RESULT_BYTES``) wins outright, even to shrink; otherwise
    the default scales with the driving model's context window
    (context_scaling.tool_result_bytes -- 32 KB at a 200k window, larger
    above it), so a big-window model isn't stuck with a cap tuned for a
    smaller one."""
    if _MAX_TOOL_RESULT_BYTES != _DEFAULT_TOOL_RESULT_BYTES:
        return _MAX_TOOL_RESULT_BYTES
    try:
        from .context_scaling import tool_result_bytes
        return max(_MAX_TOOL_RESULT_BYTES, tool_result_bytes(model))
    except Exception:  # pragma: no cover -- sizing never blocks a tool
        return _MAX_TOOL_RESULT_BYTES


def _cap_tool_output(text: str, limit: int | None = None) -> str:
    """Bound a single tool result so one runaway can't blow the context window.

    Keeps the head (2/3) and tail (1/3) -- results often put the actionable bit
    (an error, a summary, the last rows) at the end -- with a middle marker that
    states what was dropped and how to avoid it. A no-op below the cap, so normal
    results are byte-identical. The head is preserved, so a leading ``ERROR`` is
    intact for failure classification."""
    if limit is None:
        limit = _MAX_TOOL_RESULT_BYTES
    if not isinstance(text, str):  # defensive: never crash on a non-str result
        text = "" if text is None else str(text)
    if len(text) <= limit:
        return text
    head = limit * 2 // 3
    tail = limit - head
    omitted = len(text) - head - tail
    return (
        text[:head]
        + f"\n\n... [tool output truncated: {omitted} of {len(text)} chars "
        "omitted to protect the context window. Narrow the command/query, or "
        "write the full output to a file and read it back in slices.] ...\n\n"
        + text[-tail:]
    )


def _finding_excerpt(final: str) -> str:
    """The blackboard copy of a FINAL, bounded.

    A finding is re-rendered into every later worker's first turn via
    ``bb.render(40)`` — the O(K^2) swarm re-send — so post an excerpt, not the
    whole answer. The full FINAL still reaches the parent via the spawn return
    and is persisted in the world record. Tune via ``[swarm]
    finding_post_max_chars`` or MAVERICK_FINDING_POST_MAX_CHARS, which wins
    (min 200). NB: this bounds what is
    STORED; Blackboard.render applies its own (smaller) per-entry cap at
    injection, so raising this alone does not widen worker briefs — raise
    MAVERICK_BB_RENDER_ENTRY_CHARS with it."""
    from .compaction import excerpt
    try:
        from .config import get_swarm

        configured = get_swarm()["finding_post_max_chars"]
    except Exception:  # an unreadable config never blocks posting a finding
        configured = 1500
    cap = max(200, env_int("MAVERICK_FINDING_POST_MAX_CHARS", configured))
    return excerpt(final or "", cap,
                   "finding truncated; full answer in the run record")


def _last_assistant_text(messages: list[dict]) -> str:
    """Best-effort plain text of the most recent assistant message.

    Content may be a string or a list of content blocks; pull any text out so a
    worker that yields early (synthesis reserve) still returns its partial work.
    """
    for m in reversed(messages):
        if m.get("role") != "assistant":
            continue
        c = m.get("content")
        if isinstance(c, str):
            return c.strip()
        if isinstance(c, list):
            text = " ".join(
                b.get("text", "") for b in c
                if isinstance(b, dict) and b.get("type") == "text"
            ).strip()
            if text:
                return text
    return ""


def _assemble_assistant_content(resp: Any, final_dropped_tools: bool) -> list[dict]:
    """Rebuild the assistant message content blocks from an LLM response,
    preserving Anthropic's exact thinking-block order/signatures (interleaved
    blocks must be echoed back unmodified). Extracted verbatim from
    _run_inner; see the inline council-fix notes for the ordering rules."""
    assistant_content: list[dict] = []
    ordered_blocks = getattr(resp, "content_blocks", None)
    if final_dropped_tools:
        # May 28 fix #2: the model emitted a FINAL: marker AND
        # tool_use in the same turn; we discard the tool attempt and
        # treat FINAL as the answer. Do NOT replay the model's blocks
        # here. Dropping the interleaved tool_use would merge
        # previously-separated thinking blocks into one consecutive
        # run, and on a revision pass (verifier/patch reject ->
        # continue) the re-sent turn 400s:
        #   messages.N.content.M: `thinking`/`redacted_thinking`
        #   blocks in the latest assistant message cannot be modified.
        # The tool_use can't stay either (orphan with no
        # tool_result). Omitting thinking from a turn is explicitly
        # allowed (the API auto-filters prior-turn thinking), so emit
        # a clean text-only turn. resp.text is non-empty here (guarded
        # by `resp.text and resp.tool_calls` above).
        assistant_content.append({"type": "text", "text": resp.text})
    elif ordered_blocks:
        # May 28 fix: replay the model's blocks in their ORIGINAL
        # order, COMPLETE and UNMODIFIED. Anthropic rejects a
        # rearranged thinking-block sequence on the next request —
        # the bucket-by-type rebuild in the else branch reordered
        # interleaved Opus 4.7 turns (thinking between tool_use) and
        # triggered "thinking blocks in the latest assistant message
        # cannot be modified". (The only tool_use-dropping case,
        # FINAL, is handled above — here every block is kept so the
        # tool_use blocks always have matching tool_results below.)
        for blk in ordered_blocks:
            assistant_content.append(dict(blk))
    else:
        # May 26 council fix: emit ONE thinking block per original
        # block, preserving each block's exact signature. Concatenating
        # text but keeping only the first signature corrupted multi-
        # block interleaved thinking on Opus 4.7 — the signature is
        # derived from the EXACT text of its block. Falls back to
        # the legacy single-block path when thinking_blocks is empty
        # but resp.thinking is set (older mocks / non-Anthropic).
        thinking_blocks = getattr(resp, "thinking_blocks", None) or []
        if thinking_blocks:
            # May 26 council fix (API audit #2): include the block
            # EVEN IF the text is empty as long as a signature is
            # present. Anthropic still requires the signature-bearing
            # block to be echoed back to maintain continuity. The old
            # `if resp.thinking:` check at the elif below would drop
            # empty-text-signature pairs entirely.
            for tb_text, tb_sig in thinking_blocks:
                if not tb_text and not tb_sig:
                    continue
                block_dict: dict = {"type": "thinking", "thinking": tb_text}
                if tb_sig:
                    block_dict["signature"] = tb_sig
                assistant_content.append(block_dict)
        elif resp.thinking or getattr(resp, "thinking_signature", None):
            sig = getattr(resp, "thinking_signature", None)
            thinking_block: dict = {
                "type": "thinking", "thinking": resp.thinking or "",
            }
            if sig:
                thinking_block["signature"] = sig
            assistant_content.append(thinking_block)
        if resp.text:
            assistant_content.append({"type": "text", "text": resp.text})
        for tc in resp.tool_calls:
            assistant_content.append(
                {"type": "tool_use", "id": tc.id, "name": tc.name, "input": tc.input}
            )
    return assistant_content


@dataclass
class AgentResult:
    final: str | None = None
    blocked_on_user: bool = False
    error: str | None = None
    role: str = ""
    name: str = ""
    # Verifier signals (only populated on the orchestrator's FINAL).
    verifier_confidence: float = 1.0
    verifier_critique: str = ""
    # Wave 12: rendered unified diff produced by the FINAL handler
    # (SEARCH/REPLACE blocks applied + `git diff` rendered, or unified
    # diff extracted from FINAL). Best-of-N reads this directly instead
    # of re-extracting from prose at orchestrator.py:364 — the prior
    # path silently dropped SR-only candidates that produced a perfect
    # rendered diff but had no `--- a/` substring in `result`.
    final_patch: str | None = None
    # W1-33: WHY the loop stopped, as a structured tag the orchestrator
    # branches on instead of substring-matching ``error`` (a review-checkpoint
    # stop used to be reported as "a HALT file is present"). One of
    # "killswitch" | "review_checkpoint" | "budget" | "unpriced_model" |
    # "usage_accounting"; None for every other outcome. ``stop_detail``
    # carries the machine-readable specifics (e.g. "tool_calls=100").
    stop_kind: str | None = None
    stop_detail: str = ""
    tool_execution_unknown: bool = False


def _final_uncertainty_reasons(
    *,
    verifier_rejected: bool,
    verifier_incomplete: bool,
    disagreement: float,
    coding: bool,
    no_final_marker: bool = False,
) -> list[str]:
    """Reasons the orchestrator cannot cleanly stand behind a FINAL.

    Empty list means nothing to flag: a clean verification, or a
    sub-agent / coding-mode answer we must not wrap in prose (it may be a
    patch). The swarm-disagreement signal is added only as colour when we
    are *already* flagging uncertainty, so a reconciled-and-verified
    answer is never noised up.
    """
    if coding:
        return []
    reasons: list[str] = []
    if no_final_marker:
        reasons.append(
            "the response never included a FINAL: marker, so the verifier "
            "and patch-validation pipeline did not run"
        )
    if verifier_rejected:
        reasons.append("an internal self-check did not pass after one revision")
    if verifier_incomplete:
        reasons.append("verification did not finish within the budget")
    if reasons and disagreement >= 0.8:
        reasons.append(f"parallel attempts disagreed (entropy {disagreement:.2f})")
    return reasons


def _final_with_uncertainty_note(final: str | None, reasons: list[str]) -> str | None:
    """Prepend a brief honesty caveat to a user-facing answer.

    Leaves the answer body untouched; only adds a leading note so an
    unverified result is not handed over as if it were confirmed. No-op
    when there is nothing to flag or there is no answer text.
    """
    if not final or not reasons:
        return final
    note = (
        "⚠️ I could not fully verify this answer: "
        + "; ".join(reasons)
        + ". Treat it with caution."
    )
    return note + "\n\n" + final


_HIGH_RISK_FINAL_MARKERS = (
    "```",          # fenced code block
    "diff --git",   # unified diff
    "<<<<<<<",      # SEARCH/REPLACE edit block
    "--- a/",       # diff hunk header
)


def _risk_proportional_verify_enabled() -> bool:
    """Opt-in, off by default. Flipped on via
    ``MAVERICK_RISK_PROPORTIONAL_VERIFY=1`` or ``[verification]
    risk_proportional = true`` in config. When on, the orchestrator may
    skip the LLM verifier on clearly low-risk answers -- SWE-AF's
    ``needs_deeper_qa`` idea: spend verification where it matters.
    """
    if os.environ.get("MAVERICK_RISK_PROPORTIONAL_VERIFY", "").strip().lower() in {
        "1", "true", "yes", "on",
    }:
        return True
    try:
        from .config import load_config
        cfg = (load_config() or {}).get("verification") or {}
        return bool(cfg.get("risk_proportional"))
    except Exception:
        return False


def _final_is_low_risk(
    final: str | None, *, coding: bool, tool_calls: int, max_chars: int = 800,
) -> bool:
    """Cheap, conservative test: safe to skip LLM verification on this answer?

    Low-risk means a short, prose-only answer the agent reached without
    touching any tools -- a pure-knowledge reply. A coding task, any tool
    use, an embedded code block / diff / edit, or a long multi-part answer
    all fall through to full verification. Intentionally narrow: it gates
    a quality check, so it only fires when skipping is clearly safe.
    ``max_chars`` is the length past which an answer counts as long-form.
    """
    if coding or not final:
        return False
    if tool_calls > 0:
        return False
    text = final.strip()
    if len(text) > max_chars:
        return False
    return not any(marker in text for marker in _HIGH_RISK_FINAL_MARKERS)


# A chat reply is read by the person who asked; the length cap that marks a
# standalone goal's answer as long-form deliverable is far too tight for prose.
_CHAT_LOW_RISK_MAX_CHARS = 12_000


def _verify_chat_answers_enabled() -> bool:
    """Whether a conversational run still waits on the self-verifier for a
    plain prose answer. Off by default: a chat reply reached with no tool use,
    no code, and no edit block only gained a second model round trip before
    the person could read it. ``MAVERICK_VERIFY_CHAT_ANSWERS=1`` or
    ``[verification] verify_chat_answers = true`` turns it back on. Tool use,
    code, edit blocks, and long-form answers are always verified."""
    raw = os.environ.get("MAVERICK_VERIFY_CHAT_ANSWERS", "").strip().lower()
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    try:
        from .config import load_config
        cfg = (load_config() or {}).get("verification") or {}
        return bool(cfg.get("verify_chat_answers"))
    except Exception:
        return False


def _answer_streaming_enabled() -> bool:
    """``[chat] stream_answers`` / ``MAVERICK_STREAM_ANSWERS``: on by default."""
    raw = os.environ.get("MAVERICK_STREAM_ANSWERS", "").strip().lower()
    if raw in {"0", "false", "no", "off"}:
        return False
    if raw in {"1", "true", "yes", "on"}:
        return True
    try:
        from .config import load_config
        value = ((load_config() or {}).get("chat") or {}).get("stream_answers")
        return True if value is None else bool(value)
    except Exception:
        return True


def _accepts_keyword(fn: Any, name: str) -> bool:
    """True when ``fn`` takes ``name`` (or ``**kwargs``); a scripted stand-in
    model without the parameter simply gets the buffered call."""
    try:
        params = inspect.signature(fn).parameters
    except (TypeError, ValueError):
        return False
    return name in params or any(
        p.kind is inspect.Parameter.VAR_KEYWORD for p in params.values()
    )


def _strip_final_marker(text: str) -> str:
    """The answer after the last ``FINAL:`` marker, or the whole text before
    one has arrived."""
    try:
        from .coding_mode import find_final_marker_end
        end = find_final_marker_end(text)
    except Exception:  # pragma: no cover -- preview only, never the turn
        end = None
    return text[end:].lstrip() if end is not None else text


def _token_boundary(text: str, limit: int) -> int:
    """The end of the last complete token before ``limit``: a whitespace
    position, or just after a CJK / full-width character (those scripts do not
    separate words with spaces, and a credential is never written in them).
    0 when there is none."""
    for i in range(min(limit, len(text)) - 1, -1, -1):
        c = text[i]
        if c in " \n\t":
            return i
        if ord(c) >= 0x2E80:
            return i + 1
    return 0


class _ReplayedDecision(Exception):
    """A production twin's answer to a human approval gate (no human asked)."""

    def __init__(self, granted: bool) -> None:
        super().__init__("replayed approval decision")
        self.granted = granted


# ``NAME=`` / ``NAME:`` of a named secret (mirrors secret_detector's env_secret)
# followed by an opening quote.
_OPEN_SECRET_QUOTE = re.compile(
    r"[^\S\n]*(?:export\s+)?[A-Z][A-Z0-9_]*(?:TOKEN|KEY|SECRET|PASSWORD|PASS|CREDENTIAL)"
    r"[A-Z0-9_]*\s*[:=]\s*(?P<q>[\"'])")


class _PartialAnswerStream:
    """Mirror the root agent's answer into the goal's result column while the
    model is still writing it, so the chat page shows text within a second
    instead of after the whole turn (and its verification) completes.

    Writes at most once per ``MIN_INTERVAL`` seconds and stops growing at
    ``MAX_CHARS``. Each flush redacts the *whole* text received so far, then
    shows only complete whitespace-delimited tokens that end at least
    ``HOLDBACK`` characters before the tail: a credential is one token, so a
    half-written one is never displayed, and a complete one is redacted whole.
    A named secret whose quoted value is still open (``API_TOKEN="several words``)
    is held back from the start of its line until the quote closes. The last
    flush drops the holdback only when the model finished normally: a stream cut
    at the token limit or at ``MAX_CHARS`` may end mid-credential.
    Every flush also passes the Shield output scan; a block withdraws the
    preview for the rest of the turn. The orchestrator's final write replaces
    the preview. Fails closed: a redaction, scan or write error ends the
    preview, never the turn.
    """

    MIN_INTERVAL = 0.35
    HOLDBACK = 96
    MAX_CHARS = 60_000

    def __init__(self, world: Any, goal_id: int, shield: Any = None) -> None:
        self._world = world
        self._goal_id = goal_id
        self._shield = shield
        self._text = ""
        self._shown = ""
        self._last_flush = float("-inf")  # the first flush is never throttled
        self._failed = False
        self.writes = 0

    def __call__(self, delta: Any) -> None:
        if self._failed or not isinstance(delta, str) or not delta:
            return
        room = self.MAX_CHARS - len(self._text)
        if room > 0:
            self._text += delta[:room]
        now = time.monotonic()
        if now - self._last_flush >= self.MIN_INTERVAL:
            self._flush(now, holdback=self.HOLDBACK)

    def reset(self) -> None:
        """A new model attempt (provider failover) starts a fresh answer; the
        next flush replaces what the page shows."""
        self._text = ""

    def finish(self, *, complete: bool = True) -> None:
        """Flush once the turn has ended: everything received when the model
        finished normally, else still without the (possibly cut) tail."""
        whole = complete and len(self._text) < self.MAX_CHARS
        self._flush(time.monotonic(), holdback=0 if whole else self.HOLDBACK)

    def _flush(self, now: float, *, holdback: int) -> None:
        if self._failed:
            return
        try:
            from .safety.secret_detector import redact
            text = _strip_final_marker(self._text)
            if holdback:
                # A named secret's quoted value still open on the last line
                # would be redacted only up to its first space: hold the line.
                last = text.rfind("\n") + 1
                m = _OPEN_SECRET_QUOTE.match(text, last)
                if m and m.group("q") not in text[m.end():]:
                    text = text[:last]
            visible, _ = redact(text)
        except Exception:
            self._failed = True  # never show an unredacted preview
            return
        if holdback:
            # Only whole tokens that ended before the held-back tail.
            visible = visible[:_token_boundary(visible, len(visible) - holdback)]
        # A marker cut mid-word ("FI", "FINAL") is not an answer yet.
        if not visible.strip() or visible == self._shown or "FINAL:".startswith(visible.strip()):
            return
        if self._shield is not None:
            try:
                blocked = not self._shield.scan_output(visible).allowed
            except Exception:
                blocked = True
            if blocked:
                self._failed = True
                self._withdraw()
                return
        try:
            self._world.set_goal_partial_result(self._goal_id, visible)
        except Exception:
            self._failed = True
            log.debug("answer preview write failed for goal #%s", self._goal_id, exc_info=True)
            return
        self._shown = visible
        self._last_flush = now
        self.writes += 1

    def _withdraw(self) -> None:
        """Remove an already-shown preview (a later flush was blocked)."""
        if self._shown:
            try:
                self._world.set_goal_partial_result(self._goal_id, "")
            except Exception:
                log.debug("answer preview withdraw failed for goal #%s",
                          self._goal_id, exc_info=True)


class Agent:
    def __init__(
        self,
        ctx: SwarmContext,
        role: str,
        brief: str,
        model_override: str | None = None,
        depth: int = 0,
        parent: Agent | None = None,
        max_steps: int | None = None,
        capability=None,
        domain: str | None = None,
        persona: str | None = None,
        knowledge_sources: list[str] | None = None,
        domain_effort: str | None = None,
        autonomy=None,
        is_goal_root: bool = False,
    ):
        self.ctx = ctx
        self.role = role
        self.brief = brief
        self.depth = depth
        self.parent = parent
        # W1-24: whether this agent owns the GOAL's FINAL synthesis --
        # verification, the review checkpoint, prompt-cache prewarm, the
        # thinking budget, and the base system template all keyed on
        # role == "orchestrator" alone, so a domain-routed goal's depth-0
        # root (role=profile.name, e.g. "finance_controller") silently
        # skipped every one of them. agent_from_profile passes this
        # explicitly for its depth==0 construction; plain role=="orchestrator"
        # still qualifies too (the two are OR'd at each use, not merged here,
        # so this flag never needs to fake a role rename).
        self._is_goal_root = bool(is_goal_root)
        # Long-horizon review checkpoint (root only; opt-in). Built once per
        # agent from config; None when unconfigured (the common case) so the
        # turn gate stays a no-op. The reviewer uses the consent/approval path
        # with silent auto-approval disabled, so crossing an interval requires
        # an explicit operator decision to continue.
        self._review_checkpoint = None
        from .immune import twin as _twin_mod
        if (role == "orchestrator" or self._is_goal_root) and not _twin_mod.active():
            try:
                from .review_checkpoint import consent_review, from_config
                _cp = from_config(
                    review=lambda event: consent_review(event, goal_id=self.ctx.goal_id)
                )
                if _cp.policy.is_active():
                    self._review_checkpoint = _cp
            except Exception:  # pragma: no cover -- never block agent construction
                self._review_checkpoint = None
        # Agent compartments: the domain/sector this agent belongs to. The
        # factory's spawn-from-profile sets it; otherwise a child inherits its
        # parent's domain so a Rung-2 sector seal catches the whole sub-tree.
        # None == unsectored (the orchestrator and ad-hoc agents).
        self.domain = domain if domain is not None else getattr(parent, "domain", None)
        # Per-agent autonomy profile (the agent-as-employee authority dial,
        # maverick.agent_autonomy). The factory's spawn-from-profile sets it from
        # the pack's [autonomy] block; a child inherits its parent's so a sub-tree
        # shares the hire's authority. None == the default SUGGEST profile.
        self._autonomy = autonomy if autonomy is not None else getattr(parent, "_autonomy", None)
        # Workforce overrides are tied to the profile's original hire, not to an
        # ad-hoc child role. Otherwise a model-selected spawn_subagent role could
        # layer a more permissive [workforce.agents.<role>] override over an
        # inherited low-authority profile. Explicit profile spawns establish a new
        # override anchor; inherited profiles keep their parent's anchor.
        self._autonomy_name = (
            role if autonomy is not None
            else getattr(parent, "_autonomy_name", role)
        )
        # Optional domain-pack persona, appended to the system prompt below.
        self._domain_persona = persona
        # Domain knowledge collections this agent may query (the DomainProfile's
        # knowledge_sources). Children inherit the parent's, like ``domain``.
        self.knowledge_sources = (
            knowledge_sources if knowledge_sources is not None
            else list(getattr(parent, "knowledge_sources", []) or [])
        )
        # P0 identity layer: the capability grant this agent runs under. An
        # explicit arg (passed by an attenuating spawn) wins; otherwise inherit
        # the run's root grant; otherwise the depth-0 orchestrator mints the
        # root grant from config when enforcement is enabled. None ==
        # unrestricted, so enforcement is a no-op unless opted in.
        self.capability = self._resolve_capability(capability)
        # Verified peer handoffs install an attenuated task grant here; _run_tool
        # intersects it with ambient authority so verified delegations are bound
        # to execution instead of existing only as model-facing text.
        self._handoff_capability = None
        # Wave 11: Scale Labs' Pro empirical study (arxiv 2509.16941)
        # shows "most successful solutions resolve in ~25 rounds; long-
        # tail iteration past that has diminishing returns." Allow ops
        # to override globally via MAVERICK_MAX_STEPS, default 25.
        from .config import get_agent_max_steps
        self.max_steps = get_agent_max_steps(max_steps)
        self.name = f"{role}-{depth}-{uuid.uuid4().hex[:6]}"
        # Register before tool construction: bus tool closures enforce the
        # SwarmContext roster on every send/receive/delegation. If an unusual
        # test/adapter context lacks the hook, legacy tool behavior is retained;
        # a real SwarmContext registration error fails the bus closed while the
        # rest of the agent can still run.
        # Information flow: a child's brief comes from its parent's context.
        if parent is not None and (tracker := getattr(self.ctx, "ifc", None)) is not None:
            tracker.inherit(self.name, parent.name)
        _register_bus_agent = getattr(self.ctx, "register_bus_agent", None)
        if callable(_register_bus_agent):
            try:
                _register_bus_agent(self.name)
            except Exception as e:
                log.warning("agent bus roster registration failed for %s: %s", self.name, e)

        # Resolve the model BEFORE building the system prompt: the self-harness
        # addendum layer (_with_harness_addendum) recalls guidance keyed on
        # self.model, so self.model must exist when _build_system runs.
        # Otherwise recall_addendum(self.model) raises AttributeError, the
        # addendum's except swallows it, and the learned guidance is silently
        # dropped from every prompt -- the feature becomes a no-op.
        self.model = model_override or model_for_role(role)
        # Cascaded routing ([models] cascade): a role with no model of its own
        # starts its turns on a cheaper model and moves to self.model on the
        # kernel's low-confidence signals (see _cascade_turn_model).
        self._cascade_draft = None if model_override else _cascade_draft(role, self.model)
        self._cascade_escalated = False
        self.tools = self._build_tools()
        self.system = self._build_system()
        # Per-role reasoning effort (opt-in; None unless configured). Resolved
        # once against this agent's role + model so the cost/latency lever rides
        # every LLM call this agent makes. Model-gated -> never 400s.
        from .effort import effort_for_role
        self.effort = effort_for_role(role, self.model, pack_default=domain_effort)
        # Tracks whether we've already given one LLM-verifier-driven
        # revision pass for this agent run. Separate from
        # `_already_verified` so revised FINALs can be re-verified once
        # without permitting repeated reject/revise loops.
        self._verifier_revision_used = False

        # Process-reward model: scores each step's promise/progress. Resolved
        # from env (MAVERICK_PRM=null|heuristic|remote); default NullPRM is a
        # no-op, so this is off unless an operator opts in. Scores are emitted
        # to the blackboard (kind="prm") as an observability signal — the loop
        # does not gate on them, so a misconfigured PRM can't stall a run.
        from .prm import build_from_env
        self._prm = build_from_env()
        self._prm_enabled = type(self._prm).__name__ != "NullPRM"
        self._last_step_score = 0.5
        from .prm_guidance import PromiseWindow
        self._promise_window = PromiseWindow()
        self._last_prm_nudge_step = -100
        # Live-spend mirror throttle (#614): the root agent periodically
        # mirrors running totals onto its open episode row so `maverick runs`
        # / `maverick budget` reflect accruing mid-run spend instead of
        # $0.00 / 0 tools. Throttled to once per _SPEND_MIRROR_INTERVAL s.
        self._last_spend_mirror = 0.0
        # Loop guard: current consecutive failure streak. Grows only while the
        # exact same tool call fails with the same raw error; any intervening
        # different call or success starts a new streak.
        self._tool_fail_streak: dict[str, int] = {}
        # Loop guard, suppression tier: the last (call key, output sha256,
        # consecutive count) triple, success or failure. Feeds the identical-
        # result nudge and, past _LOOP_GUARD_BLOCK, the pre-exec suppression.
        self._identical_streak: tuple[str, str, int] | None = None
        # W1-25: whether the one-shot "please restate with FINAL:" nudge has
        # already been used this run. A text-only response with no tool
        # calls and no FINAL: marker skips the ENTIRE verify/patch-check/
        # STOP-hook pipeline (it only runs inside the FINAL-marker branch);
        # give a marker-less model one chance to comply before accepting its
        # answer un-nudged.
        self._final_marker_nudge_used = False

    def _resolve_capability(self, explicit):
        """Pick this agent's capability grant.

        A readable policy that leaves enforcement disabled intentionally yields
        ``None`` (legacy unrestricted behavior). Once enforcement is enabled or
        policy intent is uncertain, root-grant failures yield an explicit
        deny-all grant instead of silently restoring unrestricted authority.
        """
        if explicit is not None:
            return self._narrowed_to_run_grant(explicit)
        inherited = getattr(self.ctx, "capability", None)
        if inherited is not None:
            return inherited
        if self.depth != 0:
            return None
        principal = f"user:{getattr(self.ctx, 'user_id', None) or 'local'}"
        try:
            from .capability import (
                Capability,
                capability_enforced,
                capability_from_config,
            )
            if not capability_enforced():
                return None
            root = capability_from_config(
                principal=principal,
                channel=getattr(self.ctx, "channel", None),
                user_id=getattr(self.ctx, "user_id", None),
            )
            if not isinstance(root, Capability):
                raise TypeError("capability root mint returned an invalid grant")
            # Stash on the shared context so spawned children inherit + attenuate.
            try:
                self.ctx.capability = root
            except Exception:
                pass
            return root
        except Exception:
            log.exception(
                "capability root-grant resolution failed for %s; denying all",
                principal,
            )
            # A failure importing the capability module itself should stop agent
            # construction rather than fabricate authority. Normal mint/config
            # failures reach this import successfully and receive deny-all.
            from .capability import deny_all_capability

            root = deny_all_capability(principal)
            try:
                self.ctx.capability = root
            except Exception:
                pass
            return root

    def _narrowed_to_run_grant(self, explicit):
        """An explicit grant (a domain pack's envelope) only ever NARROWS the
        run's own grant -- it never replaces it (UX-012).

        The run's grant is what the requester is allowed to do: the caller's
        capability, or at the goal root the one minted from the requester's RBAC
        role, ``[security]`` ACL and active regulatory attenuation. Returning the
        pack's envelope alone let a read-only user (or a HIPAA deployment that
        forbids outbound tools) regain those tools by starting the task through a
        specialist pack. With no run grant (enforcement off, nothing supplied) the
        pack's envelope stays the boundary, as before.
        """
        ambient = getattr(self.ctx, "capability", None)
        if ambient is None and self.depth == 0:
            # Mint (and stash on the context) the requester's grant exactly as a
            # plain orchestrator root would; None when enforcement is off.
            ambient = self._resolve_capability(None)
        if ambient is None or ambient is explicit:
            return explicit
        return ambient.intersect(explicit, principal=explicit.principal)

    @property
    def checkpoint_id(self) -> str:
        """Stable identity for durable checkpointing, distinct from ``name``.

        ``name`` carries a per-process random suffix (for blackboard / agent-bus
        uniqueness), so it can't key a checkpoint that must survive a
        fresh-process resume. The depth-0 agent is the single orchestrator of
        its episode, so ``"{role}-0"`` is stable and unique within
        (goal_id, episode_id). Phase 2 will extend this for spawned children.
        """
        return f"{self.role}-{self.depth}"

    def _knowledge_runtime_principal(self) -> str:
        """Return the current capability principal for governed knowledge."""

        cap = self._effective_capability("knowledge_search")
        principal = getattr(cap, "principal", None)
        if isinstance(principal, str) and principal.startswith(("agent:", "worker:", "user:")):
            return principal
        return f"agent:{self.name}"

    def _knowledge_actor_authority(
        self,
        tenant: str,
        principal: str,
        action: str,
        purpose: str,
    ):
        """Resolve the agent's live roster/capability into an exact authority."""

        from .knowledge_access import KnowledgeActorAuthority
        from .paths import canonical_tenant_id

        canonical = canonical_tenant_id(tenant)
        if not self.ctx.knows_bus_agent(self.name):
            raise PermissionError("agent is no longer in the current run roster")
        if principal != self._knowledge_runtime_principal():
            raise PermissionError("knowledge actor changed during the operation")
        cap = self._effective_capability("knowledge_search")
        capability_sha256 = "capability-enforcement-off"
        if cap is not None:
            if not cap.permits("knowledge_search"):
                raise PermissionError("current capability does not allow knowledge search")
            from .revocation import revoked_principal

            principals = (
                cap.revocation_principals()
                if hasattr(cap, "revocation_principals")
                else (cap.principal,)
            )
            if revoked_principal(principals) is not None:
                raise PermissionError("current knowledge principal is revoked")
            try:
                capability_sha256 = hashlib.sha256(cap.signing_bytes()).hexdigest()
            except (AttributeError, TypeError, ValueError) as exc:
                raise PermissionError("current knowledge capability is malformed") from exc
        material = {
            "schema": "lightwork.agent-knowledge-authorization.v1",
            "tenant": canonical,
            "principal": principal,
            "action": action,
            "purpose": purpose,
            "agent_instance": self.name,
            "role": self.role,
            "domain": self.domain,
            "goal_id": self.ctx.goal_id,
            "episode_id": getattr(self.ctx, "episode_id", 0),
            "source_selectors": list(self.knowledge_sources),
            "capability_sha256": capability_sha256,
        }
        authorization_sha256 = hashlib.sha256(
            json.dumps(
                material,
                allow_nan=False,
                separators=(",", ":"),
                sort_keys=True,
            ).encode()
        ).hexdigest()
        return KnowledgeActorAuthority(
            tenant=canonical,
            principal=principal,
            action=action,
            purpose=purpose,
            authorization_sha256=authorization_sha256,
        )

    def _governed_knowledge_service(self):
        """Lazily bind the installed APP10 registry/projector to this agent."""

        current = getattr(self, "_knowledge_access_service", None)
        if current is not None:
            return current
        from .knowledge_access import KnowledgeAccessService

        base = getattr(self.ctx, "knowledge_access_service", None)
        if isinstance(base, KnowledgeAccessService):
            current = base.bind_actor_authority(self._knowledge_actor_authority)
        else:
            from .knowledge_content_projection import ExistingKnowledgeContentProjector
            from .research_registry import ResearchRegistry

            current = KnowledgeAccessService(
                registry=ResearchRegistry(),
                content_resolver=ExistingKnowledgeContentProjector(
                    knowledge_base=getattr(self.ctx, "knowledge", None),
                ),
                actor_authority_resolver=self._knowledge_actor_authority,
            )
        self._knowledge_access_service = current
        return current

    def _build_tools(self) -> ToolRegistry:
        # Honor [capabilities] from config: these gate the optional
        # high-impact tools (computer_use / browser / web_search / mobile).
        # Without this, enabling them in config (or the wizard) was a no-op --
        # base_registry's enable_* flags defaulted off and nothing set them.
        # The [security] ACL still applies on top (a capability can be enabled
        # but a tool still denied).
        try:
            from .config import get_capabilities
            caps = get_capabilities()
        except Exception:  # pragma: no cover -- never block tool build on config
            caps = {}
        reg = base_registry(
            self.ctx.world,
            self.ctx.sandbox,
            mcp_clients=self.ctx.mcp_clients,
            goal_id=self.ctx.goal_id,
            channel=self.ctx.channel,
            user_id=self.ctx.user_id,
            budget=self.ctx.budget,
            enable_computer_use=bool(caps.get("computer_use", False)),
            enable_browser=bool(caps.get("browser", False)),
            enable_web_search=bool(caps.get("web_search", False)),
            enable_mobile_tools=bool(caps.get("mobile_tools", False)),
            enable_ros=bool(caps.get("ros", False)),
        )
        # Cross-agent bus tools, bound to this agent's id so send records
        # the right sender and recv drains the right inbox.
        reg.register(send_to_agent(self.name, ctx=self.ctx))
        # recv is handoff-aware (verifies a signed delegation via the run's
        # handoff authority); delegate_to_agent is the producer, offered only
        # when capability enforcement is on -- otherwise a handoff is just a
        # plain message and send_to_agent already covers it.
        reg.register(recv_from_agent(self.name, agent=self, ctx=self.ctx))
        from .capability import capability_enforced
        if capability_enforced():
            reg.register(delegate_to_agent(self))
        if self.depth < self.ctx.max_depth:
            reg.register(spawn_subagent_tool(self))
            reg.register(spawn_swarm_tool(self))
            # The bridge from the suite roster to the running fleet: deploy a
            # curated domain pack as a specialist child (persona + compartment +
            # attenuated envelope), and discover what's available.
            reg.register(spawn_specialist_tool(self))
            reg.register(list_specialists_tool(self))
        # Per-domain knowledge is served only through APP10's current registry
        # boundary. The legacy vector handle may still exist for ingestion and
        # migration, but it is never a model-visible retrieval shortcut.
        sources = self.knowledge_sources or ([self.domain] if self.domain else [])
        try:
            from .config import knowledge_access_enabled

            governed_knowledge_enabled = knowledge_access_enabled()
        except Exception:
            governed_knowledge_enabled = False
        if sources and governed_knowledge_enabled:
            from .paths import current_tenant_id_strict
            from .tools.knowledge import knowledge_search_tool

            reg.register(
                knowledge_search_tool(
                    self._governed_knowledge_service,
                    sources,
                    tenant_resolver=current_tenant_id_strict,
                    actor_resolver=self._knowledge_runtime_principal,
                    purpose="product_research",
                    job_id_resolver=lambda: (
                        "knowledge-"
                        f"{self.ctx.goal_id}-{getattr(self.ctx, 'episode_id', 0)}-"
                        f"{uuid.uuid4().hex}"
                    ),
                )
            )
        # Self-learning: bound to this agent so governed capability discovery
        # can run by default. Executable tool creation and MCP acquisition stay
        # behind their separate high-authority opt-ins.
        try:
            from . import self_learning
            if self_learning.enabled():
                from .tools.learn import learn_capability
                reg.register(learn_capability(self))
        except Exception as e:  # pragma: no cover -- never block tool build
            log.debug("self_learning tool registration skipped: %s", e)
        # Programmatic tool calling (opt-in): a sandboxed Python script that
        # orchestrates declared tool calls, keeping their raw outputs out of the
        # model's context. Powerful (runs code + tools), so off unless enabled
        # via [capabilities] code_exec or MAVERICK_CODE_EXEC.
        code_exec_on = caps.get("code_exec", False) or (
            os.environ.get("MAVERICK_CODE_EXEC", "").strip().lower()
            in {"1", "true", "yes", "on"}
        )
        if code_exec_on:
            from .tools.code_exec import code_exec_tool
            reg.register(code_exec_tool(self))
        # Deferred tool loading (default ON): the model's per-turn catalog is
        # what [tools] exposure says, and the rest hides behind the find_tools
        # meta-tool. run() still executes ANY registered tool, so execution
        # semantics are unchanged; only what is DESCRIBED up front shrinks.
        #   exposure = "core" (default): a role-aware core set
        #     (tools.core_tool_names + [tools] core_extra), or -- for an agent
        #     running under a pack's allow_tools -- exactly that allow list
        #     (measured: 237 tools / ~188 KB / ~47k tokens rode EVERY turn
        #     for every role, ~95% of the first call's input).
        #   exposure = "full": everything except base_registry's marked SaaS
        #     long tail, subject to the model's tool ceiling. Overflow stays
        #     discoverable through find_tools.
        # A tool the agent's capability refuses (a pack's deny_tools, an
        # allow list it is outside of, a risk ceiling) is neither shown nor
        # discoverable. Disable ordinary deferral via [capabilities]
        # deferred_tools = false or MAVERICK_DEFERRED_TOOLS=0; a model's tool
        # ceiling and the agent's capability still constrain what it sees.
        env_dt = os.environ.get("MAVERICK_DEFERRED_TOOLS", "").strip().lower()
        if env_dt in {"0", "false", "no", "off"}:
            deferred_on = False
        elif env_dt in {"1", "true", "yes", "on"}:
            deferred_on = True
        else:
            deferred_on = bool(caps.get("deferred_tools", True))
        deferrable = getattr(reg, "deferrable_names", None) or set()
        if deferrable:
            from .tools.find_tools import find_tools
            reg.register(find_tools(reg))
            if deferred_on:
                core, hidden = self._tool_exposure_sets(reg, deferrable)
                reg.enable_deferred(core=core, hidden=hidden)
            elif self.capability is not None:
                # Disabling deferral widens the visible catalog, never a pack's
                # authority. Keep refused names out of both model and search.
                registered = {tool.name for tool in reg.all()}
                hidden = {name for name in registered if not self.capability.permits(name)}
                reg.enable_deferred(core=registered - hidden, hidden=hidden)
        return reg

    def _tool_catalog_for_model(self, model: str) -> list[dict]:
        """Offer capped providers a discoverable catalog, including on failover."""
        from .providers.openai_provider import _OPENAI_MAX_TOOLS

        if len(self.tools.exposed()) <= _OPENAI_MAX_TOOLS:
            return self.tools.to_anthropic()
        from .llm import _fallback_chain_for_dispatch, _parse_spec

        provider, model_id = _parse_spec(model)
        openai_style = {
            "openai", "azure", "gemini", "openrouter", "deepseek", "xai",
            "moonshot", "ollama", "vllm", "tgi", "openai_compatible",
        }
        if provider in openai_style:
            return self.tools.to_anthropic(max_tools=_OPENAI_MAX_TOOLS)
        fallbacks = _fallback_chain_for_dispatch(model, f"{provider}:{model_id}")
        if any(_parse_spec(candidate)[0] in openai_style for candidate in fallbacks):
            return self.tools.to_anthropic(max_tools=_OPENAI_MAX_TOOLS)
        return self.tools.to_anthropic()

    def _tool_exposure_sets(self, reg: ToolRegistry, deferrable) -> tuple[set[str], set[str]]:
        """(visible core names, never-discoverable names) for this agent."""
        try:
            from .config import get_tool_exposure
            exposure = get_tool_exposure()
        except Exception:  # pragma: no cover -- config never blocks tool build
            exposure = {"exposure": "core", "core_extra": []}
        registered = {t.name for t in reg.all()}
        cap = getattr(self, "capability", None)
        if exposure["exposure"] == "full":
            core = registered - set(deferrable)
        else:
            from .tools import core_tool_names
            allow = set(getattr(cap, "allow_tools", ()) or ()) if cap is not None else set()
            if allow:
                # Pack-aware: the pack's allow_tools are visible in full (the
                # capability refuses everything else at call time anyway,
                # apart from the coordination floor it always permits). The
                # suite data connectors data grounding adds to a pack's grant
                # are documented as deferred (no context cost), so the marked
                # long tail stays behind find_tools here too. A deny-all
                # grant permits nothing, so ``hidden`` below empties it.
                from .capability import COORDINATION_TOOLS
                core = ((allow - set(deferrable)) | COORDINATION_TOOLS | {reg.META_TOOL}) & registered
            else:
                core = set(core_tool_names(self.role, extra=exposure["core_extra"]))
        hidden: set[str] = set()
        if cap is not None:
            # Discovery reveals only what this grant permits: hidden names are
            # excluded from find_tools' search space and from activate(), so a
            # denied tool is never described, and activation never widens
            # authority -- _capability_permits_denial still gates every call.
            hidden = {n for n in registered if not cap.permits(n)}
            core -= hidden
        return core, hidden

    def _build_system(self) -> str:
        self._instruction_source_goals = set()
        self._instruction_capture_ready = True
        self._instruction_preferences_owner = ""
        # Wave 9 fix: coding mode applies to the ORCHESTRATOR too, not
        # just workers. The orchestrator emits the FINAL; if it's still
        # using ORCHESTRATOR_SYSTEM_TEMPLATE (prose-oriented), the patch
        # validator + test-driven verifier both operate on prose, every
        # extract_unified_diff returns None, every git apply --check
        # rejects -> Wave 8 contributes negative value. (council code
        # reviewer finding #1)
        try:
            from .coding_mode import from_env as _cm_from_env
            _coding_cfg = _cm_from_env()
        except Exception:
            _coding_cfg = None

        base = select_base_template(
            role=self.role, depth=self.depth, max_depth=self.ctx.max_depth,
            coding_enabled=bool(_coding_cfg is not None and _coding_cfg.enabled),
            is_root=getattr(self, "_is_goal_root", False),
        )

        # Swarm-wide additive overlays (persona, output style, learned-habits
        # prior) — global state, not agent-specific. Extracted as the second
        # PromptBuilder collaborator.
        base = apply_global_overlays(base)
        try:
            from .experience_preferences import prompt as preference_prompt

            user = getattr(self.ctx, "user_id", None)
            if user and getattr(self.ctx, "channel", None) == "dashboard":
                principal = user if str(user).startswith("user:") else "user:" + str(user)
                from .chat_threads import ChatThreads
                binding = ChatThreads(self.ctx.world).binding(self.ctx.goal_id)
                thread_id = binding.root_id if binding and binding.owner == principal else self.ctx.goal_id
                preferences = preference_prompt(principal, thread=thread_id)
                base += preferences
                if preferences:
                    self._instruction_preferences_owner = principal
            elif user:
                from .personal_identity import CHANNELS
                from .personal_memory import shared_principal
                channel = getattr(self.ctx, "channel", None)
                goal = self.ctx.world.get_goal(self.ctx.goal_id)
                owner = getattr(goal, "owner", "") or ""
                if channel in CHANNELS and owner.startswith("user:" + channel + ":account:"):
                    principal = shared_principal(channel, owner[len("user:" + channel + ":"):])
                    if principal:
                        preferences = preference_prompt(principal)
                        base += preferences
                        if preferences:
                            self._instruction_preferences_owner = principal
        except Exception:
            log.warning("Account response preferences are unavailable; using organization voice.")

        # Per-agent role overlays (client role-addendum + domain-pack persona).
        # Third PromptBuilder collaborator.
        base = apply_role_overlays(
            base, role=self.role, domain_persona=self._domain_persona)

        # Skills from prior runs (fourth PromptBuilder collaborator). The seam
        # renders + returns the recalled skills; the stats/ctx bookkeeping stays
        # here, all-or-nothing inside one fail-safe block, exactly as before.
        base, _skills = apply_skill_overlays(
            base, brief=self.brief, use_skills=self.ctx.use_skills,
            depth=self.depth)
        if _skills:
            try:
                self._instruction_source_goals.update(identity for skill in _skills for identity in skill.source_goal_ids)
            except Exception:
                self._instruction_capture_ready = False
            try:
                from .skill import stats as skill_stats
                from .skills import learned_skill_use_key
                names = [s.name for s in _skills]
                exact_uses = [key for s in _skills
                              if (key := learned_skill_use_key(s))]
                skill_stats.record_use(names + exact_uses)
                self.ctx.skills_used.update(names)
            except Exception:
                pass

        # Cross-session memory presence hint (fifth PromptBuilder collaborator,
        # root agent only).
        base = apply_memory_brief(base, depth=self.depth)

        base = self._with_harness_addendum(base)
        return base

    def _with_harness_addendum(self, base: str) -> str:
        """Append the self-harness addendum: model-specific operating guidance
        the loop learned from THIS model's past failures, recalled like
        skills/insights (never a kernel-template mutation). Keyed on the agent's
        resolved model so a worker's lesson never bleeds into the orchestrator's.
        Empty (no change) unless [self_harness] is enabled and an addendum
        exists. Fully fail-safe."""
        try:
            from .self_harness import note_recall, recall_addendum_with_sources
            # Pass this run's domain AND its available tools so guidance mined
            # SCOPED to a department or a tool is recalled only when it applies
            # (model-wide guidance always is): a finance lesson on finance runs, a
            # `web_fetch` lesson only when `web_fetch` is on hand (#7). Defaults to
            # the model-wide block when the agent has no domain and no scoped tools.
            _dom = getattr(self, "domain", None)
            _tools = self._harness_tool_names()
            # The agent's role scopes recall too: guidance mined per role rides
            # only that role's prompts (an orchestrator lesson stays off a
            # coder's context of the same model).
            _role = getattr(self, "role", None)
            addendum, recalled_sources = recall_addendum_with_sources(self.model, domain=_dom, tools=_tools,
                                                                      role=_role)
            if addendum:
                try:
                    self._instruction_source_goals.update(recalled_sources)
                except Exception:
                    self._instruction_capture_ready = False
                # Track that this guidance was USED (throttled, best-effort) so
                # retirement keeps actively-recalled lines and prunes dormant ones.
                note_recall(self.model, domain=_dom, tools=_tools, role=_role)
                # Register this model for run-end outcome attribution (mirrors
                # ctx.skills_used): a WORKER's recalled guidance earns outcome
                # credit too, not only the orchestrator's.
                models = getattr(getattr(self, "ctx", None), "harness_models", None)
                if isinstance(models, set):
                    models.add(str(self.model))
                return base + "\n\n" + addendum
        except Exception:  # pragma: no cover -- never block a run
            pass
        return base

    def _harness_tool_names(self) -> list[str]:
        """The names of the tools this agent has on hand, for per-tool guidance
        recall. Best-effort: a registry that can't be enumerated yields no tool
        scopes (recall falls back to model-wide + domain), never an error."""
        try:
            return [t.name for t in self.tools.all()]
        except Exception:  # pragma: no cover -- recall must never break a run
            return []

    def _thinking_budget(self) -> int | None:
        if (not getattr(self, "_is_goal_root", False)
                and self.role not in ("orchestrator", "revisor")):
            return None
        # Thinking tokens bill at the output rate, so the base is a first-class
        # cost knob: `[thinking] budget` (default 8000, the long-standing value).
        # Resolved once per agent — this runs every turn and load_config()
        # re-stats/merges overlay files on each call; config is fixed mid-run.
        base = getattr(self, "_thinking_base", None)
        if base is None:
            base = 8000
            try:
                from .config import load_config
                base = int(((load_config() or {}).get("thinking") or {})
                           .get("budget", base) or base)
            except Exception:  # pragma: no cover -- config never blocks a turn
                pass
            self._thinking_base = base
        # Adaptive controller (opt-in, default off): trims/raises by recent
        # success rate. Disabled or low-data -> returns `base` unchanged.
        from .thinking_budget import adjust
        return adjust(self.role, base)

    def _turn_max_tokens(self) -> int:
        """Per-turn output cap, scaled to the driving model's context window.

        Was a hard 4096 regardless of model; now context_scaling derives it
        from the model's real window (4096 stays the floor, so nothing
        shrinks). MAVERICK_AGENT_MAX_TOKENS / [context] max_output_tokens
        override; budget caps still bound actual spend. Fail-soft."""
        try:
            from .context_scaling import max_output_tokens
            return max_output_tokens(self.model)
        except Exception:  # pragma: no cover -- sizing never blocks a turn
            return 4096

    def _extract_and_apply_patch(self, final: str):
        """Wave 11: unify SEARCH/REPLACE and unified-diff extraction.

        Returns (patch_str_or_None, sr_summary_or_None). The patch is
        the rendered unified diff (suitable for `git apply --check` /
        the CSV). The `sr_summary` is the ApplySummary when blocks
        were applied to disk, None when only a unified diff was found.

        Caller is responsible for resetting the workdir AFTER capturing
        the patch.

        Wave 11: also runs `ast.parse` on every modified Python file
        before rendering the diff. SyntaxError gets surfaced via a
        synthesized ApplySummary so the agent re-emits, instead of
        submitting a patch that breaks pytest collection (32% of Opus
        4.1 / 57% of Gemini failures on Pro per Scale's Table 4).
        """
        from pathlib import Path as _Path

        from .coding_mode import (
            _ast_check_python_files,
            extract_unified_diff,
        )
        from .edit_format import (
            ApplyResult,
            ApplySummary,
            SearchReplaceBlock,
            apply_blocks,
            parse_blocks,
            render_diff,
        )
        from .git_safety import (
            git_env_without_user_config,
            git_metadata_may_execute_filters,
        )

        workdir = _Path(getattr(self.ctx.sandbox, "workdir", "."))
        blocks = parse_blocks(final)
        if blocks:
            import subprocess as _sub
            import tempfile as _tempfile

            sandbox = self.ctx.sandbox
            has_exec = sandbox is not None and hasattr(sandbox, "exec")
            apply_workdir = workdir
            temp_root = None
            used_temp_worktree = False

            if has_exec:
                # SEARCH/REPLACE application is necessarily host-local
                # because it rewrites files via pathlib.  Do it in a
                # disposable git worktree so exec-backed sandboxes (ssh,
                # k8s, firecracker/E2B) cannot leave attacker-influenced
                # edits behind in a host checkout while _reset_workdir()
                # resets a different backend filesystem.
                temp_root = _tempfile.TemporaryDirectory(
                    prefix="maverick-sr-worktree-"
                )
                candidate = _Path(temp_root.name) / "worktree"
                hooks_dir = _Path(temp_root.name) / "hooks"
                try:
                    # UX-013: this is HOST git against a workdir the agent (or
                    # a container it controls) can write. A repo-local
                    # `filter.<x>.smudge` runs during the checkout below and
                    # `core.hooksPath` does not stop it, so refuse outright
                    # (same guard as coding_mode.evaluate_candidate) and run
                    # git under a scrubbed environment.
                    if git_metadata_may_execute_filters(workdir):
                        raise RuntimeError(
                            "the workspace's git settings (a filter such as "
                            "git-lfs, or another command) are not allowed to "
                            "run on the host, so git was not run. This is "
                            "not a problem with the edits themselves"
                        )
                    hooks_dir.mkdir(mode=0o700)
                    wt = _sub.run(
                        [
                            "git", "-c", f"core.hooksPath={hooks_dir}",
                            "-C", str(workdir), "worktree", "add",
                            "--detach", "--quiet", str(candidate), "HEAD",
                        ],
                        capture_output=True, timeout=60,
                        env=git_env_without_user_config(),
                    )
                    if wt.returncode != 0:
                        raise RuntimeError(
                            wt.stderr.decode("utf-8", errors="replace")
                        )
                    apply_workdir = candidate
                    used_temp_worktree = True
                except Exception as exc:
                    temp_root.cleanup()
                    summary = ApplySummary()
                    summary.results.append(ApplyResult(
                        ok=False,
                        block=SearchReplaceBlock(
                            path="<sandboxed search/replace>",
                            search="", replace="",
                        ),
                        reason=(
                            "SEARCH/REPLACE requires a disposable local git "
                            f"worktree when sandbox.exec is available: {exc}"
                        ),
                    ))
                    return None, summary

            try:
                summary = apply_blocks(blocks, apply_workdir, atomic=True)
                if not summary.ok:
                    return None, summary
                touched_paths = sorted(summary.files_touched)
                syntax_errors = _ast_check_python_files(
                    apply_workdir, touched_paths
                )
                if syntax_errors:
                    # Roll back the SR application so the next attempt sees
                    # HEAD, then synthesise a failure summary the caller
                    # can convert into a repair prompt.  Disposable
                    # worktrees are cleaned below; only reset the real
                    # workdir on the legacy no-exec path.
                    if not used_temp_worktree:
                        self._reset_workdir()
                    summary.results.append(ApplyResult(
                        ok=False,
                        block=SearchReplaceBlock(
                            path="<syntax check>", search="", replace="",
                        ),
                        reason=(
                            "Python syntax errors after applying: "
                            + "; ".join(syntax_errors)
                        ),
                    ))
                    return None, summary
                patch = render_diff(apply_workdir, paths=touched_paths)
                return patch, summary
            finally:
                if used_temp_worktree:
                    try:
                        _sub.run(
                            [
                                "git", "-C", str(workdir), "worktree",
                                "remove", "--force", str(apply_workdir),
                            ],
                            capture_output=True, timeout=30,
                            env=git_env_without_user_config(),
                        )
                    except Exception:
                        pass
                if temp_root is not None:
                    temp_root.cleanup()
        return extract_unified_diff(final), None

    def _reset_workdir(self) -> None:
        """Revert the sandbox workdir to a clean HEAD.

        CLAUDE.md rule 4: route git plumbing through ``sandbox.exec`` so
        it operates on the configured backend's filesystem (ssh/k8s/fc),
        not the host. ``reset --hard`` then ``clean -fd`` in one shell
        string; we only need the exit code, so the 8000-char output
        truncation is irrelevant here. Falls back to host ``subprocess``
        only when there's no sandbox or it lacks ``exec``.
        """
        sandbox = self.ctx.sandbox
        if sandbox is not None and hasattr(sandbox, "exec"):
            try:
                sandbox.exec("git reset --hard HEAD && git clean -fd", timeout=30)
            except Exception:
                pass
            return
        import subprocess as _sub
        from pathlib import Path as _Path

        from .git_safety import (
            git_env_without_user_config,
            git_metadata_may_execute_filters,
        )
        workdir = _Path(getattr(sandbox, "workdir", "."))
        # UX-013: host git on an agent-writable workdir -- a planted
        # `filter.<x>.smudge` runs during `reset --hard`. Skip rather than run.
        if git_metadata_may_execute_filters(workdir):
            log.warning(
                "workdir reset skipped: unsafe git filter/fsmonitor metadata in %s",
                workdir,
            )
            return
        env = git_env_without_user_config()
        try:
            _sub.run(
                ["git", "-C", str(workdir), "reset", "--hard", "HEAD"],
                capture_output=True, timeout=20, env=env,
            )
            _sub.run(
                ["git", "-C", str(workdir), "clean", "-fd"],
                capture_output=True, timeout=20, env=env,
            )
        except Exception:
            pass

    def _git_apply(self, patch: str) -> bool:
        """Apply ``patch`` to the sandbox workdir; return whether it applied.

        CLAUDE.md rule 4: run ``git apply`` on the configured backend.
        ``sandbox.exec`` runs a shell string and can't pipe stdin, so we
        write the patch to a temp file inside the workdir and
        ``git apply <tmpfile>``, then clean the temp file up. Falls back
        to host ``subprocess`` (piping via stdin) only when there's no
        sandbox or it lacks ``exec``.
        """
        from pathlib import Path as _Path
        sandbox = self.ctx.sandbox
        workdir = _Path(getattr(sandbox, "workdir", "."))
        if sandbox is not None and hasattr(sandbox, "exec"):
            import os as _os
            import tempfile as _tempfile
            tmp_path = None
            try:
                with _tempfile.NamedTemporaryFile(
                    mode="w", suffix=".patch", dir=str(workdir),
                    delete=False, encoding="utf-8",
                ) as tmp:
                    tmp.write(patch)
                    tmp_path = tmp.name
                import shlex as _shlex
                rel = _shlex.quote(_os.path.basename(tmp_path))
                res = sandbox.exec(f"git apply {rel}", timeout=30)
                return getattr(res, "exit_code", 1) == 0
            except Exception:
                return False
            finally:
                if tmp_path is not None:
                    try:
                        _os.unlink(tmp_path)
                    except OSError:
                        pass
        import subprocess as _sub

        from .git_safety import (
            git_env_without_user_config,
            git_metadata_may_execute_filters,
        )
        # UX-013: `git apply` runs a repo-local clean/smudge filter too, so the
        # host fallback refuses a workdir that carries filter metadata.
        if git_metadata_may_execute_filters(workdir):
            log.warning(
                "patch apply refused: unsafe git filter/fsmonitor metadata in %s",
                workdir,
            )
            return False
        try:
            ap = _sub.run(
                ["git", "-C", str(workdir), "apply", "-"],
                input=patch.encode("utf-8"),
                capture_output=True, timeout=30,
                env=git_env_without_user_config(),
            )
            return ap.returncode == 0
        except Exception:
            return False

    def _maybe_seal(self, quarantine, verdict) -> None:
        """Conservatively escalate a shield block to a Rung-1 seal.

        Workers only; the trusted root orchestrator (the privileged promoter)
        is never sealed. Do not trust ``role`` alone here: child agents receive
        model-supplied role strings from spawn tools. Fail-open -- containment
        must never break the agent loop.
        """
        is_root_orchestrator = (
            getattr(self, "role", "") == "orchestrator"
            and getattr(self, "depth", None) == 0
            and getattr(self, "parent", None) is None
        )
        if quarantine is None or is_root_orchestrator:
            return
        try:
            from .quarantine import triage_block
            triage_block(
                quarantine, self.name,
                getattr(verdict, "severity", "high"),
                "; ".join(getattr(verdict, "reasons", []) or []),
            )
        except Exception:  # pragma: no cover -- containment must never break the loop
            pass

    def _effective_capability(self, tool_name: str):
        """Capability that gates a tool call, including active verified handoffs."""
        ambient = getattr(self, "capability", None)
        # Receiving bus messages is the control-plane path that lets an agent
        # accept a replacement handoff. Keep it governed by ambient authority so
        # an older task grant cannot strand the peer from future coordination.
        if tool_name == "recv_from_agent":
            return ambient
        handoff = getattr(self, "_handoff_capability", None)
        if handoff is None:
            return ambient
        if ambient is None:
            return handoff
        try:
            return ambient.intersect(handoff, principal=handoff.principal)
        except AttributeError:  # pragma: no cover -- defensive for foreign caps
            return ambient

    def _capability_revocation_denial(self, name: str, cap) -> str | None:
        # Revocation kill-switch: a still-valid grant can be revoked out of
        # band (leaked key / rogue agent / offboard); the registry is re-read
        # on change so a revoke in another process reaches this running agent.
        # Fail closed when configured revocation state cannot be trusted, and
        # only consult it when a grant exists (== capability enforcement is
        # on).
        if cap is None:
            return None
        from .revocation import revoked_principal as _revoked_principal
        principals = (
            cap.revocation_principals()
            if hasattr(cap, "revocation_principals") else (cap.principal,)
        )
        revoked = _revoked_principal(principals)
        if revoked is None:
            return None
        self.ctx.blackboard.post(
            self.name, "error",
            f"tool={name} DENIED: principal {revoked} REVOKED",
        )
        from .audit import EventKind
        self._audit_tool_event(
            EventKind.CAPABILITY_DENIED,
                tool=name,
                principal=cap.principal,
                revoked_principal=revoked,
                channel=getattr(self.ctx, "channel", None),
                user_id=getattr(self.ctx, "user_id", None),
        )
        return (
            f"⚠ DENIED by capability policy: principal {revoked!r} "
            f"has been revoked. The tool was not executed."
        )

    def _capability_permits_denial(self, name: str, cap) -> str | None:
        if cap is None or cap.permits(name):
            return None
        self.ctx.blackboard.post(
            self.name, "error",
            f"tool={name} DENIED by capability (principal={cap.principal})",
        )
        from .audit import EventKind
        self._audit_tool_event(
            EventKind.CAPABILITY_DENIED,
                tool=name,
                principal=cap.principal,
                channel=getattr(self.ctx, "channel", None),
                user_id=getattr(self.ctx, "user_id", None),
        )
        return (
            f"⚠ DENIED by capability policy: principal {cap.principal!r} is "
            f"not granted tool {name!r}. The tool was not executed."
        )

    def _tool_token_denial(self, name: str, cap) -> str | None:
        """Per-call token exchange: trade the run-long grant for a freshly
        minted, single-tool-scoped, short-lived, signed token and verify it
        before dispatch (Kagenti-style "token exchange for every tool call",
        mapped onto our own capability + Ed25519 primitives).

        No-op unless ``[capabilities] per_call_tokens`` is on AND a grant exists
        (== capability enforcement is on). Minting/verification never crashing
        the agent loop is treated as fail-open -- the static ``permits()`` check
        above already authorized the call; a verification *failure* (expired,
        tampered, replayed, wrong tool) fail-closes only because the operator
        explicitly turned the feature on.
        """
        if cap is None:
            return None
        from .tool_token import tool_tokens_enabled
        if not tool_tokens_enabled():
            return None
        from .tool_token import mint_tool_token, verify_tool_token
        try:
            token = mint_tool_token(cap, name)
            # Same-process mint-then-verify: the token never crosses a trust
            # boundary, so do not require a signature here -- that keeps the
            # exchange fail-open when cryptography is unavailable (kernel rule),
            # while a present signature is still verified and the exported
            # verifier default stays require_signature=True for outside callers.
            ok = verify_tool_token(token, name, require_signature=False)
        except Exception:  # pragma: no cover -- exchange must never brick a run
            return None
        if ok:
            # auditable record of the scoped credential this call ran under
            from .audit import EventKind, audit_event
            audit_event(
                EventKind.TOKEN_EXCHANGE, agent=self.name,
                goal_id=self.ctx.goal_id, tool=name, principal=cap.principal,
                jti=token.jti, expires_at=token.expires_at,
                signed=token.signature is not None,
            )
            return None
        self.ctx.blackboard.post(
            self.name, "error",
            f"tool={name} DENIED: per-call token verification failed "
            f"(principal={cap.principal})",
        )
        from .audit import EventKind
        self._audit_tool_event(EventKind.CAPABILITY_DENIED, tool=name,
                   principal=cap.principal, reason="tool_token_invalid")
        return (
            f"⚠ DENIED by capability policy: per-call token for tool {name!r} "
            "could not be verified. The tool was not executed."
        )

    def _capability_path_denial(self, name: str, args: dict, cap) -> str | None:
        # P0 capability layer (path resource-scopes): for known filesystem
        # tools, gate the canonical workspace-relative path(s) they will touch.
        # This mirrors the tools' own resolution behavior, so raw paths like
        # "allowed/../secret.txt" are checked as "secret.txt". ``list_dir``
        # gets its schema default of ".", and ``apply_patch`` checks every
        # file referenced by the unified diff. Malformed calls whose path
        # cannot be located still fall through to tool validation.
        if (
            cap is None
            or not cap.allow_paths
            or not (name in _FILE_TOOL_POLICIES or name == "apply_patch")
        ):
            return None
        denied_paths: list[str] = []
        try:
            paths = _capability_paths_for_tool(name, args, self.ctx.sandbox)
        except ValueError:
            # Absolute configured paths outside workdir are not representable
            # by a workspace-relative grant. Deny without reflecting host path
            # metadata (usernames, home layout) into model-visible output.
            denied_paths = ["<outside-workspace>"]
        else:
            if paths is not None:
                denied_paths = [p for p in paths if not cap.permits_path(p)]
        if not denied_paths:
            return None
        denied = ", ".join(denied_paths)
        self.ctx.blackboard.post(
            self.name, "error",
            f"tool={name} path={denied} DENIED by capability "
            f"(principal={cap.principal})",
        )
        from .audit import EventKind
        self._audit_tool_event(EventKind.CAPABILITY_DENIED, tool=name,
                   principal=cap.principal, path=denied)
        return (
            f"⚠ DENIED by capability policy: principal {cap.principal!r} is "
            f"not granted path {denied!r} for tool {name!r}. "
            "The tool was not executed."
        )

    def _read_only_path_denial(self, name: str, args: dict) -> str | None:
        """Keep protected workspace evidence immutable across every file tool.

        Docker overlays ``sandbox.read_only_paths`` as read-only bind mounts
        for shell/code execution. File tools run in-process, so they need the
        same check here at the common dispatch chokepoint. Directory entries
        protect their complete subtree.
        """
        protected = tuple(
            PurePosixPath(path)
            for path in getattr(self.ctx.sandbox, "read_only_paths", ())
        )
        if not protected:
            return None
        if name not in _FILE_TOOL_POLICIES and name != "apply_patch":
            return None
        try:
            accesses = _workspace_path_accesses_for_tool(
                name,
                args,
                self.ctx.sandbox,
                mutating_only=True,
            )
        except ValueError:
            return None
        if not accesses:
            return None
        denied = [
            access.path
            for access in accesses
            if any(
                PurePosixPath(access.path) == root
                or root in PurePosixPath(access.path).parents
                or (
                    access.mutates_subtree
                    and PurePosixPath(access.path) in root.parents
                )
                for root in protected
            )
        ]
        if not denied:
            return None
        rendered = ", ".join(denied)
        self.ctx.blackboard.post(
            self.name,
            "error",
            f"tool={name} path={rendered} DENIED by sandbox read-only policy",
        )
        from .audit import EventKind
        self._audit_tool_event(
            EventKind.SANDBOX_DENIED,
            tool=name,
            path=rendered,
            reason="read_only_path",
        )
        return (
            f"⚠ DENIED by sandbox policy: path {rendered!r} is read-only. "
            "The tool was not executed."
        )

    def _capability_host_denial(self, name: str, args: dict, cap) -> str | None:
        # P0 capability layer (host resource-scopes): for a known network tool,
        # the grant's allow_hosts globs also gate the host its URL reaches.
        # No-op unless a host-restricted grant is active (empty == all). Fail-
        # soft: if the URL arg is missing/unparseable (no host) we skip the
        # check rather than error -- we never deny something we can't
        # confidently locate the host for.
        url_arg = _NET_TOOL_URL_ARGS.get(name)
        if cap is None or url_arg is None:
            return None
        raw = args.get(url_arg) if isinstance(args, dict) else None
        host = None
        if isinstance(raw, str) and raw:
            try:  # malformed URLs (e.g. bad IPv6) raise -- skip, don't crash
                host = urlsplit(raw).hostname
            except ValueError:
                host = None
        if not host or cap.permits_host(host):
            return None
        self.ctx.blackboard.post(
            self.name, "error",
            f"tool={name} host={host} DENIED by capability "
            f"(principal={cap.principal})",
        )
        from .audit import EventKind
        self._audit_tool_event(EventKind.CAPABILITY_DENIED, tool=name,
                   principal=cap.principal, host=host)
        return (
            f"⚠ DENIED by capability policy: principal {cap.principal!r} is "
            f"not granted host {host!r} for tool {name!r}. "
            "The tool was not executed."
        )

    @staticmethod
    def _with_capability_host_scope(name: str, args: dict, cap) -> dict:
        """Attach active host scope for tools that enforce it internally.

        Always FORCE-overwrite ``_capability_allow_hosts`` from the grant --
        never ``setdefault``. It is a kernel-injected arg, never a legitimate
        model-supplied one; honoring a model-provided value would let a tool
        call widen its own host allowlist past the capability grant (a scope
        escalation). The ``browser`` path already did this; every other tool
        must too.
        """
        if cap is None or not cap.allow_hosts or not isinstance(args, dict):
            return args
        scoped = dict(args)
        scoped["_capability_allow_hosts"] = tuple(cap.allow_hosts)
        return scoped

    def _autonomy_denial(self, name: str, cap) -> str | None:
        # Autonomy servo (Loop 2): tighten the leash with live trust. When the
        # run's trust is low -- a high-disagreement swarm fan-out or a low
        # verifier verdict -- the effective risk ceiling drops, so an unresolved
        # disagreement can't drive an irreversible (high-risk) action
        # unattended. Composes WITH the capability ceiling above (it tightens
        # from the grant's max_risk, never broadens). No-op unless [autonomy] is
        # enabled. Fail-open: a bug here must never block a tool.
        try:
            from . import autonomy
            _av = autonomy.gate_tool(
                name,
                disagreement=float(getattr(self.ctx, "last_disagreement", 0.0) or 0.0),
                verifier_confidence=float(
                    getattr(self.ctx, "last_verifier_confidence", 1.0) or 1.0
                ),
                configured_max_risk=getattr(cap, "max_risk", None),
            )
        except Exception:  # pragma: no cover -- autonomy gate must never break the loop
            _av = None
        if _av is None or _av.allowed:
            return None
        self.ctx.blackboard.post(
            self.name, "error", f"tool={name} GATED by autonomy: {_av.reason}",
        )
        # tamper-evident record of the gate. audit_event swallows write
        # errors but propagates a refusal -- a gate we cannot record is a gate
        # whose firing we cannot later prove.
        from .audit import EventKind, audit_event
        audit_event(
            EventKind.AUTONOMY_GATED, agent=self.name,
            goal_id=self.ctx.goal_id, tool=name,
            effective_max_risk=_av.effective_max_risk,
        )
        return (
            f"⚠ GATED by autonomy policy: {_av.reason}. The tool was not "
            "executed. Resolve the disagreement (reconcile the divergent "
            "findings) or get human approval via ask_user before retrying."
        )

    async def _ifc_denial(self, name: str, args: dict) -> str | None:
        """Information-flow gate (maverick.ifc): labelled data leaving through an
        external sink is recorded, needs a human's approval, or is blocked,
        per the run's mode. ``None`` = proceed."""
        tracker = getattr(self.ctx, "ifc", None)
        if tracker is None:
            return None
        from . import ifc
        decision = tracker.decide(self.name, name, args,
                                  sandbox=getattr(self.ctx, "sandbox", None))
        if decision is None:
            return None
        goal_id = getattr(self.ctx, "goal_id", None)
        if tracker.granted(decision):
            ifc.record(decision, agent=self.name, goal_id=goal_id, outcome="allowed_by_grant",
                       tracker=tracker)
            return None
        labels = ", ".join(sorted(decision.labels))
        if decision.mode == "audit":
            ifc.record(decision, agent=self.name, goal_id=goal_id, outcome="recorded",
                       tracker=tracker)
            return None
        if decision.mode == "approve":
            from .immune import twin
            replayed = twin.human_gate(name, args)  # a twin never pings a human
            granted = False
            if not decision.explicit:
                copied = ""
            elif decision.witnessed:
                copied = " The data appears literally in the call."
            else:
                copied = " The call is too large to rule out that it carries the data literally."
            to = decision.shown
            try:
                if replayed is not None:
                    raise _ReplayedDecision(replayed)
                from .safety.consent import require_consent
                verdict = await asyncio.to_thread(
                    require_consent, name, risk="high", provenance="ifc",
                    scope=(f"{name} -> {to}" if to else name)[:512],
                    detail=(f"Agent {self.name} holds data from {labels} and wants to send "
                            f"through '{name}'" + (f" to {to}" if to else "")
                            + ", which leaves the tenant boundary." + copied),
                    allow_auto_approve=False, consult_ledger=False)
                granted = bool(verdict.granted)
            except _ReplayedDecision as replay_decision:
                granted = replay_decision.granted
            except Exception:  # consent unavailable -> fail closed
                granted = False
            if replayed is None:  # a twin's replayed answer is no human's decision
                ifc.record(decision, agent=self.name, goal_id=goal_id,
                           outcome="approved" if granted else "denied")
                twin.record_gate(self.ctx.goal_id, name, args, granted=granted)
            if granted:
                tracker.grant(decision)
                return None
            return (f"⚠ '{name}' requires human approval: this agent holds data from "
                    f"{labels}, and '{name}' sends outside the tenant boundary. Not granted. "
                    "Finish without sending it, or ask a human to share it.")
        ifc.record(decision, agent=self.name, goal_id=goal_id, outcome="blocked", tracker=tracker)
        self.ctx.blackboard.post(self.name, "error",
                                 f"tool={name} BLOCKED by information-flow control")
        return (f"⚠ BLOCKED by information-flow control: this agent holds data from {labels}, "
                f"and '{name}' sends outside the tenant boundary. Do not retry through "
                "another external tool; finish with what can stay inside.")

    async def _human_decision(self, name: str, args: dict, ask) -> tuple[bool, bool]:
        """A human approval gate's answer: ``(granted, replayed)``. In a production
        twin nobody is asked -- the recording answers -- and otherwise the answer
        is recorded for later twins. ``ask`` runs off the event loop and any
        failure of it is a refusal (fail closed)."""
        from .immune import twin as _twin
        answer = _twin.human_gate(name, args)
        if answer is not None:
            return answer, True
        try:
            granted = bool(await asyncio.to_thread(ask))
        except Exception:  # consent unavailable -> fail closed
            granted = False
        _twin.record_gate(self.ctx.goal_id, name, args, granted=granted)
        return granted, False

    def _approval_granted(self, name: str, reason: str, *, fresh: bool) -> bool:
        """One human decision for a REQUIRE_HUMAN verdict (EU AI Act Art 14).

        ``allow_auto_approve=False``: a silent auto-approve mode counts as a
        denial. A finance action under ``[finance.approvals] timeout =
        "fail_closed"`` is decided by a person (``require_person``): never the
        automatic consent mode or an earlier "remember this" grant, and a
        request nobody answers in time is refused and withdrawn.
        """
        from .safety.consent import require_consent, require_person
        from .safety.tool_risk import tool_risk
        try:
            from .finance.profile import approval_fail_closed

            fail_closed = approval_fail_closed(name)
        except Exception:
            log.warning("finance: approval posture unavailable for %r; asking a "
                        "person, fail closed", name, exc_info=True)
            fail_closed = True
        if fail_closed:
            return bool(require_person(name, risk=tool_risk(name), detail=reason,
                                       provenance="governance").granted)
        return bool(require_consent(
            name, risk=tool_risk(name), detail=reason, provenance="governance",
            allow_auto_approve=False, consult_ledger=not fresh,
        ).granted)

    def _finance_sod_verdict(self, name: str, cap, gov, profile_keys):
        """Compose the action-time finance separation-of-duties check.

        ``enforce`` (or any conflict on a hard-floored action) denies; ``warn``
        records a compensating-control finding and requires a human. The
        finding is the compensating control's evidence, so a finding that could
        not be written denies. A check that cannot run fails closed.
        """
        from .governance import Decision, Verdict
        principal = getattr(cap, "principal", None) if cap is not None else None
        try:
            from .finance import profile as fin

            check = fin.check_action_sod(
                name,
                allow_tools=getattr(cap, "allow_tools", None) if cap is not None else None,
                deny_tools=getattr(cap, "deny_tools", None) if cap is not None else None,
                compartment=self.domain,
            )
            if check is None:
                return gov
            recorded = fin.record_sod_finding(
                check, agent=self.name, goal_id=self.ctx.goal_id, principal=principal,
                outcome="blocked" if check.blocks else "human_review",
            )
        except Exception:
            log.warning("finance: separation-of-duties check failed for %r; "
                        "failing closed", name, exc_info=True)
            return Verdict(Decision.DENY,
                           "separation-of-duties check unavailable (failed closed)",
                           "finance_sod:error", compliance_profiles=profile_keys)
        if check.blocks or not recorded:
            reason = check.reason if recorded else (
                f"{check.reason}; the finding could not be recorded (failed closed)")
            return Verdict(Decision.DENY, reason, check.rule,
                           compliance_profiles=profile_keys)
        if gov is not None and gov.decision is Decision.DENY:
            return gov
        return Verdict(Decision.REQUIRE_HUMAN, check.reason, check.rule,
                       compliance_profiles=profile_keys)

    async def _governance_denial(
        self,
        name: str,
        args: dict,
        cap,
    ) -> tuple[str | None, tuple[str, ...] | None]:
        # Org oversight control plane (enterprise): on top of the per-principal
        # capability above, an org-level policy can DENY an action outright or
        # REQUIRE_HUMAN sign-off (EU AI Act Art 14). Default-open -- an empty
        # [governance] policy returns ALLOW, so this is a no-op for non-
        # enterprise installs. A present-but-invalid policy, unreadable source,
        # missing enforcement module, or evaluation error fails CLOSED: true
        # absence is represented by a successfully loaded empty Policy, so
        # policy uncertainty must never masquerade as the default-open posture.
        # A trusted empty tuple means governance evaluated a policy snapshot
        # with no compliance profiles. ``None`` means provenance was
        # unavailable, so evidence helpers may surface the policy outage rather
        # than mislabelling it as a known-empty posture.
        _profile_keys: tuple[str, ...] | None = None
        _gov = None
        try:
            from .governance import Decision as _GovDecision
            from .governance import Policy as _GovPolicy
            from .governance import Verdict as _GovVerdict
            from .governance import evaluate as _gov_evaluate
        except Exception:
            log.warning(
                "governance: enforcement module unavailable for %r; failing closed",
                name,
                exc_info=True,
            )
            self.ctx.blackboard.post(
                self.name,
                "error",
                f"tool={name} DENIED: governance enforcement unavailable",
            )
            return (
                "⚠ DENIED by governance policy (policy_unavailable): "
                "the enforcement module was unavailable, so the tool was not "
                "executed.",
                None,
            )
        else:
            try:
                _gov_policy = _GovPolicy.from_config()
                _profile_keys = _gov_policy.compliance_profiles
            except Exception:
                # The module is available but its active policy is not. This is
                # distinguishable from the intentional no-policy posture because
                # Policy.from_config returns an empty Policy for true absence and
                # raises only when the source or a present field is untrusted.
                log.warning(
                    "governance: policy unavailable for %r; failing closed",
                    name,
                    exc_info=True,
                )
                _gov_policy = None
                _gov = _GovVerdict(
                    _GovDecision.DENY,
                    "governance policy is unavailable or invalid (failed closed)",
                    "policy_unavailable",
                )
        if _gov_policy is not None and not _gov_policy.is_empty():
            try:
                # Pass the transaction amount/currency so the policy's
                # dollar-tier gates (deny_above / require_human_above) actually
                # fire -- without this the finance delegation-of-authority
                # thresholds are dead at the chokepoint.
                _gov_currency = args.get("currency") if isinstance(args, dict) else None
                _gov = _gov_evaluate(
                    name, policy=_gov_policy,
                    amount=_governance_amount(args),
                    currency=_gov_currency if isinstance(_gov_currency, str) else "",
                )
            except Exception:
                log.warning("governance: evaluation failed for %r; failing closed",
                            name, exc_info=True)
                self.ctx.blackboard.post(
                    self.name, "error",
                    f"tool={name} BLOCKED: governance evaluation error (fail-closed)",
                )
                _gov = _GovVerdict(
                    _GovDecision.DENY,
                    "governance evaluation error (failed closed)",
                    "error",
                    compliance_profiles=_profile_keys,
                )
        if _gov is not None:
            _profile_keys = _gov.compliance_profiles
        # Per-agent autonomy level (the agent-as-employee authority dial):
        # compose strictest-wins with the org governance verdict. Only
        # consequential (non-low-risk) tools are gated -- reading/searching is
        # always allowed regardless of the hire's rung. Independent of whether an
        # [governance] policy is set; no-op unless [workforce] levels is enabled.
        # Once the client enables this enforcement layer, an evaluation failure
        # must refuse the action. Otherwise a classifier/profile bug would turn
        # the authority dial into an execution bypass.
        _autonomy_levels_enabled = False
        try:
            from . import agent_autonomy as _aa
            from .governance import Decision as _GD
            from .governance import Verdict as _GV
            from .safety.tool_risk import risk_rank as _rr
            from .safety.tool_risk import tool_risk as _tr
            _autonomy_levels_enabled = _aa.levels_enabled()
            _risk = _tr(name)
            # Disabled => the gate is a strict no-op (kernel rule 1: byte-for-byte
            # historical behaviour). Low-risk tools (reads/searches) and the
            # coordination control-plane (spawn/bus/delegate -- how the workforce
            # communicates) are never gated by the dial.
            if (_autonomy_levels_enabled and _rr(_risk) > 0
                    and name not in _aa.COORDINATION_TOOLS):
                _al = _aa.decide(getattr(self, "_autonomy_name", self.role),
                                 getattr(self, "_autonomy", None),
                                 action=name, risk=_risk)
                _amap = {"allow": _GD.ALLOW, "require_human": _GD.REQUIRE_HUMAN,
                         "deny": _GD.DENY}
                _adec = _amap.get(_al.decision, _GD.REQUIRE_HUMAN)
                if _adec is not _GD.ALLOW:
                    _rank = {_GD.ALLOW: 0, _GD.REQUIRE_HUMAN: 1, _GD.DENY: 2}
                    if _gov is None or _rank[_adec] > _rank[_gov.decision]:
                        _gov = _GV(_adec, f"autonomy level: {_al.reason}",
                                   f"autonomy:{_al.level.value}",
                                   compliance_profiles=_profile_keys)
        except Exception:
            if _autonomy_levels_enabled:
                log.warning(
                    "autonomy: level evaluation failed for %r; failing closed",
                    name,
                    exc_info=True,
                )
                self.ctx.blackboard.post(
                    self.name,
                    "error",
                    f"tool={name} DENIED: autonomy evaluation unavailable",
                )
                from .audit import EventKind, audit_event

                audit_event(
                    EventKind.GOVERNANCE_DENIED,
                    agent=self.name,
                    goal_id=self.ctx.goal_id,
                    tool=name,
                    principal=(
                        getattr(cap, "principal", None)
                        if cap is not None else None
                    ),
                    rule="autonomy:error",
                    reason="autonomy level evaluation failed closed",
                )
                return (
                    "⚠ DENIED by autonomy policy (autonomy:error): "
                    "authority evaluation was unavailable, so the tool was not "
                    "executed.",
                    _profile_keys,
                )
            log.warning(
                "autonomy: level evaluation unavailable while disabled for %r; "
                "skipping",
                name,
                exc_info=True,
            )
        # Finance separation of duties at action time ([finance.sod] mode):
        # composes strictest-wins; a no-op unless the finance layer is on and
        # the tool carries a duty.
        _gov = self._finance_sod_verdict(name, cap, _gov, _profile_keys)
        if _gov is None or _gov.decision is _GovDecision.ALLOW:
            return None, _profile_keys
        _principal = getattr(cap, "principal", None) if cap is not None else None
        if _gov.decision is _GovDecision.DENY:
            self.ctx.blackboard.post(
                self.name, "error",
                f"tool={name} DENIED by governance ({_gov.rule})",
            )
            # tamper-evident record of the denial; a refusal propagates
            from .audit import EventKind, audit_event
            from .regulatory_compiler import audit_tags as _obligation_tags
            audit_event(EventKind.GOVERNANCE_DENIED, agent=self.name,
                        goal_id=self.ctx.goal_id, tool=name,
                        principal=_principal, rule=_gov.rule,
                        reason=_gov.reason,
                        **_obligation_tags(
                            name,
                            _gov.rule,
                            keys=_gov.compliance_profiles,
                        ))
            return (
                f"⚠ DENIED by org policy ({_gov.rule}): {_gov.reason}. "
                "The tool was not executed.",
                _gov.compliance_profiles,
            )
        # REQUIRE_HUMAN: the action runs only with a real human's approval
        # (Art 14). allow_auto_approve=False means a silent auto-approve mode
        # counts as a denial -- no human in the loop, no run.
        def _ask() -> bool:
            return self._approval_granted(
                name, _gov.reason,
                # When the operator opts into per-action oversight, a prior
                # persistent ledger grant must NOT silently satisfy the
                # Art-14 gate -- demand a fresh human decision each time. The
                # autonomy dial can drive REQUIRE_HUMAN with no [governance]
                # policy set (_gov_policy None), so guard the attribute read.
                fresh=bool(getattr(_gov_policy, "require_fresh_human_approval", False)),
            )
        granted, replayed = await self._human_decision(name, args, _ask)
        if granted:
            return None, _gov.compliance_profiles
        self.ctx.blackboard.post(
            self.name, "error",
            f"tool={name} BLOCKED: governance requires human approval",
        )
        if replayed:
            # A production twin: no human decided, so nothing is audited as a
            # human denial or learned as an operator's "no".
            return (
                f"⚠ {name!r} requires human approval (EU AI Act Art 14): "
                f"{_gov.reason}. Not granted, so the tool was not executed.",
                _gov.compliance_profiles,
            )
        from .audit import EventKind, audit_event
        from .regulatory_compiler import audit_tags as _obligation_tags
        audit_event(EventKind.GOVERNANCE_DENIED, agent=self.name,
               goal_id=self.ctx.goal_id, tool=name,
               principal=_principal, rule=_gov.rule,
               reason="human approval not granted",
               **_obligation_tags(
                   name,
                   _gov.rule,
                   keys=_gov.compliance_profiles,
               ))
        # Human-override ingestion: the operator's "no" is itself a
        # learning signal — recallable on the next similar goal and
        # consolidated by dreaming. No-op unless [reflexion] is on.
        try:
            from .reflexion import record_human_override
            record_human_override(
                self.brief, name, _gov.reason or _gov.rule,
                domain=self.domain,
                channel=getattr(self.ctx, "channel", None),
                user_id=getattr(self.ctx, "user_id", None),
            )
        except Exception:  # pragma: no cover -- never block the denial
            pass
        return (
            f"⚠ {name!r} requires human approval (EU AI Act Art 14): "
            f"{_gov.reason}. Not granted, so the tool was not executed.",
            _gov.compliance_profiles,
        )

    def _audit_tool_event(self, kind: str, **payload: Any) -> bool:
        """Record a tool-lifecycle audit event on the signed chain.

        ``AuditRefused`` propagates. Ordinary writer failures return ``False`` so
        the governed dispatch boundary can refuse an unrecorded effect instead
        of silently treating best-effort logging as execution evidence.

        """
        from .audit import audit_event
        return audit_event(
            kind,
            agent=self.name,
            goal_id=self.ctx.goal_id,
            **payload,
        )

    def _retain_large_result(self, output: str, *, limit: int, tool: str) -> str:
        """Cap an over-limit screened result for the model, retaining the
        complete value as a private artifact the agent can page through.

        The tool DID run; if retention fails (e.g. the tenant's artifact quota
        is full) the screened preview is delivered with a plain statement of
        what is missing, never turned into an unknown outcome.
        """
        from .tool_result_artifacts import ToolArtifactError, publish

        try:
            ref = publish(output, goal_id=self.ctx.goal_id,
                          principal=self.tools._principal, tool=tool)
        except ToolArtifactError as exc:
            return _cap_tool_output(output, limit=limit) + (
                f"\n[Complete result NOT retained ({exc}). Only this preview is "
                "available: do not treat the omitted part as known, and do not "
                "repeat the original action to recover it.]"
            )
        # Keep the existing preview/error-prefix contract. The reference binds
        # immutable full data; a preview is never executable data.
        reader_allowed = self.tools._acl_allows("read_tool_result")
        return _cap_tool_output(output, limit=limit) + (
            "\n[Complete screened result retained: artifact_id="
            f"{ref['artifact_id']}; bytes={ref['bytes']}; "
            f"expires_at={ref['expires_at']}. "
            + ("Read with read_tool_result using offset/next_offset."
               if reader_allowed else "Artifact reading is denied by the current tool ACL.")
            + " Do not repeat the original action to recover omitted data.]"
        )

    async def _run_dispatched_tool(
        self,
        name: str,
        args: dict,
        *,
        shield,
        quarantine,
        call_id: str,
        call_commitment: str,
        principal: str | None,
    ) -> str:
        """Run one already-authorized, already-audited dispatch to termination."""
        from .audit import TOOL_CALL_COMMITMENT_ALGORITHM
        from .hooks import HookContext, HookEvent
        from .hooks import dispatch as _dispatch_hooks

        terminal_attempted = False

        def record_terminal(
            *,
            status: str,
            output_summary: str,
            disposition: str,
        ) -> bool:
            nonlocal terminal_attempted
            terminal_attempted = True
            try:
                return self._audit_tool_event(
                    "tool_result",
                    name=name,
                    call_id=call_id,
                    call_commitment=call_commitment,
                    call_commitment_algorithm=TOOL_CALL_COMMITMENT_ALGORITHM,
                    origin="agent",
                    principal=principal,
                    status=status,
                    disposition=disposition,
                    output_summary=output_summary,
                )
            except Exception as exc:
                # A strict refusal before dispatch must propagate. At this
                # boundary the tool has already run, so replacing its outcome
                # with AuditRefused invites a replay. Convert only the terminal
                # write refusal to the same explicit indeterminate state as a
                # False return; the outer exception path still re-raises an
                # original BudgetExceeded/Halted/CancelledError unchanged.
                from .audit.errors import AuditRefused

                if isinstance(exc, AuditRefused):
                    log.warning(
                        "terminal audit refused after tool=%s execution",
                        name,
                        exc_info=True,
                    )
                    return False
                raise

        effect_completed = False
        execution_status = "error"
        execution_unknown = False
        try:
            # W1-31: enforce the wall-clock cap and killswitch DURING
            # dispatch, not only at turn boundaries -- a tool with no timeout
            # of its own (a plugin, an MCP server with a large default, a
            # browser/file-watcher wait) could otherwise hold the goal
            # 'active' past max_wall_seconds and ignore `maverick halt`
            # until it happened to return.
            from .connector_attempts import bind_tool_budget
            from .immune import twin
            with bind_tool_budget(self.ctx.budget):
                output = await self._await_wall_clock_guarded(
                    lambda: twin.dispatch(self.ctx.goal_id, name, args,
                                           lambda: self.tools.run(name, args)),
                    in_flight_note="mid-tool-execution")
            # From this point onward the connector returned normally and may
            # already have committed an external mutation.  Keep that fact
            # separate from failures in hooks, screening, framing, or other
            # post-effect processing so none of those failures can recast the
            # completed call as replay-safe.
            effect_completed = True
            execution_unknown = getattr(output, "tool_execution_unknown", False)
            execution_status = _tool_status(output)
            await _dispatch_hooks(HookContext(
                event=HookEvent.POST_TOOL_USE,
                tool_name=name,
                tool_args=args,
                tool_result=output,
                goal_id=self.ctx.goal_id,
                agent_role=self.role,
            ))
            if shield is not None:
                try:
                    out_verdict = shield.scan_output(output)
                except Exception as exc:  # configured screening must finish before release
                    log.warning(
                        "shield output screening failed (%s); content withheld",
                        type(exc).__name__,
                    )
                    self.ctx.blackboard.post(
                        self.name, "error",
                        "tool output-scan errored; content withheld "
                        f"({type(exc).__name__}); reconcile without replay",
                    )
                    raise
                else:
                    if not out_verdict.allowed:
                        reason = "; ".join(out_verdict.reasons)
                        self.ctx.blackboard.post(
                            self.name,
                            "error",
                            f"tool={name} OUTPUT BLOCKED by Shield: {reason}",
                        )
                        self._maybe_seal(quarantine, out_verdict)
                        if not record_terminal(
                            # The effect completed before its output was screened.
                            # Withholding delivery must not recast a mutating call
                            # as failed/replay-safe in a trust receipt.
                            status=execution_status,
                            disposition="output_withheld",
                            output_summary=_audit_summary(reason),
                        ):
                            return (
                                "INDETERMINATE: the tool executed and its output "
                                "was withheld, but terminal audit evidence could "
                                "not be written; reconcile state and do not retry"
                            )
                        return (
                            "⚠ Tool executed, but its output was BLOCKED by Shield "
                            f"({out_verdict.severity}): {reason}. Result withheld; "
                            "do not retry solely to recover the output."
                        )
            from .safety.secret_detector import redact as _redact_secrets
            from .tool_result_stream import needs_complete_output, result_bytes

            # Scan/redact the complete bounded value, including secrets split
            # across producer chunks. No listener receives producer content.
            result_bytes(output)
            output, _redacted = _redact_secrets(output)
            result_bytes(output)
            safe_name, _ = _redact_secrets(str(name)[:128])
            complete_output = output
            if (tracker := getattr(self.ctx, "ifc", None)) is not None:
                tracker.observe_output(self.name, name, complete_output)
            limit = _tool_result_limit(self.model)
            if not needs_complete_output() and len(output) > limit:
                output = self._retain_large_result(output, limit=limit, tool=safe_name)
            nonce = _secrets.token_hex(8)
            framed = (
                f"<tool_output tool={safe_name!r} id={nonce}>\n"
                f"{output}\n"
                f"</tool_output {nonce}>"
            )
            # Build every potentially fallible delivery artifact before writing
            # the one terminal row.  A later delivery failure can then be
            # recorded honestly as post-effect rather than leaving a misleading
            # already-finalized "completed" disposition.
            guard_note = self._loop_guard_note(name, args, output)
            if not record_terminal(
                status=execution_status,
                disposition=(
                    "execution_unknown" if execution_unknown else
                    "completed" if execution_status == "ok" else "tool_error"
                ),
                output_summary=_audit_summary(output),
            ):
                return (
                    "INDETERMINATE: the tool ran, but its terminal audit evidence "
                    "could not be written; reconcile state and do not retry"
                )
            # Observation is optional for compatible run-only registries, and
            # cannot change an already completed, screened and audited effect.
            try:
                deliver = getattr(self.tools, "deliver_screened", None)
                if callable(deliver):
                    deliver(safe_name, complete_output)
            except Exception as exc:
                log.warning("tool output observer failed (%s)", type(exc).__name__)
            return framed + guard_note
        except BaseException as exc:
            if not terminal_attempted:
                if effect_completed:
                    record_terminal(
                        status=execution_status,
                        disposition="post_effect_failure",
                        output_summary=_audit_summary(
                            f"post-effect processing failed: {type(exc).__name__}"
                        ),
                    )
                else:
                    disposition = {
                        "BudgetExceeded": "budget_exceeded",
                        "Halted": "halted",
                        "CancelledError": "cancelled",
                        "GoalCancelled": "cancelled",
                    }.get(type(exc).__name__, "exception")
                    if getattr(exc, "tool_execution_unknown", False):
                        disposition = "execution_unknown"
                    record_terminal(
                        status="error",
                        disposition=disposition,
                        output_summary=_audit_summary(type(exc).__name__),
                    )
            if effect_completed:
                # Preserve process/agent stop signals; their callers already
                # treat them as non-retryable control flow.  Ordinary failures
                # after a returned effect become an explicit reconciliation
                # state and never expose the unprocessed tool output.
                if type(exc).__name__ in {
                    "BudgetExceeded",
                    "CancelledError",
                    "GoalCancelled",
                    "Halted",
                    "KeyboardInterrupt",
                    "SystemExit",
                }:
                    raise
                return (
                    "INDETERMINATE: the tool completed, but post-effect "
                    "processing failed; reconcile state and do not retry"
                )
            raise

    def _plan_summary(self, name: str, args: dict) -> str:
        """What a person reviews before the first action, as one paragraph."""
        from .plan_review import summary_text
        steps, _tools, cap = _plan_facts(self)
        return summary_text(steps, name, args, cap)

    async def _plan_denial(self, name: str, args: dict) -> str | None:
        """Ask once per task run, before the first action with an outside effect."""
        from . import practice
        from .autonomy import confirm_plan_enabled
        if practice.tool_allowed(name) or not confirm_plan_enabled():
            return None
        key = (int(getattr(self.ctx, "goal_id", 0) or 0), int(getattr(self.ctx, "episode_id", 0) or 0))
        with _PLAN_DECISIONS_LOCK:
            decided = _PLAN_DECISIONS.get(key)
        if decided is None:
            from .safety.consent import require_consent
            try:
                from .plan_review import plan_card
                steps, tools, cap = _plan_facts(self)
                verdict = await asyncio.to_thread(
                    require_consent, "confirm_plan", risk="high", scope=f"task {key[0]}",
                    detail=self._plan_summary(name, args), provenance="plan",
                    allow_auto_approve=False, consult_ledger=False,
                    approval_card=plan_card(key[0], steps, name, args, tools, cap))
                decided = bool(verdict.granted)
            except Exception:  # an unavailable decision is a refusal
                decided = False
            with _PLAN_DECISIONS_LOCK:
                decided = _PLAN_DECISIONS.setdefault(key, decided)
                if len(_PLAN_DECISIONS) > 10000:
                    _PLAN_DECISIONS.pop(next(iter(_PLAN_DECISIONS)))
        if decided:
            return None
        return ("The plan was not confirmed, so nothing ran. Do not take any action. Finish by telling the "
                "person what you planned to do, so they can change the request and start again.")

    async def _practice_denial(self, name: str, args: dict) -> str | None:
        """Record, instead of run, a tool that leaves the practice workspace.

        The approval a live run would need is still asked for (in the
        practice Approvals page), so people practise the decision too; the
        tool never runs whatever the answer."""
        from . import practice
        if not practice.active() or practice.tool_allowed(name):
            return None
        from .safety.consent import require_consent
        from .safety.tool_risk import tool_risk
        try:
            verdict = await asyncio.to_thread(
                require_consent, name, risk=tool_risk(name), provenance="practice", consult_ledger=False,
                detail=(f"Practice mode: {self.name} wants to use {name}. Deciding lets you practise "
                        "this approval; the action does not run either way."))
            granted = bool(verdict.granted)
        except Exception:  # an unavailable approval is a refusal, as in live work
            granted = False
        if not granted:
            practice.record("refused", name, _audit_summary(args), goal_id=self.ctx.goal_id)
            return (f"Practice mode: {name} was not approved, so it did not run. Nothing was sent, "
                    "paid or changed outside practice.")
        return practice.record("tool", name, _audit_summary(args), goal_id=self.ctx.goal_id)

    async def _run_tool(self, name: str, args: dict) -> str:
        # Add to the action sequence only after all pre-dispatch checks pass.
        # A refused call must not masquerade as an executed action in credit,
        # rehearsal context, or learned tool trajectories.
        # Compartment Rung 1: a sealed agent runs no further tools. Its prior
        # blackboard posts are also withheld (see Blackboard.render).
        q = getattr(self.ctx, "quarantine", None)
        if q is not None:
            # Register this agent's domain so a Rung-2 sector seal reaches it.
            q.register_agent(self.name, getattr(self, "domain", None))
            if q.is_sealed(self.name):
                return _NotExecutedToolResult(
                    f"⚠ Agent sealed by compartment quarantine "
                    f"({q.reason(self.name)}). No further tools will run."
                )
        args = _normalize_file_tool_args(name, args, self.ctx.sandbox)
        # Loop-guard suppression tier: an exact call that has already returned
        # the byte-identical result _LOOP_GUARD_BLOCK times is refused before
        # any further spend (the identical bytes cleared the shield and every
        # gate on each of those executions; re-scanning them adds nothing).
        suppressed = self._loop_guard_suppression(name, args)
        if suppressed is not None:
            return _NotExecutedToolResult(suppressed)
        shield = self.ctx.shield
        if shield is not None:
            verdict = shield.scan_tool_call(name, args)
            if not verdict.allowed:
                self.ctx.blackboard.post(
                    self.name, "error",
                    f"tool={name} BLOCKED by Shield: {'; '.join(verdict.reasons)}",
                )
                # tamper-evident record of the shield block; a refusal propagates
                from .audit import EventKind, audit_event
                audit_event(EventKind.SHIELD_BLOCK, agent=self.name,
                       goal_id=self.ctx.goal_id, stage="tool",
                       reason="; ".join(verdict.reasons),
                       score=getattr(verdict, "score", None))
                self._maybe_seal(q, verdict)
                return _NotExecutedToolResult(
                    f"⚠ BLOCKED by Shield ({verdict.severity}): "
                    f"{'; '.join(verdict.reasons)}. The tool was not executed."
                )

        # P0 capability layer: a per-agent grant (attenuated from the parent
        # on spawn) gates the tool surface. None == unrestricted, so this is a
        # no-op unless capability enforcement was opted in. Deny wins -- the
        # tool never runs and the model gets a clear, non-leaky refusal.
        cap = self._effective_capability(name)
        # Capability gates that key only on (name, cap): the revocation
        # kill-switch, the tool-grant check, and the zero-trust per-call token
        # exchange (scope the grant to this one tool, short-lived + signed, and
        # verify before dispatch -- no-op unless [capabilities] per_call_tokens
        # is on). Each returns a non-leaky refusal string when it denies, else
        # None; checked in order so deny wins and the tool never runs.
        for _gate in (
            self._capability_revocation_denial,
            self._capability_permits_denial,
            self._tool_token_denial,
        ):
            if (d := _gate(name, cap)) is not None:
                return _NotExecutedToolResult(d)
        if (d := self._capability_path_denial(name, args, cap)) is not None:
            return _NotExecutedToolResult(d)
        if (d := self._read_only_path_denial(name, args)) is not None:
            return _NotExecutedToolResult(d)
        # The browser can follow redirects and later URL-less actions read or
        # interact with the current page. Pass the active host scope into the
        # tool so it can gate the final/current page host before returning
        # content or continuing a restricted session. This must happen before
        # the host-scope check so the (possibly rewritten) args carry forward.
        args = self._with_capability_host_scope(name, args, cap)
        if (d := self._capability_host_denial(name, args, cap)) is not None:
            return _NotExecutedToolResult(d)
        # Hold-mode review commits the final dispatch arguments (including a
        # browser's active host scope), and never replaces the other gates.
        d, rehearsal_verdict = await self._twin_check(name, args)
        if d is not None:
            return _NotExecutedToolResult(d)

        # Autonomy servo (Loop 2): low run-trust tightens the risk ceiling.
        # AI-3: with [autonomy] confirm_plan on, a person confirms the plan
        # before the task's first action that could reach outside Lightwork.
        if ((d := self._autonomy_denial(name, cap)) is not None
                or (d := await self._plan_denial(name, args)) is not None):
            return _NotExecutedToolResult(d)

        # Org oversight control plane (enterprise): policy DENY or REQUIRE_HUMAN
        # sign-off (EU AI Act Art 14). Default-open for non-enterprise installs.
        _governance_refusal, _compliance_profiles = await self._governance_denial(
            name,
            args,
            cap,
        )
        if _governance_refusal is not None:
            return _NotExecutedToolResult(_governance_refusal)
        # Practice mode runs last: after every approval and policy check has
        # had its say, a tool that would reach outside the practice workspace
        # is recorded in the practice outbox instead of running.
        if ((d := await self._ifc_denial(name, args)) is not None
                or (d := await self._practice_denial(name, args)) is not None):
            return _NotExecutedToolResult(d)

        # PreToolUse hooks: any registered hook can BLOCK the call by
        # returning a non-zero exit code (shell hook) or a falsy value
        # (Python callable). Modeled on Claude Code's hook surface.
        from .hooks import HookContext, HookEvent
        from .hooks import dispatch as _dispatch_hooks
        pre_ctx = HookContext(
            event=HookEvent.PRE_TOOL_USE,
            tool_name=name, tool_args=args,
            goal_id=self.ctx.goal_id, agent_role=self.role,
        )
        if not await _dispatch_hooks(pre_ctx):
            self.ctx.blackboard.post(
                self.name, "error",
                f"tool={name} BLOCKED by PreToolUse hook",
            )
            return _NotExecutedToolResult("⚠ BLOCKED by hook. The tool was not executed.")

        # Dispatch audit: TOOL_CALL proves the request reached the governed
        # execution boundary; TOOL_RESULT below proves whether it completed.
        # The shared call_id prevents a failed attempt from being mistaken for
        # a successful execution in a portable trust receipt.
        # Proof the compiled obligations were checked on this call: the row names
        # every active obligation whose scope covered the tool (empty -> omitted).
        from .audit import (
            TOOL_CALL_COMMITMENT_ALGORITHM,
            tool_call_commitment,
        )
        from .regulatory_compiler import checked_tags as _checked_tags

        call_id = _secrets.token_hex(12)
        principal = getattr(cap, "principal", None) if cap is not None else None
        try:
            call_commitment = tool_call_commitment(
                call_id=call_id,
            )
        except ValueError:
            return _NotExecutedToolResult(
                "ERROR: invalid tool-call evidence identity; dispatch refused"
            )
        if not self._audit_tool_event(
            "tool_call",
            name=name,
            call_id=call_id,
            call_commitment=call_commitment,
            call_commitment_algorithm=TOOL_CALL_COMMITMENT_ALGORITHM,
            origin="agent",
            principal=principal,
            input_summary=_audit_summary(args),
            **_checked_tags(name, keys=_compliance_profiles),
        ):
            return _NotExecutedToolResult(
                "ERROR: tool dispatch refused because its required audit "
                "evidence could not be written"
            )

        self._capture_rehearsal_prediction(rehearsal_verdict, name)

        acts = getattr(self, "_actions", None)
        if acts is None:
            acts = self._actions = []
        acts.append(name)

        _recent = getattr(self, "_recent_tool_calls", None)
        if _recent is None:
            _recent = self._recent_tool_calls = []
        _recent.append((name, dict(args) if isinstance(args, dict) else {}))
        del _recent[:-_SPECULATION_HISTORY]
        return await self._run_dispatched_tool(
            name,
            args,
            shield=shield,
            quarantine=q,
            call_id=call_id,
            call_commitment=call_commitment,
            principal=principal,
        )

    def _maybe_speculate_reads(self) -> None:
        """Warm the tool-output cache with this agent's predictable repeat reads.

        Opt-in ([tools] speculative / MAVERICK_SPECULATIVE_TOOLS) and only with
        the tool-output cache on. Runs in the background; the budget is checked
        before each read and a stopped budget stops speculation. Never raises.
        """
        try:
            from . import speculative_tools
            from .cache import tool as tool_cache

            if not (speculative_tools.enabled() and tool_cache.enabled()):
                return
            predictions = speculative_tools.predict_from_history(
                list(getattr(self, "_recent_tool_calls", None) or ()))
            speculate = getattr(self.tools, "speculate_reads", None)
            if not predictions or speculate is None:
                return
            budget = self.ctx.budget

            def _budget_open() -> bool:
                try:
                    budget.check()
                    return True
                except Exception:
                    return False

            import contextvars

            # Copy the context so the worker sees this run's tenant and scope,
            # exactly as asyncio.to_thread would -- without waiting for it.
            call_context = contextvars.copy_context()
            loop = asyncio.get_running_loop()
            future = loop.run_in_executor(
                None, call_context.run,
                lambda: speculate(predictions, budget_guard=_budget_open))
            pending = getattr(self, "_speculation_futures", None)
            if pending is None:
                pending = self._speculation_futures = set()
            pending.add(future)
            future.add_done_callback(pending.discard)
        except Exception:  # pragma: no cover -- speculation never breaks a turn
            log.debug("speculative reads skipped", exc_info=True)

    @staticmethod
    def _tool_call_key(name: str, args: dict) -> str:
        try:
            blob = json.dumps(args, sort_keys=True, default=str)
        except Exception:  # pragma: no cover -- unserializable args
            blob = repr(args)
        return f"{name}\x00{blob}"

    @staticmethod
    def _tool_failure_key(name: str, args: dict, raw_output: str) -> str:
        error_hash = hashlib.sha256((raw_output or "").strip().encode()).hexdigest()
        return f"{Agent._tool_call_key(name, args)}\x00{error_hash}"

    def _track_identical_call(self, name: str, args: dict,
                              raw_output: str) -> int:
        """Update the (call, result) identity streak and return its length.

        Byte-identical call AND byte-identical result extends the streak;
        anything else restarts it at 1. Success/failure is irrelevant here —
        an identical failure and an identical success are equally zero new
        information."""
        key = self._tool_call_key(name, args)
        out_hash = hashlib.sha256(
            (raw_output or "").strip().encode()).hexdigest()
        prev = self._identical_streak
        streak = prev[2] + 1 if prev and prev[:2] == (key, out_hash) else 1
        self._identical_streak = (key, out_hash, streak)
        return streak

    def _loop_guard_suppression(self, name: str, args: dict) -> str | None:
        """Pre-exec gate: refuse the call (without running it) once the exact
        same call has returned the byte-identical result ``_LOOP_GUARD_BLOCK``
        times in a row. Returns the refusal string, or ``None`` to proceed.

        The streak was built from EXECUTED calls, so this fires only after
        the model has already been nudged (block > nudge threshold) and kept
        going. A different tool or changed arguments executes normally and
        replaces the streak, clearing the suppression."""
        if not (_LOOP_GUARD_ENABLED and _LOOP_GUARD_BLOCK):
            return None
        cur = self._identical_streak
        if (cur is None or cur[2] < _LOOP_GUARD_BLOCK
                or cur[0] != self._tool_call_key(name, args)):
            return None
        return (
            f"ERROR: [loop-guard] `{name}` call suppressed without executing: "
            f"this exact call has returned the byte-identical result {cur[2]} "
            "times in a row, so repeating it cannot add information. Change "
            "the arguments, switch tools, or proceed with the result you "
            "already have."
        )

    def _loop_guard_note(self, name: str, args: dict, raw_output: str) -> str:
        """Track this call's outcome; return a nudge when an identical call has
        failed the same way — or returned the byte-identical success —
        ``_LOOP_GUARD_THRESHOLD`` times in a row."""
        if not _LOOP_GUARD_ENABLED:
            return ""
        identical = self._track_identical_call(name, args, raw_output)
        failed = _tool_call_failed(raw_output)
        if not failed:
            self._tool_fail_streak.clear()
            if identical < _LOOP_GUARD_THRESHOLD:
                return ""
            return (
                f"\n\n[loop-guard] You have issued this exact `{name}` call "
                f"{identical} times in a row and received the byte-identical "
                "result each time. Repeating it adds no information — use the "
                "result you already have, change the arguments, or move on."
            )
        key = self._tool_failure_key(name, args, raw_output)
        streak = self._tool_fail_streak.get(key, 0) + 1
        self._tool_fail_streak.clear()
        self._tool_fail_streak[key] = streak
        if streak < _LOOP_GUARD_THRESHOLD:
            return ""
        # Tool-failure taxonomy: the streak the loop guard detected is a
        # learnable pattern, not just an in-run nudge. Persist it (class
        # ``tool_flaky``) so recall warns the next similar goal away from the
        # tool and find_tools can demote it. No-op unless [reflexion] is on.
        try:
            from . import reflexion as _r
            if _r.enabled():
                head = (raw_output or "").strip().splitlines()
                _r.record(
                    goal_text=_r._sanitize_text(self.brief)[:500],
                    failure_class="tool_flaky",
                    failure_msg=(head[0][:200] if head else ""),
                    reflection=(
                        f"The `{name}` tool failed the same way {streak}x in a "
                        "row on this kind of goal. Reach for an alternative "
                        "tool or different arguments early instead of retrying."
                    ),
                    tools_used=[name],
                    channel=getattr(self.ctx, "channel", None),
                    user_id=getattr(self.ctx, "user_id", None),
                    domain=self.domain,
                )
        except Exception:  # pragma: no cover -- learning never blocks the loop
            pass
        return (
            f"\n\n[loop-guard] You have issued this exact `{name}` call "
            f"{streak} times in a row and it failed the same way each time. "
            "Do NOT repeat it again — change the arguments, switch tools, or "
            "step back and rethink the approach."
        )

    def _is_parallel_safe(self, name: str) -> bool:
        """Whether ``name`` may execute concurrently with the other tool
        calls in the same turn. Reads the tool's ``parallel_safe`` flag;
        unknown tools (and any tool missing the attribute, e.g. a plugin
        built against an older Tool dataclass) default to False — serial.
        """
        try:
            return bool(getattr(self.tools.get(name), "parallel_safe", False))
        except KeyError:
            return False

    @staticmethod
    def _make_tool_result(tool_use_id: str, output: str) -> dict:
        """Build a tool_result block, flagging errors for the model.

        May 26 council fix (API audit #4): set ``is_error: true`` on
        tool_results that surface an error. Per Anthropic docs, this
        tells Claude the tool failed so it can recover instead of
        treating the error string as a normal output. Our tool registry
        prefixes errors with "ERROR: " and the shield emits
        "BLOCKED by Shield".
        """
        tr: dict = {
            "type": "tool_result",
            "tool_use_id": tool_use_id,
            "content": output,
        }
        # Frame-aware: `output` is the <tool_output …>-wrapped string, so inspect
        # the content inside it (a leading-ERROR check on the frame is always
        # false -- that bug left is_error unset on every failed tool).
        if _tool_call_failed(output):
            tr["is_error"] = True
        return tr

    def _score_step(
        self,
        *,
        step_index: int,
        tool_name: str | None = None,
        tool_succeeded: bool | None = None,
        is_final: bool = False,
        error: str | None = None,
    ) -> None:
        """Score one step via the PRM and post the result to the blackboard.

        No-op unless a non-Null PRM is configured. Never raises: a scoring
        failure is observability noise, not a reason to fail the agent loop.
        """
        promise = progress = None
        if self._prm_enabled:
            try:
                from .prm import StepContext
                reward = self._prm.score(StepContext(
                    goal_id=self.ctx.goal_id or 0,
                    step_index=step_index,
                    role=self.role,
                    tool_name=tool_name,
                    tool_succeeded=tool_succeeded,
                    is_final=is_final,
                    error=error,
                    prior_step_score=self._last_step_score,
                ))
                promise, progress = reward.promise, reward.progress
                self._last_step_score = reward.promise
                self._promise_window.push(reward.promise)
                self.ctx.blackboard.post(
                    self.name, "prm",
                    f"step={step_index} promise={reward.promise:.2f} "
                    f"progress={reward.progress:+.2f} conf={reward.confidence:.2f}",
                )
            except Exception as e:  # pragma: no cover - PRM must never break the loop
                log.debug("PRM scoring skipped: %s", e)
        # Capture is DECOUPLED from the PRM (Karpathy): cheap and unconditional,
        # a no-op unless [self_improvement] capture is on. A deployment shouldn't
        # need a reward model configured just to record what its agents did.
        self._capture_trajectory_step(
            step_index, tool_name, tool_succeeded, is_final, error, promise, progress)

    def _last_tool(self) -> str:
        """The last dispatched tool, excluding calls held before dispatch."""
        acts = getattr(self, "_actions", None)
        return acts[-1] if acts else ""

    def _speculative_turn_model(self) -> str:
        """Pick the model for this turn: a cheap draft when the Operating Twin's
        world-model is confident the turn is predictable, else the normal model.

        No-op (returns ``self.model``) unless ``[speculative]`` is enabled with a
        configured draft model and the state is speculatable; fail-open on any
        error. At turn start the last EXECUTED tool is ``_actions[-1]``.
        """
        try:
            from .speculative_exec import draft_model_for_turn
            acts = getattr(self, "_actions", None)
            last = acts[-1] if acts else ""
            return (draft_model_for_turn(self.domain, self.role, last)
                    or self._cascade_turn_model())
        except Exception:  # pragma: no cover -- never break the loop
            return self.model

    def _cascade_turn_model(self) -> str:
        """The cascade's model for this turn: the cheap draft until the
        verifier rejects an answer under the confidence threshold or this
        agent's tool chain passes the depth threshold, then ``self.model`` for
        the rest of the run. ``self.model`` when the cascade is off."""
        draft = getattr(self, "_cascade_draft", None)
        if not draft or getattr(self, "_cascade_escalated", False):
            return self.model
        from .routing import RouteSignal, should_escalate

        rejected = bool(getattr(self, "_verifier_revision_used", False))
        signal = RouteSignal(
            role=self.role,
            verifier_confidence=(getattr(self.ctx, "last_verifier_confidence", None)
                                 if rejected else None),
            tool_call_depth=len(getattr(self, "_actions", None) or ()),
        )
        if should_escalate(signal):
            self._cascade_escalated = True
            return self.model
        return draft

    async def _twin_check(self, name: str, args: dict) -> tuple[str | None, object | None]:
        """Return a hold and an eligible prediction, with the first hold winning."""
        denial = self._guardrail_denial(name)
        if denial is not None:
            return denial, None
        return await self._rehearsal_check(name, args)

    def _capture_rehearsal_prediction(self, verdict: object | None, name: str) -> None:
        """Pin evidence only when a tool reaches the audited dispatch boundary."""
        if verdict is None:
            return
        try:
            from .earned_autonomy import capture_prediction
            capture_prediction(
                verdict,
                principal=getattr(self, "_autonomy_name", self.role),
                action=name, risk="high",
                goal_id=int(self.ctx.goal_id or 0),
                episode_id=int(getattr(self.ctx, "episode_id", 0) or 0),
            )
        except AuditRefused:
            raise
        except Exception:  # pragma: no cover -- prediction is optional
            pass

    async def _rehearsal_check(self, name: str, args: dict) -> tuple[str | None, object | None]:
        """Observe a prediction; explicit Hold mode asks a person for release."""
        enforcing = False
        try:
            from . import rehearsal
            if not rehearsal.enabled():
                return None, None
            enforcing = rehearsal.enforcing()
            from .safety.tool_risk import tool_risk
            if tool_risk(name) != "high":
                return None, None
            from .rehearsal_runtime import gate_tool
            v = gate_tool(domain=self.domain, role=self.role,
                          last_tool=self._last_tool(), tool_name=name)
            if not enforcing:
                log.info(
                    "rehearsal observed tool=%s decision=%s support=%s; tool remains eligible for normal checks",
                    name, v.decision, getattr(v, "support", 0),
                )
                return None, v
            if v.decision == rehearsal.PROCEED:
                return None, v
        except AuditRefused:
            raise
        except Exception:
            if enforcing:
                return (
                    "Lightwork could not check this high-impact step. Nothing ran. "
                    "Ask the person in charge to review the task before trying it again."
                ), None
            return None, None

        # A prediction is not authority. Bind a fresh person's decision to
        # this exact goal, tool and argument object; the consent permit also
        # rechecks the live actor, budget and target before it is consumed.
        plain_name = {
            "shell": "run a command", "notebook_exec": "run code",
            "repl_exec": "run code", "write_file": "save a file",
            "apply_patch": "change a file", "browser": "use the browser",
            "http_fetch": "open a web page", "spawn_subagent": "ask a specialist",
        }.get(name, f"use {name.replace('_', ' ')}")
        goal_id = int(self.ctx.goal_id or 0)
        target = {"goal_id": goal_id, "tool": name}
        parameters = {"goal_id": goal_id, "tool": name, "arguments": args}
        detail = (
            "Lightwork has too little reliable experience with this step. "
            if v.decision == rehearsal.ESCALATE else
            "Past similar steps suggest this may not work as intended. "
        ) + "Review it before work continues."
        try:
            from .safety.consent import (
                _dashboard_timeout,
                approval_undo_grace_seconds,
                consume_operation_consent,
                require_operation_consent,
            )
            permit = await asyncio.to_thread(
                require_operation_consent,
                name, risk="high", scope=f"Task {goal_id}: {plain_name}",
                detail=detail, provenance="rehearsal",
                target=target, parameters=parameters,
                parameter_summary=[
                    f"Allow Lightwork to {plain_name} once in this task.",
                    "This decision applies to the exact proposed input; other safety checks still apply.",
                ],
                known_effects=[
                    "If the remaining checks allow it, Lightwork may try this step once. Its effects may persist.",
                ],
                unresolved_assumptions=[
                    "The prediction may be wrong; approval does not guarantee the step will succeed.",
                    "The action may require another specific approval before it runs.",
                ],
                before="The proposed step has not run.",
                after="The reviewed step is eligible for the remaining safety checks.",
                irreversible=True,
                target_provider=lambda: target,
                parameters_provider=lambda: parameters,
                require_fresh_person=True,
                ttl_seconds=max(
                    300.0, _dashboard_timeout() + approval_undo_grace_seconds() + 30.0,
                ),
            )
            with consume_operation_consent(permit):
                pass
        except AuditRefused:
            raise
        except Exception:
            # Invalid/stale cards, a missing approval service, rejection and
            # timeout all stop the action. Never turn review failure into a
            # prediction error that the observe-only path would fail open.
            from .audit import EventKind, audit_event
            audit_event(
                EventKind.SHIELD_BLOCK, agent=self.name,
                goal_id=self.ctx.goal_id, stage="rehearsal",
                reason=f"{v.decision}: review not granted",
            )
            return (
                "Lightwork paused this high-impact action for review. The review "
                "was not granted or expired, so nothing ran. Do not retry it "
                "automatically in this task; ask the person in charge or choose "
                "another safe approach."
            ), None
        return None, v

    def _guardrail_denial(self, name: str) -> str | None:
        """Hold an action a learned guardrail flags as causally harmful.

        No-op unless ``[data_engine]`` is enabled (guardrails are its output);
        consults the registry the flywheel writes; incidental errors fail open,
        but audit-policy refusals stop dispatch. Unlike a
        hand-written deny-list, the guardrail carries the causal effect that
        justifies it and is auto-dropped when the harm is gone.
        """
        try:
            from . import data_engine
            if not data_engine.enabled():
                return None
            from .negative_knowledge import shared
            g = shared().consult(name)
            if g is None:
                return None
            # tamper-evident record of the hold; a refusal propagates
            from .audit import EventKind, audit_event
            audit_event(EventKind.SHIELD_BLOCK, agent=self.name, goal_id=self.ctx.goal_id,
                   stage="guardrail", reason=g.rule)
            return (
                f"⚠ Held by a learned guardrail: {g.rule}. The tool was not executed; "
                "choose another approach (this rule was learned from real outcomes and "
                "is dropped automatically once the harm is gone)."
            )
        except AuditRefused:
            raise
        except Exception:  # pragma: no cover -- incidental guardrail failure
            return None

    def _capture_trajectory_step(self, step_index, tool_name, tool_succeeded,
                                 is_final, error, promise, progress) -> None:
        """Append this step to the governed trajectory store -- the data
        foundation for self-improvement. No-op unless [self_improvement] capture
        is on; best-effort, never raises into the loop."""
        try:
            import time as _t

            from .trajectory_store import (
                TrajectoryStep,
                capture_step,
                episode_dag_fields,
            )
            # Decision-DAG edge + terminal outcome (the final answer's verifier
            # confidence, set by the verify stage just before the is_final score).
            dag = episode_dag_fields(
                int(step_index), bool(is_final),
                getattr(self.ctx, "last_verifier_confidence", None),
            )
            capture_step(TrajectoryStep(
                ts=_t.time(), goal_id=int(self.ctx.goal_id or 0),
                episode_id=int(getattr(self.ctx, "episode_id", 0) or 0),
                step=int(step_index), role=self.role, tool=tool_name or "",
                tool_succeeded=tool_succeeded,
                tool_executed=True if tool_name else None,
                is_final=bool(is_final),
                error=error or "", promise=promise, progress=progress,
                domain=self.domain or "", **dag,
            ))
        except Exception:  # pragma: no cover -- capture must never break the loop
            pass

    def _mirror_live_spend(self, episode_id: int) -> None:
        """Throttled write of running totals onto the open episode row (#614).

        Only the root agent (depth 0) of a goal-scoped run mirrors; sub-agents
        share the goal's single episode and budget, so the orchestrator's
        write already covers the whole swarm's accruing spend. Read-side
        observability only -- `update_episode_spend` guards on
        `ended_at IS NULL` so it can never clobber `end_episode`. Never raises.
        """
        if self.depth != 0 or not episode_id or self.ctx.goal_id is None:
            return
        now = time.monotonic()
        if (now - self._last_spend_mirror) < _SPEND_MIRROR_INTERVAL:
            return
        self._last_spend_mirror = now
        b = self.ctx.budget
        try:
            self.ctx.world.update_episode_spend(
                episode_id,
                cost_dollars=b.dollars,
                input_tokens=b.input_tokens,
                output_tokens=b.output_tokens,
                tool_calls=b.tool_calls,
                cache_read_tokens=b.cache_read_tokens,
                cache_write_tokens=b.cache_write_tokens,
            )
        except Exception as e:  # pragma: no cover -- observability never blocks
            log.debug("live-spend mirror skipped: %s", e)

    def _goal_cancelled(self) -> bool:
        """Whether an operator cancelled this goal (POST /goals/{id}/cancel).

        The global killswitch/budget checks above stop every run; this is
        the goal-scoped analogue -- without it, nothing in the loop ever
        read goal status, so the dashboard Cancel button flipped the row to
        'cancelled' but the swarm kept spending until FINAL, max_steps, or
        the budget cap (#W1-23). `get_goal` is a single indexed
        primary-key read at the same cadence as the untimed killswitch.check()
        /budget.check() calls beside it, so no throttling here. Fails open
        (a transient world-read error must not itself stop the run).
        """
        goal_id = getattr(self.ctx, "goal_id", None)
        if goal_id is None:
            return False
        try:
            goal = self.ctx.world.get_goal(goal_id)
        except Exception as e:  # pragma: no cover -- a stop check must not crash the run
            log.debug("goal-cancelled check skipped: %s", e)
            return False
        return goal is not None and goal.status == "cancelled"

    # Poll interval (seconds) for the killswitch while an LLM generation is
    # in flight — small enough to feel responsive, large enough to be free.
    _HALT_POLL_SECONDS = 1.0

    async def _await_wall_clock_guarded(self, make_coro, *, in_flight_note: str):
        """Await ``make_coro()``, but ABORT it when the wall-clock budget is
        exhausted, the goal is cancelled, or the killswitch trips. Raises
        :class:`BudgetExceeded` (wall), :class:`GoalCancelled`, or
        :class:`killswitch.Halted`. The owned coroutine is cancelled and drained
        before this guard exits; cancellation cannot reverse a remote effect.
        ``in_flight_note``
        names what was in flight (e.g. "mid-generation",
        "mid-tool-execution") for the wall-cap message.

        ``make_coro`` is a zero-arg callable, not an already-built
        coroutine: it is invoked only once the wall cap is confirmed to have
        time left, so an already-exhausted budget never creates (and then
        silently abandons, as an "await" warning) the underlying call.

        W1-31: shared by ``_complete_guarded`` (LLM calls) and tool dispatch
        (``_run_dispatched_tool``) — a hung tool with no timeout of its own
        (a plugin, an MCP server, a browser/file-watcher wait) previously had
        no equivalent guard, so it could hold a goal 'active' past
        ``max_wall_seconds`` and ignore ``maverick halt`` until it returned.
        """
        from .template_connections import recheck_active
        recheck_active()
        budget = self.ctx.budget
        if Agent._goal_cancelled(self):
            raise GoalCancelled("cancelled by the goal owner before dispatch")
        remaining = budget.remaining_wall()
        if remaining <= 0:
            raise BudgetExceeded(
                f"wall time {budget.elapsed():.0f}s > {budget.max_wall_seconds:.0f}s")
        task = owned_task(make_coro())
        loop = asyncio.get_running_loop()
        deadline = loop.time() + remaining
        try:
            while True:
                slice_s = min(self._HALT_POLL_SECONDS, deadline - loop.time())
                done, _ = await asyncio.wait({task}, timeout=max(0.0, slice_s))
                if Agent._goal_cancelled(self):
                    raise GoalCancelled("cancelled by the goal owner during execution")
                if task in done:
                    return task.result()  # re-raises any error from the call
                if loop.time() >= deadline:
                    raise BudgetExceeded(
                        f"wall-clock cap {budget.max_wall_seconds:.0f}s reached "
                        f"{in_flight_note}")
                killswitch.check()  # raises killswitch.Halted if tripped
        except BaseException as caught:
            # Wall cap, halt, or an upstream cancellation: cancel the in-flight
            # call (aborts the provider HTTP request / tool coroutine) before
            # propagating.
            stop_signal = task_stop(task, caught)
            if isinstance(stop_signal, GoalCancelled) and in_flight_note == "mid-tool-execution":
                # The tool coroutine was admitted. Cancellation cannot prove an
                # external operation did not commit, even if its coroutine
                # cooperates or returns normally while draining cancellation.
                stop_signal.tool_execution_unknown = True
            if not task.done():
                task.cancel()
                try:
                    await task
                except BaseException as cancelled:
                    if getattr(task_stop(task, cancelled), "tool_execution_unknown", False):
                        stop_signal.tool_execution_unknown = True
            elif stop_signal is caught:
                # Retrieve a racing terminal exception so cancelling the goal
                # cannot orphan an unobserved exception from the owned task.
                try:
                    task.result()
                except BaseException as cancelled:
                    if getattr(task_stop(task, cancelled), "tool_execution_unknown", False):
                        stop_signal.tool_execution_unknown = True
            if stop_signal is caught:
                raise
            raise stop_signal from None

    def _answer_stream_for_turn(self) -> _PartialAnswerStream | None:
        """A live answer preview for this turn, or None when the turn must not
        stream: only the root agent of a conversational run streams, only to a
        model client that can stream, and never while the evidence gateway is
        withholding model-derived text until the result is receipted."""
        if self.depth != 0 or not _answer_streaming_enabled():
            return None
        ctx = self.ctx
        world = getattr(ctx, "world", None)
        goal_id = getattr(ctx, "goal_id", None)
        if (
            world is None or goal_id is None
            or getattr(ctx, "conversation_id", None) is None
            or not hasattr(world, "set_goal_partial_result")
            or not _accepts_keyword(getattr(ctx.llm, "complete_async", None), "on_delta")
        ):
            return None
        try:
            from . import ai_evidence_gateway
            if ai_evidence_gateway.enabled():
                return None
        except Exception:
            return None
        return _PartialAnswerStream(world, goal_id, getattr(ctx, "shield", None))

    def _conversational_answer_skips_verification(
        self, final: str | None, *, coding: bool, tool_calls: int,
    ) -> bool:
        """A root chat answer reached with no tools, no code, and no edit block
        is handed to the person without the self-verifier round trip (see
        ``_verify_chat_answers_enabled``)."""
        if self.depth != 0 or getattr(self.ctx, "conversation_id", None) is None:
            return False
        if _verify_chat_answers_enabled():
            return False
        return _final_is_low_risk(
            final, coding=coding, tool_calls=tool_calls,
            max_chars=_CHAT_LOW_RISK_MAX_CHARS,
        )

    def _note_model_fallback(self, resp) -> None:
        """Record once per backup model that provider failover answered a turn,
        so the conversation can say the backup model was used."""
        primary = getattr(resp, "fallback_from", None)
        backup = getattr(resp, "served_model", None)
        if not (isinstance(primary, str) and isinstance(backup, str) and primary and backup):
            return
        noted = getattr(self, "_fallbacks_noted", None)
        if noted is None:
            noted = self._fallbacks_noted = set()
        if backup in noted:
            return
        noted.add(backup)
        try:
            self.ctx.blackboard.post(self.name, "model_fallback",
                                     f"primary={primary} backup={backup}", provenance="system")
        except Exception:  # pragma: no cover -- a notice never breaks the run
            pass

    async def _complete_guarded(self, **kw):
        """Await an LLM completion, but ABORT the in-flight call when the
        wall-clock budget is exhausted or the killswitch trips — instead of
        letting a multi-minute generation run to completion and blow the SLA or
        ignore a HALT (the cap/halt were otherwise only checked at turn
        boundaries). Raises :class:`BudgetExceeded` (wall) or
        :class:`killswitch.Halted`, both already handled by the caller."""
        async def _call():
            # Waiting for a swarm model slot counts against the wall clock too.
            slot = getattr(self.ctx, "model_slot", None)
            async with slot() if slot else contextlib.nullcontext():
                from .instruction_capture import runtime_scope
                with runtime_scope(getattr(self.ctx, "world", None),
                                   getattr(self.ctx, "goal_id", None),
                                   getattr(self, "role", ""), getattr(self, "_instruction_source_goals", ()),
                                   verified=getattr(self, "_instruction_capture_ready", False),
                                   preferences_owner=getattr(self, "_instruction_preferences_owner", "")):
                    return await self.ctx.llm.complete_async(**kw)
        return await self._await_wall_clock_guarded(
            _call, in_flight_note="mid-generation")

    async def run(self) -> AgentResult:
        try:
            return await self._run_traced()
        except GoalCancelled as exc:
            # Keep Agent.run's structured-result contract for parents and
            # standalone callers. The root orchestrator preserves this stop
            # separately from success, budget refusal, and ordinary failures.
            return AgentResult(
                error=cancellation_message(exc), role=self.role, name=self.name,
                stop_kind="cancelled", stop_detail=str(exc),
                tool_execution_unknown=bool(getattr(exc, "tool_execution_unknown", False)),
            )

    async def _run_traced(self) -> AgentResult:
        # OTel GenAI semconv: every agent execution is an ``invoke_agent``
        # span (gen_ai.agent.name/id), the third semconv leg alongside the
        # LLM (chat) and tool (execute_tool) spans. No-op when tracing is off.
        try:
            from .observability import (
                gen_ai_agent_attributes,
                safe_agent_telemetry_label,
                trace_span,
            )
        except Exception:  # pragma: no cover -- tracing never blocks a run
            return await self._run_inner()
        telemetry_role = safe_agent_telemetry_label(self.role)
        with trace_span(
            f"invoke_agent {telemetry_role}",
            attributes=gen_ai_agent_attributes(self.role, agent_id=self.name),
        ):
            return await self._run_inner()

    def _resume_from_checkpoint(self, messages: list[dict], bb, ep_id: int):
        """Resume durable loop state for a depth-0 run.

        Returns ``(ckpt, start_step, messages)``. When durable checkpointing is
        disabled, there is no goal id, or this is not the root agent, returns
        ``(None, 0, messages)`` unchanged. Missing history starts at zero;
        corrupt/unavailable history or exhausted current caps blocks execution.
        Usage is restored into the authoritative shared Budget.

        W1-28: ``messages[0]`` on entry is the brief this run was JUST built
        with (for the orchestrator, `run_goal` bakes in any newly-answered
        `ask_user` question there -- see the ``qa_block`` in orchestrator.py).
        The checkpoint's own ``messages[0]`` is that same slot from BEFORE
        the question was answered. Wholesale-replacing `messages` with the
        checkpoint would silently discard the fresh brief and re-show the
        model exactly the state it was in when it asked -- the resumed run
        re-asks the same question forever. `_save_checkpoint` always commits
        at the top of the loop (before that step's LLM call), so the saved
        history always ends on a "user" turn and starts on one too; swapping
        just index 0 for the freshly-built one keeps that alternation intact
        while carrying the rest of the crashed/blocked run's history forward.
        """
        start_step = 0
        ckpt = None
        if self.depth == 0 and self.ctx.goal_id is not None:
            try:
                from . import checkpoint as _ckpt_mod
                if _ckpt_mod.enabled():
                    ckpt = _ckpt_mod.Checkpointer(self.ctx.world)
                    # Resume keys on a STABLE id, not self.name (a per-process
                    # random uuid that never matched on a fresh-process resume).
                    saved = ckpt.latest(
                        self.ctx.goal_id, self.checkpoint_id, episode_id=ep_id,
                        required=True,
                    )
                    if saved is not None and saved.messages:
                        # Restore accounting before accepting messages. Never
                        # replace the shared Budget or import historical caps.
                        _ckpt_mod.restore_budget(saved.budget, into=self.ctx.budget)
                        fresh_first = messages[0] if messages else None
                        messages = list(saved.messages)
                        if (fresh_first is not None and messages
                                and messages[0].get("role") == "user"):
                            messages[0] = fresh_first
                        start_step = saved.step_seq
                        bb.post(self.name, "plan",
                                f"resumed from checkpoint at step {start_step}")
            except (BudgetExceeded, ValueError):
                raise
            except Exception as e:  # pragma: no cover -- optional store lookup
                log.debug("checkpoint resume skipped: %s", e)
        return ckpt, start_step, messages

    def _save_checkpoint(self, ckpt, *, step: int, messages: list[dict],
                         ep_id: int) -> None:
        """Persist resumable loop state at a turn boundary. Fail-open no-op when
        checkpointing is off (``ckpt is None``) or the store errors. Extracted
        from ``_run_inner`` (CheckpointManager)."""
        if ckpt is not None:
            try:
                ckpt.save(
                    goal_id=self.ctx.goal_id, agent_id=self.checkpoint_id,
                    episode_id=ep_id, step_seq=step, messages=messages,
                    budget=self.ctx.budget, meta={"role": self.role},
                )
            except Exception as e:  # pragma: no cover
                log.debug("checkpoint save skipped: %s", e)

    async def _run_inner(self) -> AgentResult:  # noqa: C901  -- core agent turn loop; decompose only under dedicated review (see below)
        bb = self.ctx.blackboard
        bb.post(self.name, "plan", f"role={self.role} depth={self.depth} brief={self.brief}")

        # Opt-in prompt-cache pre-warm (default OFF). Warm only the orchestrator
        # -- the first, largest, user-facing prompt -- once before its loop, so
        # the first real turn reads the system+tools cache instead of paying the
        # cold-write latency. Subagents are skipped (each has a distinct prompt;
        # warming them would be a wasted write). Never blocks the run.
        if self.role == "orchestrator" or getattr(self, "_is_goal_root", False):
            try:
                from .llm import cache_prewarm_enabled
                if cache_prewarm_enabled():
                    self.ctx.budget.check()
                    self.ctx.llm.prewarm(
                        self.system, self._tool_catalog_for_model(self.model), self.model,
                        budget=self.ctx.budget)
            except Exception:  # pragma: no cover -- prewarm never blocks a run
                pass

        # If the goal has image/PDF attachments, embed them as vision /
        # document content blocks on the first user message so the agent can
        # see them (PDFs are budgeted against the driving model's context
        # window). Everything else (audio, video, Office docs, text) is
        # reachable via `list_attachments` + `read_file` / `transcribe_audio`
        # (opt-in so we don't blow token budget on huge media).
        image_blocks: list[dict] = []
        if self.depth == 0 and self.ctx.goal_id is not None:
            try:
                from .attachments import content_blocks_for_goal
                image_blocks = content_blocks_for_goal(
                    self.ctx.world, self.ctx.goal_id, model=self.model,
                    shield=self.ctx.shield,
                )
            except Exception:
                image_blocks = []

        brief_text = (
            f"Sub-goal: {self.brief}\n\n"
            f"Recent swarm activity:\n{bb.render(40) or '(empty)'}\n\n"
            "Plan briefly, then act. End with FINAL: <answer> when done."
        )
        # Everything up to here is in the brief; later posts reach this agent
        # as per-turn deltas (see the tool_results assembly below).
        if (tracker := getattr(self.ctx, "ifc", None)) is not None:
            tracker.read_blackboard(self.name, bb.posters_since(0))
        self._bb_cursor = bb.cursor()
        first_content: list[dict] | str
        if image_blocks:
            first_content = image_blocks + [{"type": "text", "text": brief_text}]
        else:
            first_content = brief_text
        messages: list[dict] = [{"role": "user", "content": first_content}]

        # Durable execution (Phase 1): resume a crashed single-agent run from its
        # last committed step. Extracted to _resume_from_checkpoint; off by
        # default, fail-open (leaves messages/start_step untouched on any error).
        ep_id = getattr(self.ctx, "episode_id", 0) or 0
        ckpt, start_step, messages = self._resume_from_checkpoint(messages, bb, ep_id)

        truncated_responses = 0
        steps = (range(start_step, self.max_steps) if self.max_steps is not None
                 else itertools.count(start_step))
        for step in steps:
            if self.depth == 0 and self.ctx.goal_id is not None:
                from .conversation_inbox import checkpoint as conversation_checkpoint
                try:
                    conversation_checkpoint(self.ctx.world, self.ctx.goal_id,
                                            checkpoint_id=f"{ep_id}:{self.checkpoint_id}:{step}", messages=messages)
                except Exception as exc:
                    # Pending guidance is never silently skipped before further
                    # paid work. A fresh authorized retry replays its receipt.
                    bb.post(self.name, "error", "Conversation guidance could not be verified at this checkpoint")
                    return AgentResult(error="Conversation guidance is unavailable; review the pending message before resuming",
                                       role=self.role, name=self.name, stop_kind="review_checkpoint", stop_detail=type(exc).__name__)
            # Durable checkpoint at the turn boundary: commit the resumable loop
            # state BEFORE the next LLM call, so a crash mid-step loses at most
            # one step's work. Extracted to _save_checkpoint; fail-open.
            self._save_checkpoint(ckpt, step=step, messages=messages, ep_id=ep_id)

            # Turn-boundary safety gate. Evaluate the global killswitch
            # (`maverick halt`, the dashboard Halt button, or the HALT
            # file) and the wall-clock/token/tool caps BEFORE the next LLM
            # call, so a runaway or over-budget swarm stops promptly
            # instead of only after the next record_* call. killswitch and
            # budget.check() are cheap and side-effect-free.
            try:
                killswitch.check()
                self.ctx.budget.check()
            except killswitch.Halted as e:
                bb.post(self.name, "error", f"halted: {e}")
                return AgentResult(error=f"halted: {e}", role=self.role, name=self.name,
                                   stop_kind="killswitch", stop_detail=str(e))
            except BudgetExceeded as e:
                label = _budget_error_label(e)
                bb.post(self.name, "error", f"{label}: {e}")
                return AgentResult(error=f"{label}: {e}", role=self.role, name=self.name,
                                   stop_kind=_budget_stop_kind(e), stop_detail=str(e))
            if self._goal_cancelled():
                bb.post(self.name, "error", "cancelled via dashboard")
                return AgentResult(error="cancelled via dashboard", role=self.role, name=self.name)

            # Long-horizon review checkpoint (opt-in [safety] review_checkpoint):
            # at the root, fire a human-review heartbeat every N dollars / M tool
            # calls / T seconds. A reviewer vote to halt stops the run cleanly,
            # like the killswitch. Inert (no checkpoint object) when unconfigured.
            if (
                (self.role == "orchestrator" or getattr(self, "_is_goal_root", False))
                and self._review_checkpoint is not None
            ):
                _cp_event = self._review_checkpoint.check(self.ctx.budget)
                if _cp_event is not None:
                    _cp_detail = f"{_cp_event.reason}={_cp_event.value:g}"
                    bb.post(self.name, "note",
                            f"review checkpoint stopped the run at {_cp_detail}")
                    # Tagged so the orchestrator reports a reviewer decision
                    # (not "a HALT file is present" -- there is none, and
                    # `maverick unhalt` would do nothing).
                    return AgentResult(
                        error=f"stopped at review checkpoint ({_cp_detail})",
                        role=self.role, name=self.name,
                        stop_kind="review_checkpoint", stop_detail=_cp_detail)

            # #614 live-spend mirror: at the turn boundary, the root agent
            # writes the budget's running totals onto its open episode row
            # (throttled) so `maverick runs` / `maverick budget` reflect
            # accruing spend mid-run instead of $0.00 until end_episode.
            # Read-side only; never blocks the run.
            self._mirror_live_spend(ep_id)

            # #611 synthesis reserve: a deeper worker yields before it eats the
            # budget the top-level goal needs to write its answer. Only workers
            # (depth > 0) stop here; the orchestrator (depth 0) keeps the reserve
            # to synthesize. Return whatever partial findings we have rather than
            # spending into the reserve, so the run delivers SOMETHING instead of
            # paying in full for nothing.
            if self.depth > 0 and _SYNTHESIS_RESERVE > 0:
                _b = self.ctx.budget
                if (_b.max_dollars is not None
                        and _b.dollars >= _b.max_dollars * (1.0 - _SYNTHESIS_RESERVE)):
                    bb.post(
                        self.name, "note",
                        f"stopping at ${_b.dollars:.2f}/${_b.max_dollars:.2f} to "
                        f"reserve the final {_SYNTHESIS_RESERVE:.0%} for synthesis",
                    )
                    partial = _last_assistant_text(messages)
                    return AgentResult(
                        final=partial or "(stopped early to reserve synthesis budget)",
                        role=self.role, name=self.name,
                    )

            # Karpathy SOTA-review item: long-context compaction. Drop
            # raw tool_result content >2KiB once it's behind the recent
            # window, and hold the whole window under a total ceiling
            # (MAVERICK_COMPACT_MAX_TOTAL_BYTES) by shrinking into the
            # recent window oldest-first when a few results at the
            # per-result cap add up. The first message (user brief) and
            # the newest message are always kept.
            # The compaction cost is O(len(messages)) per turn -- cheap
            # vs. paying full-price input tokens for a 100k history.
            # Default path is the heuristic shrink; an operator can opt into a
            # richer strategy via [context] compaction_strategy (heuristic /
            # learned / multimodal / streaming / graph) — all registered in the
            # one compaction.plugins dispatcher, which fails safe to heuristic
            # on an unknown name — or into the ledger-learned v6 picker via
            # [compaction] hybrid (an explicit strategy name always wins). The
            # agent's llm seam + conversation id reach the strategies that use
            # them.
            # Process-reward guidance (governed default-on): if the PRM has
            # judged the last few steps unpromising, nudge a course-change. A
            # no-op unless [self_improvement] prm_guidance is on AND a PRM is
            # configured; an explicit sub-knob or env opt-out pauses it.
            from .prm_guidance import maybe_nudge
            _prm_note = maybe_nudge(self._promise_window.values())
            if _prm_note and step - self._last_prm_nudge_step >= 3:
                messages.append({"role": "user", "content": _prm_note})
                self._last_prm_nudge_step = step

            from .compaction.plugins import compact_with_async
            try:
                # Optional strategy I/O shares the same wall/halt guard and
                # caller budget as the primary completion. No detached calls.
                self.ctx.budget.check()
                messages = await self._await_wall_clock_guarded(
                    lambda messages=messages: compact_with_async(
                        messages, llm=self.ctx.llm,
                        conversation_id=str(getattr(self.ctx, "goal_id", "") or ""),
                        budget=self.ctx.budget, scope=self.domain,
                    ),
                    in_flight_note="compacting context",
                )
                # Check even when compaction finished in the same poll as a
                # halt: a completed optional request cannot authorize another.
                killswitch.check()
                # Stop BEFORE spending another call when the cap is already
                # hit. record_tokens() only checks AFTER the response lands,
                # so a goal at 99% of budget would otherwise still fire one
                # more (potentially expensive) call.
                self.ctx.budget.check()
                # Pass effort only when configured (None by default) so the call
                # signature is unchanged when the feature is off. Likewise the
                # answer preview: the chat's root turn streams, nothing else does.
                _turn_kw = {"effort": self.effort} if self.effort else {}
                _preview = self._answer_stream_for_turn()
                if _preview is not None:
                    _turn_kw["on_delta"] = _preview
                _turn_model = self._speculative_turn_model()
                resp = await self._complete_guarded(
                    system=self.system,
                    messages=messages,
                    tools=self._tool_catalog_for_model(_turn_model),
                    budget=self.ctx.budget,
                    max_tokens=self._turn_max_tokens(),
                    thinking_budget=self._thinking_budget(),
                    model=_turn_model,
                    **_turn_kw,
                )
                if _preview is not None:
                    # Only a normal completion shows its tail: any other stop
                    # (token or context limit, refusal, unknown) may cut mid-secret.
                    _preview.finish(complete=getattr(resp, "stop_reason", None)
                                    in {"end_turn", "stop_sequence", "tool_use", "stop",
                                        "eos_token"})
            except BudgetExceeded as e:
                label = _budget_error_label(e)
                bb.post(self.name, "error", f"{label}: {e}")
                return AgentResult(error=f"{label}: {e}", role=self.role, name=self.name,
                                   stop_kind=_budget_stop_kind(e), stop_detail=str(e))
            except UsageAccountingError as e:
                # The provider (or a gateway in front of it) returned usage the
                # kernel cannot bill against the cap. Stop -- the call was
                # already paid for -- but report a provider fault, not a cap
                # breach: raising max_dollars cannot fix this (W1-17).
                bb.post(self.name, "error", f"provider usage accounting: {e}")
                return AgentResult(error=f"provider usage accounting: {e}",
                                   role=self.role, name=self.name,
                                   stop_kind="usage_accounting", stop_detail=str(e))
            except killswitch.Halted as e:
                # The killswitch tripped MID-generation; the in-flight call was
                # cancelled by _complete_guarded. Stop like a turn-boundary halt.
                bb.post(self.name, "error", f"halted: {e}")
                return AgentResult(error=f"halted: {e}", role=self.role, name=self.name,
                                   stop_kind="killswitch", stop_detail=str(e))
            self._note_model_fallback(resp)

            # A provider token cutoff is not a completed answer, even if its
            # partial text already contains FINAL. Incomplete tool arguments
            # cannot be dispatched or installed as orphan tool-use history.
            if resp.stop_reason in {"max_tokens", "length"}:
                truncated_responses += 1
                if truncated_responses >= 3:
                    return AgentResult(
                        error="model response remained incomplete after token cutoffs",
                        role=self.role, name=self.name, stop_kind="incomplete",
                        stop_detail="provider output token limit",
                        verifier_confidence=0.0,
                    )
                if resp.text:
                    messages.append({"role": "assistant", "content": resp.text})
                messages.append({
                    "role": "user",
                    "content": (
                        "Your previous response was cut off by the provider's output limit. "
                        "No tool calls from that response ran. Produce a complete, concise "
                        "answer using FINAL: <answer>, or issue complete tool calls."
                    ),
                })
                continue

            truncated_responses = 0

            # May 26 smoke fix: when the response contains BOTH a FINAL:
            # marker AND tool_use blocks, the model is confused. If
            # FINAL validation fails and we `continue`, the tool_use
            # blocks get appended to assistant message history with NO
            # matching tool_result — Anthropic returns HTTP 400 on the
            # next turn:
            #   messages.N: tool_use ids were found without tool_result
            #   blocks immediately after
            # Drop the tool_use blocks before assembling the assistant
            # message; the FINAL critique is what we want the model to
            # respond to, not the orphan tools.
            final_dropped_tools = False
            if resp.text and resp.tool_calls:
                from .coding_mode import has_final_marker as _has_final
                if _has_final(resp.text):
                    resp.tool_calls = []
                    final_dropped_tools = True

            assistant_content = _assemble_assistant_content(resp, final_dropped_tools)
            messages.append({"role": "assistant", "content": assistant_content})

            if resp.text:
                # Wave 12 hotfix: the prompt instructs the model to "End
                # your turn with `FINAL:`" — many models emit a brief
                # reasoning line BEFORE FINAL: (e.g. "Target: foo.py:bar
                # — fix is X. FINAL: ..."). The prior `startswith` check
                # missed those entirely; the SR block went to the
                # blackboard as a plain observation, was never applied,
                # and the orchestrator returned the raw SR text as
                # `final` with `final_patch=None` — silent score loss.
                # Use the LAST line-anchored FINAL: marker OUTSIDE any
                # fenced code block. Skipping code-block markers
                # prevents attacker-controlled quoted content (file
                # bodies, tool output) from redefining the final
                # answer mid-response.
                from .coding_mode import find_final_marker_end as _final_end
                _fe = _final_end(resp.text)
                if _fe is not None:
                    final = resp.text[_fe:].strip()
                    # May 26 council fix: clear any stale `_final_patch`
                    # from a previous FINAL attempt. If a prior FINAL was
                    # rejected (defensive/validate) and the revised
                    # FINAL has no apply-check (because _patch_validated
                    # was True), the verifier/return branches would read
                    # the STALE patch from the earlier FINAL and submit
                    # it — wrong patch attribution.
                    self._final_patch = None
                    # May 26 council fix (agent-loop audit #4): also
                    # clear `_already_verified` so the revised FINAL
                    # gets verified afresh. Without this, a rejected
                    # FINAL's `_already_verified=True` flag would skip
                    # the verifier on the revised FINAL — and the
                    # revised version would return with
                    # `verifier_confidence=1.0` (the fallback when
                    # verdict is None) regardless of actual quality.
                    self._already_verified = False
                    # #612: `_patch_validated` was sticky for the whole run
                    # (only ever set True), so after one rejected patch a
                    # later genuinely-different FINAL would skip its own
                    # apply-check / diff extraction. Reset it alongside
                    # `_already_verified` so each new FINAL re-validates the
                    # patch it actually carries.
                    #
                    # W1-27: that reset also broke the "one retry max" this
                    # flag was doing double duty for -- every "ask for
                    # revision" branch below reads `_patch_validated` to
                    # decide whether THIS is already a retry, but by the
                    # time the model's revised FINAL arrives here, the reset
                    # above has already flipped it back to False, so every
                    # branch thinks it's seeing a first attempt again. The
                    # reject/test-driven revision loop is bounded only by
                    # `_patch_revision_attempts` below, which is deliberately
                    # NOT reset per FINAL.
                    self._patch_validated = False

                    # Wave 8: coding-mode patch self-validation. If the
                    # workdir is a git repo AND the FINAL contains a
                    # unified diff, run `git apply --check` BEFORE
                    # declaring FINAL. A rejected patch loops back with
                    # the git error as critique -- catches the
                    # ~30% of SWE-bench failures that are unapplyable
                    # patches without burning a verifier round.
                    coding_cfg = None
                    try:
                        from .coding_mode import (
                            from_env as _cm_from_env,
                        )
                        from .coding_mode import (
                            validate_patch,
                        )
                        coding_cfg = _cm_from_env()
                    except Exception:
                        pass

                    if (coding_cfg is not None and coding_cfg.enabled
                            and coding_cfg.require_apply_check
                            and not getattr(self, "_patch_validated", False)
                            and getattr(self, "_patch_revision_attempts", 0)
                                < _MAX_PATCH_REVISION_ATTEMPTS):
                        from pathlib import Path as _Path
                        workdir = _Path(getattr(self.ctx.sandbox, "workdir", "."))
                        # Wave 11: prefer SEARCH/REPLACE over unified-diff.
                        from .edit_format import repair_prompt_for_failure
                        # Serialize the apply->reset on the SHARED sandbox.workdir
                        # with the same lock the verifier branch below uses.
                        # require_apply_check runs for every coding-mode agent
                        # regardless of depth/role, so concurrent coder children
                        # under spawn_swarm would otherwise interleave apply +
                        # `git reset --hard` on one git tree and corrupt each
                        # other's edits. (Sequential with the verifier branch's
                        # own `async with`, so no nested/reentrant acquire.)
                        async with self.ctx.workdir_lock:
                            # Offload blocking git/subprocess work to a thread so
                            # it doesn't stall the event loop (freezing every other
                            # concurrent sub-agent, the channel server, and the
                            # dashboard) while the lock is held. Mirrors the
                            # asyncio.to_thread wrap in tools/agent_bus_tool.py.
                            patch, sr_summary = await asyncio.to_thread(
                                self._extract_and_apply_patch, final)
                            # Reset workdir AFTER capturing the diff so the
                            # verifier branch (and downstream evaluators) see
                            # HEAD when they re-apply.
                            if sr_summary is not None:
                                await asyncio.to_thread(self._reset_workdir)
                                try:
                                    self.ctx.blackboard.post(
                                        self.name, "tool_signal",
                                        "search_replace_used=1",
                                    )
                                except Exception:
                                    pass
                        if patch is None and sr_summary is not None:
                            self._patch_validated = True
                            self._patch_revision_attempts = (
                                getattr(self, "_patch_revision_attempts", 0) + 1)
                            bb.post(
                                self.name, "verify",
                                f"SEARCH/REPLACE apply failed: "
                                f"{sr_summary.summary_text()}",
                            )
                            first_fail = next(
                                (r for r in sr_summary.results if not r.ok),
                                None,
                            )
                            critique = (
                                "Your FINAL SEARCH/REPLACE block(s) did "
                                "not apply.\n\n" + sr_summary.summary_text()
                            )
                            if first_fail is not None:
                                critique += "\n\n" + repair_prompt_for_failure(
                                    first_fail,
                                )
                            messages.append({"role": "user", "content": critique})
                            continue
                        if patch is None:
                            self._patch_validated = True
                            self._patch_revision_attempts = (
                                getattr(self, "_patch_revision_attempts", 0) + 1)
                            bb.post(
                                self.name, "verify",
                                "no valid SEARCH/REPLACE or unified diff in "
                                "FINAL; asking for revision",
                            )
                            messages.append({
                                "role": "user",
                                "content": (
                                    "Your FINAL did not contain valid edits. "
                                    "Use SEARCH/REPLACE format (preferred):\n\n"
                                    "path/to/file.py\n"
                                    "<<<<<<< SEARCH\n"
                                    "<exact existing lines>\n"
                                    "=======\n"
                                    "<new lines>\n"
                                    ">>>>>>> REPLACE\n\n"
                                    "Multiple blocks allowed, each can target "
                                    "a different file. Or as a fallback, a "
                                    "unified diff in ```diff fences."
                                ),
                            })
                            continue
                        # Stash the rendered patch so the verifier branch
                        # doesn't have to re-parse.
                        self._final_patch = patch
                        # Wave 11: defensive validation BEFORE git apply
                        # --check. Catches grader-fatal patches (test
                        # files, dep pins, cheating-detector overlap)
                        # so we ask for revision instead of submitting
                        # something the grader will silently zero out.
                        try:
                            from .coding_mode import (
                                defensive_validate,
                                get_gold_patch,
                            )
                            def_check = defensive_validate(
                                patch,
                                fail_to_pass=coding_cfg.fail_to_pass,
                                pass_to_pass=coding_cfg.pass_to_pass,
                                gold_patch=get_gold_patch(),
                                opaque=(os.environ.get(
                                    "MAVERICK_BENCHMARK_OPAQUE", "1",
                                ) != "0"),
                            )
                        except Exception:
                            def_check = None
                        if def_check is not None and not def_check.ok:
                            # May 26 smoke fix: DO NOT set
                            # `_patch_validated = True` here. The flag
                            # short-circuits the entire SR-extract-apply
                            # block on the next iteration, so when the
                            # agent revises in response to the critique,
                            # the new SR blocks are silently ignored
                            # (workdir untouched, no patch produced).
                            # Fired on pallets/flask-5014 — agent
                            # produced correct fix, cheating detector
                            # false-positive rejected it, then the
                            # agent's revision attempt was no-op'd.
                            bb.post(
                                self.name, "verify",
                                f"patch rejected by defensive validator: "
                                f"{def_check.blocked_paths or def_check.warnings}",
                            )
                            messages.append({
                                "role": "user",
                                "content": def_check.critique(),
                            })
                            continue
                        # Wave 12 hardening: when defensive validate
                        # passes (ok=True) but emitted warnings (WARN
                        # path — conftest.py / pyproject.toml etc.),
                        # post the advisory to the blackboard so it
                        # shows up in trace; we still ACCEPT the patch.
                        if def_check is not None and def_check.warnings:
                            bb.post(
                                self.name, "verify",
                                f"defensive warnings (accepted anyway): "
                                f"{def_check.warnings}",
                            )
                        validation = validate_patch(
                            patch, workdir,
                            allow_checkout_eol=(
                                sr_summary is not None
                                and bool(getattr(self.ctx.sandbox, "host_visible_fs", False))
                            ),
                        )
                        if not validation.valid:
                            self._patch_validated = True  # one retry max
                            self._patch_revision_attempts = (
                                getattr(self, "_patch_revision_attempts", 0) + 1)
                            bb.post(
                                self.name, "verify",
                                f"patch rejected: {validation.reason}",
                            )
                            messages.append({
                                "role": "user",
                                "content": (
                                    "Your FINAL patch did not pass "
                                    "`git apply --check`.\n\n"
                                    f"Reason: {validation.reason}\n\n"
                                    f"git stderr:\n{validation.git_apply_stderr}\n\n"
                                    "Re-examine the exact line content via "
                                    "`read_file`, fix the edits, and respond "
                                    "with a new FINAL using SEARCH/REPLACE "
                                    "blocks (preferred) or a unified diff."
                                ),
                            })
                            continue

                    # Karpathy SOTA-review item: verifier role exists in
                    # prompt strings only -- no code actually runs a
                    # second-pass check. Now we do, but only on the
                    # orchestrator's FINAL (depth=0) and only once per
                    # goal. Sub-agents skip verification (their parent
                    # is the verifier of last resort).
                    verdict = None
                    # Set True once verify_final actually runs, so a verifier
                    # that hits the budget (verdict stays None) is distinguished
                    # from a role that never verifies -- see the FINAL return.
                    verifier_attempted = False
                    # Risk-proportional verification (opt-in): set True when the
                    # orchestrator deems the FINAL low-risk and skips the LLM
                    # verifier. Distinct from "attempted but did not complete".
                    verification_skipped = False
                    _skip_reason = ""
                    # Only the orchestrator's FINAL is verified. Sub-agents
                    # answer to their parent; the parent is their verifier.
                    if (
                        (self.role == "orchestrator" or getattr(self, "_is_goal_root", False))
                        and self.depth == 0
                        and not getattr(self, "_already_verified", False)
                        and self.ctx.goal_id is not None
                    ):
                        # Wave 8: when SWE-bench-style ground-truth tests
                        # are provided, run them as the verifier instead
                        # of (or alongside) the LLM judge. Ground truth
                        # >> opinion; this is how OpenHands gets to 72%.
                        if (coding_cfg is not None and coding_cfg.enabled
                                and (coding_cfg.fail_to_pass or coding_cfg.pass_to_pass)):
                            async with self.ctx.workdir_lock:
                                from pathlib import Path as _Path

                                from .coding_mode import run_failing_tests
                                workdir = _Path(getattr(self.ctx.sandbox, "workdir", "."))
                                # Wave 11: reuse the patch produced by the
                                # validate branch above (SEARCH/REPLACE or
                                # unified-diff). If the validate branch was
                                # skipped (e.g. require_apply_check=False),
                                # extract here.
                                patch = getattr(self, "_final_patch", None)
                                if patch is None:
                                    patch, _ = await asyncio.to_thread(
                                        self._extract_and_apply_patch, final)
                                    if patch is not None:
                                        # We applied to disk; reset for the
                                        # verifier's own apply.
                                        await asyncio.to_thread(self._reset_workdir)
                                if patch is None:
                                    if (not getattr(self, "_patch_validated", False)
                                            and getattr(self, "_patch_revision_attempts", 0)
                                                < _MAX_PATCH_REVISION_ATTEMPTS):
                                        self._patch_validated = True
                                        self._patch_revision_attempts = (
                                            getattr(self, "_patch_revision_attempts", 0) + 1)
                                        bb.post(
                                            self.name, "verify",
                                            "no valid diff in FINAL; asking for revision",
                                        )
                                        messages.append({
                                            "role": "user",
                                            "content": (
                                                "Your FINAL did not contain valid "
                                                "edits. Use SEARCH/REPLACE blocks "
                                                "(preferred) or a unified diff in "
                                                "```diff fences."
                                            ),
                                        })
                                        continue
                                    # Already revised, or out of revision
                                    # attempts (W1-27): surface and exit.
                                    return AgentResult(
                                        final=final, role=self.role, name=self.name,
                                        verifier_confidence=0.0,
                                        verifier_critique="no valid diff in FINAL",
                                    )

                                apply_ok = await asyncio.to_thread(self._git_apply, patch)

                                # Wave 10 (D10): only run tests when apply
                                # succeeded. Running tests on HEAD when apply
                                # failed wastes a full test run (minutes on
                                # SWE-bench), reports all FAIL_TO_PASS as
                                # failing for the wrong reason, then misleads
                                # the revision pass.
                                if not apply_ok:
                                    test_result = None  # type: ignore[assignment]
                                else:
                                    try:
                                        # run_failing_tests shells out to pytest --
                                        # minutes on SWE-bench. Off-load it so the
                                        # whole event loop isn't frozen for the
                                        # duration.
                                        test_result = await asyncio.to_thread(
                                            run_failing_tests,
                                            workdir,
                                            coding_cfg.fail_to_pass,
                                            coding_cfg.pass_to_pass,
                                            self.ctx.sandbox,
                                            language=coding_cfg.language,
                                        )
                                    finally:
                                        # Always revert the workdir so the next
                                        # attempt reads HEAD, not the post-patch
                                        # tree. Without this, successive
                                        # revisions see corrupted state and
                                        # compound the error.
                                        await asyncio.to_thread(self._reset_workdir)

                                if not apply_ok:
                                    # Wave 10 (D10): tests were skipped because
                                    # the patch wouldn't apply. Tell the agent
                                    # so it doesn't 'fix' a working patch into
                                    # a broken one based on apply-fail noise.
                                    if (not getattr(self, "_patch_validated", False)
                                            and getattr(self, "_patch_revision_attempts", 0)
                                                < _MAX_PATCH_REVISION_ATTEMPTS):
                                        self._patch_validated = True
                                        self._patch_revision_attempts = (
                                            getattr(self, "_patch_revision_attempts", 0) + 1)
                                        bb.post(
                                            self.name, "verify",
                                            "patch failed to apply pre-test; "
                                            "asking proposer to revise",
                                        )
                                        messages.append({
                                            "role": "user",
                                            "content": (
                                                "Your patch could not be applied "
                                                "via `git apply`. Re-examine the "
                                                "current file contents with "
                                                "`read_file` and produce a fresh "
                                                "unified diff against HEAD."
                                            ),
                                        })
                                        continue
                                    # Already retried, or out of revision
                                    # attempts (W1-27): surface and exit.
                                    return AgentResult(
                                        final=final, role=self.role, name=self.name,
                                        verifier_confidence=0.0,
                                        verifier_critique="patch did not apply",
                                    )

                                bb.post(
                                    self.name, "verify",
                                    f"test-driven verifier: {test_result.summary()}",
                                )
                                # Calibration auto-collection: tests are GROUND
                                # TRUTH here, so also ask the LLM verifier and
                                # record (confidence, correct) -- this is how the
                                # calibration interlock learns whether the judge
                                # still tracks reality. Opt-in (one extra verifier
                                # call) + fail-open.
                                try:
                                    from . import calibration
                                    if calibration.collect_from_coding_enabled():
                                        from .verifier import verify_proposal
                                        _cv = await verify_proposal(
                                            self.brief, final, self.ctx.llm,
                                            self.ctx.budget, proposer_model=self.model,
                                        )
                                        calibration.record_sample(
                                            _cv.confidence, test_result.all_pass,
                                            source="coding",
                                        )
                                except Exception:  # pragma: no cover -- never break the loop
                                    pass
                                if test_result.all_pass:
                                    # Tests pass → accept FINAL. Skip LLM verifier.
                                    self._already_verified = True
                                    return AgentResult(
                                        final=final, role=self.role, name=self.name,
                                        verifier_confidence=test_result.score,
                                        verifier_critique=test_result.summary(),
                                        final_patch=getattr(self, "_final_patch", None),
                                    )
                                # Tests failed → revise. Wave 9 (council H2):
                                # do NOT leak raw assertion bodies to the
                                # agent in benchmark mode -- that's a recipe
                                # for hardcoding to the test's expected value.
                                # Wave 11 (PROBE-lite): classify the failure
                                # type and surface a targeted hint without
                                # leaking expected values.
                                opaque = os.environ.get("MAVERICK_BENCHMARK_OPAQUE", "1") != "0"
                                from .coding_mode import classify_failure
                                fail_class, fail_hint = classify_failure(
                                    test_result.raw_output,
                                )
                                class_line = (
                                    f"Dominant failure class: {fail_class}.\n{fail_hint}"
                                    if fail_class != "other" else ""
                                )
                                if opaque:
                                    critique = (
                                        "Your patch did not pass the required tests.\n\n"
                                        f"{test_result.summary()}\n\n"
                                        f"{class_line}\n\n"
                                        "Revise based on your understanding of the "
                                        "code, not from inspecting the failing "
                                        "tests' expected values. Respond with a "
                                        "new FINAL using SEARCH/REPLACE blocks."
                                    ).strip()
                                else:
                                    critique = (
                                        "Your patch did not pass the required tests.\n\n"
                                        f"{test_result.summary()}\n\n"
                                        f"{class_line}\n\n"
                                        f"Recent test output:\n{test_result.raw_output}\n\n"
                                        "Inspect the failing tests, revise your patch, "
                                        "and respond with a new FINAL using "
                                        "SEARCH/REPLACE blocks."
                                    ).strip()
                                # Wave 9 fix (#2): one retry max so a flaky
                                # verifier or unfixable instance doesn't loop
                                # forever. The retry IS re-verified. W1-27:
                                # `_patch_validated` alone under-counts once a
                                # new FINAL resets it, so also stop once the
                                # (never-reset) revision-attempt cap is hit.
                                if (getattr(self, "_patch_validated", False)
                                        or getattr(self, "_patch_revision_attempts", 0)
                                            >= _MAX_PATCH_REVISION_ATTEMPTS):
                                    # Already revised, or out of revision
                                    # attempts; accept whatever this is.
                                    self._already_verified = True
                                    return AgentResult(
                                        final=final, role=self.role, name=self.name,
                                        verifier_confidence=test_result.score,
                                        verifier_critique=test_result.summary(),
                                        final_patch=getattr(self, "_final_patch", None),
                                    )
                                self._patch_validated = True
                                self._patch_revision_attempts = (
                                    getattr(self, "_patch_revision_attempts", 0) + 1)
                                messages.append({"role": "user", "content": critique})
                                continue

                        _coding_on = bool(coding_cfg and coding_cfg.enabled)
                        _tool_calls = self.ctx.budget.tool_calls
                        if (
                            _risk_proportional_verify_enabled()
                            and _final_is_low_risk(
                                final, coding=_coding_on, tool_calls=_tool_calls,
                            )
                        ):
                            verification_skipped = True
                            _skip_reason = "risk-proportional: low-risk answer"
                            bb.post(
                                self.name, "verify",
                                "verification skipped (risk-proportional: "
                                "low-risk answer, no tools/code)",
                            )
                        elif self._conversational_answer_skips_verification(
                            final, coding=_coding_on, tool_calls=_tool_calls,
                        ):
                            verification_skipped = True
                            _skip_reason = "conversational answer, no tools or code"
                            bb.post(
                                self.name, "verify",
                                "verification skipped (conversational answer: "
                                "no tools, no code)",
                            )
                        else:
                            try:
                                from .verifier import verify_final
                                verifier_attempted = True
                                # Loop 1: a high-disagreement swarm fan-out asks
                                # FINAL to face the cross-family ensemble.
                                verdict = await verify_final(
                                    self.brief, final, self.ctx.llm, self.ctx.budget,
                                    proposer_model=self.model,
                                    force_ensemble=bool(
                                        getattr(self.ctx, "escalate_verification", False)
                                    ),
                                )
                                # Stamp the verdict so the autonomy servo (Loop 2)
                                # can tighten the leash on low-confidence runs.
                                self.ctx.last_verifier_confidence = verdict.confidence
                            except BudgetExceeded:
                                verdict = None
                                # Say so in the record: a check that started but
                                # did not finish must never read as "checked".
                                bb.post(self.name, "verify",
                                        "verifier did not complete (budget)")
                            except Exception as e:  # pragma: no cover
                                bb.post(self.name, "error", f"verifier failed: {e}")
                                verdict = None

                        if verdict is not None and not verdict.accepts:
                            if getattr(self, "_verifier_revision_used", False):
                                self._already_verified = True
                                bb.post(
                                    self.name, "verify",
                                    "verifier rejected after retry; accepting "
                                    "second attempt per one-revision cap",
                                )
                                _reasons = _final_uncertainty_reasons(
                                    verifier_rejected=True,
                                    verifier_incomplete=False,
                                    disagreement=float(
                                        getattr(self.ctx, "last_disagreement", 0.0) or 0.0
                                    ),
                                    coding=bool(coding_cfg and coding_cfg.enabled),
                                )
                                return AgentResult(
                                    final=_final_with_uncertainty_note(final, _reasons),
                                    role=self.role, name=self.name,
                                    verifier_confidence=verdict.confidence,
                                    verifier_critique=verdict.critique,
                                    final_patch=getattr(self, "_final_patch", None),
                                )
                            self._already_verified = True
                            self._verifier_revision_used = True
                            bb.post(
                                self.name, "verify",
                                f"verifier rejected (conf={verdict.confidence:.2f}): "
                                f"{verdict.critique}",
                            )
                            # Capture the rejected draft as the "rejected" half of
                            # a DPO preference pair: the agent revises until the
                            # verifier accepts, so the accepted FINAL (high score)
                            # vs THIS rejected draft (low score) is the genuine
                            # quality gradient this architecture produces -- the
                            # only one it produces, since every shipped answer is
                            # driven to "good". Stashed on ctx (the same channel
                            # donation reads last_disagreement/credit from); a bug
                            # here must never break the run loop.
                            try:
                                self.ctx.last_rejected_attempts = (
                                    getattr(self.ctx, "last_rejected_attempts", []) or []
                                ) + [{
                                    "text": final,
                                    "confidence": float(verdict.confidence),
                                    "critique": verdict.critique or "",
                                }]
                            except Exception:  # pragma: no cover -- never block
                                pass
                            # Hand the critique to the proposer as a
                            # revision brief. One revision pass max --
                            # the second attempt is accepted regardless.
                            issues_block = (
                                "\n".join(f"  - {i}" for i in verdict.issues)
                                if verdict.issues else "  (no specific issues listed)"
                            )
                            messages.append({
                                "role": "user",
                                "content": (
                                    "A verifier rejected your FINAL answer. "
                                    "Revise and try again.\n\n"
                                    f"Verifier confidence: {verdict.confidence:.2f}\n"
                                    f"Critique: {verdict.critique}\n"
                                    f"Specific issues:\n{issues_block}\n\n"
                                    "Address each issue and respond with a "
                                    "new FINAL: <revised answer>."
                                ),
                            })
                            continue
                        if verdict is not None:
                            bb.post(
                                self.name, "verify",
                                f"verifier accepted (conf={verdict.confidence:.2f})",
                            )

                    bb.post(self.name, "finding", _finding_excerpt(final))
                    self.ctx.world.append_message(
                        self.ctx.goal_id, f"agent:{self.name}", final
                    )
                    # Stop hooks: the agent has decided on FINAL. Post-style
                    # (non-blocking) -- observers/loggers, cannot veto.
                    from .hooks import HookEvent
                    from .hooks import emit as _emit_hook
                    await _emit_hook(
                        HookEvent.STOP,
                        goal_id=self.ctx.goal_id, agent_role=self.role,
                        extra={"name": self.name, "final": final},
                    )
                    self._score_step(step_index=step, is_final=True)
                    if verification_skipped:
                        _vconf, _vcrit = 0.9, (
                            "verification skipped ("
                            + (_skip_reason or "risk-proportional: low-risk answer")
                            + ")"
                        )
                    elif verdict is not None:
                        _vconf, _vcrit = verdict.confidence, verdict.critique
                    elif verifier_attempted:
                        # Attempted but hit budget/error (verdict is None) must
                        # NOT report high confidence: a budget-starved run would
                        # otherwise be donated as a "high-confidence" trajectory
                        # it never verified (#612).
                        _vconf, _vcrit = 0.0, "verifier did not complete (budget)"
                    else:
                        # Roles that never verify keep the 1.0 default.
                        _vconf, _vcrit = 1.0, ""
                    _reasons = _final_uncertainty_reasons(
                        verifier_rejected=False,
                        verifier_incomplete=(verdict is None and verifier_attempted),
                        disagreement=float(
                            getattr(self.ctx, "last_disagreement", 0.0) or 0.0
                        ),
                        coding=bool(coding_cfg and coding_cfg.enabled),
                    )
                    return AgentResult(
                        final=_final_with_uncertainty_note(final, _reasons),
                        role=self.role, name=self.name,
                        verifier_confidence=_vconf,
                        verifier_critique=_vcrit,
                        final_patch=getattr(self, "_final_patch", None),
                    )
                bb.post(self.name, "observation", resp.text[:1000])

            if not resp.tool_calls:
                if resp.text:
                    # W1-25: reaching here means resp.text carried no FINAL:
                    # marker (find_final_marker_end returned None above) --
                    # the entire verify/patch-validation/STOP-hook pipeline
                    # only runs inside that branch. Give the model ONE
                    # chance to comply before accepting an un-verified
                    # answer as if it were fully confident.
                    if not self._final_marker_nudge_used:
                        self._final_marker_nudge_used = True
                        messages.append({
                            "role": "user",
                            "content": (
                                "Your response must end your turn with a line "
                                "starting `FINAL:` followed by your complete "
                                "answer -- required so it can be verified. "
                                "Please restate your answer now, ending with "
                                "`FINAL: <answer>`."
                            ),
                        })
                        continue
                    # Nudged once and still no marker: accept the text as
                    # final, but report honestly that verification never
                    # ran -- never the default verifier_confidence=1.0,
                    # which would claim a confidence no verifier produced.
                    from .coding_mode import from_env as _cm_from_env
                    try:
                        _coding_cfg = _cm_from_env()
                    except Exception:
                        _coding_cfg = None
                    _reasons = _final_uncertainty_reasons(
                        verifier_rejected=False, verifier_incomplete=False,
                        disagreement=0.0,
                        coding=bool(_coding_cfg and _coding_cfg.enabled),
                        no_final_marker=True,
                    )
                    return AgentResult(
                        final=_final_with_uncertainty_note(resp.text, _reasons),
                        role=self.role, name=self.name,
                        verifier_confidence=0.0,
                        verifier_critique="no FINAL: marker; verification did not run",
                    )
                return AgentResult(
                    error="empty response with no tools", role=self.role, name=self.name
                )

            # Tool-call boundary: honour a halt that arrived while the
            # model was producing this turn (e.g. the user hit Halt
            # during a long think) before executing any tool.
            try:
                killswitch.check()
            except killswitch.Halted as e:
                bb.post(self.name, "error", f"halted: {e}")
                return AgentResult(
                    error=f"halted: {e}", role=self.role, name=self.name,
                    stop_kind="killswitch", stop_detail=str(e),
                )
            if self._goal_cancelled():
                bb.post(self.name, "error", "cancelled via dashboard")
                return AgentResult(
                    error="cancelled via dashboard", role=self.role, name=self.name,
                )

            # Frontier-loop optimization: when the model emits 2+ tool
            # calls in one turn and EVERY one is parallel-safe (pure,
            # idempotent reads — read_file / list_dir / repo_map /
            # dep_graph), run them concurrently with asyncio.gather. This
            # is the dominant localization pattern ("read these 5 files")
            # and collapses N serial awaits into one round-trip's worth of
            # latency. A turn containing ANY stateful tool (shell, write,
            # spawn, ask_user, a rate-limited network tool) falls through
            # to the serial path below, so side-effect ordering and the
            # ask_user block-on-user semantics are unchanged. Disable with
            # MAVERICK_PARALLEL_TOOLS=0.
            run_parallel = (
                len(resp.tool_calls) > 1
                and _env_or_config("MAVERICK_PARALLEL_TOOLS", "agent", "parallel_tools", "1") != "0"
                and all(self._is_parallel_safe(tc.name) for tc in resp.tool_calls)
            )

            tool_results: list[dict] = []
            blocked = False

            def _answer_pending_tool_uses(
                reason: str,
                *,
                resp=resp,
                messages=messages,
                tool_results=tool_results,
            ) -> None:
                # #612: a control-flow stop (BudgetExceeded / Halted) can fire
                # mid-dispatch -- most often from budget.record_tool_call(),
                # which calls check() and can raise AFTER the assistant turn's
                # tool_use blocks are already in `messages`. If we unwind now,
                # the saved/resumed history (or a parent that keeps these
                # messages) has tool_use ids with no matching tool_result ->
                # Anthropic 400 on the next call. Append an error tool_result
                # for every still-unanswered tool_use, then let the caller
                # re-raise/return so the stop is NOT swallowed.
                answered = {tr["tool_use_id"] for tr in tool_results}
                pending = [tc for tc in resp.tool_calls if tc.id not in answered]
                if not pending:
                    return
                for tc in pending:
                    tool_results.append(self._make_tool_result(tc.id, reason))
                messages.append({"role": "user", "content": tool_results})

            if run_parallel:
                import asyncio as _asyncio
                # Account every call up front; record_tool_call mirrors
                # the serial path (one per tool, same count). A budget trip
                # here raises BEFORE any tool ran -- answer every pending
                # tool_use so the turn isn't left with orphan tool_use blocks.
                try:
                    for _tc in resp.tool_calls:
                        self.ctx.budget.record_tool_call()
                except BudgetExceeded:
                    _answer_pending_tool_uses(
                        "ERROR: tool not executed (budget exceeded)"
                    )
                    raise

                # Per-host concurrency cap (#434): same-host network reads in
                # this turn are throttled by a semaphore so a fan-out of reads
                # to one host can't hammer it / trip its rate limit; local and
                # cross-host calls stay fully concurrent (no-op context).
                from . import net_concurrency as _netcc

                async def _run_capped(tc):
                    async with _netcc.limit(tc.name, tc.input):
                        return await self._run_tool(tc.name, tc.input)

                # return_exceptions=True: tools.run swallows its own errors, but
                # the shield scan / PreToolUse hooks inside _run_tool can still
                # raise. Without this, one such raise propagates out of gather,
                # discards the sibling results, and leaves the assistant turn's
                # tool_use blocks with no matching tool_results -> the next API
                # call 400s. Convert a raised exception into an error
                # tool_result so every tool_use is still answered -- but
                # re-raise control-flow signals (budget/halt) so a stop isn't
                # silently downgraded to a tool error.
                outputs = await _asyncio.gather(
                    *(_run_capped(tc) for tc in resp.tool_calls),
                    return_exceptions=True,
                )
                from . import killswitch as _ks
                from .budget import BudgetExceeded as _BE
                _norm: list[str] = []
                _tool_stop: Exception | None = None
                for o in outputs:
                    if isinstance(o, (_BE, _ks.Halted, AuditRefused)):
                        # Keep budget/halt precedence, but finish normalizing:
                        # every sibling already ran under gather, and a stop
                        # may have occurred after its own dispatch began.
                        if _tool_stop is None or (
                            isinstance(_tool_stop, AuditRefused)
                            and not isinstance(o, AuditRefused)
                        ):
                            _tool_stop = o
                        _stop_reason = "audit refused" if isinstance(o, AuditRefused) else type(o).__name__
                        _norm.append(
                            f"ERROR: tool interrupted ({_stop_reason}); "
                            "reconcile state before retrying"
                        )
                        continue
                    _norm.append(
                        o if isinstance(o, str)
                        else f"ERROR: tool raised {type(o).__name__}: {o}"
                    )
                outputs = _norm
                if _tool_stop is not None:
                    # Concurrent siblings may already have completed. Preserve
                    # their actual results, answer every tool_use, and stop
                    # before another model turn; never label them unexecuted.
                    tool_results.extend(
                        self._make_tool_result(tc.id, output)
                        for tc, output in zip(resp.tool_calls, outputs, strict=False)
                    )
                    messages.append({"role": "user", "content": tool_results})
                    raise _tool_stop
                # Preserve original call order in the results (matched by
                # tool_use_id, but ordering keeps traces readable).
                for tc, output in zip(resp.tool_calls, outputs, strict=False):
                    bb.post(
                        self.name, "observation",
                        f"tool={tc.name} -> {output[:500]}",
                    )
                    if not isinstance(output, _NotExecutedToolResult):
                        self._score_step(
                            step_index=step,
                            tool_name=tc.name,
                            tool_succeeded=not _tool_call_failed(output),
                        )
                    tool_results.append(self._make_tool_result(tc.id, output))
            else:
                for tc in resp.tool_calls:
                    # Per-tool halt check: a serial turn may run a long
                    # shell command; honour a halt that lands mid-turn.
                    try:
                        killswitch.check()
                    except killswitch.Halted as e:
                        # #612: answer any tool_use we've already passed (and
                        # this + remaining ones) so the assistant turn isn't
                        # left with orphan tool_use blocks before we unwind.
                        _answer_pending_tool_uses("ERROR: tool not executed (halted)")
                        bb.post(self.name, "error", f"halted: {e}")
                        return AgentResult(
                            error=f"halted: {e}", role=self.role, name=self.name,
                        )
                    if self._goal_cancelled():
                        _answer_pending_tool_uses(
                            "ERROR: tool not executed (goal cancelled)"
                        )
                        bb.post(self.name, "error", "cancelled via dashboard")
                        return AgentResult(
                            error="cancelled via dashboard", role=self.role, name=self.name,
                        )
                    # #612: record_tool_call() calls check() and can raise
                    # mid-turn; answer pending tool_use blocks, then re-raise
                    # so the budget trip still stops the run cleanly.
                    try:
                        self.ctx.budget.record_tool_call()
                    except BudgetExceeded:
                        _answer_pending_tool_uses(
                            "ERROR: tool not executed (budget exceeded)"
                        )
                        raise
                    # #612: a stateful serial tool (notably `spawn_subagent`,
                    # whose child shares this Budget) can trip BudgetExceeded
                    # — or a halt can land — DURING execution. That raise would
                    # leave this tool_use + any siblings already dispatched this
                    # turn without matching tool_results -> Anthropic 400 on a
                    # resume/parent. Answer every pending tool_use, then re-raise
                    # so the stop still halts the run cleanly.
                    try:
                        output = await self._run_tool(tc.name, tc.input)
                    except (AuditRefused, BudgetExceeded, killswitch.Halted) as exc:
                        _stop_reason = "audit refused" if isinstance(exc, AuditRefused) else type(exc).__name__
                        _answer_pending_tool_uses(
                            f"ERROR: tool interrupted ({_stop_reason}); "
                            "reconcile state before retrying"
                        )
                        raise
                    if tc.name == "ask_user" and not isinstance(output, _NotExecutedToolResult):
                        from .autonomy import assume_when_headless
                        if assume_when_headless():
                            # No human is available to answer (headless /
                            # autonomous run). Blocking forever on a question
                            # nobody will answer is worse than proceeding -- and
                            # a blocked run never reaches FINAL, so it also never
                            # distills what it learned. Turn the question into a
                            # directive to assume and continue, and require the
                            # assumption be stated so the choice stays auditable.
                            # Off by default (MAVERICK_AUTONOMOUS / [autonomy]
                            # headless_assume); unchanged blocking otherwise.
                            output = (
                                "AUTONOMOUS MODE: no human is available to "
                                "answer questions. Do not wait. Choose the most "
                                "reasonable assumption, state it explicitly in "
                                "your FINAL answer, and continue."
                            )
                        else:
                            blocked = True
                    bb.post(
                        self.name, "observation",
                        f"tool={tc.name} -> {output[:500]}",
                    )
                    if not isinstance(output, _NotExecutedToolResult):
                        self._score_step(
                            step_index=step,
                            tool_name=tc.name,
                            tool_succeeded=not _tool_call_failed(output),
                        )
                    tool_results.append(self._make_tool_result(tc.id, output))
                    # Governed-action lineage (opt-in via [actions] enable /
                    # MAVERICK_GOVERNED_ACTIONS; off by default). Record a
                    # tamper-evident link for each CONSEQUENTIAL tool call so a
                    # run's actions are traceable. Fail-open: never breaks a run.
                    try:
                        from . import governed_actions as _gov
                        if getattr(self, "_lineage_on", None) is None:
                            self._lineage_on = _gov.enabled()
                        if self._lineage_on and not isinstance(output, _NotExecutedToolResult):
                            _gov.record_tool_lineage(
                                self.ctx.goal_id, tc.name, tc.input,
                                skills=tuple(sorted(self.ctx.skills_used or ())),
                                actor=self.name)
                    except Exception:
                        pass

            # Step-budget awareness: when only a few tool-using turns remain
            # before max_steps force-stops the run, tell the model so it
            # synthesizes a FINAL now instead of starting new work and getting
            # cut off with no answer. Appended after the tool_results (text
            # after tool_result blocks is a valid user turn); only on turns that
            # ran tools, which is exactly when a working agent risks the cutoff.
            # W1-44: what other agents posted to the blackboard since this
            # agent's last tool-running turn, as a trailing text block (same
            # slot as the step-budget warning). Own posts and sealed agents
            # are excluded; the block is bounded. Best-effort: never let a
            # render problem break the turn.
            if tool_results and _BB_TURN_DELTA_ENTRIES:
                try:
                    if (tracker := getattr(self.ctx, "ifc", None)) is not None:
                        tracker.read_blackboard(
                            self.name, bb.posters_since(getattr(self, "_bb_cursor", 0)))
                    _bb_delta, self._bb_cursor = bb.render_since(
                        getattr(self, "_bb_cursor", 0), exclude_agent=self.name,
                        max_entries=_BB_TURN_DELTA_ENTRIES,
                    )
                except Exception:  # pragma: no cover -- coordination hint only
                    _bb_delta = ""
                if _bb_delta:
                    tool_results.append({
                        "type": "text",
                        "text": (
                            "New on the swarm blackboard since your last turn "
                            "(other agents' posts; use them to adjust your plan):\n"
                            + _bb_delta
                        ),
                    })
            remaining = self.max_steps - 1 - step if self.max_steps is not None else 0
            if tool_results and _STEP_BUDGET_WARNING and 0 < remaining <= _STEP_BUDGET_WARNING:
                tool_results.append({
                    "type": "text",
                    "text": (
                        f"⚠ Step budget almost exhausted: about {remaining} more "
                        "tool-using turn(s) remain before this run is force-stopped. "
                        "Prioritize giving your FINAL: answer now with the best "
                        "result you have rather than starting new work."
                    ),
                })
            messages.append({"role": "user", "content": tool_results})
            # Speculative tool execution (opt-in via [tools] speculative):
            # while the model reads these results, re-run the side-effect-free
            # reads this agent keeps repeating so the next ask is a cache hit.
            self._maybe_speculate_reads()

            if blocked:
                return AgentResult(blocked_on_user=True, role=self.role, name=self.name)

        # Wave 12 hotfix: when the agent loop exhausts max_steps without
        # emitting FINAL, the workdir may STILL contain edits made via
        # `str_replace_editor` (the secondary tool channel). The May 26
        # smoke surfaced 3/6 instances where the agent edited via the
        # tool but never produced a FINAL — those instances reported
        # `no-diff` even though the patch was already on disk.
        # Salvage that work by rendering the workdir as the final_patch
        # if there are uncommitted changes.
        try:
            from pathlib import Path as _Path

            from .edit_format import render_diff
            workdir = _Path(getattr(self.ctx.sandbox, "workdir", "."))
            if (workdir / ".git").exists():
                rendered = render_diff(workdir)
                if rendered and rendered.strip():
                    return AgentResult(
                        error=f"hit max_steps={self.max_steps}; "
                              "captured workdir diff as final_patch",
                        final_patch=rendered,
                        role=self.role,
                        name=self.name,
                    )
        except Exception:
            pass
        return AgentResult(
            error=f"hit max_steps={self.max_steps}",
            role=self.role,
            name=self.name,
        )
