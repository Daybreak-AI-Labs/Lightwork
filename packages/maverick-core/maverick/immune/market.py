"""Tacit-collusion signal between pricing agents.

Two agents that repeatedly observe each other's prices can converge on moving
together without any agreement -- the pattern regulators have challenged in
algorithmic pricing. :func:`parallelism` measures, over the periods where either
price changed, how often both moved in the same direction, at the lag (0..max)
where the follower best matches the leader. It is a signal for human and
counsel review, never a finding of collusion.
"""
from __future__ import annotations


def _moves(series: list[float]) -> list[int]:
    return [(b > a) - (b < a) for a, b in zip(series, series[1:], strict=False)]


def parallelism(a: list[float], b: list[float], *, max_lag: int = 2,
                threshold: float = 0.9, min_moves: int = 10) -> dict:
    """Best co-movement share of ``b`` following ``a`` by 0..max_lag periods."""
    ma, mb = _moves(a), _moves(b)
    best = {"lag": 0, "co_moves": 0.0, "moves": 0}
    for lag in range(max_lag + 1):
        pairs = [(x, y) for x, y in zip(ma, mb[lag:], strict=False) if x or y]
        if pairs:
            share = sum(x == y for x, y in pairs) / len(pairs)
            if share > best["co_moves"]:
                best = {"lag": lag, "co_moves": share, "moves": len(pairs)}
    best["flag"] = best["moves"] >= min_moves and best["co_moves"] >= threshold
    return best
