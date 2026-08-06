"""The prompt loop CLI: the platform tells you what to work on next.

Split out of cli/__init__.py. Registered by importing this module at the end
of the package __init__ so the @main.group decorators fire on package import.
"""
from __future__ import annotations

import click

from . import main


def _fail(exc: Exception) -> None:
    raise SystemExit(f"error: {exc}")


@main.group()
def loop() -> None:
    """The self-prompting work loop: what should I do next, and why.

    `maverick loop cycle` reads what is true about this deployment right now
    and writes work orders — ready-to-run prompts with their rationale,
    evidence and definition of done. `maverick loop next` shows the top of the
    queue. Completing one composes its own follow-up, so the loop keeps asking
    and you keep answering.
    """


@loop.command("cycle")
@click.option("--limit", default=5, show_default=True,
              help="Most orders to add this cycle.")
@click.option("--quiet", is_flag=True,
              help="Print a one-line summary instead of the full prompts.")
def loop_cycle(limit: int, quiet: bool) -> None:
    """Observe the system and compose the next work orders.

    Read-only and deterministic: sensors look at parked approvals, repeated
    failure clusters, goals stuck in flight, frozen learning, unreached
    modules, past-due deprecations, contained external agents and spend burnt
    on failed work. Nothing runs and nothing is spent.
    """
    from ..prompt_loop import PromptLoopError, cycle, render
    from ..world_model import open_world
    try:
        orders = cycle(world=open_world(), limit=limit)
    except PromptLoopError as e:
        _fail(e)
        return
    if quiet:
        click.echo(f"{len(orders)} new work order(s).")
        for o in orders:
            click.echo(f"  {o['id']}  [{o['kind']}] {o['title']}")
        return
    click.echo(render(orders))


@loop.command("next")
@click.option("--limit", default=1, show_default=True,
              help="How many orders to show.")
@click.option("--prompt-only", is_flag=True,
              help="Print only the prompt text, ready to pipe or paste.")
def loop_next(limit: int, prompt_only: bool) -> None:
    """Show the top of the queue — the next best thing to ask for."""
    from ..prompt_loop import PromptLoopError, next_orders, render
    try:
        orders = next_orders(limit=limit)
    except PromptLoopError as e:
        _fail(e)
        return
    if prompt_only:
        for o in orders:
            click.echo(o["prompt"])
        return
    click.echo(render(orders))


@loop.command("list")
@click.option("--state", default="", help="Filter by state "
              "(open/taken/done/skipped/muted). Default: all.")
@click.option("--limit", default=20, show_default=True)
def loop_list(state: str, limit: int) -> None:
    """List work orders with their state and score."""
    from ..prompt_loop import PromptLoopError, next_orders
    try:
        orders = next_orders(limit=limit, state=state.strip())
    except PromptLoopError as e:
        _fail(e)
        return
    if not orders:
        click.echo("no work orders.")
        return
    for o in orders:
        click.echo(f"  {o['id']}  {o.get('state', ''):8s} "
                   f"{o.get('score', 0):>6}  [{o.get('kind')}] {o.get('title')}")


@loop.command("show")
@click.argument("order_id")
def loop_show(order_id: str) -> None:
    """Print one work order in full, prompt included."""
    from ..prompt_loop import PromptLoopError, get, render
    try:
        click.echo(render([get(order_id)]))
    except PromptLoopError as e:
        _fail(e)


@loop.command("take")
@click.argument("order_id")
@click.option("--by", default="", help="Who is taking it.")
def loop_take(order_id: str, by: str) -> None:
    """Claim an order so nobody else picks up the same work."""
    from ..prompt_loop import PromptLoopError, take
    try:
        order = take(order_id, by=by)
    except PromptLoopError as e:
        _fail(e)
        return
    click.echo(f"taken: {order['id']} — {order['title']}")


@loop.command("done")
@click.argument("order_id")
@click.option("--outcome", default="",
              help="What happened. The follow-up prompt is composed from it, "
                   "so say whether anything was found.")
@click.option("--by", default="", help="Who completed it.")
def loop_done(order_id: str, outcome: str, by: str) -> None:
    """Close an order — and get the next one, composed from the outcome.

    This is the self-prompting step. An investigation earns a fix, a fix earns
    its verification, and a verification that found something routes back to a
    fix rather than forward to the write-up.
    """
    from ..prompt_loop import PromptLoopError, complete, render
    try:
        result = complete(order_id, outcome=outcome, by=by)
    except PromptLoopError as e:
        _fail(e)
        return
    click.echo(f"done: {result['order']['id']}")
    if result["next"]:
        click.echo("\nNext, composed from that outcome:\n")
        click.echo(render([result["next"]]))
    else:
        click.echo("the chain ends here; run `maverick loop cycle` for more.")


@loop.command("skip")
@click.argument("order_id")
@click.option("--reason", default="", help="Why you are declining it.")
def loop_skip(order_id: str, reason: str) -> None:
    """Decline an order. The loop learns: that source loses weight and the
    signal sits out a cooldown. Skip the same thing three times and it stops
    being proposed at all."""
    from ..prompt_loop import PromptLoopError, skip
    try:
        order = skip(order_id, reason=reason)
    except PromptLoopError as e:
        _fail(e)
        return
    click.echo(f"{order['state']}: {order['id']}")


@loop.command("dispatch")
@click.argument("order_id")
@click.option("--by", default="", help="Who is dispatching it.")
def loop_dispatch(order_id: str, by: str) -> None:
    """Hand an order back to the agent as a goal (auto mode only).

    Refuses unless `[prompt_loop] mode = "auto"`, the order's risk is inside
    the configured ceiling, and — by default — a human has approved this exact
    prompt. Editing the order after approval voids the approval.
    """
    from ..prompt_loop import PromptLoopError, dispatch
    try:
        result = dispatch(order_id, by=by)
    except PromptLoopError as e:
        _fail(e)
        return
    if result["status"] == "awaiting_approval":
        click.echo(f"parked approval #{result['approval_id']} — approve it in "
                   "the dashboard, then run this again.")
        return
    click.echo(f"{result['status']}: goal #{result['goal_id']}")


@loop.command("status")
def loop_status() -> None:
    """Show the loop's mode and queue counts."""
    from ..prompt_loop import status
    st = status()
    click.echo(f"  enabled        {st['enabled']}")
    click.echo(f"  mode           {st['mode']}")
    if not st.get("readable", True):
        click.echo("  ledger         UNREADABLE — the loop is refusing rather "
                   "than starting from a blank queue")
        return
    click.echo(f"  model composer {st.get('compose_with_model')}")
    click.echo(f"  auto ceiling   {st.get('auto_risk_ceiling')}")
    click.echo(f"  cycles         {st.get('cycles')}")
    click.echo(f"  open/taken     {st.get('open')}/{st.get('taken')}")
    click.echo(f"  done/muted     {st.get('done')}/{st.get('muted')}")
