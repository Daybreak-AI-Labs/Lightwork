"""What a person should know about an answer before relying on it.

Three facts, each read from the task's own record and never assumed:

* whether the extra check reviewed the answer (``Checked``) or why it did not
  (``Not checked: <reason>``), from the main agent's recorded check steps;
* which agent answered and when it last changed (:mod:`.agent_changelog`);
* whether the backup AI model was used because the main one was unavailable.
"""
from __future__ import annotations

import re

from .step_narration import agent_parts

_TESTS = re.compile(r"FAIL_TO_PASS: (\d+)/(\d+) pass; PASS_TO_PASS: (\d+)/(\d+) pass")
_EVENT_WINDOW = 2000

CHECKED = "A separate AI check compared this answer with your request before it was shown."
# The main agent's recorded check outcomes, newest wins. Coding-mode patch
# checks along the way are not outcomes and are ignored.
_OUTCOMES = (
    ("verifier accepted", "checked", CHECKED),
    ("verifier rejected after retry", "concerns",
     "The extra check still had concerns after one revision."),
    ("verifier rejected", "not_checked",
     "The extra check asked for changes, and the revised answer was not checked again."),
    ("verification skipped (conversational", "not_checked",
     "Short conversational answers that use no tools or files are not sent to the extra check."),
    ("verification skipped (risk-proportional", "not_checked",
     "Your workspace skips the extra check for low-risk answers that use no tools or code."),
    ("verifier did not complete", "not_checked",
     "The extra check started but could not finish within the spending limit."),
    ("verifier failed", "not_checked", "The extra check could not run."),
)
LABELS = {"checked": "Checked", "concerns": "Checked with concerns",
          "not_checked": "Not checked", "hidden": "Check result not shown"}
NOT_SENT = "This answer was not sent to the extra check."
HIDDEN = "Step details are withheld in this workspace, so the check result cannot be shown."
FALLBACK = "The backup AI model was used for this answer because the main one was unavailable."


def _recent_events(world, goal_id: int) -> list:
    recent = getattr(world, "recent_goal_events", None)
    if callable(recent):
        return recent(goal_id, limit=_EVENT_WINDOW)
    return world.goal_events(goal_id, limit=_EVENT_WINDOW)


def _latest_root_run(events: list) -> list:
    """Events of the last main-agent run (a resumed task starts a fresh one)."""
    root = None
    for event in events:
        if agent_parts(event.agent)[1] == 0:
            root = event.agent
    return [event for event in events if event.agent == root] if root else []


def _tests_outcome(content: str) -> tuple[str, str]:
    match = _TESTS.search(content)
    if match is None:
        return "not_checked", "The required tests could not run."
    a, b, c, d = (int(value) for value in match.groups())
    if a == b and c == d:
        return "checked", "The required tests passed."
    return "concerns", "The required tests did not all pass."


def _outcome(event, withheld: frozenset[str]) -> tuple[str, str] | None:
    content = str(event.content or "").strip()
    if event.kind == "verify" and content in withheld:
        return "hidden", HIDDEN
    lowered = content.casefold()
    if event.kind == "verify" and lowered.startswith("test-driven verifier"):
        return _tests_outcome(content)
    for prefix, state, reason in _OUTCOMES:
        if lowered.startswith(prefix) and (event.kind == "verify" or prefix == "verifier failed"):
            return state, reason
    return None


def verification(events: list, withheld: frozenset[str] = frozenset()) -> dict:
    """The check outcome of the latest main-agent run, exactly as recorded."""
    state, reason = "not_checked", NOT_SENT
    for event in _latest_root_run(events):
        found = _outcome(event, withheld)
        if found is not None:
            state, reason = found
    return {"state": state, "label": LABELS[state], "reason": reason}


def used_backup_model(events: list) -> bool:
    """Whether any step of the latest run was answered by the backup model."""
    root_run = _latest_root_run(events)
    start = root_run[0].id if root_run else 0
    return any(event.kind == "model_fallback" and event.id >= start for event in events)


def answer_facts(world, goal, withheld: frozenset[str] = frozenset()) -> dict:
    """Template context for a finished answer; empty while there is none."""
    if getattr(goal, "status", "") != "done" or not getattr(goal, "result", ""):
        return {"answer_check": None, "answer_agent": None, "answer_fallback": False}
    from . import agent_changelog

    try:
        events = _recent_events(world, goal.id)
    except Exception:  # an unreadable record never becomes a "checked" claim
        events = []
    answered_at = getattr(goal, "updated_at", None)
    try:
        label = agent_changelog.version_label(goal.domain or "", answered_at=answered_at)
    except Exception:  # pragma: no cover -- the label is best effort
        label = None
    if label is not None:
        from .plain_labels import datetime_text

        label["updated_text"] = datetime_text(label["updated_at"], date_only=True) if label["updated_at"] else ""
        for row in (*label["changes"], *label["changed_since"]):
            row["date_text"] = datetime_text(row["ts"], date_only=True)
    return {"answer_check": verification(events, withheld), "answer_agent": label,
            "answer_fallback": used_backup_model(events)}


__all__ = ["CHECKED", "FALLBACK", "HIDDEN", "LABELS", "NOT_SENT", "answer_facts",
           "used_backup_model", "verification"]
