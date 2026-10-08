"""Closed-loop test: the coordinator in Automatic mode against a small model of a farm.

An open-loop replay can't show a loop: there every moment is decided once, on recorded
readings. Here each applied command changes the farm the next cycle reads, so rules that undo
each other show up as they did on the reference farm (2026-10-08: a stop/start loop every
10 minutes at sunset; a restart read as a stop). The model:

- grid import = house + miner draw − PV, with PV = min(what the panels could give, the load)
  (zero export: the inverters throttle to the load);
- a limit change restarts the miner as measured on 2026-10-08: the number shows the new value
  at once, after ~40 s its pause switch reads off, power and hashrate are gone for ~90 s, then
  it draws ~85% of the new limit for a minute and its limit after that; a start boots the
  same way without the switch going off; a stop is immediate.

Checks (the collision checks of scripts/replay.py): no change reversed within 15 minutes and
nothing started or stepped up during the sunset, no two changes within a minute, and no start
of a miner that is only restarting.
"""
from __future__ import annotations

import json
import time as time_module
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path

import pytest
from homeassistant.core import ServiceCall
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.solar_smart_miner.config_flow import (
    CONF_BATTERY_ENTITY,
    CONF_GRID_ENTITY,
    CONF_PV_ENTITY,
    CONF_SOLAR_ENTITY,
    CONF_SOLAR_ENTITY_TYPE,
)
from custom_components.solar_smart_miner.const import (
    CONF_CONTROL_MODE,
    CONTROL_MODE_AUTO,
    DOMAIN,
    SOLAR_ENTITY_TYPE_NET_EXPORT,
)
from custom_components.solar_smart_miner.coordinator import SolarMinerCoordinator

METER = "sensor.grid_meter"  # + export / − import, the reference farm's "solar entity"
PV = "sensor.pv_total"
LIMITS = {"min": 500.0, "max": 3500.0}
TICK_S = 15
HOUSE_W = 500.0  # the reference farm's base load
SUNSET_PV = json.loads((Path(__file__).parent / "replay" / "2026-10-08-sunset-pv.json").read_text())

RESTART_SWITCH_OFF_S = 40
RESTART_DARK_S = 90  # no power, no hashrate
RESTART_LOW_S = 150  # ~85% of the new limit until then


@dataclass
class SimMiner:
    reg: dict
    limit: float
    stopped: bool = False
    changed_at: float | None = None  # a restart (limit change) or a boot (start) began
    booting: bool = False  # a start, not a limit change: the switch stays on

    def set_limit(self, watts: float, now: float) -> None:
        self.limit = watts
        if not self.stopped and (self.changed_at is None or not self.booting):
            self.changed_at, self.booting = now, False

    def stop(self) -> None:
        self.stopped, self.changed_at = True, None

    def start(self, now: float) -> None:
        if self.stopped:
            self.stopped, self.changed_at, self.booting = False, now, True

    def reading(self, now: float) -> tuple[float | None, str, float]:
        """(power, switch, hashrate) as hass-miner would show them."""
        if self.stopped:
            return None, "off", 0.0
        age = None if self.changed_at is None else now - self.changed_at
        if age is not None and age < RESTART_DARK_S:
            switch = "off" if not self.booting and age >= RESTART_SWITCH_OFF_S else "on"
            return None, switch, 0.0
        if age is not None and age < RESTART_LOW_S:
            return 0.85 * self.limit, "on", self.limit / 25
        self.changed_at = None
        return self.limit, "on", self.limit / 22

    def draw(self, now: float) -> float:
        return self.reading(now)[0] or 0.0


@dataclass
class Farm:
    hass: object
    miners: list[SimMiner]
    pv_potential: object  # callable: seconds since the start -> W
    sets_after_s: float | None = None  # when the sun sets, in seconds from the start
    now: float = 0.0
    commands: list[tuple[float, str, str, float | None]] = field(default_factory=list)

    def by_entity(self, entity_id: str) -> SimMiner:
        return next(
            m for m in self.miners
            if entity_id in (m.reg["power_limit"].entity_id, m.reg["active"].entity_id)
        )

    def publish(self) -> None:
        if self.sets_after_s is not None:
            up = self.now < self.sets_after_s
            setting = dt_util.now() + timedelta(seconds=self.sets_after_s - self.now)
            self.hass.states.async_set(
                "sun.sun", "above_horizon" if up else "below_horizon",
                {"rising": False, "next_setting": setting.isoformat()},
            )
        load = HOUSE_W + sum(m.draw(self.now) for m in self.miners)
        pv = min(self.pv_potential(self.now), load)
        self.hass.states.async_set(METER, f"{pv - load:.0f}", {"unit_of_measurement": "W"})
        self.hass.states.async_set(PV, f"{pv:.0f}", {"unit_of_measurement": "W"})
        for m in self.miners:
            power, switch, hashrate = m.reading(self.now)
            self.hass.states.async_set(
                m.reg["miner_consumption"].entity_id, "unknown" if power is None else f"{power:.0f}"
            )
            self.hass.states.async_set(m.reg["active"].entity_id, switch)
            self.hass.states.async_set(m.reg["hashrate"].entity_id, f"{hashrate:.1f}")
            self.hass.states.async_set(m.reg["power_limit"].entity_id, f"{m.limit:.0f}", LIMITS)


def _kind(farm: Farm, miner: SimMiner, service: str, value: float | None) -> str:
    if service == "turn_off":
        return "stop"
    if service == "turn_on":
        return "start"
    return "up" if value > miner.limit else "down"


async def _run(
    hass, add_hass_miner, monkeypatch, limits, stopped, pv_potential, minutes: int,
    sets_after_min: float | None = None,
) -> Farm:
    clock = [0.0]
    monkeypatch.setattr(time_module, "monotonic", lambda: clock[0] + 10_000.0)
    sims = []
    for n, (limit, off) in enumerate(zip(limits, stopped), start=1):
        reg = add_hass_miner(f"192.0.2.{n}", name=f"Miner {n}", limit=f"{limit:.0f}", power="0",
                             temperature="50", hashrate="0", active="off" if off else "on",
                             limit_attrs=LIMITS)
        sims.append(SimMiner(reg, limit, stopped=off))
    farm = Farm(hass, sims, pv_potential, None if sets_after_min is None else sets_after_min * 60)

    def _handler(service: str):
        def handle(call: ServiceCall) -> None:
            entity_id = call.data["entity_id"]
            miner = farm.by_entity(entity_id)
            value = call.data.get("value")
            farm.commands.append((farm.now, miner.reg["entry"].title, _kind(farm, miner, service, value), value))
            if service == "set_value":
                miner.set_limit(float(value), farm.now)
            elif service == "turn_off":
                miner.stop()
            else:
                miner.start(farm.now)
            farm.publish()  # hass-miner shows the new value at once
        return handle

    hass.services.async_register("number", "set_value", _handler("set_value"))
    hass.services.async_register("switch", "turn_off", _handler("turn_off"))
    hass.services.async_register("switch", "turn_on", _handler("turn_on"))
    hass.states.async_set("sun.sun", "above_horizon", {"elevation": 10.0, "rising": False})
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_SOLAR_ENTITY: METER, CONF_SOLAR_ENTITY_TYPE: SOLAR_ENTITY_TYPE_NET_EXPORT,
              CONF_GRID_ENTITY: None, CONF_BATTERY_ENTITY: None, CONF_PV_ENTITY: PV},
        options={CONF_CONTROL_MODE: CONTROL_MODE_AUTO},
    )
    entry.add_to_hass(hass)
    coordinator = SolarMinerCoordinator(hass, entry)
    for _ in range(minutes * 60 // TICK_S):
        clock[0] = farm.now
        farm.publish()
        await coordinator._async_update_data()
        farm.now += TICK_S
    return farm


def _reversals(farm: Farm) -> list[str]:
    opposite = {"up": "down", "down": "up", "stop": "start", "start": "stop"}
    found = []
    for i, (t, miner, kind, _) in enumerate(farm.commands):
        for t2, miner2, kind2, _ in farm.commands[i + 1 :]:
            if t2 - t > 15 * 60:
                break
            if miner2 == miner and opposite[kind] == kind2:
                found.append(f"{miner} {kind} at {t / 60:.1f} min, {kind2} at {t2 / 60:.1f} min")
                break
    return found


def _close_pairs(farm: Farm) -> list[tuple]:
    return [(a, b) for a, b in zip(farm.commands, farm.commands[1:]) if b[0] - a[0] < 60]


SUNSET_AT_MIN = 137  # 2026-10-08: the sun set at about 18:47, 137 minutes after 16:30


def _sunset_potential(seconds: float) -> float:
    """The reference farm's PV from 16:30 on 2026-10-08, one reading a minute."""
    minute = int(seconds // 60)
    curve = SUNSET_PV["pv_w"]
    return float(curve[min(minute, len(curve) - 1)][1] or 0.0)


async def test_the_sunset_of_2026_10_08_runs_down_without_a_stop_start_loop(
    hass, add_hass_miner, monkeypatch
) -> None:
    # Overlap 8: during sunset nothing starts or steps up (owner, 2026-10-09). Sunset can only
    # begin in the last two hours before the sun sets (16:47 here); before that, midday rules.
    farm = await _run(hass, add_hass_miner, monkeypatch, [900, 900, 900], [False] * 3,
                      _sunset_potential, minutes=105, sets_after_min=SUNSET_AT_MIN)
    window_opens = (SUNSET_AT_MIN - 120) * 60
    evening = [c for c in farm.commands if c[0] >= window_opens]

    stops = [t for t, _, kind, _ in evening if kind == "stop"]
    assert len(stops) == 3, evening  # every miner stopped, one at a time
    assert [c for c in evening if c[2] in ("start", "up")] == []
    assert _reversals(Farm(hass, [], None, commands=evening)) == []


async def test_a_sunny_farm_ramps_up_one_restart_at_a_time(hass, add_hass_miner, monkeypatch) -> None:
    # 2026-10-08 13:09 and 15:22: a miner restarting after a limit change showed its switch off
    # and was sent "start at 900 W". With surplus sun every miner steps up, one at a time.
    farm = await _run(hass, add_hass_miner, monkeypatch, [1300, 1100, 900], [False] * 3,
                      lambda s: 7000.0, minutes=60)

    assert len(farm.commands) >= 4
    assert all(kind == "up" for _, _, kind, _ in farm.commands), farm.commands
    assert _close_pairs(farm) == []
    assert _reversals(farm) == []
