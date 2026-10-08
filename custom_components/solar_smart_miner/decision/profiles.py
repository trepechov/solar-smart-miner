"""Group 4, Target: what the profile asks for, up, down or hold, as a power budget for the
allocation (Solar-follow also steers on the measured grid import)."""
from __future__ import annotations

import math

from ..const import HOLD_TOLERANCE_W
from ..protocols import Decision
from .allocation import min_import_range_w
from .context import Context
from .describe import _w


def target(ctx: Context) -> Decision | None:
    energy = ctx.energy
    profile_label = ctx.profile_label
    trace = ctx.trace

    if energy.available_for_miners_w is None:
        trace.append("Can't compute the power budget (grid balance unknown) → keep current limits")
        for m in ctx.candidates:
            ctx.plans[m.miner_id] = ctx.hold(m, "budget unknown")
        return ctx.done(f"{profile_label}: budget unknown")

    available = energy.available_for_miners_w
    down_at_w = HOLD_TOLERANCE_W
    step_up_anyway = False
    # Aim for a small steady import: at 0 W throttled inverters hide what the panels could give.
    import_min_w, import_max_w = ctx.import_min_w, ctx.import_max_w
    available += import_min_w
    if energy.grid_net_w is not None:
        import_w = -energy.grid_net_w
        if import_max_w - import_min_w < (width := min_import_range_w(ctx.steps)):
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
            if ctx.sun_up is False:
                trace.append("Import below the minimum but the sun is down → nothing starts or steps up")
            else:
                step_up_anyway = True
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
                return ctx.done(f"{profile_label}: import {_w(import_w)} above the maximum, waiting")
    budget = max(available, 0.0)
    trace.append(f"Power budget: {_w(budget)} for {len(ctx.candidates)} miner(s)")
    ctx.budget, ctx.down_at_w, ctx.step_up_anyway = budget, down_at_w, step_up_anyway
    return None
