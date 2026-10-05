"""Shared UI helpers instead of page copies.

Pages each carried their own DOM builder, fetch wrapper and status-line
setter. Eight copies of the DOM builder took ``el(tag, text, cls)`` while
the shared ``mvEl`` and every other copy take ``(tag, cls, text)``, so a call
copied from one page to another silently put the text in the class. These
ratchets keep one argument order, and keep the shared fetch and status
helpers in lightwork-ui.js.
"""
from __future__ import annotations

import re
from pathlib import Path

PACKAGE = Path(__file__).resolve().parents[1] / "maverick_dashboard"
_BUILDER = re.compile(r"function\s+el\s*\(\s*(\w+)\s*,\s*(\w+)\s*(?:,\s*(\w+)\s*)?\)")


def _scripts() -> list[Path]:
    return sorted([*PACKAGE.glob("static/*.js"), *PACKAGE.glob("templates/*.html"), *PACKAGE.glob("templates/*.js")])


def test_dom_builders_take_the_class_before_the_text() -> None:
    reversed_order = []
    for path in _scripts():
        text = path.read_text(encoding="utf-8")
        for match in _BUILDER.finditer(text):
            second, third = match.group(2), match.group(3) or ""
            if re.fullmatch(r"te?xt|label|content", second) and re.fullmatch(r"cls|className|klass|cl", third):
                line = text.count("\n", 0, match.start()) + 1
                reversed_order.append(f"{path.relative_to(PACKAGE)}:{line}")
    assert reversed_order == [], "Write el(tag, cls, text), the order mvEl uses: " + ", ".join(reversed_order)


def test_the_shared_fetch_and_status_helpers_exist() -> None:
    ui = (PACKAGE / "static" / "lightwork-ui.js").read_text(encoding="utf-8")
    assert "window.mvFetchJSON = async function (url, opts, action)" in ui
    assert "window.mvStatus = function (element, text, opts)" in ui
