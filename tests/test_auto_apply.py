"""Tests for Automatic mode: the coordinator applies the proposal at the end of each cycle."""
from __future__ import annotations

import time as time_module
from datetime import timedelta

import pytest
from homeassistant.exceptions import HomeAssistantError
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_mock_service

from custom_components.solar_smart_miner import coordinator as coordinator_module
from custom_components.solar_smart_miner.config_flow import (
    CONF_BATTERY_ENTITY,
    CONF_GRID_ENTITY,
    CONF_POWER_STEPS,
    CONF_SOLAR_ENTITY,
)
from custom_components.solar_smart_miner.const import (
    CONF_CONTROL_MODE,
    CONTROL_MODE_AUTO,
    CONTROL_MODE_MANUAL,
    DOMAIN,
)
from custom_components.solar_smart_miner.coordinator import SolarMinerCoordinator
from custom_components.solar_smart_miner.protocols import (
    ACTION_HOLD,
    ACTION_SET_LIMIT,
    ACTION_STOP,
    STOP_METHOD_PAUSE,
    Decision,
    MinerPlan,
)

SOLAR = "sensor.solar_power"
GRID = "sensor.grid_consumption"
LIMITS = {"min": 500.0, "max": 3500.0}
STEPS = [900, 1100, 1300, 1500]


@pytest.fixture
def clock(monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(time_module, "monotonic", lambda: now[0])
    return now


def _coordinator(hass, mode: str = CONTROL_MODE_AUTO) -> SolarMinerCoordinator:
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_SOLAR_ENTITY: SOLAR, CONF_GRID_ENTITY: GRID, CONF_BATTERY_ENTITY: None},
        options={CONF_CONTROL_MODE: mode, CONF_POWER_STEPS: STEPS},
    )
    entry.add_to_hass(hass)
    coordinator = SolarMinerCoordinator(hass, entry)
    coordinator.lines = []  # the action-log lines, as they would be written

    async def _write(line):
        coordinator.lines.append(line)

    coordinator.action_log.async_write = _write
    return coordinator


def _surplus_two_miners(hass, add_hass_miner) -> dict:
    """Two cool miners at the lowest step and plenty of sun: the rules raise one per proposal."""
    a = add_hass_miner("192.168.1.10", name="Brod1", limit="900", power="900", temperature="55",
                       limit_attrs=LIMITS)
    b = add_hass_miner("192.168.1.11", name="Brod2", limit="900", power="900", temperature="55",
                       limit_attrs=LIMITS)
    hass.states.async_set(SOLAR, "9000")
    hass.states.async_set(GRID, "1800")
    return {"Brod1": a, "Brod2": b}


def _fixed_decision(monkeypatch, plans_by_ip: dict[str, MinerPlan]) -> None:
    """Make the rules return these plans (every other miner holds)."""

    def _build(snapshot, **_kw):
        plans = {m.miner_id: plans_by_ip.get(m.miner_id, MinerPlan(ACTION_HOLD, limit_w=m.power_limit_w))
                 for m in snapshot.miners}
        return Decision(summary="fixed", plans=plans)

    monkeypatch.setattr(coordinator_module, "build_decision", _build)


async def test_automatic_applies_the_proposal_in_the_same_cycle(hass, add_hass_miner, clock) -> None:
    _surplus_two_miners(hass, add_hass_miner)
    calls = async_mock_service(hass, "number", "set_value")
    coordinator = _coordinator(hass)

    snapshot = await coordinator._async_update_data()

    (mid, limit_w), = snapshot.decision.proposals.items()
    assert [c.data["value"] for c in calls] == [limit_w]
    line = coordinator.lines[-1]
    assert line["trigger"] == "auto"
    assert line["result"] == "pending"
    assert line["miner_id"] == mid
    # Logged with the readings that produced the plan, not the previous cycle's (there is none).
    assert line["rule_summary"] == snapshot.decision.summary
    assert line["before"]["limit_w"] == 900


async def test_manual_mode_applies_nothing_on_its_own(hass, add_hass_miner, clock) -> None:
    _surplus_two_miners(hass, add_hass_miner)
    calls = async_mock_service(hass, "number", "set_value")
    coordinator = _coordinator(hass, CONTROL_MODE_MANUAL)

    snapshot = await coordinator._async_update_data()

    assert snapshot.decision.proposals  # there is something to apply
    assert not calls
    assert not coordinator.lines


async def test_the_ramp_lock_spaces_the_automatic_changes(hass, add_hass_miner, clock) -> None:
    miners = _surplus_two_miners(hass, add_hass_miner)
    calls = async_mock_service(hass, "number", "set_value")
    coordinator = _coordinator(hass)

    snapshot = await coordinator._async_update_data()
    (first_id, first_w), = snapshot.decision.proposals.items()
    assert len(calls) == 1

    # Next cycle, the command is still being checked: nothing more is sent.
    clock[0] += 15
    await coordinator._async_update_data()
    assert len(calls) == 1

    # The miner shows its new limit and restarts; the lock still runs from the change.
    first = next(r for r in miners.values() if r["power_limit"].entity_id == calls[0].data["entity_id"])
    hass.states.async_set(first["power_limit"].entity_id, str(first_w), LIMITS)
    hass.states.async_set(first["miner_consumption"].entity_id, "unknown")
    clock[0] += 60
    snapshot = await coordinator._async_update_data()
    assert len(calls) == 1
    assert any("ramp lock" in line for line in snapshot.decision.trace)

    # Mining again below its new limit (still ramping): the lock holds.
    hass.states.async_set(first["miner_consumption"].entity_id, str(first_w * 0.85))
    clock[0] += 60
    await coordinator._async_update_data()
    assert len(calls) == 1

    # Once the lock is over, the next proposal goes out.
    clock[0] += 3 * 60
    await coordinator._async_update_data()
    assert len(calls) == 2


async def test_reductions_go_first(hass, add_hass_miner, clock, monkeypatch) -> None:
    a = add_hass_miner("192.168.1.10", name="Brod1", limit="1100", power="1100", temperature="55",
                       limit_attrs=LIMITS)
    add_hass_miner("192.168.1.11", name="Brod2", limit="1100", power="1100", temperature="55",
                   limit_attrs=LIMITS)
    hass.states.async_set(SOLAR, "5000")
    hass.states.async_set(GRID, "2200")
    order: list[str] = []
    hass.services.async_register("number", "set_value", lambda call: order.append("up"))
    hass.services.async_register("switch", "turn_off", lambda call: order.append("stop"))
    _fixed_decision(monkeypatch, {
        "192.168.1.10": MinerPlan(ACTION_SET_LIMIT, limit_w=1300, reason="budget"),
        "192.168.1.11": MinerPlan(ACTION_STOP, reason="safety", method=STOP_METHOD_PAUSE,
                                  target_entity_id="switch.192_168_1_11_active"),
    })
    hass.states.async_set("switch.192_168_1_11_active", "on")
    assert a  # Brod1 is listed first, so the order below is the sort, not the listing
    coordinator = _coordinator(hass)

    await coordinator._async_update_data()
    await hass.async_block_till_done()

    assert order == ["stop", "up"]


async def test_a_hold_only_decision_sends_and_logs_nothing(hass, add_hass_miner, clock) -> None:
    add_hass_miner("192.168.1.10", name="Brod1", limit="1500", power="1500", temperature="55",
                   limit_attrs=LIMITS)
    hass.states.async_set(SOLAR, "1700")
    hass.states.async_set(GRID, "1500")
    calls = async_mock_service(hass, "number", "set_value")
    coordinator = _coordinator(hass)

    snapshot = await coordinator._async_update_data()

    assert all(p.action == ACTION_HOLD for p in snapshot.decision.plans.values())
    assert not calls
    assert not coordinator.lines


async def test_a_refused_plan_is_not_retried_every_cycle(hass, add_hass_miner, clock, monkeypatch) -> None:
    add_hass_miner("192.168.1.10", name="Brod1", limit="1100", power="1100", temperature="55",
                   limit_attrs=LIMITS)
    hass.states.async_set(SOLAR, "5000")
    hass.states.async_set(GRID, "1500")
    calls = async_mock_service(hass, "number", "set_value")
    _fixed_decision(monkeypatch, {"192.168.1.10": MinerPlan(ACTION_SET_LIMIT, limit_w=1234, reason="x")})
    coordinator = _coordinator(hass)

    for _ in range(3):
        await coordinator._async_update_data()
        clock[0] += 15

    assert not calls
    assert [line["result"] for line in coordinator.lines] == ["refused"]  # logged once
    assert "power steps" in coordinator.lines[0]["reason"]

    # A different plan is tried at once.
    _fixed_decision(monkeypatch, {"192.168.1.10": MinerPlan(ACTION_SET_LIMIT, limit_w=1300, reason="x")})
    await coordinator._async_update_data()
    assert [c.data["value"] for c in calls] == [1300]


async def test_a_failed_send_waits_out_the_ramp_lock(hass, add_hass_miner, clock) -> None:
    _surplus_two_miners(hass, add_hass_miner)
    sent: list = []

    def _boom(call):
        sent.append(call)
        raise HomeAssistantError("miner not answering")

    hass.services.async_register("number", "set_value", _boom)
    coordinator = _coordinator(hass)

    snapshot = await coordinator._async_update_data()  # the update itself still succeeds
    await hass.async_block_till_done()

    assert snapshot.decision is not None
    assert len(sent) == 1
    assert coordinator.lines[-1]["result"] == "failed"
    assert any(k.startswith("solar_smart_miner_apply_failed") for k in hass.data["persistent_notification"])

    clock[0] += 15
    await coordinator._async_update_data()
    assert len(sent) == 1  # no retry inside the ramp lock

    clock[0] += 4 * 60
    await coordinator._async_update_data()
    assert len(sent) == 2


@pytest.mark.parametrize(("minutes_ago", "locked"), [(1, True), (10, False)])
async def test_the_last_send_in_the_action_log_seeds_the_ramp_lock(hass, clock, minutes_ago, locked) -> None:
    coordinator = _coordinator(hass)
    sent = dt_util.now() - timedelta(minutes=minutes_ago)
    coordinator.action_log.last_sent_ts = sent.isoformat(timespec="seconds")

    coordinator.seed_activity()

    age = coordinator._minutes_since_change([])
    assert age == pytest.approx(minutes_ago, abs=0.1)
    assert (age < 4) is locked


async def test_no_send_in_the_action_log_leaves_no_ramp_lock(hass, clock) -> None:
    coordinator = _coordinator(hass)

    coordinator.seed_activity()

    assert coordinator._minutes_since_change([]) is None
