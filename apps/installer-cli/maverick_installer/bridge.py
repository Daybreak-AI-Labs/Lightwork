"""Optional JSON setup sidecar for clients that explicitly invoke it.

Reads wizard steps from stdin, writes JSON-formatted next-step
descriptors to stdout. The shipped desktop installer instead bootstraps
headlessly and opens dashboard onboarding; it does not invoke this sidecar.

Protocol (line-delimited JSON):

  stdin:  <user_answer_string>  (empty string for first call)
  stdout: {"id": "<step_id>", "question": "...", "choices": ["...", ...]}

When the wizard is complete, sidecar emits ``{"id": "__done__", ...}``
and exits.

The six questions mirror the CLI's consumer mode exactly (name,
governance level, sign-in key, working directory, budget, the team that
tries Lightwork first) and the resulting config is written by the SAME
``write_consumer_config``
helper the CLI uses -- and the governance menu is imported from the
SAME ``GOVERNANCE_CHOICES`` list the CLI shows -- so the GUI and CLI
can't drift.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

from .wizard import (
    CONFIG_FILE,
    DEFAULT_GOVERNANCE_PROFILE,
    ENV_FILE,
    GOVERNANCE_CHOICES,
    GOVERNANCE_PROFILES,
    _existing_setup,
    first_outcome_choices,
    recover_pending_setup,
    write_consumer_config,
)


def _step(id: str, question: str, choices: list[str], *, kind: str = "choice") -> dict[str, Any]:
    # `kind` lets the Svelte UI render the right control: a choice list,
    # a free-text box, or a masked secret field.
    return {"id": id, "question": question, "choices": choices, "kind": kind}


def _send(step: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(step) + "\n")
    sys.stdout.flush()


def _recv() -> str:
    return sys.stdin.readline().strip()


def run() -> None:
    """Sidecar entry point: the six-question consumer flow.

    Matches ``maverick_installer.wizard.run_consumer`` question for
    question. No jargon, no provider picker, no DevTools paste.
    """
    default_name = os.environ.get("USER") or os.environ.get("USERNAME") or ""
    default_workdir = str(Path.home() / "Documents" / "Maverick")

    # The first invoke carries no answer; consume it and ask Q1.
    _recv()
    try:
        if recover_pending_setup():
            _send(_step("__done__", "Lightwork finished the interrupted setup. Open Lightwork.", []))
            return
    except (OSError, RuntimeError) as exc:
        raise RuntimeError(
            "Interrupted Lightwork setup needs assistance; saved files were not changed."
        ) from exc
    if _existing_setup():
        if not CONFIG_FILE.exists() and ENV_FILE.exists():
            raise FileExistsError(
                "Lightwork found saved keys but no completed setup. Nothing was changed. "
                "Ask your Lightwork administrator or support to recover this installation."
            )
        raise FileExistsError(
            "Lightwork found saved setup files. Nothing was changed. "
            "Open Lightwork Settings to change provider keys, task spending, "
            "and other available controls. Some installation choices are not there yet."
        )

    # Q1: name
    _send(_step(
        "name",
        "What should we call you?",
        [],
        kind="text",
    ))
    user_name = _recv().strip() or default_name or "you"

    # Q2: governance level. The menu is wizard.GOVERNANCE_CHOICES verbatim --
    # the CLI's list is the UX source of truth; do not re-author it here.
    # Resolution is deliberately strict: a BLANK answer means "skipped" and
    # takes the default, but a non-blank answer that names no known level is
    # a protocol bug in the UI and must fail LOUDLY. Silently falling back
    # would downgrade a bank that picked "regulated" to the least-governed
    # tier with no error -- the one failure direction this selector must
    # never have.
    _send(_step(
        "governance",
        "What kind of business is this for? Every level learns and "
        "improves; the level sets how much governance wraps it.",
        list(GOVERNANCE_CHOICES),
    ))
    raw_answer = _recv().strip()
    if not raw_answer:
        profile = DEFAULT_GOVERNANCE_PROFILE
    else:
        first = raw_answer.split()[0].lower()
        profile = first if first in GOVERNANCE_PROFILES else next(
            (name for name in GOVERNANCE_PROFILES if name in raw_answer.lower()),
            "",
        )
        if not profile:
            raise ValueError(
                f"unrecognized governance level answer: {raw_answer!r} "
                f"(expected one of {sorted(GOVERNANCE_PROFILES)})"
            )

    # Q3: API key (sign-in). The UI shows the console.anthropic.com link
    # and a masked field. Blank == skip (config saved without a key).
    _send(_step(
        "api_key",
        "Paste your Anthropic API key. Get one at "
        "https://console.anthropic.com/settings/keys (or leave blank to skip).",
        [],
        kind="secret",
    ))
    raw_key = _recv().strip()
    keys: dict[str, str] = {"ANTHROPIC_API_KEY": raw_key} if raw_key else {}

    # Q4: working directory
    _send(_step(
        "workdir",
        "Where can Lightwork work? (a folder it can create files in)",
        [],
        kind="text",
    ))
    workdir = _recv().strip() or default_workdir

    # Q5: budget
    _send(_step(
        "budget",
        "Task limits for each goal? Standard is $2 and 30 minutes.",
        ["Standard ($2, 30 minutes)", "No task limit", "$1", "$5", "$20"],
    ))
    budget_ans = _recv().strip() or "Standard ($2, 30 minutes)"
    if budget_ans not in {"Standard ($2, 30 minutes)", "No task limit", "$1", "$5", "$20"}:
        raise ValueError("Choose standard limits, no task limit, or one of the offered spending limits")
    if budget_ans == "No task limit":
        budget = {"task_limits_enabled": False}
    else:
        budget = {"max_dollars": (float(budget_ans[1:]) if budget_ans.startswith("$") else 2.0),
                  "max_wall_seconds": 1800.0}

    # Q6: the team that tries Lightwork first (its sample task and files).
    from maverick.sample_outcomes import DEFAULT_DEPARTMENT, resolve_department

    _send(_step(
        "first_outcome",
        "Which team will try Lightwork first? Its first task runs on bundled sample data.",
        first_outcome_choices(),
    ))
    team = _recv().strip()
    department = resolve_department(team, default=None) if team else DEFAULT_DEPARTMENT
    if department is None:
        raise ValueError("Choose one of the listed teams")
    backend = write_consumer_config(
        user_name=user_name, keys=keys, workdir=workdir, budget=budget,
        profile=profile, first_outcome=department,
        protect_existing=True,
    )

    if keys:
        msg = f"Setup saved, {user_name}. Lightwork is ready to answer questions."
    else:
        msg = (
            f"Setup saved, {user_name}. Add an API key later in Lightwork Settings."
        )
    if backend == "local":
        msg += " An isolated work area could not be connected, so file changes and program execution are off until you connect one in Permissions."
    _send(_step("__done__", msg, []))


if __name__ == "__main__":
    try:
        run()
    except Exception as e:
        _send({"id": "__error__", "question": str(e), "choices": [], "kind": "error"})
        sys.exit(1)
