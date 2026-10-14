"""Copilot handoff to the current, authorized Book of Work editor."""
from __future__ import annotations

from urllib.parse import quote

from fastapi import HTTPException
from maverick import shoring
from maverick.governed_operations import digest
from pydantic import Field

from .auth import require_permission
from .product_operations import Adapter, Preview, Snapshot, StrictInput

_NEW = "__new__"


class AreaInput(StrictInput):
    key: str = Field(min_length=1, max_length=64)


def _areas(request, selection):
    require_permission(request, "operate")
    if selection:
        raise HTTPException(422, "Choose a business area in the Book of Work control.")
    if not shoring.enabled():
        raise HTTPException(404, "Autonomous shoring is turned off.")
    try:
        return shoring.load(strict=True)
    except (OSError, RuntimeError) as exc:
        raise HTTPException(503, "The Book of Work is unavailable. Open Shoring to check it.") from exc


def snapshot(request, selection):
    areas = _areas(request, selection)
    choices = [{"value": _NEW, "label": "Add a new business area"}]
    choices.extend({"value": area.key, "label": "Edit " + area.name} for area in areas)
    return Snapshot(
        digest([area.to_dict() for area in areas]),
        "Current Book of Work",
        "Choose a business area, then open its complete service-level form. No changes are made in Copilot.",
        {"Saved business areas": len(areas)},
        [{"name": "key", "label": "Business area", "kind": "select", "required": True,
          "choices": choices}],
    )


def prepare(request, selection, arguments):
    areas = _areas(request, selection)
    key = arguments["key"]
    if key == _NEW:
        label, href = "Add a business area", "/shoring#sh-form"
    else:
        area = next((area for area in areas if area.key == key), None)
        if area is None:
            raise HTTPException(409, "That business area changed. Refresh the Book of Work.")
        label = "Edit " + area.name
        href = "/shoring?edit=" + quote(key, safe="") + "#sh-form"
    return Preview(
        "Open the complete Book of Work form to review its owner, stage and service-level targets before saving.",
        native={"open": {"label": label, "href": href}},
    )


def entries():
    return (Adapter(
        "shoring.area.edit", "Add or update a business area and its service targets",
        "tasks", "/shoring#sh-form", ("/shoring",), AreaInput,
        snapshot, prepare,
        aliases=("autonomous shoring", "set service-level target", "edit book of work"),
    ),)
