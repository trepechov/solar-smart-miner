"""Message rule 5, evening: the sun is below the horizon (message.evening).

Once the sun, seen up today, is down (without sun.sun: the sunset transition, after the
morning). Sent on a day the integration started no miner too. With a battery the miners may mine on,
so the day isn't called done.
"""
from __future__ import annotations

from .context import DayContext, message, miner_name, status_lines, time_of

NAME = "evening"


def check(ctx: DayContext, state: dict) -> str | None:
    if ctx.sun_up is None:
        down = ctx.sunset and state["first_start_at"] is not None
    else:
        down = ctx.sun_up is False and state["sun_seen_up"]
    if not down:
        return None
    state["evening_at"] = ctx.stamp
    first = (
        f"🌙 Sunset ({time_of(ctx.stamp)})."
        if ctx.battery
        else f"🌙 Day done: the sun has set ({time_of(ctx.stamp)})."
    )
    lines = []
    if state["first_start_at"] is None:
        lines.append("The integration started no miner today." if state["last_mining_at"] else "No miner mined today.")
    else:
        if not ctx.mining and state["last_mining_at"]:
            lines.append(f"The last miner stopped at {time_of(state['last_mining_at'])}.")
        if state["peak_at"] is None and state["miners"]:
            lines.append(f"Peak: {state['peak_at_top']} of {state['miners']} miners at their top step.")
        clouds = state["cloud_step_downs"]
        lines.append(f"Cloud step-downs: {clouds}." if clouds else "No cloud step-downs.")
    if ctx.mining:
        lines.append("Still mining after sunset: " + ", ".join(miner_name(m) for m in ctx.mining) + ".")
    return message(first, lines, status_lines(ctx))
