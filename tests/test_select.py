"""Tests for the select entities (control mode) and the profile migration."""
from __future__ import annotations

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.solar_smart_miner.config_flow import (
    CONF_GRID_ENTITY,
    CONF_POLLING_INTERVAL,
    CONF_PROFILE,
    CONF_SOLAR_ENTITY,
)
from custom_components.solar_smart_miner.const import CONF_CONTROL_MODE, DOMAIN
from custom_components.solar_smart_miner.select import ControlModeSelect

SOLAR_ENTITY = "sensor.solar_power"
GRID_ENTITY = "sensor.grid_consumption"


def _make_entry(hass, profile: str = "solar_follow") -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_SOLAR_ENTITY: SOLAR_ENTITY, CONF_GRID_ENTITY: GRID_ENTITY},
        options={CONF_PROFILE: profile, CONF_POLLING_INTERVAL: 15},
    )
    entry.add_to_hass(hass)
    return entry


@pytest.mark.parametrize("stored", ["solar_max", "grid_agnostic", "grid_independent", "battery_focused"])
async def test_every_old_profile_becomes_solar_follow_at_setup(hass, stored) -> None:
    # Owner, 2026-10-09: one profile until the battery ones; Solar-max was its forerunner.
    hass.states.async_set(SOLAR_ENTITY, "2000")
    hass.states.async_set(GRID_ENTITY, "1500")
    entry = _make_entry(hass, profile=stored)

    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    coordinator = entry.runtime_data
    await hass.async_block_till_done()

    assert entry.options[CONF_PROFILE] == "solar_follow"
    assert entry.options[CONF_POLLING_INTERVAL] == 15  # the other options are kept
    assert entry.runtime_data is coordinator  # rewritten before the reload listener existed
    assert coordinator.data.decision.summary.startswith(("Solar-follow", "No miners"))


async def test_there_is_no_profile_select_and_an_old_one_is_removed(hass) -> None:
    from homeassistant.helpers import entity_registry as er

    hass.states.async_set(SOLAR_ENTITY, "2000")
    hass.states.async_set(GRID_ENTITY, "1500")
    entry = _make_entry(hass)
    registry = er.async_get(hass)
    old = registry.async_get_or_create("select", DOMAIN, f"{entry.entry_id}_profile", config_entry=entry)

    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert registry.async_get(old.entity_id) is None
    assert [s.entity_id for s in hass.states.async_all("select")] == [
        next(s.entity_id for s in hass.states.async_all("select") if s.entity_id.endswith("control_mode"))
    ]


async def test_control_mode_defaults_to_manual(hass) -> None:
    select = ControlModeSelect(_make_entry(hass))
    assert select.current_option == "Manual"
    assert select.options == ["Manual", "Automatic"]


@pytest.mark.parametrize("stored", ["preview", "something-else"])
async def test_a_stored_mode_that_no_longer_exists_reads_as_manual(hass, stored) -> None:
    entry = _make_entry(hass)
    hass.config_entries.async_update_entry(entry, options={**entry.options, CONF_CONTROL_MODE: stored})

    assert ControlModeSelect(entry).current_option == "Manual"


async def test_control_mode_select_device_is_hub(hass) -> None:
    entry = _make_entry(hass)
    assert ControlModeSelect(entry).device_info["identifiers"] == {(DOMAIN, entry.entry_id)}


async def test_selecting_control_mode_updates_options_and_coordinator(hass) -> None:
    hass.states.async_set(SOLAR_ENTITY, "2000")
    hass.states.async_set(GRID_ENTITY, "1500")
    entry = _make_entry(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.runtime_data.control_mode == "manual"
    select_id = next(
        s.entity_id for s in hass.states.async_all("select") if s.entity_id.endswith("control_mode")
    )

    await hass.services.async_call(
        "select", "select_option", {"entity_id": select_id, "option": "Automatic"}, blocking=True
    )
    await hass.async_block_till_done()

    assert entry.options[CONF_CONTROL_MODE] == "auto"
    assert entry.runtime_data.control_mode == "auto"  # the reloaded coordinator
    assert hass.states.get(select_id).state == "Automatic"


async def test_a_stored_preview_mode_becomes_manual_at_setup_without_a_reload(hass) -> None:
    hass.states.async_set(SOLAR_ENTITY, "2000")
    hass.states.async_set(GRID_ENTITY, "1500")
    entry = _make_entry(hass)
    hass.config_entries.async_update_entry(entry, options={**entry.options, CONF_CONTROL_MODE: "preview"})

    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    coordinator = entry.runtime_data
    await hass.async_block_till_done()

    assert entry.options[CONF_CONTROL_MODE] == "manual"
    assert entry.runtime_data is coordinator  # rewritten before the reload listener existed
    assert coordinator.control_mode == "manual"
    assert entry.options[CONF_POLLING_INTERVAL] == 15  # the other options are kept


async def test_the_old_import_target_is_dropped_at_setup(hass) -> None:
    # Up to 0.7.4 "import_target" held the single 400 W target; read as the minimum it made the
    # range 400 to 400 W. Dropped, the range comes from its own settings (the defaults here).
    hass.states.async_set(SOLAR_ENTITY, "2000")
    hass.states.async_set(GRID_ENTITY, "1500")
    entry = _make_entry(hass)
    hass.config_entries.async_update_entry(entry, options={**entry.options, "import_target": 400})

    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert "import_target" not in entry.options
    assert entry.options[CONF_POLLING_INTERVAL] == 15  # the other options are kept


@pytest.mark.parametrize("stored", [60, 5, 20])
async def test_the_retired_tuning_time_is_dropped_at_setup(hass, stored) -> None:
    # 0.8.0: the tuning window is merged into the settling after a restart (the ramp lock).
    hass.states.async_set(SOLAR_ENTITY, "2000")
    hass.states.async_set(GRID_ENTITY, "1500")
    entry = _make_entry(hass)
    hass.config_entries.async_update_entry(entry, options={**entry.options, "tuning_settle_minutes": stored})

    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert "tuning_settle_minutes" not in entry.options
    assert entry.options[CONF_POLLING_INTERVAL] == 15  # the other options are kept
