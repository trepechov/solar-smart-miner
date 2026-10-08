"""Rule-based decisions.

Works out one plan per miner each cycle and explains it in a trace. Nothing here touches
the miners: a plan is carried out only through control.py, when the control mode allows
it and the owner presses Apply.

Every change restarts a miner (2 to 4 minutes at no power), so limits only move between
a few fixed steps (const.DEFAULT_POWER_STEPS), and a proposal changes one miner at a time:
the farm's load would drop to almost 0 W if several restarted together. That one change may
skip steps. After any change the whole farm waits for the ramp lock before the next.
Below the lowest step a miner is stopped, through its relay or its own pause switch
(see MinerSnapshot).
"""
from __future__ import annotations

import math

from .const import (
    DEFAULT_IMPORT_MAX_W,
    DEFAULT_IMPORT_MIN_W,
    DEFAULT_POWER_STEPS,
    DEFAULT_RAMP_LOCK_MINUTES,
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


def _one_change(
    candidates: list[MinerSnapshot],
    budget: float,
    ladders: dict[str, list[float]],
    caps: dict[str, int],
    tuning: dict[str, float],
    trace: list[str],
    *,
    down_at_w: float = HOLD_TOLERANCE_W,
    step_up_anyway: bool = False,
    may_step_up: bool = True,
) -> tuple[MinerSnapshot, int | None, str] | None:
    """The one miner to change for a budget, its new step (index; None = stop) and the reason.

    Starts from where each miner is now, so a small budget wobble changes nothing: a
    shortfall up to `down_at_w` keeps the current steps (a cut then lands within
    HOLD_TOLERANCE_W of the budget), and stepping up needs UP_MARGIN_W of spare power on top.
    The change may skip steps; it is one restart either way.
    `step_up_anyway`: the meter shows spare solar the budget can't (throttled inverters), so
    take one increment even without the spare power for it: start a stopped miner at its
    lowest step, else raise the weakest running miner one step.
    `caps` is the highest step a miner may step up to (a warm miner: its current one), and
    `tuning` the minutes a miner is still tuning (no step up until then).

    Once every miner runs, the load is spread evenly (rule.even-load, secondary): extra power
    raises a miner no further than its even share of the budget or one step above the
    next-weakest. When the budget needs no change but two limits are two or more steps apart,
    the weakest (of equals, the coolest) steps up one step, or if it can't (or `may_step_up` is
    False), the hungriest (of equals, the hottest) steps down one. A step up that pushes the
    import over the range is then taken back from the hungriest.
    """
    level: dict[str, int | None] = {
        m.miner_id: None if m.is_stopped else _nearest_level(ladders[m.miner_id], m.power_limit_w)
        for m in candidates
    }

    def watts(m: MinerSnapshot) -> float:
        lv = level[m.miner_id]
        return 0.0 if lv is None else ladders[m.miner_id][lv]

    def temp(m: MinerSnapshot) -> float:
        return m.temperature_c if m.temperature_c is not None else 0.0

    def top_of(m: MinerSnapshot) -> int:
        return min(caps.get(m.miner_id, len(ladders[m.miner_id]) - 1), len(ladders[m.miner_id]) - 1)

    total = sum(watts(m) for m in candidates)
    running = [m for m in candidates if level[m.miner_id] is not None]
    all_running = len(running) == len(candidates) > 1

    if total > budget + down_at_w:
        # Too much: the hungriest miner that can take the whole cut and keep running,
        # on the highest step that fits; if none can, stop the lowest-power one.
        fits: list[tuple[MinerSnapshot, int]] = []
        for m in running:
            lv = level[m.miner_id]
            ladder = ladders[m.miner_id]
            for new in range(lv - 1, -1, -1):
                if total - (ladder[lv] - ladder[new]) <= budget + HOLD_TOLERANCE_W:
                    fits.append((m, new))
                    break
        # Of equals, the last miner in the list: the first ones start first and stop last.
        if fits:
            m, new = max(reversed(fits), key=lambda pair: (watts(pair[0]), temp(pair[0])))
            return m, new, "budget"
        if running:
            return min(reversed(running), key=watts), None, "budget"
        return None

    spare = budget - total
    # Room to spare: start a stopped miner at its lowest step first ...
    for m in candidates:
        if level[m.miner_id] is None and (
            step_up_anyway or ladders[m.miner_id][0] + UP_MARGIN_W <= spare
        ):
            return m, 0, "budget"
    # ... else raise the weakest running miner (of equals, the coolest) as far as the spare
    # power allows; with every miner running, no further than its even share of the budget or
    # one step above the next-weakest, whichever is higher.
    share = budget / len(running) if running else 0.0
    for m in sorted(running, key=lambda x: (watts(x), temp(x))):
        lv = level[m.miner_id]
        ladder = ladders[m.miner_id]
        top = top_of(m)
        if all_running:
            ceiling = max(min(watts(o) for o in running if o is not m), ladder[lv])
            top = min(top, max(
                i for i in range(len(ladder))
                if i == 0 or ladder[i] <= share or ladder[i - 1] <= ceiling
            ))
        new = max(
            (i for i in range(lv + 1, top + 1) if ladder[i] - ladder[lv] + UP_MARGIN_W <= spare),
            default=None,
        )
        if new is None and step_up_anyway and lv < top:
            new = lv + 1
        if new is None:
            continue
        if m.miner_id in tuning:
            trace.append(
                f"{m.name}: could step up to {_w(ladder[new])} but is still tuning "
                f"(~{tuning[m.miner_id]:.0f} min left) → holding {_w(m.power_limit_w)}"
            )
            continue
        return m, new, "budget"
    if all_running:
        # Even load: one step closer when two miners are two or more steps apart. Within one
        # step is even enough; evening that out would only make them trade places.
        low = min(running, key=lambda x: (watts(x), temp(x)))
        high = max(running, key=lambda x: (watts(x), temp(x)))
        lv_low, lv_high = level[low.miner_id], level[high.miner_id]
        if lv_high > 0 and ladders[high.miner_id][lv_high - 1] > watts(low):
            spread = f"{high.name} {_w(watts(high))} against {low.name} {_w(watts(low))}"
            if may_step_up and lv_low < top_of(low) and low.miner_id not in tuning:
                trace.append(f"Even load: {spread} → {low.name} one step up")
                return low, lv_low + 1, "even load"
            trace.append(f"Even load: {spread} → {high.name} one step down")
            return high, lv_high - 1, "even load"
    # Nothing to change for the budget: move a limit that is off the steps onto the nearest one.
    for m in running:
        if m.power_limit_w is not None and m.power_limit_w != ladders[m.miner_id][level[m.miner_id]]:
            return m, level[m.miner_id], "budget"
    return None


def min_import_range_w(steps: list[float]) -> float:
    """The narrowest Solar-max import range: the largest gap between neighbouring power steps
    (200 W with one step), so one step up from below the minimum stays within the maximum."""
    return max((b - a for a, b in zip(steps, steps[1:])), default=200.0)


def _tuning_left(m: MinerSnapshot, settle_minutes: float) -> float | None:
    """Minutes the miner is still assumed to be tuning, or None if it has settled."""
    since = m.minutes_since_limit_change
    if since is None or since >= settle_minutes:
        return None
    return settle_minutes - since


def build_decision(
    snapshot: CoordinatorSnapshot,
    profile: str,
    temp_target: float,
    temp_tolerance: float,
    battery_floor: float,
    power_steps: list[float] | None = None,
    tuning_settle_minutes: float = DEFAULT_TUNING_SETTLE_MINUTES,
    import_min_w: float = DEFAULT_IMPORT_MIN_W,
    import_max_w: float = DEFAULT_IMPORT_MAX_W,
    minutes_since_change: float | None = None,
    ramp_lock_minutes: float = DEFAULT_RAMP_LOCK_MINUTES,
    ramp_done: list[str] | tuple[str, ...] = (),
    minutes_import_high: float | None = None,
    step_down_delay_minutes: float = 0.0,
    morning_step_down_delay_minutes: float = 0.0,
    sun_up: bool | None = None,
    sun_rising: bool | None = None,
) -> Decision:
    """One plan per miner. Apart from safety, at most one miner changes per decision.

    `minutes_since_change` is the time since the last change on any miner (a command sent,
    or a limit or stop seen to change), None if none is known; 0 while a command is still
    being checked. Until `ramp_lock_minutes` have passed every miner holds. `ramp_done` names
    the changed miners that already draw their new power, so they no longer hold the farm.

    Solar-max with a known meter steers on the grid import (rule.small-import-target):
    below `import_min_w` (the minimum) it takes one increment, up to `import_max_w` it
    holds, and above that it steps down once the import has been that high for `minutes_import_high` >= the step-down delay (the morning delay while
    `sun_rising`). `sun_up` False blocks starts and step-ups; None means unknown.
    """
    energy = snapshot.energy
    steps = list(power_steps) if power_steps else list(DEFAULT_POWER_STEPS)
    profile_def = PROFILES_BY_NAME.get(profile)
    profile_label = profile_def["display_name"] if profile_def else profile

    trace = ["READ", *_describe_energy(energy)]
    trace += [_describe_miner(m) for m in snapshot.miners]
    too_warm_c = temp_target + temp_tolerance
    trace += [
        "THINK",
        f"Profile: {profile_label}",
        f"Power steps: {', '.join(f'{x:,.0f}' for x in steps)} W",
        f"Temperature: target {temp_target:.0f} °C, step down at {too_warm_c:.0f} °C",
    ]
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
        # In the order the miners are listed, whichever was decided first.
        ordered = {m.miner_id: plans[m.miner_id] for m in snapshot.miners if m.miner_id in plans}
        plans.clear()
        plans.update(ordered)
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

    def others_wait(changed: MinerSnapshot | None, reason: str) -> None:
        for m in candidates:
            if m is not changed:
                plans[m.miner_id] = stop(m, reason) if m.is_stopped else hold(m, reason)

    # Ramp lock: a miner that just changed is restarting and the readings are misleading.
    if minutes_since_change is not None and minutes_since_change < ramp_lock_minutes:
        left = ramp_lock_minutes - minutes_since_change
        trace.append(
            f"A miner changed {minutes_since_change:.0f} min ago and is restarting "
            f"→ every miner holds for ~{left:.0f} min more (ramp lock)"
        )
        others_wait(None, "ramp lock")
        return done("Waiting for a miner to restart")
    if ramp_done:
        trace.append(
            f"{', '.join(ramp_done)} already at the new power (hashrate may still be settling) "
            "→ no need to wait out the ramp lock"
        )

    # Temperature band: not a safety step, it only shapes the allocation below.
    caps: dict[str, int] = {}  # highest step a warm miner may have: its current one
    too_warm: list[MinerSnapshot] = []
    for m in candidates:
        if m.is_stopped or m.temperature_c is None:
            continue
        if _tuning_left(m, tuning_settle_minutes) is not None:
            # The change restarted it: its temperature fell and says nothing yet.
            continue
        ladder = _ladder(m, steps)
        lv = _nearest_level(ladder, m.power_limit_w)
        temp = f"{m.temperature_c:.0f} °C"
        if m.temperature_c < temp_target:
            continue
        if m.temperature_c < too_warm_c or lv == 0:
            if m.temperature_c >= too_warm_c:
                # Not the plugin's job: the Braiins OS cutoff is the last defense.
                trace.append(f"{m.name}: {temp} at its lowest step → left to the miner's own cutoff")
            else:
                trace.append(f"{m.name}: {temp}, within the target band → no step up")
            caps[m.miner_id] = lv
            continue
        trace.append(f"{m.name}: {temp}, at or above {too_warm_c:.0f} °C → one step down")
        too_warm.append(m)
    if too_warm:
        # One change per proposal: the hottest miner now, the others in the next decisions.
        m = max(too_warm, key=lambda x: x.temperature_c)
        ladder = _ladder(m, steps)
        plans[m.miner_id] = to_step(m, ladder[_nearest_level(ladder, m.power_limit_w) - 1], "too warm")
        others_wait(m, "waits its turn")
        if len(too_warm) > 1:
            trace.append(f"One miner changes at a time → {m.name} first")
        return done(f"Temperature: {m.name} too warm")

    ladders = {m.miner_id: _ladder(m, steps) for m in candidates}

    if profile == "grid_agnostic":
        trace.append("Grid draw allowed without penalty → every miner to its top step, one at a time")
        for m in candidates:
            ladder = ladders[m.miner_id]
            top = min(caps.get(m.miner_id, len(ladder) - 1), len(ladder) - 1)
            plan = to_step(m, ladder[top], "grid agnostic")
            if plan.action != ACTION_HOLD:
                plans[m.miner_id] = plan
                others_wait(m, "waits its turn")
                break
        else:
            others_wait(None, "grid agnostic")
        return done(profile_label)

    if energy.available_for_miners_w is None:
        trace.append("Can't compute the power budget (grid balance unknown) → keep current limits")
        for m in candidates:
            plans[m.miner_id] = hold(m, "budget unknown")
        return done(f"{profile_label}: budget unknown")

    available = energy.available_for_miners_w
    down_at_w = HOLD_TOLERANCE_W
    step_up_anyway = False
    if profile == "solar_max":
        # Aim for a small steady import: at 0 W throttled inverters hide what the panels could give.
        available += import_min_w
        if energy.grid_net_w is not None:
            import_w = -energy.grid_net_w
            if import_max_w - import_min_w < (width := min_import_range_w(steps)):
                # Narrower than one step: a step up from below the minimum could land above
                # the maximum, and the farm would step up and down around it.
                import_max_w = import_min_w + width
                trace.append(
                    "Import maximum less than one power step above the minimum → using "
                    f"{_w(import_max_w)}; fix it in Configure → Settings"
                )
            trace.append(f"Grid import range: {_w(import_min_w)} to {_w(import_max_w)}")
            if import_w <= import_max_w:
                # The measured import decides whether to step down; the budget only sizes the cut.
                down_at_w = math.inf
            else:
                # A cut sized to bring the import back to the maximum, no further.
                available += import_max_w - import_min_w - HOLD_TOLERANCE_W
            if import_w < import_min_w:
                if sun_up is False:
                    trace.append("Import below the minimum but the sun is down → nothing starts or steps up")
                else:
                    step_up_anyway = True
                    trace.append(
                        f"Import {_w(import_w)} below the minimum: the solar covers the house and the "
                        "inverters may be holding back → one more increment (start a stopped "
                        "miner first, else one step up)"
                    )
            elif import_w > import_max_w:
                wait = morning_step_down_delay_minutes if sun_rising else step_down_delay_minutes
                lasted = minutes_import_high or 0.0
                if lasted < wait:
                    why = " (the sun is rising and should catch up)" if sun_rising else ""
                    trace.append(
                        f"Import {_w(import_w)} above the maximum for {lasted:.0f} min; steps down "
                        f"after {wait:.0f} min{why} → every miner holds"
                    )
                    others_wait(None, "waiting for the sun" if sun_rising else "waiting out the shortfall")
                    return done(f"{profile_label}: import {_w(import_w)} above the maximum, waiting")
    budget = max(available, 0.0)
    if profile == "battery_focused" and energy.battery_soc_pct is not None and profile_def:
        stop_at = profile_def["parameters"]["stop_at_soc_pct"]
        if energy.battery_soc_pct <= stop_at:
            trace.append(f"Battery at or below {stop_at}% → no budget for miners")
            budget = 0.0
    trace.append(f"Power budget: {_w(budget)} for {len(candidates)} miner(s)")

    tuning = {
        m.miner_id: left
        for m in candidates
        if not m.is_stopped and (left := _tuning_left(m, tuning_settle_minutes)) is not None
    }
    change = _one_change(
        candidates, budget, ladders, caps, tuning, trace,
        down_at_w=down_at_w, step_up_anyway=step_up_anyway, may_step_up=sun_up is not False,
    )
    if change is None:
        others_wait(None, "budget")
        for mid in tuning:
            plans[mid] = MinerPlan(ACTION_HOLD, limit_w=plans[mid].limit_w, reason="tuning")
        return done(f"{profile_label}: budget {_w(budget)}")
    m, lv, why = change
    ladder = ladders[m.miner_id]
    if lv is None:
        reason = "not enough power for the lowest step"
        if profile == "grid_independent":
            reason += f" (would import ~{_w(ladder[0])})"
        plans[m.miner_id] = stop(m, reason)
        if plans[m.miner_id].action == ACTION_STOP:
            trace.append(f"{m.name}: {reason}")
    else:
        plans[m.miner_id] = to_step(m, ladder[lv], why)
    trace.append(f"One miner changes at a time → {m.name}; the others wait for the next decision")
    others_wait(m, "budget")
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


def describe_proposal(miner: MinerSnapshot | None, plan: MinerPlan) -> str:
    """Plan text for the owner; a step also says where the miner is now."""
    text = _describe_plan(plan)
    if plan.action == ACTION_SET_LIMIT and miner is not None and miner.power_limit_w is not None:
        text += f" (from {_w(miner.power_limit_w)})"
    return text
