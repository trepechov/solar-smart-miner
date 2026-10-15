"""Message rule 1, morning: the day's first usable production (message.morning).

First usable production is set by DayMessages (one definition, also used by the evening): the
integration's own first start, or the PV at the lowest power step on two polls in a row, while
the sun is up. A day picked up by HA with production already there is said so.
"""
from __future__ import annotations

from .context import DayContext, kw, message, status_lines, time_of

NAME = "morning"


def check(ctx: DayContext, state: dict) -> str | None:
    if state["first_production_at"] is None:
        return None
    energy = ctx.energy
    forecast = []
    if energy.forecast_remaining_kwh is not None:
        forecast.append(f"{energy.forecast_remaining_kwh:.1f} kWh for the rest of today")
    if energy.forecast_next_hour_w is not None:
        forecast.append(f"{kw(energy.forecast_next_hour_w)} next hour")
    first = (
        f"☀️ Picked up the day at {time_of(state['first_production_at'])}: production is already up."
        if state["picked_up"]
        else f"☀️ Production is up ({time_of(state['first_production_at'])})."
    )
    return message(first, "Forecast: " + ", ".join(forecast) + "." if forecast else None, status_lines(ctx))
