"""Shared liquid-glass ownership and accessibility fallbacks."""
from __future__ import annotations

import re
from pathlib import Path

PACKAGE = Path(__file__).resolve().parents[1] / "maverick_dashboard"
CSS = PACKAGE / "static" / "lightwork.css"
TEMPLATES = PACKAGE / "templates"

NAMED_SURFACES = {
    "finance_board.html": (".fb-kpi", ".fb-card"),
    "privacy_board.html": (".pb-kpi", ".pb-card"),
    "get_started.html": (".gs__step", ".gs__step--done"),
    "chat_goal.html": (
        ".msg--user .bubble",
        ".worklog",
        ".deliverable",
        ".signoff",
        ".artifact",
        ".ask",
        ".activity",
    ),
    "learning.html": (".lrn__row", ".lrn__moat"),
    "partner.html": (".pf-tile",),
    "run_tree.html": (".rt-node",),
}


def _rule(source: str, selector: str) -> str:
    match = re.search(
        rf"{re.escape(selector)}\s*\{{([^{{}}]*)\}}", source, re.DOTALL
    )
    assert match is not None, selector
    return match.group(1)


def test_named_page_surfaces_delegate_material_to_shared_css() -> None:
    """Page CSS owns layout/state; the shared stylesheet owns its material."""
    shared = CSS.read_text(encoding="utf-8")

    for filename, selectors in NAMED_SURFACES.items():
        template = (TEMPLATES / filename).read_text(encoding="utf-8")
        for selector in selectors:
            assert "background:" not in _rule(template, selector), (filename, selector)
            shared_selector = ".gs__step" if selector == ".gs__step--done" else selector
            assert shared_selector in shared, (filename, selector)

    # Base solid fallback, enhanced glass, and reduced-transparency fallback.
    assert shared.count(".fb-kpi, .fb-card") == 3
    assert "var(--glass-sheen), var(--glass)" in shared
    assert "linear-gradient(var(--glass-tint, transparent)" in shared


def test_copilot_foregrounds_and_transparency_fallbacks_are_theme_safe() -> None:
    shared = CSS.read_text(encoding="utf-8")

    assert "color: var(--brand-fg)" in _rule(shared, ".mv-copilot__fab")
    assert "color: var(--brand-fg)" in _rule(shared, ".mv-copilot__msg--user")
    assert "body.theme-hicontrast .mv-copilot" in shared
    assert "@media (prefers-reduced-transparency: reduce)" in shared

    # Both accessibility paths explicitly disable both the standard and WebKit
    # filters on the panel and every Copilot sub-surface that can be translucent.
    copilot_fallback = shared[shared.index("body.theme-hicontrast .mv-copilot") :]
    assert copilot_fallback.count("-webkit-backdrop-filter: none") >= 2
    assert copilot_fallback.count("backdrop-filter: none") >= 2
    for selector in (
        ".mv-copilot__prompt",
        ".mv-copilot__msg--bot",
        ".mv-copilot__egress",
        ".mv-copilot__row .input",
    ):
        assert copilot_fallback.count(selector) >= 2, selector


def test_danger_actions_remain_semantic_not_glass_controls() -> None:
    shared = CSS.read_text(encoding="utf-8")

    assert ".btn:not(.btn--primary):not(.btn--danger)" in shared
    danger = _rule(shared, ".btn--danger")
    assert "background: var(--danger)" in danger
    assert "border-color: var(--danger)" in danger
    assert "color: var(--danger-fg)" in danger
