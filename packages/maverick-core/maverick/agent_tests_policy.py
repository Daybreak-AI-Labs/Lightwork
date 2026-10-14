"""The ``[agent_tests]`` switches for the saved answer checks that gate publishing.

``max_cases_per_specialist`` (1 to 200, default 20): how many saved checks one
specialist may hold, and how many one run may check at once. Lowering it never
deletes a saved check; a publish that needs every check on one run waits until
the set fits again.

``require_for_new_specialists`` (default off): a specialist with no published
version cannot be published until it has a saved check and a passing full run
of those checks on exactly the draft being published. No reason overrides it.

``bypass_requires_second_person`` (default off): publishing changed instructions
without a passing run, with a reason, needs another administrator's approval of
that reason first.

Unreadable settings keep the stricter reading for the two switches and the
default for the number. A switch whose value is not true or false is read as on.
"""
from __future__ import annotations

DEFAULT_MAX_CASES = 20
MAX_CASES_LIMIT = 200
_UNREADABLE = object()
NEW_SPECIALIST_NEEDS_TESTS = (
    "A new specialist needs at least one saved check and a full passing run of those checks on its exact draft "
    "before it can be added. Create it in Agent Factory, add checks in Workforce, run them on the draft, then "
    "publish it there. Nothing was saved.")


class NewSpecialistNeedsTests(PermissionError):
    """A specialist with no published version cannot be saved while ``require_for_new_specialists`` is on."""


def _section():
    """The ``[agent_tests]`` table, ``{}`` when absent, or ``_UNREADABLE``."""
    try:
        from .config import config_source_errors, load_config

        config = load_config()
        if config_source_errors() or not isinstance(config, dict):
            return _UNREADABLE
        section = config.get("agent_tests")
        if section is None:
            return {}
        return section if isinstance(section, dict) else _UNREADABLE
    except Exception:  # unreadable settings keep the stricter reading
        return _UNREADABLE


def _switch(key: str) -> bool:
    section = _section()
    if section is _UNREADABLE:
        return True
    value = section.get(key, False)
    return value if isinstance(value, bool) else True


def max_cases_per_specialist() -> int:
    """The ``[agent_tests] max_cases_per_specialist`` knob."""
    section = _section()
    if section is _UNREADABLE:
        return DEFAULT_MAX_CASES
    value = section.get("max_cases_per_specialist", DEFAULT_MAX_CASES)
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= MAX_CASES_LIMIT:
        return DEFAULT_MAX_CASES
    return value


def require_for_new_specialists() -> bool:
    """The ``[agent_tests] require_for_new_specialists`` knob."""
    return _switch("require_for_new_specialists")


def bypass_requires_second_person() -> bool:
    """The ``[agent_tests] bypass_requires_second_person`` knob."""
    return _switch("bypass_requires_second_person")


__all__ = ["DEFAULT_MAX_CASES", "MAX_CASES_LIMIT", "NEW_SPECIALIST_NEEDS_TESTS", "NewSpecialistNeedsTests",
           "bypass_requires_second_person", "max_cases_per_specialist", "require_for_new_specialists"]
