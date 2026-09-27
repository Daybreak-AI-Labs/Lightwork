"""Production twin: re-run a real goal against exactly what production saw.

Recording (opt-in, ``[immune] twin_record``) captures every tool result a goal
received at the agent's single execution point, sealed and tenant-scoped. A
replay runs a fresh goal with the same title and description while serving
those recorded results in place of the tools: the model gets production's
inputs, nothing live is touched, and no marker tells it that it is a test.
That makes a twin the unwatched arm for :func:`invariance.awareness_gap`, and a
safe way to try a new model, prompt or policy on real work.

Coordination tools (spawn, peer bus) still run, so the swarm re-forms; their
children replay too. ``ask_user`` is replayed, so a twin never pings a human.
A call production never made -- different tool or arguments -- is not executed:
it returns an ordinary tool failure and is reported. The divergence is the finding.
"""
from __future__ import annotations

import base64
import hashlib
import json
from collections import defaultdict
from collections.abc import Awaitable, Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from pathlib import Path

from ..capability import COORDINATION_TOOLS
from ..paths import data_dir
from . import _ledger

LIVE_IN_TWIN = COORDINATION_TOOLS - {"ask_user"}
MAX_RECORDING_BYTES = 64 * 1024 * 1024  # per goal; later calls are not recorded


def recording_path(goal_id: int) -> Path:
    return data_dir("immune", "twin", f"{int(goal_id)}.jsonl")


def recording_enabled() -> bool:
    try:
        from ..config import get_immune
        return get_immune()["twin_record"]
    except Exception:
        return False


def _key(tool: str, args: dict) -> str:
    return hashlib.sha256(_ledger.canonical([tool, args])).hexdigest()


def _context(goal_id: int) -> str:
    return f"twin/{int(goal_id)}"


def record(goal_id: int, tool: str, args: dict, output: str) -> None:
    """Append one tool result to the goal's recording (sealed when at-rest
    encryption is on). Best-effort: a recording failure never fails a run."""
    from ..crypto_at_rest import at_rest_enabled, seal_bound
    path = recording_path(goal_id)
    body = _ledger.canonical({"args": args, "output": output})
    entry = {"tool": tool, "key": _key(tool, args)}
    if at_rest_enabled():
        entry["sealed"] = base64.b64encode(seal_bound(body, _context(goal_id))).decode()
    else:
        entry["body"] = body.decode()
    if path.exists() and path.stat().st_size >= MAX_RECORDING_BYTES:
        return
    _ledger.append(path, entry)


def load(goal_id: int) -> list[dict]:
    """The goal's recording as ``[{tool, key, args, output}]`` in call order."""
    from ..crypto_at_rest import unseal_bound
    out = []
    for entry in _ledger.read(recording_path(goal_id)):
        body = (unseal_bound(base64.b64decode(entry["sealed"]), _context(goal_id))
                if "sealed" in entry else entry["body"].encode())
        out.append({"tool": entry["tool"], "key": entry["key"], **json.loads(body)})
    return out


@dataclass
class Replay:
    """Serves a recording: each (tool, arguments) pair in production order."""
    records: list[dict]
    served: int = 0
    unmatched: list[dict] = field(default_factory=list)
    _queues: dict[str, list[str]] = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self._queues = defaultdict(list)
        for r in self.records:
            self._queues[r["key"]].append(r["output"])

    def serve(self, tool: str, args: dict) -> str:
        queue = self._queues.get(_key(tool, args))
        if queue:
            self.served += 1
            return queue.pop(0)
        self.unmatched.append({"tool": tool, "args": args})
        return f"ERROR: {tool} failed; no result was returned."

    def report(self) -> dict:
        return {"recorded": len(self.records), "served": self.served,
                "unserved": sum(len(q) for q in self._queues.values()),
                "unmatched": self.unmatched}


_ACTIVE: ContextVar[Replay | None] = ContextVar("maverick_twin_replay", default=None)


@contextmanager
def replaying(replay: Replay) -> Iterator[Replay]:
    """Run everything started inside (including spawned agents) as a twin."""
    token = _ACTIVE.set(replay)
    try:
        yield replay
    finally:
        _ACTIVE.reset(token)


async def dispatch(goal_id: int, tool: str, args: dict,
                   run: Callable[[str, dict], Awaitable[str]]) -> str:
    """The agent's tool execution, routed through the twin: replay when one is
    active, otherwise run live and record when recording is on."""
    replay = _ACTIVE.get()
    if replay is not None and tool not in LIVE_IN_TWIN:
        return replay.serve(tool, args)
    output = await run(tool, args)
    if replay is None and tool not in LIVE_IN_TWIN and recording_enabled():
        try:
            record(goal_id, tool, args, output if isinstance(output, str) else str(output))
        except Exception:  # noqa: BLE001 -- observation never breaks production
            pass
    return output


def replays_path() -> Path:
    return data_dir("immune", "twin", "replays.jsonl")


def replay_goal(world, goal_id: int, run: Callable[[int], str | None]) -> dict:
    """Replay production goal ``goal_id`` as a new goal with the same title,
    description, owner, principal, domain and project; ``run(twin_goal_id)``
    executes it (the caller's normal runner). Returns and ledgers the report."""
    goal = world.get_goal(goal_id)
    if goal is None:
        raise LookupError(f"no goal #{goal_id}")
    records = load(goal_id)
    if not records:
        raise LookupError(f"goal #{goal_id} has no twin recording "
                          "([immune] twin_record was off when it ran)")
    twin_goal = world.create_goal(
        goal.title, goal.description or "", owner=goal.owner,
        execution_principal=goal.execution_principal, domain=goal.domain,
        project_id=goal.project_id)
    replay = Replay(records)
    with replaying(replay):
        status = run(twin_goal)
    report = {"production_goal": goal_id, "twin_goal": twin_goal,
              "production_status": goal.status, "twin_status": status, **replay.report()}
    # The ledger keeps counts only: unmatched arguments may hold tenant data.
    _ledger.append(replays_path(), {**report, "unmatched": len(replay.unmatched)})
    return report
