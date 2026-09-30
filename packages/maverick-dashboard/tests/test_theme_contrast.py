"""Palette checks; runtime/browser qualification is tracked separately."""
from maverick_dashboard.themes import contrast_issues, contrast_ratio, readable_palette


def test_white_on_white_is_refused_with_human_readable_reasons():
    palette = {key: "#ffffff" for key in ("bg", "panel", "text", "accent")}
    assert any("Text against page background" in issue for issue in contrast_issues(palette))
    assert contrast_ratio("#fff", "#000") == 21


def test_legacy_palette_stays_recoverable_on_both_surfaces():
    palette = readable_palette({"bg": "#000", "panel": "#fff", "text": "#000", "accent": "#000"})
    assert contrast_issues(palette) == []


def test_quiet_text_control_and_focus_pairs_are_checked():
    palette = {"bg": "#fff", "panel": "#fff", "text": "#111", "accent": "#fff", "border": "#eee", "muted": "#ddd"}
    issues = " ".join(contrast_issues(palette))
    assert "Quiet text" in issues
    assert "Highlights and focus" in issues
    assert "Control borders" in issues
