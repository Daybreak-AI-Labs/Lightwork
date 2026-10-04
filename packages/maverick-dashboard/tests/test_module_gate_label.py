"""The module gate's button keeps acronyms and names in their own case.

``Turn on {{ label|lower }}`` produced "Turn on ai system tasks" and "Turn on
no-code grc studio". Only the first letter of an ordinary phrase is lowered
now; acronyms ("AI", "DSAR") and names ("Databricks", "Consumer Trust
Center") keep their capitals.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from jinja2 import Environment, FileSystemLoader

TEMPLATES = Path(__file__).resolve().parents[1] / "maverick_dashboard" / "templates"


def _button(label: str) -> str:
    env = Environment(loader=FileSystemLoader(str(TEMPLATES)), autoescape=True)
    html = str(env.get_template("_module_gate.html").module.module_gate("example", label))
    start = html.index('class="btn btn--primary mg-on">') + len('class="btn btn--primary mg-on">')
    return html[start:html.index("</button>", start)]


@pytest.mark.parametrize(("label", "button"), [
    ("AI system tasks", "Turn on AI system tasks"),
    ("AI evidence analysis", "Turn on AI evidence analysis"),
    ("DSAR fulfillment", "Turn on DSAR fulfillment"),
    ("EU AI Act workflows", "Turn on EU AI Act workflows"),
    ("No-code GRC studio", "Turn on no-code GRC studio"),
    ("Overall AI risk scoring", "Turn on overall AI risk scoring"),
    ("Consent delivery", "Turn on consent delivery"),
    ("Global opt-out", "Turn on global opt-out"),
    ("Databricks", "Turn on Databricks"),
    ("Connections", "Turn on Connections"),
    ("Consumer Trust Center", "Turn on Consumer Trust Center"),
])
def test_turn_on_button_keeps_acronyms_and_names(label: str, button: str) -> None:
    assert _button(label) == button
