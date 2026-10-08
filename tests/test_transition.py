"""Tests for sunrise and sunset as periods of changing production (transition.py)."""
from __future__ import annotations

from custom_components.solar_smart_miner.const import SUNSET_GATE_MIN, TRANSITION_CHANGE_W
from custom_components.solar_smart_miner.transition import Transition

MORNING = dict(sun_up=True, sun_rising=True, minutes_to_setting=600.0)
AFTERNOON = dict(sun_up=True, sun_rising=False, minutes_to_setting=300.0)
EVENING = dict(sun_up=True, sun_rising=False, minutes_to_setting=SUNSET_GATE_MIN - 30.0)


def _feed(t: Transition, watts: list[float], start_min: float = 0.0, **sun) -> float:
    """One reading a minute; returns the time after the last one."""
    now = start_min * 60
    for w in watts:
        t.update(now, w, **sun)
        now += 60
    return now / 60


def test_production_rising_in_the_morning_is_sunrise() -> None:
    t = Transition()
    _feed(t, [1000 + 30 * i for i in range(15)], **MORNING)  # +420 W in 14 minutes

    assert t.sunrise and t.name == "sunrise"
    assert t.change_w() > TRANSITION_CHANGE_W


def test_sunrise_ends_when_production_stops_rising() -> None:
    t = Transition()
    end = _feed(t, [1000 + 30 * i for i in range(15)], **MORNING)
    _feed(t, [1420] * 15, start_min=end, **MORNING)

    assert not t.sunrise and t.name is None


def test_a_rise_after_noon_is_not_sunrise() -> None:
    t = Transition()
    _feed(t, [1000 + 30 * i for i in range(15)], **AFTERNOON)

    assert not t.sunrise


def test_nothing_is_said_before_the_window_covers_ten_minutes() -> None:
    t = Transition()
    _feed(t, [1000 + 100 * i for i in range(9)], **MORNING)

    assert t.change_w() is None and not t.sunrise


def test_production_falling_near_sunset_latches_until_the_sun_rises_again() -> None:
    t = Transition()
    end = _feed(t, [3000 - 40 * i for i in range(15)], **EVENING)
    assert t.sunset and t.name == "sunset"

    # The stop it causes, a cloud clearing, a steady reading: still sunset (2026-10-08 loop).
    end = _feed(t, [2000 + 50 * i for i in range(15)], start_min=end, **EVENING)
    assert t.sunset

    t.update(end * 60 + 3600, None, sun_up=False, sun_rising=True, minutes_to_setting=900.0)
    assert not t.sunset  # past solar midnight: a new day


def test_a_fall_earlier_in_the_afternoon_is_not_sunset() -> None:
    # A 14:00 house load or a long cloud must not block starts for the rest of a sunny afternoon.
    t = Transition()
    _feed(t, [3000 - 40 * i for i in range(15)], **AFTERNOON)

    assert not t.sunset


def test_missing_readings_are_skipped_not_counted() -> None:
    # Production is read only outside ramp locks and while there is some import.
    t = Transition()
    now = 0.0
    for i in range(20):
        t.update(now, None if i % 2 else 3000 - 40 * i, **EVENING)
        now += 60

    assert t.sunset
