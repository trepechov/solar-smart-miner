"""Group 2, Pacing: after a change every miner holds while it restarts (the ramp lock)."""
from __future__ import annotations

from ..protocols import Decision, MinerSnapshot
from .context import Context


def tuning_left(m: MinerSnapshot, settle_minutes: float) -> float | None:
    """Minutes the miner is still assumed to be tuning, or None if it has settled."""
    since = m.minutes_since_limit_change
    if since is None or since >= settle_minutes:
        return None
    return settle_minutes - since


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
