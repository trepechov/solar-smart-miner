"""Rule-based decision preview.

Stands in for the AI agent (U5) so the decision log shows what the integration
reads and what it *would* do. Nothing here touches the miners: proposals are
only reported, never applied.
"""
from __future__ import annotations

import math

from .const import PROFILES_BY_NAME
from .protocols import CoordinatorSnapshot, Decision, EnergySnapshot, MinerSnapshot

LIMIT_STEP_W = 10  # proposals are rounded down to this step


def _w(value: float | None) -> str:
    return f"{value:,.0f} W" if value is not None else "unknown"


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
        lines.append(f"Forecast PV now: {_w(energy.forecast_now_w)}")
    if energy.forecast_next_hour_w is not None:
        lines.append(f"Forecast PV next hour: {_w(energy.forecast_next_hour_w)}")
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


def _clamp(m: MinerSnapshot, limit_w: float) -> float:
    if m.max_power_w is not None:
        limit_w = min(limit_w, m.max_power_w)
    if m.min_power_w is not None:
        limit_w = max(limit_w, m.min_power_w)
    return math.floor(limit_w / LIMIT_STEP_W) * LIMIT_STEP_W


def _floor_w(m: MinerSnapshot) -> float:
    """Lowest limit the miner accepts; 'pause' means running at this."""
    return m.min_power_w if m.min_power_w is not None else 0.0


def build_decision(
    snapshot: CoordinatorSnapshot,
    profile: str,
    temp_ceiling: float,
    battery_floor: float,
) -> Decision:
    energy = snapshot.energy
    profile_def = PROFILES_BY_NAME.get(profile)
    profile_label = profile_def["display_name"] if profile_def else profile

    trace = ["READ", *_describe_energy(energy)]
    trace += [_describe_miner(m) for m in snapshot.miners]
    trace += ["THINK", f"Profile: {profile_label}"]
    proposals: dict[str, float] = {}

    def done(summary: str) -> Decision:
        if proposals:
            names = {m.miner_id: m.name for m in snapshot.miners}
            trace.append("PROPOSE")
            trace.extend(f"{names[mid]} → {w:,.0f} W" for mid, w in proposals.items())
            summary += " → " + ", ".join(
                f"{names[mid]} {w:,.0f} W" for mid, w in proposals.items()
            )
        return Decision(summary=summary, trace=trace, proposals=proposals)

    if not snapshot.miners:
        trace.append("No hass-miner miners found — nothing to decide.")
        return done("No miners found")

    candidates = [m for m in snapshot.miners if m.is_available]
    if not candidates:
        trace.append("All miners unavailable — nothing to decide.")
        return done("All miners unavailable")

    # Safety checks come first and override the profile.
    if energy.solar_fault:
        trace.append("SAFETY: solar sensor unavailable → all miners to minimum")
        proposals.update({m.miner_id: _clamp(m, _floor_w(m)) for m in candidates})
        return done("Safety: solar sensor unavailable")
    if energy.battery_soc_pct is not None and energy.battery_soc_pct < battery_floor:
        trace.append(
            f"SAFETY: battery {energy.battery_soc_pct:.0f}% below floor "
            f"{battery_floor:.0f}% → all miners to minimum"
        )
        proposals.update({m.miner_id: _clamp(m, _floor_w(m)) for m in candidates})
        return done("Safety: battery below floor")

    for m in list(candidates):
        if m.temperature_c is not None and m.temperature_c > temp_ceiling:
            trace.append(
                f"SAFETY: {m.name} at {m.temperature_c:.0f} °C exceeds "
                f"{temp_ceiling:.0f} °C → minimum"
            )
            proposals[m.miner_id] = _clamp(m, _floor_w(m))
            candidates.remove(m)
    if not candidates:
        return done("Safety: all miners too hot")

    if profile == "grid_agnostic":
        trace.append("Grid draw allowed without penalty → every miner to maximum")
        for m in candidates:
            proposals[m.miner_id] = _clamp(m, m.max_power_w or m.power_limit_w or 0.0)
        return done(profile_label)

    if energy.available_for_miners_w is None:
        trace.append("Can't compute the power budget (grid balance unknown) → keep current limits")
        for m in candidates:
            if m.power_limit_w is not None:
                proposals[m.miner_id] = m.power_limit_w
        return done(f"{profile_label}: budget unknown")

    budget = max(energy.available_for_miners_w, 0.0)
    if profile == "battery_focused" and energy.battery_soc_pct is not None and profile_def:
        stop_at = profile_def["parameters"]["stop_at_soc_pct"]
        if energy.battery_soc_pct <= stop_at:
            trace.append(f"Battery at or below {stop_at}% → no budget for miners")
            budget = 0.0
    trace.append(f"Power budget: {_w(budget)} split across {len(candidates)} miner(s)")

    share = budget / len(candidates)
    for m in candidates:
        limit = _clamp(m, share)
        if share < _floor_w(m):
            note = "below its minimum → idle at minimum"
            if profile == "grid_independent":
                note += f" (would import ~{_w(_floor_w(m) - share)})"
            trace.append(f"{m.name}: share {_w(share)} {note}")
        proposals[m.miner_id] = limit
    return done(f"{profile_label}: budget {_w(budget)}")
