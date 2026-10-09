"""Group 3, Limits: block a direction, or require one miner to come down.

The sun down or setting: nothing starts or steps up (rule.sunset-one-by-one). Starting or
stopping a miner moves the import by its lowest step, more than the import range is wide, so at
sunset every stop would otherwise be followed by a start (2026-10-08: nine rounds in 85 minutes).
Even load may still step down. Sunset lasts until the next sunrise (transition.py), so an evening
cloud that clears starts nothing until the morning (owner, 2026-10-09).

The temperature band: below the target a miner may step up; up to target + tolerance it holds
(its step becomes its cap); at or above that it steps down one step.
"""
from __future__ import annotations

from ..protocols import Decision, MinerSnapshot
from .allocation import _ladder, _nearest_level
from .context import Context


def check(ctx: Context) -> Decision | None:
    if ctx.sun_up is False:
        ctx.may_step_up, ctx.no_up_reason = False, "the sun is down"
    elif ctx.sunset:
        ctx.may_step_up, ctx.no_up_reason = False, "the sun is setting"
        ctx.trace.append("Sunset: production is falling → nothing starts or steps up until sunrise")
    too_warm: list[MinerSnapshot] = []
    for m in ctx.candidates:
        if m.is_stopped or m.temperature_c is None:
            continue
        ladder = _ladder(m, ctx.steps)
        lv = _nearest_level(ladder, m.power_limit_w)
        temp = f"{m.temperature_c:.0f} °C"
        if m.temperature_c < ctx.temp_target:
            continue
        if m.temperature_c < ctx.too_warm_c or lv == 0:
            if m.temperature_c >= ctx.too_warm_c:
                # Not the plugin's job: the Braiins OS cutoff is the last defense.
                ctx.trace.append(f"{m.name}: {temp} at its lowest step → left to the miner's own cutoff")
            else:
                ctx.trace.append(f"{m.name}: {temp}, within the target band → no step up")
            ctx.caps[m.miner_id] = lv
            continue
        ctx.trace.append(f"{m.name}: {temp}, at or above {ctx.too_warm_c:.0f} °C → one step down")
        too_warm.append(m)
    if too_warm:
        # One change per proposal: the hottest miner now, the others in the next decisions.
        m = max(too_warm, key=lambda x: x.temperature_c)
        ladder = _ladder(m, ctx.steps)
        ctx.plans[m.miner_id] = ctx.to_step(
            m, ladder[_nearest_level(ladder, m.power_limit_w) - 1], "too warm"
        )
        ctx.others_wait(m, "waits its turn")
        if len(too_warm) > 1:
            ctx.trace.append(f"One miner changes at a time → {m.name} first")
        return ctx.done(f"Temperature: {m.name} too warm", "rule.temperature-band")
    return None
