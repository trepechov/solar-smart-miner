"""Group 4, Target: Solar-follow keeps the grid import inside a range (rule.small-import-target).

Without a battery the inverters hold their output to the load, so at 0 W on the meter you
can't tell 500 W of sun from 5 kW; a small steady import is the proof that all the solar is
used. The measured import alone decides, there is no power budget:

- below the minimum: one increment up (Allocation picks the miner);
- inside the range: hold (Allocation may still even out the load or tidy a limit);
- above the maximum, once it has lasted the step-down delay (the sunrise delay while the sun
  rises): down, by the smallest cut that brings the import back under the maximum.
"""
from __future__ import annotations

from ..protocols import Decision
from .allocation import DOWN, UP, min_import_range_w
from .context import Context
from .describe import _w


def target(ctx: Context) -> Decision | None:
    energy = ctx.energy
    trace = ctx.trace
    label = ctx.profile_label

    if energy.grid_net_w is None:
        trace.append("Grid import unknown → keep current limits")
        for m in ctx.candidates:
            ctx.plans[m.miner_id] = ctx.hold(m, "grid import unknown")
        return ctx.done(f"{label}: grid import unknown")

    import_w = -energy.grid_net_w
    ctx.import_w = import_w
    import_min_w, import_max_w = ctx.import_min_w, ctx.import_max_w
    if import_max_w - import_min_w < (width := min_import_range_w(ctx.steps)):
        # Narrower than one step: a step up from below the minimum could land above
        # the maximum, and the farm would step up and down around it.
        import_max_w = import_min_w + width
        trace.append(
            "Import maximum less than one power step above the minimum → using "
            f"{_w(import_max_w)}; fix it in Configure → Settings"
        )
    trace.append(f"Grid import range: {_w(import_min_w)} to {_w(import_max_w)}")
    ctx.may_step_up = ctx.sun_up is not False

    if import_w < import_min_w:
        if not ctx.may_step_up:
            trace.append("Import below the minimum but the sun is down → nothing starts or steps up")
        else:
            ctx.direction = UP
            trace.append(
                f"Import {_w(import_w)} below the minimum: the solar covers the house and the "
                "inverters may be holding back → one more increment (start a stopped "
                "miner first, else one step up)"
            )
    elif import_w > import_max_w:
        rising = ctx.sun_rising
        wait = ctx.morning_step_down_delay_minutes if rising else ctx.step_down_delay_minutes
        lasted = ctx.minutes_import_high or 0.0
        if lasted < wait:
            why = " (the sun is rising and should catch up)" if rising else ""
            trace.append(
                f"Import {_w(import_w)} above the maximum for {lasted:.0f} min; steps down "
                f"after {wait:.0f} min{why} → every miner holds"
            )
            ctx.others_wait(None, "waiting for the sun" if rising else "waiting out the shortfall")
            return ctx.done(f"{label}: import {_w(import_w)} above the maximum, waiting")
        ctx.direction = DOWN
        ctx.excess_w = import_w - import_max_w
        trace.append(
            f"Import {_w(import_w)} above the maximum for {lasted:.0f} min → step down by at "
            f"least {_w(ctx.excess_w)}"
        )
    else:
        trace.append(f"Import {_w(import_w)} inside the range → hold")
    return None
