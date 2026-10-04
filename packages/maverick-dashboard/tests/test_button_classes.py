"""One way to write each button kind.

Pages used class names that were never part of the design system
(``btn primary``, ``btn-primary``, ``btn-danger``); aliases in the stylesheet
painted them brand blue while real primary buttons render in the text colour,
so two "main" buttons on one screen looked different. The aliases are gone;
these ratchets keep them from coming back and keep a single primary-button
definition for each theme family.
"""
from __future__ import annotations

import re
from pathlib import Path

PACKAGE = Path(__file__).resolve().parents[1] / "maverick_dashboard"
CSS = PACKAGE / "static" / "lightwork.css"

# A class list written in markup (class="…") or assigned by a script
# (className = '…'), with the quote it opened with.
_CLASS_LIST = re.compile(r"""\bclass(?:Name)?\s*=\s*(["'])(.*?)\1""")
_LEGACY_SELECTOR = re.compile(r"\.btn-(?:primary|danger)\b|\.btn\.(?:primary|danger)\b")


def _sources() -> list[Path]:
    files: set[Path] = set()
    for pattern in ("templates/*.html", "static/*.js", "static/*.html", "static/*.css", "*.py"):
        files.update(PACKAGE.glob(pattern))
    return sorted(files)


def test_no_legacy_button_class_names() -> None:
    offenders = []
    for path in _sources():
        text = path.read_text(encoding="utf-8")
        for match in _CLASS_LIST.finditer(text):
            names = set(match.group(2).split())
            legacy = names & {"btn-primary", "btn-danger"}
            if "btn" in names and names & {"primary", "danger"}:
                legacy |= names & {"primary", "danger"}
            if legacy:
                line = text.count("\n", 0, match.start()) + 1
                offenders.append(f"{path.relative_to(PACKAGE)}:{line} {sorted(legacy)}")
        for match in _LEGACY_SELECTOR.finditer(text):
            line = text.count("\n", 0, match.start()) + 1
            offenders.append(f"{path.relative_to(PACKAGE)}:{line} {match.group(0)}")
    assert not offenders, "Use btn btn--primary / btn btn--danger: " + "; ".join(offenders)


def test_primary_button_has_one_definition_per_theme_family() -> None:
    """High contrast keeps the brand fill (the base rule); every other theme
    uses the later text-coloured rule. Superseded glass-era primary rules are
    gone, so each look is written once."""
    css = CSS.read_text(encoding="utf-8")
    assert len(re.findall(r"(?m)^\s*\.btn--primary\s*\{", css)) == 1
    assert len(re.findall(r"(?m)^\s*\.btn--primary:hover\s*\{", css)) == 1
    assert len(re.findall(r"body:not\(\.theme-hicontrast\) \.btn--primary\s*\{", css)) == 1
    assert len(re.findall(r"body:not\(\.theme-hicontrast\) \.btn--primary:hover\s*\{", css)) == 1
