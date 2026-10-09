"""Rule-based decisions.

Works out one plan per miner each cycle and explains it in a trace. Nothing here touches
the miners: a plan is carried out only through control.py, when the control mode allows it.

The rules run in groups, in a fixed order; an earlier group always wins and a later one never
undoes it (CLAUDE.md, "Fewer Rules"):

1. Safety (safety.py): may end the decision and change several miners at once.
2. Pacing (pacing.py): after a change every miner holds while it settles (the ramp lock).
3. Limits (limits.py): the sun down or setting (nothing starts or steps up), the temperature band.
4. Target (profiles.py): up, down or hold, from the measured grid import.
5. Allocation and 6. Tidy (allocation.py): which one miner moves, and to which step.

If the rules themselves fail, every miner holds and the trace says why (a failed update would
leave the integration's entities unavailable).
"""
from __future__ import annotations

import logging

from ..const import (
    DEFAULT_IMPORT_MAX_W,
    DEFAULT_IMPORT_MIN_W,
    DEFAULT_POWER_STEPS,
    DEFAULT_RAMP_LOCK_MINUTES,
    PROFILES,
    PROFILES_BY_NAME,
)
from ..protocols import ACTION_HOLD, CoordinatorSnapshot, Decision, MinerPlan
from . import limits, pacing, profiles, safety
from .allocation import _ladder, allocate, min_import_range_w
from .context import Context
from .describe import _describe_energy, _describe_miner, _describe_plan, describe_proposal

__all__ = [
    "_describe_plan",
    "_ladder",
    "build_decision",
    "describe_proposal",
    "min_import_range_w",
]

_LOGGER = logging.getLogger(__name__)


def build_decision(
    snapshot: CoordinatorSnapshot,
    profile: str,
    temp_target: float,
    temp_tolerance: float,
    battery_floor: float,
    power_steps: list[float] | None = None,
    import_min_w: float = DEFAULT_IMPORT_MIN_W,
    import_max_w: float = DEFAULT_IMPORT_MAX_W,
    minutes_since_change: float | None = None,
    ramp_lock_minutes: float = DEFAULT_RAMP_LOCK_MINUTES,
    ramp_done: list[str] | tuple[str, ...] = (),
    minutes_import_high: float | None = None,
    step_down_delay_minutes: float = 0.0,
    morning_step_down_delay_minutes: float = 0.0,
    sun_up: bool | None = None,
    sunrise: bool = False,
    sunset: bool = False,
    meter_lost_minutes: float | None = None,
    base_load_w: float | None = None,
) -> Decision:
    """One plan per miner. Apart from safety, at most one miner changes per decision.

    `minutes_since_change` is the time since the last change on any miner (a command sent,
    or a limit or stop seen to change), None if none is known; 0 while a command is still
    being checked. Until `ramp_lock_minutes` have passed every miner holds. `ramp_done` names
    the changed miners that already draw their new power, so they no longer hold the farm.

    Solar-follow steers on the measured grid import alone (rule.small-import-target): below
    `import_min_w` (the minimum) it takes one increment, up to `import_max_w` it holds, and
    above that it steps down once the import has been that high for `minutes_import_high` >=
    the step-down delay (the longer sunrise delay during `sunrise`). The sun down (`sun_up`
    False; None means unknown) or `sunset` blocks starts and step-ups. Sunrise and sunset come
    from transition.py. While the grid meter is unknown (`meter_lost_minutes`), every miner
    holds for the grace period, then an import estimated with `base_load_w` decides, only
    downwards (safety.py).
    """
    # One profile: an older stored name (before the migration ran) reads as Solar-follow.
    profile_def = PROFILES_BY_NAME.get(profile, PROFILES[0])
    ctx = Context(
        snapshot=snapshot,
        steps=list(power_steps) if power_steps else list(DEFAULT_POWER_STEPS),
        profile=profile,
        profile_label=profile_def["display_name"],
        temp_target=temp_target,
        temp_tolerance=temp_tolerance,
        battery_floor=battery_floor,
        import_min_w=import_min_w,
        import_max_w=import_max_w,
        minutes_since_change=minutes_since_change,
        ramp_lock_minutes=ramp_lock_minutes,
        ramp_done=ramp_done,
        minutes_import_high=minutes_import_high,
        step_down_delay_minutes=step_down_delay_minutes,
        morning_step_down_delay_minutes=morning_step_down_delay_minutes,
        sun_up=sun_up,
        sunrise=sunrise,
        sunset=sunset,
        meter_lost_minutes=meter_lost_minutes,
        base_load_w=base_load_w,
    )
    try:
        return _run(ctx)
    except Exception as err:  # noqa: BLE001 - a bug in a rule must not stop the update
        _LOGGER.exception("The decision failed; every miner holds")
        return _hold_all(snapshot, f"{type(err).__name__}: {err}")


def _run(ctx: Context) -> Decision:
    snapshot = ctx.snapshot
    ctx.trace += ["READ", *_describe_energy(ctx.energy)]
    ctx.trace += [_describe_miner(m) for m in snapshot.miners]
    ctx.trace += [
        "THINK",
        f"Profile: {ctx.profile_label}",
        f"Power steps: {', '.join(f'{x:,.0f}' for x in ctx.steps)} W",
        f"Temperature: target {ctx.temp_target:.0f} °C, step down at {ctx.too_warm_c:.0f} °C",
    ]
    if not snapshot.miners:
        ctx.trace.append("No hass-miner miners found — nothing to decide.")
        return ctx.done("No miners found")

    # A stopped miner is still ours to start again; only unreachable ones are skipped.
    ctx.candidates = [m for m in snapshot.miners if m.is_available or m.is_stopped]
    if not ctx.candidates:
        ctx.trace.append("All miners unavailable — nothing to decide.")
        return ctx.done("All miners unavailable")

    for group in (safety.check, pacing.check, limits.check, profiles.target):
        if (decision := group(ctx)) is not None:
            return decision
    return allocate(ctx)


def _hold_all(snapshot: CoordinatorSnapshot, error: str) -> Decision:
    plans = {
        m.miner_id: MinerPlan(ACTION_HOLD, limit_w=m.power_limit_w, reason="decision error")
        for m in snapshot.miners
    }
    return Decision(
        summary="Error in the rules: every miner holds",
        trace=["ERROR", f"The decision failed ({error}) → every miner holds", "See the Home Assistant log."],
        plans=plans,
    )
