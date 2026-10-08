"""Group 1, Safety: may end the decision and change several miners at once; doesn't wait for
the pacing."""
from __future__ import annotations

from ..protocols import Decision
from .context import Context


def check(ctx: Context) -> Decision | None:
    energy = ctx.energy
    if energy.solar_fault:
        ctx.trace.append(
            "SAFETY: solar sensor unavailable → holding every miner as is "
            "(a short sensor drop must not re-tune them)"
        )
        ctx.plans.update({m.miner_id: ctx.hold(m, "sensor unavailable") for m in ctx.candidates})
        return ctx.done("Safety: solar sensor unavailable")
    if energy.battery_soc_pct is not None and energy.battery_soc_pct < ctx.battery_floor:
        ctx.trace.append(
            f"SAFETY: battery {energy.battery_soc_pct:.0f}% below floor "
            f"{ctx.battery_floor:.0f}% → stop all miners"
        )
        ctx.plans.update({m.miner_id: ctx.stop(m, "battery low") for m in ctx.candidates})
        return ctx.done("Safety: battery below floor")
    return None
