"""Message rule 3, peak: the miners at full load (message.peak).

Every miner seen today mines at its top power step, nothing is settling, every hottest chip is
below the top of the temperature band; seen on two polls in a row, after the morning.
"""
from __future__ import annotations

from .context import CONFIRM_POLLS, DayContext, actual_pv_w, duration, kw, message, status_lines, time_of

NAME = "peak"


def check(ctx: DayContext, state: dict) -> str | None:
    full = (
        state["first_start_at"] is not None
        and ctx.miners
        and len(ctx.miners) >= state["miners"]  # none of today's miners is missing
        and len(ctx.at_top) == len(ctx.miners)
        and ctx.settled
        and all(m.temperature_c is None or m.temperature_c < ctx.too_warm_c for m in ctx.miners)
    )
    polls = ctx.memory[NAME] = ctx.memory.get(NAME, 0) + 1 if full else 0
    if polls < CONFIRM_POLLS:
        return None
    state.update(peak_at=ctx.stamp, peak_miners=len(ctx.miners))
    pv = actual_pv_w(ctx.energy)
    first = f"⚡ All {len(ctx.miners)} miners run at their top step, settled ({time_of(ctx.stamp)}"
    first += f", PV {kw(pv)})." if pv is not None else ")."
    took = (
        f"It took {duration(state['first_start_at'], ctx.stamp)} from the morning "
        f"({time_of(state['first_start_at'])})."
    )
    return message(first, took, status_lines(ctx))
