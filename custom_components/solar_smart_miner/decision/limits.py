"""Group 3, Limits: block a direction for a miner, or require one to come down.

The temperature band: below the target a miner may step up; up to target + tolerance it holds
(its step becomes its cap); at or above that it steps down one step. A miner still tuning is
skipped, since the restart cooled it.
"""
from __future__ import annotations

from ..protocols import Decision, MinerSnapshot
from .allocation import _ladder, _nearest_level
from .context import Context
from .pacing import tuning_left


def check(ctx: Context) -> Decision | None:
    too_warm: list[MinerSnapshot] = []
    for m in ctx.candidates:
        if m.is_stopped or m.temperature_c is None:
            continue
        if tuning_left(m, ctx.tuning_settle_minutes) is not None:
            # The change restarted it: its temperature fell and says nothing yet.
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
        return ctx.done(f"Temperature: {m.name} too warm")
    return None
