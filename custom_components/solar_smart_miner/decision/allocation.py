"""Groups 5 and 6, Allocation and Tidy: turn the target into one miner's step.

Every change restarts a miner, so limits only move between a few fixed steps
(const.DEFAULT_POWER_STEPS), and a proposal changes one miner at a time: the farm's load would
drop to almost 0 W if several restarted together. That one change may skip steps. Below the
lowest step a miner is stopped, through its relay or its own pause switch (see MinerSnapshot).
When nothing else changes, a limit that is off the steps moves onto the nearest one (Tidy).
"""
from __future__ import annotations

import math
from typing import TYPE_CHECKING

from ..const import HOLD_TOLERANCE_W, UP_MARGIN_W
from ..protocols import (
    ACTION_STOP,
    ACTION_HOLD,
    STOP_METHOD_PAUSE,
    STOP_METHOD_RELAY,
    Decision,
    MinerPlan,
    MinerSnapshot,
)
from .describe import _w

if TYPE_CHECKING:
    from .context import Context

LIMIT_STEP_W = 10  # fallback rounding when a miner's range holds none of the steps


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


def allocate(ctx: Context) -> Decision:
    """One change for the budget the target set, or every miner holds."""
    from .pacing import tuning_left

    ladders = {m.miner_id: _ladder(m, ctx.steps) for m in ctx.candidates}
    tuning = {
        m.miner_id: left
        for m in ctx.candidates
        if not m.is_stopped and (left := tuning_left(m, ctx.tuning_settle_minutes)) is not None
    }
    change = _one_change(
        ctx.candidates, ctx.budget, ladders, ctx.caps, tuning, ctx.trace,
        down_at_w=ctx.down_at_w, step_up_anyway=ctx.step_up_anyway,
        may_step_up=ctx.sun_up is not False,
    )
    label = ctx.profile_label
    if change is None:
        ctx.others_wait(None, "budget")
        for mid in tuning:
            ctx.plans[mid] = MinerPlan(ACTION_HOLD, limit_w=ctx.plans[mid].limit_w, reason="tuning")
        return ctx.done(f"{label}: budget {_w(ctx.budget)}")
    m, lv, why = change
    ladder = ladders[m.miner_id]
    if lv is None:
        reason = "not enough power for the lowest step"
        ctx.plans[m.miner_id] = ctx.stop(m, reason)
        if ctx.plans[m.miner_id].action == ACTION_STOP:
            ctx.trace.append(f"{m.name}: {reason}")
    else:
        ctx.plans[m.miner_id] = ctx.to_step(m, ladder[lv], why)
    ctx.trace.append(f"One miner changes at a time → {m.name}; the others wait for the next decision")
    ctx.others_wait(m, "budget")
    return ctx.done(f"{label}: budget {_w(ctx.budget)}")
