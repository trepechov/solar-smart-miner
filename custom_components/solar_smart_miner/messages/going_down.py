"""Message rule 3, going down: a step down that matches the sun's decline (message.going-down).

After the morning, the first step down or stop (DayMessages finds it: one per poll, a restart
after a limit change excluded, a stop seen on two polls) that comes after solar noon while the
forecast for the next hour is below the forecast now. Without a next-hour forecast, after solar
noon is enough; without sun.sun, the falling forecast is. Any other step down is a cloud: it
sends nothing and the evening counts it.
"""
from __future__ import annotations

from ..decision.describe import _w
from .context import DayContext, actual_pv_w, kw, message, miner_name, status_lines

NAME = "going-down"


def _declining(ctx: DayContext) -> bool:
    energy = ctx.energy
    falling = (
        None
        if energy.forecast_next_hour_w is None or energy.forecast_now_w is None
        else energy.forecast_next_hour_w < energy.forecast_now_w
    )
    noon = ctx.noon_passed
    if noon is None and falling is None:
        return False
    return (noon is None or noon) and (falling is None or falling)


def check(ctx: DayContext, state: dict) -> str | None:
    if ctx.down is None or state["first_production_at"] is None:
        return None
    if not _declining(ctx):
        state["cloud_step_downs"] += 1
        return None
    miner, old, new, reason = ctx.down
    energy = ctx.energy
    state["going_down"] = {"at": ctx.stamp, "miner": miner_name(miner), "old_w": old, "new_w": new}
    change = "stopped" if new is None else f"{_w(old)} → {_w(new)}"
    first = f"🌇 Going down: {miner_name(miner)} {change}" + (f" ({reason})." if reason else ".")
    pv = actual_pv_w(energy)
    forecast = None
    if energy.forecast_now_w is not None:
        forecast = f"Forecast {kw(energy.forecast_now_w)}"
        if energy.forecast_next_hour_w is not None:
            forecast += f", {kw(energy.forecast_next_hour_w)} next hour"
        forecast += f"; panels give {kw(pv)}." if pv is not None else "."
    return message(first, forecast, status_lines(ctx))
