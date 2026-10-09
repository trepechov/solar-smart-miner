"""Group 1, Safety: may end the decision and change several miners at once; doesn't wait for
the pacing.

Grid balance unknown (rule.required-inputs): Solar-follow needs the grid import. On a farm whose
configured solar entity is the grid meter this is the meter lost; with a production sensor and a
house sensor, the balance derived from them. One path for both:

- for METER_GRACE_MIN, every miner holds (meter gaps of seconds to a few minutes are normal);
- after that the import is estimated from the miners' draw, the house load besides the miners
  (Configure -> Farm) and the actual PV output (the reference PV sensor, never the lost meter):
  the normal rules decide on it, but it can only show a shortfall (with zero export PV equals the
  load), so nothing starts or steps up on it. Without a base load or a PV reading, hold.

A fault of the actual-PV sensor alone holds nothing; it only makes the estimate unavailable.
"""
from __future__ import annotations

from ..const import METER_GRACE_MIN
from ..protocols import Decision, EnergySnapshot
from .context import Context
from .describe import _w


def estimated_import_w(energy: EnergySnapshot, base_load_w: float | None) -> float | None:
    """What the grid import should be: miners + the house besides them − actual PV (None if any
    of them is unknown). One definition, for the decision and the step-down timer."""
    if base_load_w is None or energy.pv_power_w is None:
        return None
    return (energy.miner_consumption_sum_w or 0.0) + base_load_w - energy.pv_power_w


def check(ctx: Context) -> Decision | None:
    energy = ctx.energy
    if energy.grid_net_w is None:
        lost = ctx.meter_lost_minutes or 0.0
        estimate = estimated_import_w(energy, ctx.base_load_w)
        if lost < METER_GRACE_MIN or estimate is None:
            why = (
                f"for {lost * 60:.0f} s; a short gap must not re-tune the miners"
                if lost < METER_GRACE_MIN
                else f"for {lost:.0f} min and no estimate (it needs the house load besides the "
                "miners in Configure → Farm and an actual PV reading)"
            )
            ctx.trace.append(f"SAFETY: grid meter unknown {why} → every miner holds")
            ctx.plans.update({m.miner_id: ctx.hold(m, "grid meter unknown") for m in ctx.candidates})
            return ctx.done("Safety: grid meter unknown", "rule.required-inputs")
        ctx.import_w = estimate
        ctx.import_estimated = True
        ctx.may_step_up, ctx.no_up_reason = False, "the import is estimated"
        ctx.trace.append(
            f"SAFETY: grid meter unknown for {lost:.0f} min → estimated import {_w(estimate)} "
            f"(miners {_w(energy.miner_consumption_sum_w)} + house {_w(ctx.base_load_w)} − PV "
            f"{_w(energy.pv_power_w)}); it can show a shortfall, never spare sun → nothing starts "
            "or steps up"
        )
    if energy.battery_soc_pct is not None and energy.battery_soc_pct < ctx.battery_floor:
        ctx.trace.append(
            f"SAFETY: battery {energy.battery_soc_pct:.0f}% below floor "
            f"{ctx.battery_floor:.0f}% → stop all miners"
        )
        ctx.plans.update({m.miner_id: ctx.stop(m, "battery low") for m in ctx.candidates})
        return ctx.done("Safety: battery below floor", "rule.battery-floor")
    return None
