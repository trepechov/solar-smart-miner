"""Rule-based decision preview.

Stands in for the AI agent (U5) so the decision log shows what the integration
reads and what it *would* do. Nothing here touches the miners: plans are
only reported, never applied.

A miner re-tunes for 14 min to an hour after every power-limit change, so limits
only move between a few fixed steps (const.DEFAULT_POWER_STEPS), one step at a time
from where the miner is now. Below the lowest step a miner is stopped, through its
relay or its own pause switch (see MinerSnapshot).
"""
from __future__ import annotations

import math

from .const import (
    DEFAULT_POWER_STEPS,
    DEFAULT_TUNING_SETTLE_MINUTES,
    HOLD_TOLERANCE_W,
    PROFILES_BY_NAME,
    UP_MARGIN_W,
)
from .protocols import (
    ACTION_HOLD,
    ACTION_SET_LIMIT,
    ACTION_START,
    ACTION_STOP,
    STOP_METHOD_PAUSE,
    STOP_METHOD_RELAY,
    CoordinatorSnapshot,
    Decision,
    EnergySnapshot,
    MinerPlan,
    MinerSnapshot,
)

LIMIT_STEP_W = 10  # fallback rounding when a miner's range holds none of the steps


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


def _ladder(m: MinerSnapshot, steps: list[float]) -> list[float]:
    """The power steps this miner accepts, lowest first."""
    lo = m.min_power_w if m.min_power_w is not None else 0.0
    hi = m.max_power_w if m.max_power_w is not None else math.inf
    ladder = sorted({float(step) for step in steps if lo <= step <= hi})
    if ladder:
        return ladder
    # None of the steps fits this miner: fall back to the ends of its own range.
    ends = {math.floor(lo / LIMIT_STEP_W) * LIMIT_STEP_W}
    if math.isfinite(hi):
        ends.add(math.floor(hi / LIMIT_STEP_W) * LIMIT_STEP_W)
    return sorted(ends)


def _stop_procedure(m: MinerSnapshot) -> tuple[str, str] | None:
    """(method, switch entity) used to stop / start this miner, if it has one."""
    if m.relay_entity_id:
        return STOP_METHOD_RELAY, m.relay_entity_id
    if m.switch_entity_id:
        return STOP_METHOD_PAUSE, m.switch_entity_id
    return None


def _nearest_level(ladder: list[float], limit_w: float | None) -> int:
    """Index of the step closest to the miner's current limit (lowest if unknown)."""
    if limit_w is None:
        return 0
    return min(range(len(ladder)), key=lambda i: abs(ladder[i] - limit_w))


def _allocate(
    candidates: list[MinerSnapshot], budget: float, ladders: dict[str, list[float]]
) -> dict[str, int | None]:
    """Power step (index into its ladder) per miner for a budget; None = stopped.

    Starts from where each miner is now and moves along its steps, so a small
    budget wobble doesn't re-tune anything: a shortfall up to HOLD_TOLERANCE_W keeps
    the current steps, and stepping up needs UP_MARGIN_W of spare power on top.
    """
    level: dict[str, int | None] = {
        m.miner_id: None if m.is_stopped else _nearest_level(ladders[m.miner_id], m.power_limit_w)
        for m in candidates
    }

    def watts(m: MinerSnapshot) -> float:
        lv = level[m.miner_id]
        return 0.0 if lv is None else ladders[m.miner_id][lv]

    def total() -> float:
        return sum(watts(m) for m in candidates)

    # Too much: step the hungriest miner down; below its lowest step it stops.
    while total() > budget + HOLD_TOLERANCE_W:
        running = [m for m in candidates if level[m.miner_id] is not None]
        m = max(reversed(running), key=watts)
        lv = level[m.miner_id]
        level[m.miner_id] = lv - 1 if lv else None

    # Room to spare: step the weakest miner up first (a stopped one counts as 0 W).
    while True:
        for m in sorted(candidates, key=watts):
            lv = level[m.miner_id]
            ladder = ladders[m.miner_id]
            nxt = 0 if lv is None else lv + 1
            if nxt >= len(ladder):
                continue
            if budget - total() >= ladder[nxt] - watts(m) + UP_MARGIN_W:
                level[m.miner_id] = nxt
                break
        else:
            return level


def _tuning_left(m: MinerSnapshot, settle_minutes: float) -> float | None:
    """Minutes the miner is still assumed to be tuning, or None if it has settled."""
    since = m.minutes_since_limit_change
    if since is None or since >= settle_minutes:
        return None
    return settle_minutes - since


def build_decision(
    snapshot: CoordinatorSnapshot,
    profile: str,
    temp_ceiling: float,
    battery_floor: float,
    power_steps: list[float] | None = None,
    tuning_settle_minutes: float = DEFAULT_TUNING_SETTLE_MINUTES,
) -> Decision:
    energy = snapshot.energy
    steps = list(power_steps) if power_steps else list(DEFAULT_POWER_STEPS)
    profile_def = PROFILES_BY_NAME.get(profile)
    profile_label = profile_def["display_name"] if profile_def else profile

    trace = ["READ", *_describe_energy(energy)]
    trace += [_describe_miner(m) for m in snapshot.miners]
    trace += ["THINK", f"Profile: {profile_label}", f"Power steps: {', '.join(f'{x:,.0f}' for x in steps)} W"]
    plans: dict[str, MinerPlan] = {}
    names = {m.miner_id: m.name for m in snapshot.miners}

    def hold(m: MinerSnapshot, reason: str) -> MinerPlan:
        return MinerPlan(ACTION_HOLD, limit_w=m.power_limit_w, reason=reason)

    def stop(m: MinerSnapshot, reason: str) -> MinerPlan:
        if m.is_stopped:
            return MinerPlan(ACTION_HOLD, reason="stays stopped")
        procedure = _stop_procedure(m)
        if procedure is None:
            low = _ladder(m, steps)[0]
            trace.append(f"{m.name}: no stop method (no relay or pause switch) → lowest step instead")
            return MinerPlan(ACTION_SET_LIMIT, limit_w=low, reason="no stop method")
        method, entity = procedure
        return MinerPlan(ACTION_STOP, reason=reason, method=method, target_entity_id=entity)

    def lowest(m: MinerSnapshot, reason: str) -> MinerPlan:
        return to_step(m, _ladder(m, steps)[0], reason)

    def to_step(m: MinerSnapshot, limit_w: float, reason: str) -> MinerPlan:
        """Plan for moving a miner to a step, starting it first if it is stopped."""
        if m.is_stopped:
            procedure = _stop_procedure(m)
            method, entity = procedure if procedure else (None, None)
            return MinerPlan(
                ACTION_START, limit_w=limit_w, reason=reason, method=method, target_entity_id=entity
            )
        if m.power_limit_w == limit_w:
            return hold(m, reason)
        return MinerPlan(ACTION_SET_LIMIT, limit_w=limit_w, reason=reason)

    def done(summary: str) -> Decision:
        if plans:
            trace.append("PROPOSE")
            parts = []
            for mid, plan in plans.items():
                text = _describe_plan(plan)
                trace.append(f"{names[mid]} → {text}")
                parts.append(f"{names[mid]} {text}")
            summary += " → " + ", ".join(parts)
        return Decision(summary=summary, trace=trace, plans=plans)

    if not snapshot.miners:
        trace.append("No hass-miner miners found — nothing to decide.")
        return done("No miners found")

    # A stopped miner is still ours to start again; only unreachable ones are skipped.
    candidates = [m for m in snapshot.miners if m.is_available or m.is_stopped]
    if not candidates:
        trace.append("All miners unavailable — nothing to decide.")
        return done("All miners unavailable")

    # Safety checks come first and override the profile.
    if energy.solar_fault:
        trace.append(
            "SAFETY: solar sensor unavailable → holding every miner as is "
            "(a short sensor drop must not re-tune them)"
        )
        plans.update({m.miner_id: hold(m, "sensor unavailable") for m in candidates})
        return done("Safety: solar sensor unavailable")
    if energy.battery_soc_pct is not None and energy.battery_soc_pct < battery_floor:
        trace.append(
            f"SAFETY: battery {energy.battery_soc_pct:.0f}% below floor "
            f"{battery_floor:.0f}% → stop all miners"
        )
        plans.update({m.miner_id: stop(m, "battery low") for m in candidates})
        return done("Safety: battery below floor")

    reserved_w = 0.0  # what miners forced to a step by safety still draw from the budget
    for m in list(candidates):
        if m.temperature_c is not None and m.temperature_c > temp_ceiling:
            trace.append(
                f"SAFETY: {m.name} at {m.temperature_c:.0f} °C exceeds "
                f"{temp_ceiling:.0f} °C → lowest step"
            )
            plans[m.miner_id] = lowest(m, "too hot")
            reserved_w += plans[m.miner_id].limit_w or 0.0
            candidates.remove(m)
    if not candidates:
        return done("Safety: all miners too hot")

    ladders = {m.miner_id: _ladder(m, steps) for m in candidates}

    if profile == "grid_agnostic":
        trace.append("Grid draw allowed without penalty → every miner to its top step")
        for m in candidates:
            plans[m.miner_id] = to_step(m, ladders[m.miner_id][-1], "grid agnostic")
        return done(profile_label)

    if energy.available_for_miners_w is None:
        trace.append("Can't compute the power budget (grid balance unknown) → keep current limits")
        for m in candidates:
            plans[m.miner_id] = hold(m, "budget unknown")
        return done(f"{profile_label}: budget unknown")

    budget = max(energy.available_for_miners_w - reserved_w, 0.0)
    if profile == "battery_focused" and energy.battery_soc_pct is not None and profile_def:
        stop_at = profile_def["parameters"]["stop_at_soc_pct"]
        if energy.battery_soc_pct <= stop_at:
            trace.append(f"Battery at or below {stop_at}% → no budget for miners")
            budget = 0.0
    trace.append(f"Power budget: {_w(budget)} for {len(candidates)} miner(s)")

    levels = _allocate(candidates, budget, ladders)
    for m in candidates:
        lv = levels[m.miner_id]
        ladder = ladders[m.miner_id]
        if lv is None:
            reason = "not enough power for the lowest step"
            if profile == "grid_independent":
                reason += f" (would import ~{_w(ladder[0])})"
            plans[m.miner_id] = stop(m, reason)
            if plans[m.miner_id].action == ACTION_STOP:
                trace.append(f"{m.name}: {reason}")
            continue
        target = ladder[lv]
        left = _tuning_left(m, tuning_settle_minutes)
        if (
            left is not None
            and not m.is_stopped
            and m.power_limit_w is not None
            and target > m.power_limit_w
        ):
            trace.append(
                f"{m.name}: could step up to {_w(target)} but is still tuning "
                f"(~{left:.0f} min left) → holding {_w(m.power_limit_w)}"
            )
            plans[m.miner_id] = hold(m, "tuning")
            continue
        plans[m.miner_id] = to_step(m, target, "budget")
    return done(f"{profile_label}: budget {_w(budget)}")


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
