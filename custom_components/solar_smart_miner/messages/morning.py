"""Message rule 2, morning: the integration's first start (message.morning).

The first start is set by DayMessages (one definition, also used by the peak, going down and the
evening): the first start command the integration sent today that finished ok, while the sun is
up. A Manual-mode Apply counts (the integration sends it); a miner started by hand doesn't. A day
picked up by HA with miners mining is said so. The forecast is a rough figure; the one shown is
kept, and the day report compares it with the solar that came after.
"""
from __future__ import annotations

from ..decision.describe import _w
from .context import DayContext, kw, message, status_lines, time_of

NAME = "morning"


def check(ctx: DayContext, state: dict) -> str | None:
    if state["first_start_at"] is None:
        return None
    energy = ctx.energy
    state["forecast_at_start_kwh"] = energy.forecast_remaining_kwh
    state["solar_at_start_kwh"] = state["totals"]["solar_kwh"]
    forecast = []
    if energy.forecast_remaining_kwh is not None:
        forecast.append(f"about {energy.forecast_remaining_kwh:.0f} kWh for the rest of today")
    if energy.forecast_next_hour_w is not None:
        forecast.append(f"{kw(energy.forecast_next_hour_w)} next hour")
    if state["picked_up"]:
        n = len(ctx.mining)
        first = (
            f"⛏️ Picked up the day at {time_of(state['first_start_at'])}: "
            f"{n} miner{'s' if n != 1 else ''} already mining."
        )
    else:
        starts = sorted((c for c in state["commands"] if c["kind"] == "start"), key=lambda c: c["at"])
        if starts:
            start = starts[0]
            limit = f", {_w(start['limit_w'])}" if start.get("limit_w") is not None else ""
            first = f"⛏️ First miner started at {time_of(start['at'])} ({start['miner']}{limit})."
        else:  # a day saved by 0.8.8, opened by the PV before the update
            first = f"⛏️ The day started at {time_of(state['first_start_at'])}."
    return message(
        first, "Forecast, rough: " + ", ".join(forecast) + "." if forecast else None, status_lines(ctx)
    )
