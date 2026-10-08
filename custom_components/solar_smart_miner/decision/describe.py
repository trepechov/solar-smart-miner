"""The decision's words: readings, miners and plans as the trace, the card and the AI read them."""
from __future__ import annotations

from ..protocols import (
    ACTION_SET_LIMIT,
    ACTION_START,
    ACTION_STOP,
    EnergySnapshot,
    MinerPlan,
    MinerSnapshot,
)


def _w(value: float | None) -> str:
    return f"{round(value):,} W" if value is not None else "unknown"  # an int: never "-0 W"


def _describe_energy(energy: EnergySnapshot) -> list[str]:
    lines = []
    solar = _w(energy.solar_production_w)
    if energy.mock_solar:
        solar += " (mock)"
    if energy.solar_fault:
        solar = "SENSOR UNAVAILABLE"
    lines.append(f"Solar production: {solar}")

    if energy.grid_net_w is None:
        lines.append("Grid: unknown")
    elif energy.grid_net_w >= 0:
        lines.append(f"Grid: exporting {_w(energy.grid_net_w)}")
    else:
        lines.append(f"Grid: importing {_w(-energy.grid_net_w)}")

    house = _w(energy.grid_consumption_w)
    if energy.mock_consumption:
        house += " (mock = miner sum)"
    lines.append(f"House consumption: {house}")
    if energy.battery_soc_pct is not None:
        lines.append(f"Battery: {energy.battery_soc_pct:.0f}%")
    lines.append(f"Miners drawing: {_w(energy.miner_consumption_sum_w)}")
    lines.append(f"Available for miners: {_w(energy.available_for_miners_w)}")
    if energy.pv_power_w is not None:
        lines.append(f"Actual PV output: {_w(energy.pv_power_w)}")
    if energy.forecast_now_w is not None:
        lines.append(f"Forecast PV now: {_w(energy.forecast_now_w)} (reference only)")
    if energy.forecast_next_hour_w is not None:
        lines.append(f"Forecast PV next hour: {_w(energy.forecast_next_hour_w)} (reference only)")
    if energy.forecast_remaining_kwh is not None:
        lines.append(f"Forecast PV left today: {energy.forecast_remaining_kwh:,.1f} kWh")
    return lines


def _describe_miner(m: MinerSnapshot) -> str:
    if not m.is_available:
        return f"{m.name}: unavailable"
    temp = f"{m.temperature_c:.0f} °C" if m.temperature_c is not None else "? °C"
    rate = f"{m.hashrate_th:.1f} TH/s" if m.hashrate_th is not None else "? TH/s"
    rng = (
        f"{m.min_power_w:.0f}–{m.max_power_w:.0f} W"
        if m.min_power_w is not None and m.max_power_w is not None
        else "range unknown"
    )
    return (
        f"{m.name}: {_w(m.power_w)}, {temp}, {rate}, "
        f"limit {_w(m.power_limit_w)} ({rng})"
    )


def _describe_plan(plan: MinerPlan) -> str:
    if plan.action == ACTION_STOP:
        return f"stop ({plan.method})"
    if plan.action == ACTION_START:
        return f"start at {_w(plan.limit_w)}"
    if plan.action == ACTION_SET_LIMIT:
        return _w(plan.limit_w)
    if plan.reason == "stays stopped":
        return "stopped"
    return f"hold {_w(plan.limit_w)}" if plan.limit_w is not None else "hold"


def describe_proposal(miner: MinerSnapshot | None, plan: MinerPlan) -> str:
    """Plan text for the owner; a step also says where the miner is now."""
    text = _describe_plan(plan)
    if plan.action == ACTION_SET_LIMIT and miner is not None and miner.power_limit_w is not None:
        text += f" (from {_w(miner.power_limit_w)})"
    return text
