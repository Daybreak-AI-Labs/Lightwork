"""Price an agent's claims against reality: assured accuracy, bonds, truth markets.

Outcomes come from systems of record (an invoice paid, a ticket reopened -- see
:mod:`maverick.consequence`), never from a model grading itself.

* :func:`assured_accuracy` -- Wilson lower bound: what you can rely on, not the
  lucky point estimate.
* :func:`bond` -- collateral an agent (or its vendor) should post against its
  outputs: exposure x conservative failure rate.
* :func:`allocate` / :func:`settle` -- a swarm truth market. Agents stake for or
  against claims; scarce verification goes to the most *contested* claims, and
  on resolution the wrong side's stake pays the right side pro rata.
"""
from __future__ import annotations

from ..wilson_score import wilson_lower_bound

Stake = tuple[str, bool, float]  # (agent, believes_true, amount)


def assured_accuracy(outcomes: list[bool]) -> float:
    return wilson_lower_bound(sum(outcomes), len(outcomes))


def bond(exposure: float, outcomes: list[bool]) -> float:
    """Conservative expected loss; with no track record, the full exposure."""
    if exposure < 0:
        raise ValueError("exposure must be non-negative")
    return exposure * (1 - assured_accuracy(outcomes))


def contest(stakes: list[Stake]) -> float:
    """Money on the minority side: 0 when everyone agrees."""
    yes = sum(a for _, v, a in stakes if v)
    no = sum(a for _, v, a in stakes if not v)
    return min(yes, no)


def allocate(market: dict[str, list[Stake]], budget: int) -> list[str]:
    """The ``budget`` claims most worth verifying (most contested first)."""
    ranked = sorted(market, key=lambda c: (-contest(market[c]), c))
    return [c for c in ranked if contest(market[c]) > 0][:budget]


def settle(stakes: list[Stake], truth: bool) -> dict[str, float]:
    """Net payout per agent. Winners get their stake back plus the losers'
    stakes pro rata; if nobody was right, stakes are returned (no burn)."""
    win = [(g, a) for g, v, a in stakes if v == truth]
    lost = sum(a for _, v, a in stakes if v != truth)
    total_win = sum(a for _, a in win)
    if not total_win:
        return {g: 0.0 for g, _, _ in stakes}
    out = {g: -a for g, v, a in stakes if v != truth}
    for g, a in win:
        out[g] = out.get(g, 0.0) + lost * a / total_win
    return out
