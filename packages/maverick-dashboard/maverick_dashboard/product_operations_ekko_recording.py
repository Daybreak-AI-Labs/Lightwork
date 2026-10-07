"""Read-only Copilot handoff to the ordinary consented Ekko recorder controls."""
from __future__ import annotations

from fastapi import HTTPException

from . import ekko_ops_api as native
from .product_operations import Adapter, EmptyInput, Preview, Snapshot


def snapshot(request, selection):
    if selection:
        raise HTTPException(422, "Open Ekko to choose your device and recording permissions.")
    state = native.recording_options(request)
    return Snapshot(state["context"], "Open the complete activity recording controls",
        "Choose this enrolled device and confirm what Ekko may record before starting. "
        "Opening the controls never records activity or changes your choices.",
        {"automatic_recording_available": state["active_app"],
         "availability": state["availability_message"], "computer": state["platform"]})


def prepare(request, selection, _arguments):
    current = snapshot(request, selection)
    return Preview(current.summary, native={"ekko_recording_review": {
        "scope": "current_page", "context": current.revision}})


def entries():
    return (Adapter("learning.ekko_recording_controls", "Open activity recording controls", "learning",
        "/ekko#ekko-recording", ("/ekko",), EmptyInput, snapshot, prepare,
        aliases=("record the apps I use", "open activity recording controls",
                 "start recording activity", "how can Ekko record activity")),)
