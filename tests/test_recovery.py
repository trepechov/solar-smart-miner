"""Whether a miner's chips have recovered from its own restart (recovery.py)."""
from __future__ import annotations

from custom_components.solar_smart_miner.control import MinerController
from custom_components.solar_smart_miner.protocols import MinerSnapshot
from custom_components.solar_smart_miner.recovery import ChipRecovery


def _run(recovery: ChipRecovery, temps: list[float | None], start: float = 0.0, step: float = 60.0) -> float:
    """Feed one reading per minute from `start`; returns the time after the last one."""
    t = start
    for temp in temps:
        recovery.observe("a", temp, t)
        t += step
    return t


def test_a_window_across_the_restart_isnt_flat() -> None:
    # Reference farm, 2026-10-15, Brod3 2,300 -> 2,500 W: 61 °C before, 41 at the restart, 61 again
    # five minutes later. Measured from the low point, the chips are still rising.
    recovery = ChipRecovery()
    _run(recovery, [61.0])
    recovery.note_change("a", 60.0)
    _run(recovery, [61.0, 52.0, 41.0, 46.0, 54.0, 61.0], start=60.0)

    assert recovery.recovering() == {"a": 61.0}


def test_recovered_once_the_rise_stays_under_a_degree_for_five_minutes_after_the_low() -> None:
    recovery = ChipRecovery()
    _run(recovery, [61.0])
    recovery.note_change("a", 60.0)
    t = _run(recovery, [52.0, 41.0, 46.0, 54.0, 61.0, 63.0, 64.0, 64.0, 64.0, 64.0], start=60.0)
    assert recovery.recovering() == {"a": 61.0}  # 63 -> 64 °C at +7 to +10 min: not 5 minutes flat

    _run(recovery, [64.0], start=t)  # +11 min: 64 °C five minutes after 63... still a rise of 1 °C
    assert "a" in recovery.recovering()
    _run(recovery, [64.0, 64.0], start=t + 60.0)

    assert recovery.recovering() == {}


def test_a_change_while_recovering_keeps_the_reading_from_before_the_first() -> None:
    # 2026-10-11: stepped down at 74 °C; a second change during the warm-up must not take the
    # restart-cooled reading as the miner's temperature.
    recovery = ChipRecovery()
    _run(recovery, [74.0])
    recovery.note_change("a", 60.0)
    t = _run(recovery, [50.0, 45.0], start=60.0)
    recovery.note_change("a", t)
    _run(recovery, [44.0, 50.0], start=t)

    assert recovery.recovering() == {"a": 74.0}

    # A warmer reading during the warm-up (still settling up at the new step) replaces it.
    warmer = ChipRecovery()
    _run(warmer, [69.0])
    warmer.note_change("a", 60.0)
    t = _run(warmer, [55.0, 70.0, 74.0], start=60.0)
    warmer.note_change("a", t)

    assert warmer.recovering() == {"a": 74.0}


def test_no_change_seen_means_nothing_recovers_and_unknown_before_is_none() -> None:
    recovery = ChipRecovery()
    _run(recovery, [70.0, 70.0])
    assert recovery.recovering() == {}  # HA restarted: no change is known, the reading counts

    recovery.note_change("b", 0.0)  # a miner never read before its change
    assert recovery.recovering() == {"b": None}


def test_missing_readings_are_skipped() -> None:
    recovery = ChipRecovery()
    _run(recovery, [60.0])
    recovery.note_change("a", 60.0)
    _run(recovery, [None, 45.0, None, 58.0], start=60.0)

    assert recovery.recovering() == {"a": 60.0}


def test_every_change_through_the_controller_starts_a_recovery(hass) -> None:
    controller = MinerController(hass, get_mode=lambda: "automatic")
    miner = MinerSnapshot(
        miner_id="a", ip="a", name="M-a", power_w=2500.0, power_limit_w=2300.0, min_power_w=500.0,
        max_power_w=3500.0, temperature_c=72.0, is_available=True, power_limit_entity_id="number.a",
    )
    controller.recovery.observe("a", 72.0, 0.0)

    controller.note_change(miner, stopping=False, restarting=True)

    assert controller.recovery.recovering() == {"a": 72.0}
