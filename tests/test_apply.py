"""Tests for applying the shown plan through the coordinator: refresh, compare, run."""
from __future__ import annotations

from unittest.mock import AsyncMock

from homeassistant.helpers.update_coordinator import UpdateFailed
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_mock_service

from custom_components.solar_smart_miner.config_flow import (
    CONF_BATTERY_ENTITY,
    CONF_GRID_ENTITY,
    CONF_POWER_STEPS,
    CONF_SOLAR_ENTITY,
)
from custom_components.solar_smart_miner.const import (
    CONF_CONTROL_MODE,
    CONTROL_MODE_MANUAL,
    CONTROL_MODE_PREVIEW,
    DOMAIN,
)
from custom_components.solar_smart_miner.coordinator import SolarMinerCoordinator
from custom_components.solar_smart_miner.protocols import MinerPlan

SOLAR = "sensor.solar_power"
GRID = "sensor.grid_consumption"
LIMITS = {"min": 500.0, "max": 3500.0}


def _coordinator(hass, mode: str = CONTROL_MODE_MANUAL) -> SolarMinerCoordinator:
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_SOLAR_ENTITY: SOLAR, CONF_GRID_ENTITY: GRID, CONF_BATTERY_ENTITY: None},
        # The short ladder these tests were written for; the default ladder is tested elsewhere.
        options={CONF_CONTROL_MODE: mode, CONF_POWER_STEPS: [900, 1100, 1300, 1500]},
    )
    entry.add_to_hass(hass)
    return SolarMinerCoordinator(hass, entry)


def _energy(hass, solar: str = "5000", house: str = "1500") -> None:
    hass.states.async_set(SOLAR, solar)
    hass.states.async_set(GRID, house)


async def _plan_for(coordinator, name: str = "Brod1") -> tuple[str, MinerPlan]:
    await coordinator.async_refresh()
    names = {m.miner_id: m.name for m in coordinator.data.miners}
    return next((mid, p) for mid, p in coordinator.data.decision.plans.items() if names[mid] == name)


async def test_unchanged_plan_is_applied(hass, add_hass_miner) -> None:
    reg = add_hass_miner("192.168.1.10", name="Brod1", limit="1100", temperature="55", limit_attrs=LIMITS)
    _energy(hass)
    calls = async_mock_service(hass, "number", "set_value")
    coordinator = _coordinator(hass)
    miner_id, plan = await _plan_for(coordinator)
    assert plan.action == "set_limit"

    result = await coordinator.async_apply_shown(miner_id, plan.fingerprint)

    assert result.status == "pending"
    assert [(c.data["entity_id"], c.data["value"]) for c in calls] == [
        (reg["power_limit"].entity_id, plan.limit_w)
    ]


async def test_changed_plan_is_not_applied_and_notifies(hass, add_hass_miner) -> None:
    add_hass_miner("192.168.1.10", name="Brod1", limit="1100", temperature="55", limit_attrs=LIMITS)
    _energy(hass)
    calls = async_mock_service(hass, "number", "set_value")
    coordinator = _coordinator(hass)
    miner_id, plan = await _plan_for(coordinator)

    _energy(hass, solar="1000")  # the sun went in between looking and pressing
    result = await coordinator.async_apply_shown(miner_id, plan.fingerprint)
    await hass.async_block_till_done()

    assert result.status == "refused"
    assert result.reason.startswith("changed")
    assert result.notified
    assert not calls
    note = hass.data["persistent_notification"]["solar_smart_miner_apply_changed_192_168_1_10"]
    assert "Brod1: the proposal changed" in note["message"]
    assert "check" in note["message"].lower()


async def test_failed_refresh_refuses(hass, add_hass_miner) -> None:
    add_hass_miner("192.168.1.10", name="Brod1", limit="1100", temperature="55", limit_attrs=LIMITS)
    _energy(hass)
    calls = async_mock_service(hass, "number", "set_value")
    coordinator = _coordinator(hass)
    miner_id, plan = await _plan_for(coordinator)

    coordinator._async_update_data = AsyncMock(side_effect=UpdateFailed("sensor read failed"))
    result = await coordinator.async_apply_shown(miner_id, plan.fingerprint)

    assert result.status == "refused"
    assert "stale" in result.reason
    assert not calls


async def test_preview_mode_refuses_before_refreshing(hass, add_hass_miner) -> None:
    add_hass_miner("192.168.1.10", name="Brod1", limit="1100", temperature="55", limit_attrs=LIMITS)
    _energy(hass)
    calls = async_mock_service(hass, "number", "set_value")
    coordinator = _coordinator(hass, CONTROL_MODE_PREVIEW)
    miner_id, plan = await _plan_for(coordinator)
    coordinator.async_refresh = AsyncMock()

    result = await coordinator.async_apply_shown(miner_id, plan.fingerprint)

    assert result.status == "refused"
    assert "preview" in result.reason
    coordinator.async_refresh.assert_not_called()
    assert not calls


async def test_unknown_miner_is_refused(hass, add_hass_miner) -> None:
    add_hass_miner("192.168.1.10", name="Brod1", limit_attrs=LIMITS)
    _energy(hass)
    coordinator = _coordinator(hass)
    await coordinator.async_refresh()

    result = await coordinator.async_apply_shown("10.9.9.9", "set_limit|1300||")

    assert result.status == "refused"


# --- Apply all ------------------------------------------------------------


def _two_miners(hass, add_hass_miner) -> dict:
    """Brod1 (listed first) is cool and could step up; Brod2 is too warm and steps down.

    The rules change one miner per proposal, so they propose only Brod2's step down.
    """
    up = add_hass_miner("192.168.1.10", name="Brod1", limit="900", power="900", temperature="55",
                        limit_attrs=LIMITS)
    down = add_hass_miner("192.168.1.11", name="Brod2", limit="1500", power="1500", temperature="80",
                          limit_attrs=LIMITS)
    hass.states.async_set(SOLAR, "6000")
    hass.states.async_set(GRID, "2000")
    return {
        "up": up["power_limit"].entity_id,
        "down": down["power_limit"].entity_id,
        "up_temperature": up["temperature"].entity_id,
    }


def _brod1_steps_up_too(coordinator) -> None:
    """Make the shown proposal a two-miner bundle (as a safety step may be)."""
    brod1 = next(m for m in coordinator.data.miners if m.name == "Brod1")
    coordinator.data.decision.plans[brod1.miner_id] = MinerPlan("set_limit", limit_w=1100.0, reason="budget")


async def test_the_rules_propose_one_miner_at_a_time(hass, add_hass_miner) -> None:
    _two_miners(hass, add_hass_miner)
    coordinator = _coordinator(hass)
    await coordinator.async_refresh()
    plans = {m.name: coordinator.data.decision.plans[m.miner_id] for m in coordinator.data.miners}

    assert plans["Brod2"].limit_w == 1300  # too warm: one step down
    assert plans["Brod1"].action == "hold"  # could step up, waits for the next decision


async def test_apply_all_does_reductions_before_increases(hass, add_hass_miner) -> None:
    entities = _two_miners(hass, add_hass_miner)
    calls = async_mock_service(hass, "number", "set_value")
    coordinator = _coordinator(hass)
    await coordinator.async_refresh()
    _brod1_steps_up_too(coordinator)
    coordinator._async_update_data = AsyncMock(return_value=coordinator.data)

    results = await coordinator.async_apply_all()

    assert [r.status for r in results.values()] == ["pending", "pending"]
    # Brod1 is listed first and steps up, but Brod2's step down is sent before it.
    assert [c.data["entity_id"] for c in calls] == [entities["down"], entities["up"]]


async def test_apply_all_stops_before_step_ups(hass, add_hass_miner) -> None:
    up = add_hass_miner("192.168.1.10", name="Brod1", limit="900", power="900", temperature="55",
                        limit_attrs=LIMITS)
    add_hass_miner("192.168.1.11", name="Brod2", limit="1500", power="1500", temperature="55",
                   limit_attrs=LIMITS, active="on")
    hass.states.async_set(SOLAR, "6000")
    hass.states.async_set(GRID, "2000")
    order: list[str] = []
    async_mock_service(hass, "number", "set_value")
    hass.services.async_register(
        "number", "set_value", lambda call: order.append(f"set {call.data['entity_id']}")
    )
    hass.services.async_register(
        "switch", "turn_off", lambda call: order.append(f"off {call.data['entity_id']}")
    )
    coordinator = _coordinator(hass)
    await coordinator.async_refresh()
    plans = {m.name: coordinator.data.decision.plans[m.miner_id] for m in coordinator.data.miners}
    # Force the scenario: Brod1 steps up, Brod2 stops.
    plans["Brod2"].action, plans["Brod2"].method = "stop", "pause"
    plans["Brod2"].target_entity_id = "switch.miner_00_00_00_00_00_02_active"
    plans["Brod2"].limit_w = None
    coordinator._async_update_data = AsyncMock(return_value=coordinator.data)

    await coordinator.async_apply_all()

    assert order[0].startswith("off ")
    assert order[1] == f"set {up['power_limit'].entity_id}"


async def test_apply_all_applies_nothing_when_any_part_of_the_bundle_changed(hass, add_hass_miner) -> None:
    """The plans share one budget, so a change to any of them withdraws the whole proposal."""
    entities = _two_miners(hass, add_hass_miner)
    calls = async_mock_service(hass, "number", "set_value")
    coordinator = _coordinator(hass)
    await coordinator.async_refresh()
    _brod1_steps_up_too(coordinator)  # shown: Brod1 up and Brod2 down

    # On press the rules no longer step Brod1 up (they never did here: one miner per proposal).
    results = await coordinator.async_apply_all()
    await hass.async_block_till_done()

    assert {r.status for r in results.values()} == {"refused"}
    assert all(r.reason.startswith("changed") and r.notified for r in results.values())
    assert not calls  # not even Brod2, whose own step down is unchanged
    note = hass.data["persistent_notification"]["solar_smart_miner_apply_changed"]["message"]
    assert "The proposal changed" in note and "Brod2" in note


async def test_apply_all_refuses_when_a_new_step_appears_after_the_refresh(hass, add_hass_miner) -> None:
    entities = _two_miners(hass, add_hass_miner)
    calls = async_mock_service(hass, "number", "set_value")
    coordinator = _coordinator(hass)
    await coordinator.async_refresh()
    # Pretend the owner looked while Brod2 was holding and Brod1 was the one change on screen.
    brod2 = next(m for m in coordinator.data.miners if m.name == "Brod2")
    coordinator.data.decision.plans[brod2.miner_id] = MinerPlan("hold", limit_w=1500.0, reason="budget")
    _brod1_steps_up_too(coordinator)

    results = await coordinator.async_apply_all()

    assert {r.status for r in results.values()} == {"refused"}
    assert not calls
    assert entities  # both miners are in the refusal: the bundle is judged as a whole


async def test_apply_all_with_nothing_actionable_does_nothing(hass, add_hass_miner) -> None:
    add_hass_miner("192.168.1.10", name="Brod1", limit="1500", power="1500", temperature="55",
                   limit_attrs=LIMITS)
    _energy(hass, solar="1700", house="1500")  # already at the top step, nothing higher to go to
    calls = async_mock_service(hass, "number", "set_value")
    coordinator = _coordinator(hass)
    await coordinator.async_refresh()
    assert {p.action for p in coordinator.data.decision.plans.values()} == {"hold"}

    assert await coordinator.async_apply_all() == {}
    assert not calls


async def test_apply_all_in_preview_mode_refuses_each_plan(hass, add_hass_miner) -> None:
    _two_miners(hass, add_hass_miner)
    calls = async_mock_service(hass, "number", "set_value")
    coordinator = _coordinator(hass, CONTROL_MODE_PREVIEW)
    await coordinator.async_refresh()

    results = await coordinator.async_apply_all()

    assert {r.status for r in results.values()} == {"refused"}
    assert not calls


async def test_apply_all_refuses_when_the_refresh_failed(hass, add_hass_miner) -> None:
    _two_miners(hass, add_hass_miner)
    calls = async_mock_service(hass, "number", "set_value")
    coordinator = _coordinator(hass)
    await coordinator.async_refresh()
    coordinator._async_update_data = AsyncMock(side_effect=UpdateFailed("down"))

    results = await coordinator.async_apply_all()

    assert {r.status for r in results.values()} == {"refused"}
    assert all("stale" in r.reason for r in results.values())
    assert not calls
