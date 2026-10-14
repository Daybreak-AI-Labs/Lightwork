"""Incomplete Shield results must not promote persisted text into model context."""

from types import SimpleNamespace

from maverick import dreaming, experience, fleet_memory, reflexion
from maverick.skill import synthesis


class _IncompleteShield:
    def scan_input(self, text):
        return SimpleNamespace()


def test_unverified_memory_verdict_withholds_model_facing_text():
    shield = _IncompleteShield()
    text = "A prior customer note about quarterly invoice state"

    assert text not in dreaming._sanitize(text, shield=shield)
    assert text not in reflexion._sanitize_text(text, shield=shield)
    assert text not in experience._sanitize_anchor_title(text, shield=shield)
    assert synthesis._sanitize_text(
        text, shield=shield, max_chars=2000, scan_method="scan_input"
    ) is None
    assert fleet_memory._sanitize(text, shield=shield) is None
