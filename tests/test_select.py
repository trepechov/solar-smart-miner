"""Tests for the profile select entity."""
from __future__ import annotations

from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.solar_smart_miner.config_flow import (
    CONF_GRID_ENTITY,
    CONF_POLLING_INTERVAL,
    CONF_PROFILE,
    CONF_SOLAR_ENTITY,
)
from custom_components.solar_smart_miner.const import DOMAIN
from custom_components.solar_smart_miner.select import ProfileSelect

SOLAR_ENTITY = "sensor.solar_power"
GRID_ENTITY = "sensor.grid_consumption"


def _make_entry(hass, profile: str = "solar_max") -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_SOLAR_ENTITY: SOLAR_ENTITY, CONF_GRID_ENTITY: GRID_ENTITY},
        options={CONF_PROFILE: profile, CONF_POLLING_INTERVAL: 15},
    )
    entry.add_to_hass(hass)
    return entry


async def test_profile_select_shows_display_names(hass) -> None:
    select = ProfileSelect(_make_entry(hass, profile="grid_independent"))
    assert select.current_option == "Grid-independent"
    assert select.options == ["Battery-focused", "Solar-max", "Grid-agnostic", "Grid-independent"]


async def test_profile_select_device_is_hub(hass) -> None:
    entry = _make_entry(hass)
    assert ProfileSelect(entry).device_info["identifiers"] == {(DOMAIN, entry.entry_id)}


async def test_selecting_profile_updates_options_and_reloads(hass) -> None:
    hass.states.async_set(SOLAR_ENTITY, "2000")
    hass.states.async_set(GRID_ENTITY, "1500")
    entry = _make_entry(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    first_coordinator = entry.runtime_data
    select_id = next(
        s.entity_id for s in hass.states.async_all("select") if s.entity_id.endswith("profile")
    )

    await hass.services.async_call(
        "select", "select_option",
        {"entity_id": select_id, "option": "Grid-agnostic"}, blocking=True,
    )
    await hass.async_block_till_done()

    assert entry.options[CONF_PROFILE] == "grid_agnostic"
    assert entry.runtime_data is not first_coordinator  # reloaded with new settings
    assert hass.states.get(select_id).state == "Grid-agnostic"
