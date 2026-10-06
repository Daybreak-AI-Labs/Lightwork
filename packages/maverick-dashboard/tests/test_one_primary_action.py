"""SYS-2: one main action per view (the page-matrix column P as a ratchet).

The convention: at most one ``btn btn--primary`` per view, in the header;
every other action is ``btn``, ``btn--ghost`` or ``btn--danger``. Many
templates still carry more than one in their source. This counts them and
fails when the number grows, so each fix stays fixed and new pages follow
the rule. When you remove extra main actions, lower the numbers below.
"""
from __future__ import annotations

import re
from pathlib import Path

TEMPLATES = Path(__file__).resolve().parents[1] / "maverick_dashboard" / "templates"
PRIMARY = re.compile(r"btn--primary|btn-primary")
# Measured 6 October 2026 after governance, data use rules and the vendor
# pages were brought to one main action. Only ever lower these.
MAX_TEMPLATES_WITH_EXTRA = 138
MAX_EXTRA_PRIMARIES = 288
FIXED = ("governance.html", "data_use_governance.html", "vendor_detail.html")


def _extra() -> dict[str, int]:
    out = {}
    for path in sorted(TEMPLATES.glob("*.html")):
        count = len(PRIMARY.findall(path.read_text(encoding="utf-8")))
        if count > 1:
            out[path.name] = count - 1
    return out


def test_extra_main_actions_never_grow():
    extra = _extra()
    assert len(extra) <= MAX_TEMPLATES_WITH_EXTRA, sorted(extra)
    assert sum(extra.values()) <= MAX_EXTRA_PRIMARIES, sorted(extra.items(), key=lambda kv: -kv[1])[:20]


def test_pages_brought_to_one_main_action_stay_there():
    extra = _extra()
    assert [name for name in FIXED if name in extra] == []
