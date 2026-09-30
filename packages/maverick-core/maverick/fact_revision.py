"""Optimistic concurrency contract for current facts on every backend."""
from __future__ import annotations


class FactConflict(ValueError):
    """The fact changed after the caller read it; the draft was not written."""


def is_private_fact_key(key: str) -> bool:
    """Reserved agent/user namespaces are not shared business facts."""
    return key.casefold().startswith(("goal:", "user:"))


def check_expected_revision(expected: int | None, actual: int) -> None:
    if expected is None:
        return  # Trusted internal unconditional writes remain explicit at call sites.
    if isinstance(expected, bool) or not isinstance(expected, int) or expected < 0:
        raise ValueError("expected fact revision must be a nonnegative integer")
    if expected != actual:
        raise FactConflict("The fact changed. Read its current value before trying again.")
