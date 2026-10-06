"""Tests for the apply entities: proposed-action sensor, Apply buttons, Last action sensor."""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er_module
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_mock_service

from custom_components.solar_smart_miner.config_flow import (
    CONF_BATTERY_ENTITY,
    CONF_GRID_ENTITY,
    CONF_SOLAR_ENTITY,
)
from custom_components.solar_smart_miner.const import (
    CONF_CONTROL_MODE,
    CONTROL_MODE_MANUAL,
    CONTROL_MODE_PREVIEW,
    DOMAIN,
)
from custom_components.solar_smart_miner.control import CommandResult

SOLAR = "sensor.solar_power"
GRID = "sensor.grid_consumption"
LIMITS = {"min": 500.0, "max": 3500.0}


async def _setup(hass, add_hass_miner, *, mode=CONTROL_MODE_MANUAL, solar="5000", house="1500", **miner):
    miner = {"name": "Brod1", "limit": "1100", "power": "1100", "temperature": "55", "limit_attrs": LIMITS,
             **miner}
    reg = add_hass_miner("192.168.1.10", **miner)
    hass.states.async_set(SOLAR, solar)
    hass.states.async_set(GRID, house)
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_SOLAR_ENTITY: SOLAR, CONF_GRID_ENTITY: GRID, CONF_BATTERY_ENTITY: None},
        options={CONF_CONTROL_MODE: mode},
    )
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry, reg


def _entity_id(hass, domain: str, suffix: str) -> str:
    er = er_module.async_get(hass)
    return next(
        e.entity_id for e in er.entities.values()
        if e.platform == DOMAIN and e.domain == domain and e.unique_id.endswith(suffix)
    )


def _state(hass, domain: str, suffix: str):
    return hass.states.get(_entity_id(hass, domain, suffix))


async def _press(hass, entity_id: str) -> None:
    await hass.services.async_call("button", "press", {"entity_id": entity_id}, blocking=True)


# --- proposed action sensor -----------------------------------------------


async def test_proposed_action_sensor_shows_the_plan_and_its_fingerprint(hass, add_hass_miner) -> None:
    entry, _ = await _setup(hass, add_hass_miner)

    state = _state(hass, "sensor", "192_168_1_10_proposed_action")

    assert state.state == "1,500 W (from 1,100 W)"
    plan = next(iter(entry.runtime_data.data.decision.plans.values()))
    assert state.attributes["fingerprint"] == plan.fingerprint
    assert state.attributes["action"] == "set_limit"
    assert state.attributes["limit_w"] == 1500.0
    assert state.attributes["reason"] == "budget"
    assert state.attributes["ai_action"] is None
    assert state.attributes["pending"] is False
    assert state.name.endswith("Brod1 proposed action")


async def test_proposed_action_sensor_shows_a_stop(hass, add_hass_miner) -> None:
    await _setup(hass, add_hass_miner, solar="0", house="1100", active="on")

    state = _state(hass, "sensor", "192_168_1_10_proposed_action")

    assert state.state == "stop (pause)"
    assert state.attributes["action"] == "stop"


async def test_proposed_action_sensor_shows_hold(hass, add_hass_miner) -> None:
    await _setup(hass, add_hass_miner, limit="1500", power="1500", solar="1700", house="1500")

    state = _state(hass, "sensor", "192_168_1_10_proposed_action")

    assert state.state == "hold 1,500 W"
    assert state.attributes["action"] == "hold"


async def test_proposed_action_sensor_is_pending_while_a_command_is_checked(hass, add_hass_miner) -> None:
    entry, _ = await _setup(hass, add_hass_miner)
    async_mock_service(hass, "number", "set_value")
    coordinator = entry.runtime_data
    miner_id, plan = next(iter(coordinator.data.decision.plans.items()))

    await coordinator.async_apply_shown(miner_id, plan.fingerprint)
    await hass.async_block_till_done()

    assert _state(hass, "sensor", "192_168_1_10_proposed_action").attributes["pending"] is True


# --- the Apply button -------------------------------------------------------


async def test_apply_button_is_available_in_manual_mode_with_an_actionable_plan(hass, add_hass_miner) -> None:
    await _setup(hass, add_hass_miner)

    assert _state(hass, "button", "192_168_1_10_apply").state != "unavailable"


async def test_apply_button_is_unavailable_in_preview_mode(hass, add_hass_miner) -> None:
    await _setup(hass, add_hass_miner, mode=CONTROL_MODE_PREVIEW)

    assert _state(hass, "button", "192_168_1_10_apply").state == "unavailable"
    assert _state(hass, "button", "_apply_all").state == "unavailable"


async def test_apply_button_is_unavailable_on_a_hold_plan(hass, add_hass_miner) -> None:
    await _setup(hass, add_hass_miner, limit="1500", power="1500", solar="1700", house="1500")

    assert _state(hass, "button", "192_168_1_10_apply").state == "unavailable"
    assert _state(hass, "button", "_apply_all").state == "unavailable"


async def test_apply_button_is_unavailable_while_a_command_is_pending(hass, add_hass_miner) -> None:
    entry, _ = await _setup(hass, add_hass_miner)
    async_mock_service(hass, "number", "set_value")
    coordinator = entry.runtime_data
    miner_id, plan = next(iter(coordinator.data.decision.plans.items()))

    await coordinator.async_apply_shown(miner_id, plan.fingerprint)
    await hass.async_block_till_done()

    assert _state(hass, "button", "192_168_1_10_apply").state == "unavailable"


async def test_pressing_apply_passes_the_shown_fingerprint(hass, add_hass_miner) -> None:
    entry, _ = await _setup(hass, add_hass_miner)
    coordinator = entry.runtime_data
    shown = next(iter(coordinator.data.decision.plans.values())).fingerprint
    coordinator.async_apply_shown = AsyncMock(return_value=CommandResult("pending"))

    await _press(hass, _entity_id(hass, "button", "192_168_1_10_apply"))

    coordinator.async_apply_shown.assert_awaited_once_with("192.168.1.10", shown)


async def test_pressing_apply_sends_the_command(hass, add_hass_miner) -> None:
    entry, reg = await _setup(hass, add_hass_miner)
    calls = async_mock_service(hass, "number", "set_value")

    await _press(hass, _entity_id(hass, "button", "192_168_1_10_apply"))

    assert [(c.data["entity_id"], c.data["value"]) for c in calls] == [
        (reg["power_limit"].entity_id, 1500.0)
    ]
    assert _state(hass, "sensor", "_last_action").state == "Brod1: 1,500 W (pending)"


@pytest.mark.parametrize("status", ["refused", "failed"])
async def test_pressing_apply_shows_a_refusal_to_the_user(hass, add_hass_miner, status) -> None:
    entry, _ = await _setup(hass, add_hass_miner)
    entry.runtime_data.async_apply_shown = AsyncMock(return_value=CommandResult(status, "miner is busy"))

    with pytest.raises(HomeAssistantError, match="Brod1: miner is busy"):
        await _press(hass, _entity_id(hass, "button", "192_168_1_10_apply"))


async def test_a_refusal_already_notified_does_not_raise_again(hass, add_hass_miner) -> None:
    entry, _ = await _setup(hass, add_hass_miner)
    entry.runtime_data.async_apply_shown = AsyncMock(
        return_value=CommandResult("refused", "changed", notified=True)
    )

    await _press(hass, _entity_id(hass, "button", "192_168_1_10_apply"))


# --- Apply all -------------------------------------------------------------


async def test_apply_all_is_available_when_one_miner_has_an_actionable_plan(hass, add_hass_miner) -> None:
    await _setup(hass, add_hass_miner)

    assert _state(hass, "button", "_apply_all").state != "unavailable"


async def test_pressing_apply_all_applies_the_plans(hass, add_hass_miner) -> None:
    entry, reg = await _setup(hass, add_hass_miner)
    calls = async_mock_service(hass, "number", "set_value")

    await _press(hass, _entity_id(hass, "button", "_apply_all"))

    assert [c.data["entity_id"] for c in calls] == [reg["power_limit"].entity_id]


async def test_pressing_apply_all_with_everything_refused_shows_why(hass, add_hass_miner) -> None:
    entry, _ = await _setup(hass, add_hass_miner)
    entry.runtime_data.async_apply_all = AsyncMock(
        return_value={"192.168.1.10": CommandResult("refused", "the latest update failed")}
    )

    with pytest.raises(HomeAssistantError, match="the latest update failed"):
        await _press(hass, _entity_id(hass, "button", "_apply_all"))


async def test_pressing_apply_all_with_nothing_to_do_says_so(hass, add_hass_miner) -> None:
    entry, _ = await _setup(hass, add_hass_miner)
    entry.runtime_data.async_apply_all = AsyncMock(return_value={})

    with pytest.raises(HomeAssistantError, match="nothing to apply"):
        await _press(hass, _entity_id(hass, "button", "_apply_all"))


# --- Last action sensor ----------------------------------------------------


async def test_last_action_sensor_starts_empty(hass, add_hass_miner) -> None:
    await _setup(hass, add_hass_miner)

    state = _state(hass, "sensor", "_last_action")

    assert state.state == "None yet"
    assert state.attributes["history"] == []
    assert state.attributes["log_file"].endswith("actions.jsonl")


# --- entities follow the miners -------------------------------------------


async def test_a_miner_that_shows_up_later_gets_its_proposal_and_button(hass, add_hass_miner) -> None:
    entry, _ = await _setup(hass, add_hass_miner)

    add_hass_miner("192.168.1.11", name="Brod2", limit="900", power="900", limit_attrs=LIMITS)
    await entry.runtime_data.async_refresh()
    await hass.async_block_till_done()

    assert _entity_id(hass, "sensor", "192_168_1_11_proposed_action")
    assert _entity_id(hass, "button", "192_168_1_11_apply")


async def test_unique_ids_are_stable_across_a_reload(hass, add_hass_miner) -> None:
    entry, _ = await _setup(hass, add_hass_miner)
    er = er_module.async_get(hass)
    before = sorted(e.unique_id for e in er.entities.values() if e.platform == DOMAIN)

    await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()

    after = sorted(e.unique_id for e in er.entities.values() if e.platform == DOMAIN)
    assert after == before
    assert any(u.endswith("192_168_1_10_apply") for u in after)
    assert any(u.endswith("192_168_1_10_proposed_action") for u in after)


# --- activity feed ---------------------------------------------------------


async def test_activity_feed_follows_proposals_then_the_applied_action(hass, add_hass_miner) -> None:
    entry, reg = await _setup(hass, add_hass_miner)
    async_mock_service(hass, "number", "set_value")
    coordinator = entry.runtime_data
    state = _state(hass, "sensor", "_activity")
    first = state.attributes["feed"][0]
    assert (first["kind"], first["miner"], first["plan"]) == ("proposal", "Brod1", "1,500 W (from 1,100 W)")
    assert first["current"] is True and first["reason"] == "budget"
    assert state.state == "Brod1: 1,500 W (from 1,100 W) (proposal)"

    await _press(hass, _entity_id(hass, "button", "192_168_1_10_apply"))
    hass.states.async_set(reg["power_limit"].entity_id, "1500", LIMITS)
    await coordinator.async_refresh()
    await hass.async_block_till_done()

    feed = _state(hass, "sensor", "_activity").attributes["feed"]
    kinds = [(e["kind"], e.get("result")) for e in feed]
    # Newest first: once the miner reached 1,500 W the rules propose a hold, after the ok line.
    assert kinds == [("proposal", None), ("applied", "ok"), ("applied", "pending"), ("proposal", None)]
    assert feed[0]["action"] == "hold" and feed[0]["current"] is False
    assert feed[2]["plan"] == "1,500 W"


async def test_a_changed_proposal_adds_an_entry_and_retires_the_old_one(hass, add_hass_miner) -> None:
    entry, reg = await _setup(hass, add_hass_miner)
    hass.states.async_set(reg["temperature"].entity_id, "80")  # warm: a step down now
    await entry.runtime_data.async_refresh()
    await hass.async_block_till_done()

    feed = _state(hass, "sensor", "_activity").attributes["feed"]

    assert [(e["reason"], e["current"]) for e in feed] == [("too warm", True), ("budget", False)]
    assert feed[1]["plan"] == "1,500 W (from 1,100 W)"


async def test_an_unchanged_proposal_is_not_repeated(hass, add_hass_miner) -> None:
    entry, _ = await _setup(hass, add_hass_miner)
    for _ in range(3):
        await entry.runtime_data.async_refresh()
    await hass.async_block_till_done()

    assert len(_state(hass, "sensor", "_activity").attributes["feed"]) == 1


async def test_a_first_hold_is_not_news_but_a_later_one_is(hass, add_hass_miner) -> None:
    entry, reg = await _setup(hass, add_hass_miner, limit="1500", power="1500", solar="1700", house="1500")
    assert _state(hass, "sensor", "_activity").state == "Nothing yet"  # holding from the start

    hass.states.async_set(reg["power_limit"].entity_id, "900", LIMITS)
    await entry.runtime_data.async_refresh()
    await hass.async_block_till_done()

    top = _state(hass, "sensor", "_activity").attributes["feed"][0]
    assert (top["plan"], top["reason"], top["current"]) == ("hold 900 W", "tuning", False)
    count = len(_state(hass, "sensor", "_activity").attributes["feed"])
    assert count == 1  # the change to 900 W is news; the hold it started from was not


async def test_activity_feed_restarts_from_the_action_log(hass, add_hass_miner) -> None:
    entry, _ = await _setup(hass, add_hass_miner)
    async_mock_service(hass, "number", "set_value")
    await _press(hass, _entity_id(hass, "button", "192_168_1_10_apply"))

    await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()

    kinds = [e["kind"] for e in _state(hass, "sensor", "_activity").attributes["feed"]]
    assert kinds == ["proposal", "applied"]  # the proposal is new; the applied action came from the file
