"""Tests for the farm-level apply entities: Apply proposal button, Activity and Last action sensors."""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er_module
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_mock_service

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
from custom_components.solar_smart_miner.control import CommandResult
from custom_components.solar_smart_miner.protocols import MinerPlan

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
        # The short ladder these tests were written for; the default ladder is tested elsewhere.
        options={CONF_CONTROL_MODE: mode, CONF_POWER_STEPS: [900, 1100, 1300, 1500]},
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


# --- the Apply proposal button ---------------------------------------------


async def test_there_is_one_apply_button_for_the_whole_farm(hass, add_hass_miner) -> None:
    await _setup(hass, add_hass_miner)
    add_hass_miner("192.168.1.11", name="Brod2", limit="900", power="900", limit_attrs=LIMITS)

    buttons = [s.entity_id for s in hass.states.async_all("button") if "apply" in s.entity_id]

    assert len(buttons) == 1 and buttons[0].endswith("apply_proposal")


async def test_apply_is_available_in_manual_mode_with_a_proposal(hass, add_hass_miner) -> None:
    await _setup(hass, add_hass_miner)

    assert _state(hass, "button", "_apply_all").state != "unavailable"


async def test_apply_is_unavailable_in_automatic_mode(hass, add_hass_miner) -> None:
    await _setup(hass, add_hass_miner, mode=CONTROL_MODE_AUTO)

    assert _state(hass, "button", "_apply_all").state == "unavailable"


async def test_apply_is_unavailable_when_everything_holds(hass, add_hass_miner) -> None:
    await _setup(hass, add_hass_miner, limit="1500", power="1500", solar="1700", house="1500")

    assert _state(hass, "button", "_apply_all").state == "unavailable"


async def test_pressing_apply_sends_the_whole_proposal(hass, add_hass_miner) -> None:
    entry, reg = await _setup(hass, add_hass_miner)
    calls = async_mock_service(hass, "number", "set_value")

    await _press(hass, _entity_id(hass, "button", "_apply_all"))

    assert [(c.data["entity_id"], c.data["value"]) for c in calls] == [
        (reg["power_limit"].entity_id, 1500.0)
    ]
    assert _state(hass, "sensor", "_last_action").state == "Brod1: 1,500 W (pending)"


async def test_pressing_apply_with_everything_refused_shows_why(hass, add_hass_miner) -> None:
    entry, _ = await _setup(hass, add_hass_miner)
    entry.runtime_data.async_apply_all = AsyncMock(
        return_value={"192.168.1.10": CommandResult("refused", "the latest update failed")}
    )

    with pytest.raises(HomeAssistantError, match="the latest update failed"):
        await _press(hass, _entity_id(hass, "button", "_apply_all"))


async def test_a_refusal_already_notified_does_not_raise_again(hass, add_hass_miner) -> None:
    entry, _ = await _setup(hass, add_hass_miner)
    entry.runtime_data.async_apply_all = AsyncMock(
        return_value={"192.168.1.10": CommandResult("refused", "changed", notified=True)}
    )

    await _press(hass, _entity_id(hass, "button", "_apply_all"))


async def test_pressing_apply_with_nothing_to_do_says_so(hass, add_hass_miner) -> None:
    entry, _ = await _setup(hass, add_hass_miner)
    entry.runtime_data.async_apply_all = AsyncMock(return_value={})

    with pytest.raises(HomeAssistantError, match="nothing to apply"):
        await _press(hass, _entity_id(hass, "button", "_apply_all"))


async def test_pressing_apply_after_the_proposal_changed_applies_nothing(hass, add_hass_miner) -> None:
    entry, reg = await _setup(hass, add_hass_miner)
    calls = async_mock_service(hass, "number", "set_value")
    assert entry.runtime_data.data.decision.proposals  # the owner is looking at a proposal

    hass.states.async_set(reg["temperature"].entity_id, "80")  # the miner warms up meanwhile
    await _press(hass, _entity_id(hass, "button", "_apply_all"))
    await hass.async_block_till_done()

    assert not calls  # nothing sent; the owner is told by a notification instead
    assert "solar_smart_miner_apply_changed" in hass.data["persistent_notification"]


# --- Last action sensor ----------------------------------------------------


async def test_last_action_sensor_starts_empty(hass, add_hass_miner) -> None:
    await _setup(hass, add_hass_miner)

    state = _state(hass, "sensor", "_last_action")

    assert state.state == "None yet"
    assert state.attributes["history"] == []
    assert state.attributes["log_file"].endswith("actions.jsonl")


# --- activity: the farm's proposal and the feed -----------------------------


async def test_activity_shows_the_farm_proposal(hass, add_hass_miner) -> None:
    entry, _ = await _setup(hass, add_hass_miner)

    state = _state(hass, "sensor", "_activity")

    assert state.state == "Brod1 1,500 W (from 1,100 W)"
    assert state.attributes["proposal"] == "Brod1 1,500 W (from 1,100 W)"
    assert state.attributes["feed"][0]["plan"] == "Brod1 1,500 W (from 1,100 W)"
    assert state.attributes["feed"][0]["current"] is True


async def test_the_proposal_changes_one_miner_and_lists_reductions_first(hass, add_hass_miner) -> None:
    add_hass_miner("192.168.1.10", name="Brod1", limit="900", power="900", temperature="55",
                        limit_attrs=LIMITS)
    add_hass_miner("192.168.1.11", name="Brod2", limit="1500", power="1500", temperature="80",
                   limit_attrs=LIMITS)
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_SOLAR_ENTITY: SOLAR, CONF_GRID_ENTITY: GRID, CONF_BATTERY_ENTITY: None},
        options={CONF_CONTROL_MODE: CONTROL_MODE_MANUAL},
    )
    entry.add_to_hass(hass)
    hass.states.async_set(SOLAR, "6000")
    hass.states.async_set(GRID, "2000")
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    state = _state(hass, "sensor", "_activity")

    # Brod1 could step up too, but one miner changes per proposal.
    assert state.attributes["proposal"] == "Brod2 1,300 W (from 1,500 W)"
    assert len(state.attributes["feed"]) == 1

    # A bundle of several (a safety step) is listed in the order Apply sends it: reduction first.
    coordinator = entry.runtime_data
    brod1 = next(m for m in coordinator.data.miners if m.name == "Brod1")
    coordinator.data.decision.plans[brod1.miner_id] = MinerPlan("set_limit", limit_w=1500.0, reason="budget")
    assert coordinator.proposal_text() == "Brod2 1,300 W (from 1,500 W) · Brod1 1,500 W (from 900 W)"


async def test_activity_feed_follows_proposals_then_the_applied_action(hass, add_hass_miner) -> None:
    entry, reg = await _setup(hass, add_hass_miner)
    async_mock_service(hass, "number", "set_value")
    coordinator = entry.runtime_data

    await _press(hass, _entity_id(hass, "button", "_apply_all"))
    hass.states.async_set(reg["power_limit"].entity_id, "1500", LIMITS)
    await coordinator.async_refresh()
    await hass.async_block_till_done()

    feed = _state(hass, "sensor", "_activity").attributes["feed"]
    kinds = [(e["kind"], e.get("result")) for e in feed]
    # Newest first: once the miner reached 1,500 W nothing is left to propose, after the ok line.
    assert kinds == [("proposal", None), ("applied", "ok"), ("applied", "pending"), ("proposal", None)]
    assert feed[0]["plan"] == "no action" and feed[0]["current"] is False
    assert feed[2]["plan"] == "1,500 W" and feed[2]["miner"] == "Brod1"
    assert feed[3]["current"] is False  # an old proposal is never current


async def test_a_changed_proposal_adds_an_entry_and_retires_the_old_one(hass, add_hass_miner) -> None:
    entry, reg = await _setup(hass, add_hass_miner)
    hass.states.async_set(reg["temperature"].entity_id, "80")  # warm: a step down now
    await entry.runtime_data.async_refresh()
    await hass.async_block_till_done()

    feed = _state(hass, "sensor", "_activity").attributes["feed"]

    assert [(e["plan"], e["current"]) for e in feed] == [
        (feed[0]["plan"], True),
        ("Brod1 1,500 W (from 1,100 W)", False),
    ]
    assert feed[0]["plan"].startswith("Brod1 ") and feed[0]["plan"] != feed[1]["plan"]


async def test_an_unchanged_proposal_is_not_repeated(hass, add_hass_miner) -> None:
    entry, _ = await _setup(hass, add_hass_miner)
    for _ in range(3):
        await entry.runtime_data.async_refresh()
    await hass.async_block_till_done()

    assert len(_state(hass, "sensor", "_activity").attributes["feed"]) == 1


async def test_nothing_to_propose_from_the_start_is_not_news(hass, add_hass_miner) -> None:
    await _setup(hass, add_hass_miner, limit="1500", power="1500", solar="1700", house="1500")

    state = _state(hass, "sensor", "_activity")

    assert state.state == "no action"
    assert state.attributes["feed"] == []


async def test_activity_feed_restarts_from_the_action_log(hass, add_hass_miner) -> None:
    entry, _ = await _setup(hass, add_hass_miner)
    async_mock_service(hass, "number", "set_value")
    await _press(hass, _entity_id(hass, "button", "_apply_all"))

    await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()

    kinds = [e["kind"] for e in _state(hass, "sensor", "_activity").attributes["feed"]]
    assert kinds == ["proposal", "applied"]  # the proposal is new; the applied action came from the file


async def test_unique_ids_are_stable_across_a_reload(hass, add_hass_miner) -> None:
    entry, _ = await _setup(hass, add_hass_miner)
    er = er_module.async_get(hass)
    before = sorted(e.unique_id for e in er.entities.values() if e.platform == DOMAIN)

    await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()

    after = sorted(e.unique_id for e in er.entities.values() if e.platform == DOMAIN)
    assert after == before
    assert any(u.endswith("_apply_all") for u in after)


async def test_per_miner_entities_of_v060_are_removed_on_setup(hass, add_hass_miner) -> None:
    entry = MockConfigEntry(domain=DOMAIN, data={CONF_SOLAR_ENTITY: SOLAR, CONF_GRID_ENTITY: GRID})
    entry.add_to_hass(hass)
    er = er_module.async_get(hass)
    old_button = er.async_get_or_create(
        "button", DOMAIN, f"{entry.entry_id}_192_168_1_10_apply", config_entry=entry
    )
    old_sensor = er.async_get_or_create(
        "sensor", DOMAIN, f"{entry.entry_id}_192_168_1_10_proposed_action", config_entry=entry
    )
    hass.states.async_set(SOLAR, "5000")
    hass.states.async_set(GRID, "1500")
    add_hass_miner("192.168.1.10", name="Brod1", limit="1100", power="1100", limit_attrs=LIMITS)

    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert er.async_get(old_button.entity_id) is None
    assert er.async_get(old_sensor.entity_id) is None
    assert any(  # the farm-level Apply button stays
        e.unique_id.endswith("_apply_all") for e in er.entities.values() if e.platform == DOMAIN
    )
