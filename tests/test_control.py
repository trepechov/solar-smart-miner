"""Tests for the command executor: plan -> service calls, guards, verification."""
from __future__ import annotations

import pytest
from homeassistant.core import ServiceCall
from homeassistant.exceptions import HomeAssistantError
from pytest_homeassistant_custom_component.common import async_mock_service

from custom_components.solar_smart_miner import control
from custom_components.solar_smart_miner.const import (
    APPLY_VERIFY_GRACE_RELAY_START_S,
    APPLY_VERIFY_GRACE_S,
    CONTROL_MODE_MANUAL,
    CONTROL_MODE_PREVIEW,
    DEFAULT_POWER_STEPS,
)
from custom_components.solar_smart_miner.control import (
    RESULT_FAILED,
    RESULT_OK,
    RESULT_PENDING,
    RESULT_REFUSED,
    TRIGGER_AUTO,
    TRIGGER_MANUAL,
    CommandEvent,
    MinerController,
)
from custom_components.solar_smart_miner.protocols import (
    ACTION_HOLD,
    ACTION_SET_LIMIT,
    ACTION_START,
    ACTION_STOP,
    STOP_METHOD_PAUSE,
    STOP_METHOD_RELAY,
    MinerPlan,
    MinerSnapshot,
)

NUMBER = "number.brod1_power_limit"
SWITCH = "switch.brod1_active"
RELAY = "switch.brod1_relay"
STEPS = [float(x) for x in DEFAULT_POWER_STEPS]  # 900, 1100, 1300, 1500


def _miner(**kw) -> MinerSnapshot:
    base = dict(
        miner_id="192.168.1.10", ip="192.168.1.10", name="Brod1", power_w=1100.0,
        power_limit_w=1100.0, min_power_w=500.0, max_power_w=3500.0, temperature_c=60.0,
        is_available=True, power_limit_entity_id=NUMBER, switch_entity_id=SWITCH,
    )
    return MinerSnapshot(**{**base, **kw})


def _set_limit(limit_w: float = 1300.0) -> MinerPlan:
    return MinerPlan(ACTION_SET_LIMIT, limit_w=limit_w, reason="budget")


def _stop(method: str = STOP_METHOD_PAUSE, entity: str = SWITCH) -> MinerPlan:
    return MinerPlan(ACTION_STOP, reason="no power", method=method, target_entity_id=entity)


def _start(method: str = STOP_METHOD_PAUSE, entity: str = SWITCH, limit_w: float = 1100.0) -> MinerPlan:
    return MinerPlan(ACTION_START, limit_w=limit_w, reason="budget", method=method, target_entity_id=entity)


class Harness:
    """A controller with recorded events, tuning marks and fake number / switch services."""

    def __init__(self, hass, mode: str = CONTROL_MODE_MANUAL) -> None:
        self.hass = hass
        self.mode = mode
        self.events: list[CommandEvent] = []
        self.marked: list[tuple[str, float]] = []
        self.controller = MinerController(
            hass,
            get_mode=lambda: self.mode,
            on_limit_applied=lambda mid, w: self.marked.append((mid, w)),
            on_event=self._record,
        )
        self.number_calls = async_mock_service(hass, "number", "set_value")
        self.on_calls = async_mock_service(hass, "switch", "turn_on")
        self.off_calls = async_mock_service(hass, "switch", "turn_off")

    async def _record(self, event: CommandEvent) -> None:
        self.events.append(event)

    async def apply(self, plan: MinerPlan, miner: MinerSnapshot | None = None, trigger=TRIGGER_MANUAL):
        return await self.controller.async_apply(
            miner or _miner(), plan, trigger=trigger, steps=STEPS
        )

    @property
    def statuses(self) -> list[str]:
        return [e.status for e in self.events]


@pytest.fixture
def h(hass) -> Harness:
    hass.states.async_set(NUMBER, "1100")
    hass.states.async_set(SWITCH, "on")
    return Harness(hass)


@pytest.fixture
def clock(monkeypatch):
    """Controllable time.monotonic() for the controller."""
    now = [1000.0]
    monkeypatch.setattr(control.time, "monotonic", lambda: now[0])
    return now


# --- plan -> service call -------------------------------------------------


async def test_set_limit_calls_number_set_value(h) -> None:
    result = await h.apply(_set_limit(1300.0))

    assert result.status == RESULT_PENDING
    assert [(c.data["entity_id"], c.data["value"]) for c in h.number_calls] == [(NUMBER, 1300.0)]
    assert not h.on_calls and not h.off_calls


async def test_stop_with_pause_turns_the_active_switch_off(h) -> None:
    result = await h.apply(_stop())

    assert result.status == RESULT_PENDING
    assert [c.data["entity_id"] for c in h.off_calls] == [SWITCH]
    assert not h.number_calls


async def test_stop_with_relay_turns_the_relay_off(h) -> None:
    h.hass.states.async_set(RELAY, "on")

    await h.apply(_stop(STOP_METHOD_RELAY, RELAY))

    assert [c.data["entity_id"] for c in h.off_calls] == [RELAY]


async def test_start_turns_the_switch_on_and_leaves_the_limit_for_later(h) -> None:
    h.hass.states.async_set(SWITCH, "off")

    result = await h.apply(_start(), _miner(is_stopped=True))

    assert result.status == RESULT_PENDING
    assert [c.data["entity_id"] for c in h.on_calls] == [SWITCH]
    assert not h.number_calls  # the limit follows once the switch reads "on" (see below)


async def test_hold_is_refused_without_a_call(h) -> None:
    result = await h.apply(MinerPlan(ACTION_HOLD, reason="tuning"))

    assert result.status == RESULT_REFUSED
    assert "hold" in result.reason
    assert not h.number_calls and not h.on_calls and not h.off_calls


# --- guards ---------------------------------------------------------------


async def test_preview_mode_refuses_every_trigger(h) -> None:
    h.mode = CONTROL_MODE_PREVIEW

    result = await h.apply(_set_limit())

    assert result.status == RESULT_REFUSED
    assert "preview" in result.reason
    assert not h.number_calls


async def test_auto_trigger_is_not_allowed_yet(h) -> None:
    result = await h.apply(_set_limit(), trigger=TRIGGER_AUTO)

    assert result.status == RESULT_REFUSED
    assert not h.number_calls


@pytest.mark.parametrize("limit_w", [1000.0, 700.0, 4000.0])
async def test_limit_off_the_ladder_is_refused_not_clamped(h, limit_w) -> None:
    result = await h.apply(_set_limit(limit_w))

    assert result.status == RESULT_REFUSED
    assert "power steps" in result.reason
    assert not h.number_calls


async def test_limit_outside_the_miner_range_is_refused(h) -> None:
    # 1300 is a configured step but this miner's range ends below it.
    result = await h.apply(_set_limit(1300.0), _miner(max_power_w=1200.0))

    assert result.status == RESULT_REFUSED
    assert not h.number_calls


async def test_start_limit_off_the_ladder_is_refused(h) -> None:
    h.hass.states.async_set(SWITCH, "off")

    result = await h.apply(_start(limit_w=1234.0), _miner(is_stopped=True))

    assert result.status == RESULT_REFUSED
    assert not h.on_calls


@pytest.mark.parametrize("state", ["unavailable", None])
async def test_unavailable_number_is_refused(h, state) -> None:
    if state is None:
        h.hass.states.async_remove(NUMBER)
    else:
        h.hass.states.async_set(NUMBER, state)

    result = await h.apply(_set_limit())

    assert result.status == RESULT_REFUSED
    assert "power limit" in result.reason
    assert not h.number_calls


async def test_missing_number_entity_is_refused(h) -> None:
    result = await h.apply(_set_limit(), _miner(power_limit_entity_id=None))

    assert result.status == RESULT_REFUSED
    assert not h.number_calls


async def test_unavailable_switch_is_refused_for_stop(h) -> None:
    h.hass.states.async_set(SWITCH, "unavailable")

    result = await h.apply(_stop())

    assert result.status == RESULT_REFUSED
    assert "switch" in result.reason
    assert not h.off_calls


async def test_start_without_a_limit_entity_is_refused(h) -> None:
    h.hass.states.async_set(SWITCH, "off")

    result = await h.apply(_start(), _miner(is_stopped=True, power_limit_entity_id=None))

    assert result.status == RESULT_REFUSED
    assert not h.on_calls


async def test_second_press_while_pending_is_refused(h) -> None:
    first = await h.apply(_set_limit(1300.0))
    second = await h.apply(_set_limit(1500.0))

    assert first.status == RESULT_PENDING
    assert second.status == RESULT_REFUSED
    assert "still being checked" in second.reason
    assert len(h.number_calls) == 1


async def test_two_presses_at_once_send_one_command(hass) -> None:
    """The lock is taken before the first await, so concurrent presses can't both pass."""
    import asyncio

    hass.states.async_set(NUMBER, "1100")
    harness = Harness(hass)

    results = await asyncio.gather(harness.apply(_set_limit(1300.0)), harness.apply(_set_limit(1300.0)))

    assert sorted(r.status for r in results) == [RESULT_PENDING, RESULT_REFUSED]
    assert len(harness.number_calls) == 1


async def test_other_miners_are_not_locked(h) -> None:
    h.hass.states.async_set("number.brod2_power_limit", "1100")
    await h.apply(_set_limit(1300.0))

    second = await h.apply(
        _set_limit(1500.0),
        _miner(miner_id="192.168.1.11", name="Brod2", power_limit_entity_id="number.brod2_power_limit"),
    )

    assert second.status == RESULT_PENDING


# --- failures -------------------------------------------------------------


async def test_service_error_gives_failed_and_a_notification(hass) -> None:
    hass.states.async_set(NUMBER, "1100")
    harness = Harness(hass)

    async def _boom(call: ServiceCall) -> None:
        raise HomeAssistantError("miner said no")

    hass.services.async_register("number", "set_value", _boom)

    result = await harness.apply(_set_limit(1300.0))
    await hass.async_block_till_done()

    assert result.status == RESULT_FAILED
    assert "miner said no" in result.reason
    assert not harness.controller.is_pending("192.168.1.10")  # lock released
    assert harness.statuses == [RESULT_FAILED]
    assert not harness.marked
    notes = hass.data["persistent_notification"]
    assert "solar_smart_miner_apply_failed_192_168_1_10" in notes


# --- the tuning clock -----------------------------------------------------


async def test_set_limit_restarts_the_tuning_clock(h) -> None:
    await h.apply(_set_limit(1300.0))

    assert h.marked == [("192.168.1.10", 1300.0)]


async def test_stop_does_not_touch_the_tuning_clock(h) -> None:
    await h.apply(_stop())

    assert h.marked == []


async def test_refused_command_does_not_touch_the_tuning_clock(h) -> None:
    await h.apply(_set_limit(1000.0))

    assert h.marked == []


# --- verification ---------------------------------------------------------


async def test_limit_reading_back_finishes_ok(h) -> None:
    await h.apply(_set_limit(1300.0))
    h.hass.states.async_set(NUMBER, "1300.0")

    await h.controller.async_check_pending()

    assert h.statuses == [RESULT_PENDING, RESULT_OK]
    assert h.events[0].command_id == h.events[1].command_id
    assert not h.controller.is_pending("192.168.1.10")


async def test_still_waiting_inside_the_grace_time(h, clock) -> None:
    await h.apply(_set_limit(1300.0))
    clock[0] += APPLY_VERIFY_GRACE_S - 1

    await h.controller.async_check_pending()

    assert h.statuses == [RESULT_PENDING]
    assert h.controller.is_pending("192.168.1.10")


async def test_timeout_gives_failed_and_one_replaced_notification(h, clock, hass) -> None:
    await h.apply(_set_limit(1300.0))
    clock[0] += APPLY_VERIFY_GRACE_S + 1

    await h.controller.async_check_pending()
    await hass.async_block_till_done()

    assert h.statuses == [RESULT_PENDING, RESULT_FAILED]
    assert not h.controller.is_pending("192.168.1.10")
    assert list(hass.data["persistent_notification"]) == ["solar_smart_miner_apply_failed_192_168_1_10"]

    # The lock is free again: a retry works, and its failure replaces the notification.
    await h.apply(_set_limit(1300.0))
    clock[0] += APPLY_VERIFY_GRACE_S + 1
    await h.controller.async_check_pending()
    await hass.async_block_till_done()
    assert len(hass.data["persistent_notification"]) == 1


async def test_stop_is_ok_when_the_switch_reads_off(h) -> None:
    await h.apply(_stop())
    h.hass.states.async_set(SWITCH, "off")

    await h.controller.async_check_pending()

    assert h.statuses == [RESULT_PENDING, RESULT_OK]


async def test_start_sets_the_limit_only_after_the_switch_is_on(h) -> None:
    h.hass.states.async_set(SWITCH, "off")
    h.hass.states.async_set(NUMBER, "unavailable")
    await h.apply(_start(limit_w=1300.0), _miner(is_stopped=True))

    await h.controller.async_check_pending()  # switch still off
    assert not h.number_calls

    h.hass.states.async_set(SWITCH, "on")
    await h.controller.async_check_pending()  # switch on, number still booting
    assert not h.number_calls
    assert h.statuses == [RESULT_PENDING]

    h.hass.states.async_set(NUMBER, "900")
    await h.controller.async_check_pending()  # number is back: set the limit now
    assert [c.data["value"] for c in h.number_calls] == [1300.0]
    assert h.statuses == [RESULT_PENDING]
    assert h.marked[-1] == ("192.168.1.10", 1300.0)

    h.hass.states.async_set(NUMBER, "1300")
    await h.controller.async_check_pending()
    assert h.statuses == [RESULT_PENDING, RESULT_OK]
    assert [c["service"] for c in h.events[-1].calls] == ["switch.turn_on", "number.set_value"]


async def test_start_skips_the_limit_when_the_miner_already_has_it(h) -> None:
    h.hass.states.async_set(SWITCH, "off")
    await h.apply(_start(limit_w=1100.0), _miner(is_stopped=True))
    h.hass.states.async_set(SWITCH, "on")  # the number still reads 1100

    await h.controller.async_check_pending()

    assert not h.number_calls
    assert h.statuses == [RESULT_PENDING, RESULT_OK]


async def test_relay_start_gets_the_longer_grace_time(h, clock) -> None:
    h.hass.states.async_set(RELAY, "off")
    await h.apply(_start(STOP_METHOD_RELAY, RELAY), _miner(is_stopped=True, relay_entity_id=RELAY))

    clock[0] += APPLY_VERIFY_GRACE_S + 1  # past a pause start's limit, inside a relay start's
    await h.controller.async_check_pending()
    assert h.statuses == [RESULT_PENDING]

    clock[0] += APPLY_VERIFY_GRACE_RELAY_START_S
    await h.controller.async_check_pending()
    assert h.statuses == [RESULT_PENDING, RESULT_FAILED]


async def test_start_where_setting_the_limit_fails_is_reported(hass) -> None:
    hass.states.async_set(NUMBER, "900")
    hass.states.async_set(SWITCH, "off")
    harness = Harness(hass)
    await harness.apply(_start(limit_w=1300.0), _miner(is_stopped=True))

    async def _boom(call: ServiceCall) -> None:
        raise HomeAssistantError("limit rejected")

    hass.services.async_register("number", "set_value", _boom)
    hass.states.async_set(SWITCH, "on")
    await harness.controller.async_check_pending()
    await hass.async_block_till_done()

    assert harness.statuses == [RESULT_PENDING, RESULT_FAILED]
    assert "limit rejected" in harness.events[-1].reason
    assert not harness.controller.is_pending("192.168.1.10")


async def test_refusals_are_logged_as_events(h) -> None:
    await h.apply(_set_limit(1000.0))

    assert h.statuses == [RESULT_REFUSED]
    assert h.events[0].plan.limit_w == 1000.0
    assert h.events[0].trigger == TRIGGER_MANUAL


async def test_start_fails_when_the_number_never_comes_back(h, clock, hass) -> None:
    h.hass.states.async_set(SWITCH, "off")
    h.hass.states.async_set(NUMBER, "unavailable")
    await h.apply(_start(limit_w=1300.0), _miner(is_stopped=True))
    h.hass.states.async_set(SWITCH, "on")  # on, but the miner's number stays unavailable

    await h.controller.async_check_pending()
    assert h.statuses == [RESULT_PENDING]

    clock[0] += APPLY_VERIFY_GRACE_S + 1
    await h.controller.async_check_pending()
    await hass.async_block_till_done()

    assert h.statuses == [RESULT_PENDING, RESULT_FAILED]
    assert "never came back" in h.events[-1].reason
    assert not h.controller.is_pending("192.168.1.10")
    assert not h.number_calls


async def test_failure_notification_describes_the_plan_in_words(h, clock, hass) -> None:
    await h.apply(_set_limit(1300.0))
    clock[0] += APPLY_VERIFY_GRACE_S + 1

    await h.controller.async_check_pending()
    await hass.async_block_till_done()

    message = hass.data["persistent_notification"]["solar_smart_miner_apply_failed_192_168_1_10"]["message"]
    assert message.startswith("Brod1: the command (1,300 W) didn't take")
    assert "|" not in message
