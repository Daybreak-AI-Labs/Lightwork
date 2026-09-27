"""Epistemic hygiene: which company facts rest on an unverified agent claim?

As agents write into shared memory, one hallucination can become the premise of
many later facts. Given facts with a source (``human``, ``agent`` or
``external``), a ``verified`` flag and ``derived_from`` edges, this traces the
blast radius of a falsified fact and measures how much of the knowledge base
rests on agent claims no human has verified.
"""
from __future__ import annotations

from collections import deque

Facts = dict[str, dict]  # id -> {"source": str, "verified": bool, "derived_from": [ids]}


def quarantine(facts: Facts, falsified: str) -> list[str]:
    """Every fact that depends, directly or transitively, on ``falsified``."""
    children: dict[str, list[str]] = {}
    for fid, f in facts.items():
        for parent in f.get("derived_from", []):
            children.setdefault(parent, []).append(fid)
    seen: set[str] = set()
    queue = deque([falsified])
    while queue:
        for child in children.get(queue.popleft(), []):
            if child not in seen:
                seen.add(child)
                queue.append(child)
    return sorted(seen)


def unverified_basis(facts: Facts) -> list[str]:
    """Facts resting on an agent claim that no human verified (at any depth).
    A human-verified fact is a firebreak: what rests on it is grounded."""
    memo: dict[str, bool] = {}

    def tainted(fid: str, trail: frozenset[str]) -> bool:
        if fid in memo:
            return memo[fid]
        f = facts.get(fid, {"source": "agent", "verified": False})  # unknown = untrusted
        if f.get("verified"):
            result = False
        elif f.get("source") == "agent" or fid in trail:
            result = True
        else:
            result = any(tainted(p, trail | {fid}) for p in f.get("derived_from", []))
        memo[fid] = result
        return result

    return sorted(fid for fid in facts if tainted(fid, frozenset()))
