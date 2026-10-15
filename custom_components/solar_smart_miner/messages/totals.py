"""The day's totals: one definition of the figures the day report gives (plan 2026-10-15-002 U5).

Added up per poll from the readings the decision is given: the solar (the actual PV reading,
else the solar production reading), the grid balance, each miner's draw and hashrate. The
energy of the time since the last poll is the reading now times that time.

- A PV or grid reading missing for up to READING_HOLD_S holds its last value: on the reference
  farm the inverters' readings drop out 143 times a day (2026-10-12), mostly for about a minute,
  the longest for 577 s, and counting those as 0 W made the day's solar 30% short (0.8.4).
  Missing for longer, it adds nothing and the day is incomplete.
- A gap between polls longer than MAX_GAP_S (HA not running) adds nothing and makes the day
  incomplete; so does a first poll that long after midnight or a last poll that long before.
- A miner adds its draw only while readable (a paused miner's reading goes unknown: 0 W).
- The house and other loads are a remainder: solar + import - export - miners.

The totals live in the day's state (DayMessages), so they survive a restart of HA.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from ..control import is_mining
from ..protocols import EnergySnapshot, MinerSnapshot
from .context import actual_pv_w, miner_name

READING_HOLD_S = 600  # a reading missing this long still holds its last value (2026-10-12: gaps up to 577 s)
MAX_GAP_S = 300  # a gap between polls longer than this isn't filled: HA wasn't running
LIGHT_W = 50  # solar above this counts as production (first and last light)
SLICE_MIN = 15  # the best solar slice of the day


def new_totals() -> dict:
    return {
        "solar_kwh": 0.0,
        "import_kwh": 0.0,
        "export_kwh": 0.0,
        "miners_kwh": 0.0,
        "miners_solar_kwh": 0.0,  # the miners' energy the solar covered
        "miners_grid_h": [0.0] * 24,  # the miners' grid energy, per hour of the day
        "miners": {},  # name -> {"kwh", "mining_s", "th"} (th: TH/s x s)
        "first_light": None,  # ISO times
        "last_light": None,
        "slice": None,  # index of the current slice of the day, and its solar so far
        "slice_kwh": 0.0,
        "best": None,  # {"slice", "kwh"}
        "last_poll": None,  # ISO time
        "held": {},  # reading -> [last value, ISO time it was read]
        "solar_known": False,  # a solar reading was seen today
        "incomplete": False,
    }


def _held(totals: dict, key: str, value: float | None, now: datetime) -> float | None:
    if value is not None:
        totals["held"][key] = [value, now.isoformat(timespec="seconds")]
        return value
    last = totals["held"].get(key)
    if last is None:
        return None  # not configured, or not read yet today
    if (now - datetime.fromisoformat(last[1])).total_seconds() <= READING_HOLD_S:
        return last[0]
    totals["incomplete"] = True
    return None


def _midnight(now: datetime) -> datetime:
    return now.replace(hour=0, minute=0, second=0, microsecond=0)


def add_poll(totals: dict, now: datetime, energy: EnergySnapshot, miners: list[MinerSnapshot]) -> None:
    """One poll: the energy since the last poll, at the readings now."""
    stamp = now.isoformat(timespec="seconds")
    pv = _held(totals, "pv", actual_pv_w(energy), now)
    grid = _held(totals, "grid", energy.grid_net_w, now)
    if pv is not None:
        totals["solar_known"] = True
        if pv > LIGHT_W:
            totals["first_light"] = totals["first_light"] or stamp
            totals["last_light"] = stamp
    last = totals["last_poll"]
    totals["last_poll"] = stamp
    if last is None:
        if (now - _midnight(now)).total_seconds() > MAX_GAP_S:
            totals["incomplete"] = True  # the day began without us
        return
    seconds = (now - datetime.fromisoformat(last)).total_seconds()
    if seconds <= 0:
        return
    if seconds > MAX_GAP_S:
        totals["incomplete"] = True
        return
    hours = seconds / 3600
    miners_w = 0.0
    for m in miners:
        w = m.power_w if m.is_available and m.power_w is not None else 0.0
        miners_w += w
        figures = totals["miners"].setdefault(miner_name(m), {"kwh": 0.0, "mining_s": 0.0, "th": 0.0})
        figures["kwh"] += w * hours / 1000
        if is_mining(m):
            figures["mining_s"] += seconds
            figures["th"] += (m.hashrate_th or 0.0) * seconds
    export_w = max(0.0, grid) if grid is not None else 0.0
    import_w = max(0.0, -grid) if grid is not None else 0.0
    solar_w = pv if pv is not None else 0.0
    miners_solar_w = min(miners_w, max(0.0, solar_w - export_w))
    totals["solar_kwh"] += solar_w * hours / 1000
    totals["import_kwh"] += import_w * hours / 1000
    totals["export_kwh"] += export_w * hours / 1000
    totals["miners_kwh"] += miners_w * hours / 1000
    totals["miners_solar_kwh"] += miners_solar_w * hours / 1000
    totals["miners_grid_h"][now.hour] += (miners_w - miners_solar_w) * hours / 1000
    index = (now.hour * 60 + now.minute) // SLICE_MIN
    if index != totals["slice"]:
        close_slice(totals)
        totals["slice"], totals["slice_kwh"] = index, 0.0
    totals["slice_kwh"] += solar_w * hours / 1000


def close_slice(totals: dict) -> None:
    """The current slice counts towards the best one."""
    if totals["slice"] is None:
        return
    best = totals["best"]
    if best is None or totals["slice_kwh"] > best["kwh"]:
        totals["best"] = {"slice": totals["slice"], "kwh": totals["slice_kwh"]}


def ended_early(totals: dict, date: str) -> bool:
    """The day's last poll came more than MAX_GAP_S before its end (midnight)."""
    last = totals["last_poll"]
    if last is None:
        return True
    end = datetime.fromisoformat(last)
    end = _midnight(end) + timedelta(days=1)
    return last[:10] != date or (end - datetime.fromisoformat(last)).total_seconds() > MAX_GAP_S
