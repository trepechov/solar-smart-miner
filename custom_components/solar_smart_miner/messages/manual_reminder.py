"""Message rule 1, the Manual-mode reminder (message.manual-reminder).

In Manual mode the decision still runs, and its plans wait for the Apply button. The first time
in the day that they start a miner while the sun is up (the moment Automatic would have started
one), this says so once: nothing starts until the owner applies it. Before the morning, so the
morning follows when Apply is pressed. Only reads the decision; never applies a plan.
"""
from __future__ import annotations

from ..decision.describe import _w
from ..protocols import ACTION_START
from .context import DayContext, actual_pv_w, kw, message, miner_name, status_lines

NAME = "manual-reminder"


def check(ctx: DayContext, state: dict) -> str | None:
    decision = ctx.snapshot.decision
    if not ctx.manual or ctx.sun_up is False or state["first_start_at"] is not None or decision is None:
        return None
    names = {m.miner_id: miner_name(m) for m in ctx.snapshot.miners}
    starts = [
        f"{names.get(miner_id, miner_id)}" + (f" at {_w(plan.limit_w)}" if plan.limit_w is not None else "")
        for miner_id, plan in decision.plans.items()
        if plan.action == ACTION_START
    ]
    if not starts:
        return None
    pv = actual_pv_w(ctx.energy)
    first = "🖐 Manual mode: the sun could run a miner now" + (f" (solar {kw(pv)})." if pv is not None else ".")
    return message(
        first,
        "Proposed: start " + ", ".join(starts) + ".",
        "Nothing starts until you press Apply or switch to Automatic.",
        status_lines(ctx),
    )
