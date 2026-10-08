"""Groups 5 and 6, Allocation and Tidy: turn the target's up or down into one miner's step.

Every change restarts a miner, so limits only move between a few fixed steps
(const.DEFAULT_POWER_STEPS), and a proposal changes one miner at a time: the farm's load would
drop to almost 0 W if several restarted together. Below the lowest step a miner is stopped,
through its relay or its own pause switch (see MinerSnapshot).

- Down: the hungriest miner that can take the whole cut, on the highest step that does
  (a cut may skip steps: one restart either way); if none can, the lowest-power one stops.
- Up: one increment. A stopped miner starts at its lowest step first, else the weakest running
  miner (of equals, the coolest) goes one step up.
- Even load (once every miner runs, the import inside the range): two miners two or more steps
  apart come one step closer, the weakest up, or the hungriest down when it can't.
- Tidy: when nothing else changes, a limit that is off the steps moves onto the nearest one.
"""
from __future__ import annotations

import math
from typing import TYPE_CHECKING

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
UP = "up"  # what the target asks of the allocation (None: hold, even out, tidy)
DOWN = "down"


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
    ladders: dict[str, list[float]],
    caps: dict[str, int],
    tuning: dict[str, float],
    trace: list[str],
    *,
    direction: str | None,
    excess_w: float = 0.0,
    may_step_up: bool = True,
) -> tuple[MinerSnapshot, int | None, str] | None:
    """The one miner to change, its new step (index; None = stop) and the reason.

    `caps` is the highest step a miner may step up to (a warm miner: its current one), and
    `tuning` the minutes a miner is still tuning (no step up until then). `excess_w` is how far
    the import is over the maximum, for a step down.
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

    running = [m for m in candidates if level[m.miner_id] is not None]

    if direction == DOWN:
        # The hungriest miner that can take the whole cut and keep running, on the highest
        # step that does; if none can, stop the lowest-power one.
        fits: list[tuple[MinerSnapshot, int]] = []
        for m in running:
            lv = level[m.miner_id]
            ladder = ladders[m.miner_id]
            new = next((n for n in range(lv - 1, -1, -1) if ladder[lv] - ladder[n] >= excess_w), None)
            if new is not None:
                fits.append((m, new))
        # Of equals, the last miner in the list: the first ones start first and stop last.
        if fits:
            m, new = max(reversed(fits), key=lambda pair: (watts(pair[0]), temp(pair[0])))
            return m, new, "import above the maximum"
        if running:
            return min(reversed(running), key=watts), None, "import above the maximum"
        return None

    if direction == UP:
        # One increment: start a stopped miner at its lowest step first ...
        for m in candidates:
            if level[m.miner_id] is None:
                return m, 0, "import below the minimum"
        # ... else the weakest running miner (of equals, the coolest) one step up.
        for m in sorted(running, key=lambda x: (watts(x), temp(x))):
            lv = level[m.miner_id]
            if lv >= top_of(m):
                continue
            if m.miner_id in tuning:
                trace.append(
                    f"{m.name}: could step up to {_w(ladders[m.miner_id][lv + 1])} but is still "
                    f"tuning (~{tuning[m.miner_id]:.0f} min left) → holding {_w(m.power_limit_w)}"
                )
                continue
            return m, lv + 1, "import below the minimum"

    if len(running) == len(candidates) > 1:
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
    # Nothing else to change: move a limit that is off the steps onto the nearest one.
    for m in running:
        if m.power_limit_w is not None and m.power_limit_w != ladders[m.miner_id][level[m.miner_id]]:
            return m, level[m.miner_id], "off step"
    return None


def min_import_range_w(steps: list[float]) -> float:
    """The narrowest Solar-max import range: the largest gap between neighbouring power steps
    (200 W with one step), so one step up from below the minimum stays within the maximum."""
    return max((b - a for a, b in zip(steps, steps[1:])), default=200.0)


def allocate(ctx: Context) -> Decision:
    """One change for what the target asked, or every miner holds."""
    from .pacing import tuning_left

    ladders = {m.miner_id: _ladder(m, ctx.steps) for m in ctx.candidates}
    tuning = {
        m.miner_id: left
        for m in ctx.candidates
        if not m.is_stopped and (left := tuning_left(m, ctx.tuning_settle_minutes)) is not None
    }
    change = _one_change(
        ctx.candidates, ladders, ctx.caps, tuning, ctx.trace,
        direction=ctx.direction, excess_w=ctx.excess_w, may_step_up=ctx.may_step_up,
    )
    summary = f"{ctx.profile_label}: import {_w(ctx.import_w)}"
    if change is None:
        ctx.others_wait(None, "import in range" if ctx.direction is None else "nothing to change")
        for mid in tuning:
            ctx.plans[mid] = MinerPlan(ACTION_HOLD, limit_w=ctx.plans[mid].limit_w, reason="tuning")
        return ctx.done(summary)
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
    ctx.others_wait(m, "one change at a time")
    return ctx.done(summary)
