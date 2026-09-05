"""Contrast guards for the shared built-in theme text and status controls."""
from __future__ import annotations

import re
from pathlib import Path

import pytest

_STYLESHEET = (
    Path(__file__).resolve().parents[1]
    / "maverick_dashboard" / "static" / "lightwork.css"
)


def _palette(theme: str) -> dict[str, str]:
    # Theme-specific selectors outrank inherited root colors. Read every rule
    # because the final design layer deliberately refines the earlier palette.
    source = re.sub(r"/\*.*?\*/", "", _STYLESHEET.read_text(encoding="utf-8"), flags=re.DOTALL)
    inherited: dict[str, str] = {}
    explicit: dict[str, str] = {}
    for selector, declarations in re.findall(r"([^{}]+)\{([^{}]*)\}", source):
        selectors = {part.strip() for part in selector.split(",")}
        colors = dict(re.findall(r"(--[\w-]+)\s*:\s*(#[0-9a-fA-F]{6})\s*;", declarations))
        if ":root" in selectors:
            inherited.update(colors)
        if f"body.theme-{theme}" in selectors:
            explicit.update(colors)
    return {**inherited, **explicit}


def _rgb(value: str) -> tuple[float, ...]:
    return tuple(int(value[index:index + 2], 16) / 255 for index in (1, 3, 5))


def _contrast(foreground: tuple[float, ...], background: tuple[float, ...]) -> float:
    def luminance(color):
        channels = (c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in color)
        return sum(c * weight for c, weight in zip(channels, (0.2126, 0.7152, 0.0722), strict=True))

    low, high = sorted((luminance(foreground), luminance(background)))
    return (high + 0.05) / (low + 0.05)


@pytest.mark.parametrize("theme", ["dark", "light", "solarized", "hicontrast"])
def test_shared_status_badges_have_readable_text_on_panels(theme):
    palette = _palette(theme)
    panel = _rgb(palette["--panel"])
    for token, alpha in [("accent", 0.20), ("warn", 0.18), ("danger", 0.18),
                         ("info", 0.16), ("muted", 0.18)]:
        foreground = _rgb(palette[f"--{token}"])
        background = tuple(alpha * fg + (1 - alpha) * bg for fg, bg in zip(foreground, panel, strict=True))
        ratio = _contrast(foreground, background)
        assert ratio >= 4.5, f"{theme} {token} badge contrast is {ratio:.2f}:1"


@pytest.mark.parametrize("theme", ["dark", "light", "solarized", "hicontrast"])
def test_shared_action_labels_and_links_meet_normal_text_contrast(theme):
    palette = _palette(theme)
    for foreground, background in [("--brand", "--panel"), ("--brand-fg", "--brand"),
                                   ("--danger-fg", "--danger")]:
        ratio = _contrast(_rgb(palette[foreground]), _rgb(palette[background]))
        assert ratio >= 4.5, f"{theme} {foreground} on {background} is {ratio:.2f}:1"


def test_danger_controls_use_the_theme_foreground():
    css = _STYLESHEET.read_text(encoding="utf-8")
    for selector in (".btn--danger", ".halt-pill.armed"):
        rule = re.search(re.escape(selector) + r"\s*\{([^}]+)\}", css)
        assert rule and "color: var(--danger-fg)" in rule.group(1)
