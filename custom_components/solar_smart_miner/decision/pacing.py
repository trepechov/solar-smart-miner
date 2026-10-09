"""Group 2, Pacing: after a change every miner holds while it settles (the ramp lock).

A change restarts the miner; it has settled once it is seen to have restarted and draws its new
limit (control.Settling), at most the ramp lock after the change. While any miner settles,
every miner holds: its readings are misleading, and its temperature fell with the restart. This
is the only "wait after a change" (the separate tuning window went in 0.8.0).
"""
from __future__ import annotations

from ..protocols import Decision
from .context import Context


def check(ctx: Context) -> Decision | None:
    # Ramp lock: a miner that just changed is restarting and the readings are misleading.
    since = ctx.minutes_since_change
    if since is not None and since < ctx.ramp_lock_minutes:
        left = ctx.ramp_lock_minutes - since
        ctx.trace.append(
            f"A miner changed {since:.0f} min ago and is restarting "
            f"→ every miner holds for ~{left:.0f} min more (ramp lock)"
        )
        ctx.others_wait(None, "ramp lock")
        return ctx.done("Waiting for a miner to restart")
    if ctx.ramp_done:
        ctx.trace.append(
            f"{', '.join(ctx.ramp_done)} already at the new power (hashrate may still be settling) "
            "→ no need to wait out the ramp lock"
        )
    return None
