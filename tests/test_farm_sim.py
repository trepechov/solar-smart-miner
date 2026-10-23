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
  same way without the switch going off; a stop is immediate;
- with `chips`, the hottest chip as measured on 2026-10-15: settled at 40 °C at 900 W plus 3 °C
  per 200 W (plus a slow `drift` per minute, the room warming), a restart drops it to 40 °C and
  it is back within 2 °C about 5 minutes later.

Checks (the collision checks of scripts/replay.py): no change reversed within 15 minutes and
nothing started or stepped up during the sunset, no two changes within a minute, and no start
of a miner that is only restarting.
"""
from __future__ import annotations

import json
import math
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
    CONTROL_MODE_MANUAL,
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
CHIP_COLD_C = 40.0  # chips at the restart's low point, and settled at 900 W
CHIP_C_PER_W = 3.0 / 200  # settled rise per watt (2026-10-15: about 3 °C per 200 W)
CHIP_WARM_TAU_S = 90  # warm-up time constant after the restart: within 2 °C after ~5 minutes


@dataclass
class SimMiner:
    reg: dict
    limit: float
    stopped: bool = False
    changed_at: float | None = None  # a restart (limit change) or a boot (start) began
    booting: bool = False  # a start, not a limit change: the switch stays on
    drift: float = 0.0  # °C per minute the settled chip temperature rises (the room warming)
    restarted_at: float | None = None  # the last restart or boot, for the chips
    dark_every_s: float | None = None  # faulty: dark (restarting) every other such period

    def chip(self, now: float) -> float:
        settled = CHIP_COLD_C + (self.limit - 900.0) * CHIP_C_PER_W + self.drift * now / 60
        if self.stopped:
            return CHIP_COLD_C
        if self.restarted_at is None:
            return settled
        warm_s = now - self.restarted_at - RESTART_DARK_S
        if warm_s < 0:
            return CHIP_COLD_C
        return settled - (settled - CHIP_COLD_C) * math.exp(-warm_s / CHIP_WARM_TAU_S)

    def set_limit(self, watts: float, now: float) -> None:
        self.limit = watts
        if not self.stopped and (self.changed_at is None or not self.booting):
            self.changed_at, self.booting = now, False
            self.restarted_at = now

    def stop(self) -> None:
        self.stopped, self.changed_at = True, None

    def start(self, now: float) -> None:
        if self.stopped:
            self.stopped, self.changed_at, self.booting = False, now, True
            self.restarted_at = now

    def reading(self, now: float) -> tuple[float | None, str, float]:
        """(power, switch, hashrate) as hass-miner would show them."""
        if self.stopped:
            return None, "off", 0.0
        if self.dark_every_s and int(now // self.dark_every_s) % 2:
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
    rising: bool = False  # the morning: sun.sun says rising
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
                {"rising": self.rising, "next_setting": setting.isoformat()},
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
            if "0-chip_temperature" in m.reg:
                self.hass.states.async_set(m.reg["0-chip_temperature"].entity_id, f"{m.chip(self.now):.0f}")


def _kind(farm: Farm, miner: SimMiner, service: str, value: float | None) -> str:
    if service == "turn_off":
        return "stop"
    if service == "turn_on":
        return "start"
    return "up" if value > miner.limit else "down"


async def _run(
    hass, add_hass_miner, monkeypatch, limits, stopped, pv_potential, minutes: int,
    sets_after_min: float | None = None, rising: bool = False, options: dict | None = None,
    freezer=None, chips: bool = False, drift: float = 0.0, dark_every_s: dict | None = None,
) -> Farm:
    clock = [0.0]
    monkeypatch.setattr(time_module, "monotonic", lambda: clock[0] + 10_000.0)
    sims = []
    for n, (limit, off) in enumerate(zip(limits, stopped), start=1):
        reg = add_hass_miner(f"192.0.2.{n}", name=f"Miner {n}", limit=f"{limit:.0f}", power="0",
                             temperature="50", hashrate="0", active="off" if off else "on",
                             limit_attrs=LIMITS, chips=("50",) if chips else ())
        sims.append(SimMiner(reg, limit, stopped=off, drift=drift,
                             dark_every_s=(dark_every_s or {}).get(n)))
    farm = Farm(hass, sims, pv_potential, None if sets_after_min is None else sets_after_min * 60,
                rising=rising)

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
    hass.states.async_set("sun.sun", "above_horizon", {"elevation": 10.0, "rising": rising})
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_SOLAR_ENTITY: METER, CONF_SOLAR_ENTITY_TYPE: SOLAR_ENTITY_TYPE_NET_EXPORT,
              CONF_GRID_ENTITY: None, CONF_BATTERY_ENTITY: None, CONF_PV_ENTITY: PV},
        options={CONF_CONTROL_MODE: CONTROL_MODE_AUTO, **(options or {})},
    )
    entry.add_to_hass(hass)
    coordinator = SolarMinerCoordinator(hass, entry)
    for _ in range(minutes * 60 // TICK_S):
        clock[0] = farm.now
        farm.publish()
        await coordinator._async_update_data()
        farm.now += TICK_S
        if freezer is not None:  # the wall clock too (the day report's times and energy)
            freezer.tick(TICK_S)
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


@pytest.mark.parametrize("dark_every_s", [None, 300], ids=["steady", "restarting"])
async def test_an_unmanaged_miner_is_left_alone_and_holds_nothing_up(
    hass, add_hass_miner, monkeypatch, dark_every_s
) -> None:
    # Miner 3 left out (Configure -> Managed miners) at 2,500 W: house load to the others. Steady,
    # or faulty and restarting every 5 minutes on its own: its swings start no ramp lock, so
    # miners 1 and 2 keep stepping up, one restart at a time, and nothing is sent to miner 3.
    farm = await _run(hass, add_hass_miner, monkeypatch, [1300, 1100, 2500], [False] * 3,
                      lambda s: 6000.0, minutes=60, options={"unmanaged_miners": ["192.0.2.3"]},
                      dark_every_s=None if dark_every_s is None else {3: dark_every_s})

    assert {miner for _, miner, _, _ in farm.commands} == {"Miner 1", "Miner 2"}
    assert all(kind == "up" for _, _, kind, _ in farm.commands), farm.commands
    assert _close_pairs(farm) == []
    assert _reversals(farm) == []


async def test_a_sunrise_climbs_in_bigger_steps_with_fewer_restarts(hass, add_hass_miner, monkeypatch) -> None:
    # Owner, 2026-10-10: on 2026-10-09 and 10 each miner walked 900 to 2,500 W one step at a time
    # (about 27 commands in 3.5 hours). The sun rises 50 W a minute from 1.5 kW, past what three draw.
    def morning(steps: int):
        return _run(hass, add_hass_miner, monkeypatch, [900, 900, 900], [True] * 3,
                    lambda s: min(9000.0, 1500.0 + s / 60 * 50), minutes=180, rising=True,
                    options={"transition_steps": steps})

    farm = await morning(2)
    ups = [c for c in farm.commands if c[2] == "up"]
    assert [m.limit for m in farm.miners] == [2500.0] * 3 and not any(m.stopped for m in farm.miners)
    assert len(ups) <= 3 * 5, farm.commands  # 900 → 1,300 → 1,700 → 2,100 → 2,500, plus at most one
    assert _close_pairs(farm) == []
    assert [c for c in farm.commands if c[2] in ("down", "stop")] == [], farm.commands


async def test_a_sunrise_sends_the_morning_and_peak_messages_and_changes_nothing(
    hass, add_hass_miner, monkeypatch, mock_telegram, freezer
) -> None:
    """The day messages only observe: the same sunrise sends the same commands with them on or off."""
    freezer.move_to("2026-10-12T07:00:00+03:00")

    def sunrise(day_messages: bool):
        return _run(hass, add_hass_miner, monkeypatch, [900, 900, 900], [True] * 3,
                    lambda s: min(9000.0, 1500.0 + s / 60 * 50), minutes=180, rising=True,
                    freezer=freezer, options={
                        "telegram_bot_token": "1:T", "telegram_chat_id": "42",
                        "telegram_day_messages": day_messages,
                    })

    farm = await sunrise(True)
    await hass.async_block_till_done(wait_background_tasks=True)
    texts = [c.args[3] for c in mock_telegram.await_args_list]

    assert [t[0] for t in texts] == ["⛏", "⚡"], texts
    assert texts[0].startswith("⛏️ First miner started at "), texts[0]  # HA's time zone
    assert "(Miner 1, 900 W)." in texts[0].splitlines()[0]
    assert texts[1].startswith("⚡ All 3 miners run at their top step, settled")

    mock_telegram.reset_mock()
    for entry in hass.config_entries.async_entries("miner"):
        await hass.config_entries.async_remove(entry.entry_id)
    freezer.move_to("2026-10-12T07:00:00+03:00")
    quiet = await sunrise(False)
    await hass.async_block_till_done(wait_background_tasks=True)

    mock_telegram.assert_not_awaited()
    assert [c[1:] for c in quiet.commands] == [c[1:] for c in farm.commands]


async def test_a_sunrise_in_manual_mode_sends_one_reminder_and_starts_nothing(
    hass, add_hass_miner, monkeypatch, mock_telegram, freezer
) -> None:
    """Plan 2026-10-15-003: in Manual mode the decision proposes a start; nothing applies it, so a
    reminder goes out once and no morning comes."""
    freezer.move_to("2026-10-12T07:00:00+03:00")
    farm = await _run(hass, add_hass_miner, monkeypatch, [900, 900, 900], [True] * 3,
                      lambda s: min(9000.0, 1500.0 + s / 60 * 50), minutes=60, rising=True,
                      freezer=freezer, options={
                          CONF_CONTROL_MODE: CONTROL_MODE_MANUAL,
                          "telegram_bot_token": "1:T", "telegram_chat_id": "42",
                      })
    await hass.async_block_till_done(wait_background_tasks=True)
    texts = [c.args[3] for c in mock_telegram.await_args_list]

    assert farm.commands == []
    assert len(texts) == 1 and texts[0].startswith("🖐 Manual mode: the sun could run a miner now"), texts
    assert "Proposed: start Miner " in texts[0] and texts[0].endswith("Mode: Manual")


async def test_a_warming_miner_steps_down_once_and_isnt_raised_on_its_restart(
    hass, add_hass_miner, monkeypatch
) -> None:
    # 2026-10-11: the room warmed a miner at the top step into the step-down; the restart cooled
    # its chips and the import raised it straight back, every 12 minutes for hours. Here the
    # chips warm 0.1 °C a minute: at 2,500 W the band steps it down; back at 2,300 W it waits for
    # its chips and reads inside the band, and when the room makes that step too warm as well
    # it steps down again. One restart per step, never raised back on cooled chips.
    farm = await _run(hass, add_hass_miner, monkeypatch, [2300], [False], lambda s: 7000.0,
                      minutes=110, chips=True, drift=0.1,
                      options={"temp_target": 62, "temp_tolerance": 10})

    kinds = [kind for _, _, kind, _ in farm.commands]
    assert kinds == ["up", "down", "down"], farm.commands
    down_at = [t for t, _, kind, _ in farm.commands if kind == "down"]
    assert down_at[1] - down_at[0] > 25 * 60  # the room, not the restart, brought the second
    assert _reversals(farm) == []


async def test_a_sunrise_with_warming_chips_still_reaches_the_top(hass, add_hass_miner, monkeypatch) -> None:
    # The chips recover from every restart before a miner near the target may step up again;
    # far below it they don't wait. The farm's band (67 / 77 °C) settles 2,500 W at about 64 °C.
    farm = await _run(hass, add_hass_miner, monkeypatch, [900, 900, 900], [True] * 3,
                      lambda s: min(9000.0, 1500.0 + s / 60 * 50), minutes=180, rising=True,
                      chips=True, options={"transition_steps": 2, "temp_target": 67, "temp_tolerance": 10})

    assert [m.limit for m in farm.miners] == [2500.0] * 3, farm.commands
    assert [c for c in farm.commands if c[2] in ("down", "stop")] == [], farm.commands
    assert _close_pairs(farm) == []
