"""Copilot reads the same current, owned department boards as ordinary pages."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from maverick.governed_operations import digest

from . import department_boards as native
from .product_operations import Adapter, Preview, Snapshot, StrictInput


class Period(StrictInput):
    days: Literal[30, 90, 180, 365] = 90


def _summary(board, value):
    if board == "overview":
        return {"Your tasks, all time": value["totals"]["total"], "In progress": value["totals"]["active"],
                "Finished, all time": value["totals"]["done"], "Waiting": value["totals"]["blocked"],
                "Started in this period": value["window"]["started"], "Finished tasks last updated in this period": value["window"]["delivered"]}
    return {"Known runtime cost in USD": value["totals"]["dollars"], "Finished attempts": value["totals"]["runs"],
            "Attempts with missing prices": value["totals"]["unknown_prices"], "Running attempts": value["outcomes"]["running"],
            "Known runtime cost, all time in USD": value["all_time"]["dollars"]}


def _adapter(board, label, family):
    def snapshot(request, selection):
        value = native.build(request, board)
        summary = _summary(board, value)
        # The model guide receives counts and scope only, never titles, task
        # results, decrypted outcomes, owner identities or unscoped totals.
        return Snapshot(digest({"context": value["context"], "figures": summary}),
            "Current native records for your own permitted work", native.SCOPE_NOTICE,
            {"figures": summary}, fields=[{"name": "days", "label": "Report period", "kind": "select", "value": 90,
                "choices": [{"value": days, "label": label} for days, label in
                            ((30, "30 days"), (90, "90 days"), (180, "6 months"), (365, "1 year"))]}])
    def prepare(request, selection, arguments):
        value = native.build(request, board, arguments["days"])
        return Preview("Current permitted report. No work or settings changed.",
            native={"Scope": native.SCOPE_NOTICE, "Period in days": value["days"], "Figures": _summary(board, value),
                    "Checked at": datetime.fromtimestamp(value["generated_at"], timezone.utc).strftime("%b %d, %Y at %H:%M UTC"),
                    **({"Cost limits": native.COST_NOTICE} if board == "spend" else {})})
    return Adapter("boards." + board + ".read", label, family, "/" + board, ("/" + board,), Period, snapshot, prepare)


def entries():
    return [_adapter("overview", "Read my permitted work overview", "tasks"),
            _adapter("spend", "Read my permitted spending", "budget")]
