"""Tests for button platform — AddToDashboardButton."""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.solar_smart_miner.button import AddToDashboardButton, async_setup_entry
from custom_components.solar_smart_miner.config_flow import (
    CONF_GRID_ENTITY,
    CONF_MINERS,
    CONF_POLLING_INTERVAL,
    CONF_SOLAR_ENTITY,
)
from custom_components.solar_smart_miner.const import (
    CONF_MOCK_CONSUMPTION_ENABLED,
    CONF_MOCK_SOLAR_ENABLED,
    DEFAULT_POLLING_INTERVAL,
    DOMAIN,
)

SOLAR_ENTITY = "sensor.solar_power"
GRID_ENTITY = "sensor.grid_consumption"

PN_MODULE = "custom_components.solar_smart_miner.button.pn_create"


def _make_entry(hass):
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="My Miner Hub",
        data={
            CONF_SOLAR_ENTITY: SOLAR_ENTITY,
            CONF_GRID_ENTITY: GRID_ENTITY,
            CONF_MINERS: [],
        },
        options={
            CONF_POLLING_INTERVAL: DEFAULT_POLLING_INTERVAL,
            CONF_MOCK_SOLAR_ENABLED: False,
            CONF_MOCK_CONSUMPTION_ENABLED: False,
        },
        version=1,
    )
    entry.add_to_hass(hass)
    return entry


# ---------------------------------------------------------------------------
# Entity construction
# ---------------------------------------------------------------------------

async def test_button_name(hass) -> None:
    entry = _make_entry(hass)
    button = AddToDashboardButton(entry)
    assert button.name == "Add to dashboard"


async def test_button_unique_id(hass) -> None:
    entry = _make_entry(hass)
    button = AddToDashboardButton(entry)
    assert button.unique_id == f"{entry.entry_id}_add_to_dashboard"


async def test_button_device_info_uses_hub_identifier(hass) -> None:
    entry = _make_entry(hass)
    button = AddToDashboardButton(entry)
    assert (DOMAIN, entry.entry_id) in button.device_info["identifiers"]


async def test_button_icon(hass) -> None:
    entry = _make_entry(hass)
    button = AddToDashboardButton(entry)
    assert button.icon == "mdi:view-dashboard-variant"


# ---------------------------------------------------------------------------
# async_setup_entry registers the button
# ---------------------------------------------------------------------------

async def test_async_setup_entry_registers_one_button(hass) -> None:
    hass.states.async_set(SOLAR_ENTITY, "2000")
    hass.states.async_set(GRID_ENTITY, "1500")
    entry = _make_entry(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    button_states = [s for s in hass.states.async_all() if "add_to_dashboard" in s.entity_id]
    assert len(button_states) == 1


# ---------------------------------------------------------------------------
# async_press — no sensor entities registered yet
# ---------------------------------------------------------------------------

async def test_press_notifies_no_entities_when_hub_device_missing(hass) -> None:
    entry = _make_entry(hass)
    button = AddToDashboardButton(entry)
    button.hass = hass

    with patch(PN_MODULE) as mock_pn:
        await button.async_press()

    mock_pn.assert_not_called()


async def test_press_notifies_no_entities_when_no_sensors_registered(hass) -> None:
    """Hub device exists but no sensor entities have been registered yet."""
    from homeassistant.helpers import device_registry as dr

    entry = _make_entry(hass)
    # Create the hub device without any entities
    device_reg = dr.async_get(hass)
    device_reg.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, entry.entry_id)},
        name=entry.title,
    )

    button = AddToDashboardButton(entry)
    button.hass = hass

    with patch(PN_MODULE) as mock_pn:
        await button.async_press()

    mock_pn.assert_called_once()
    call_args = mock_pn.call_args
    assert "No sensor entities found" in call_args.args[1]
    assert call_args.kwargs["notification_id"] == f"{DOMAIN}_add_to_dashboard"


# ---------------------------------------------------------------------------
# async_press — sensor entities present
# ---------------------------------------------------------------------------

async def _register_hub_with_sensors(hass, entry, sensor_entity_ids: list[str]) -> None:
    """Create the hub device and register sensor entities for it."""
    from homeassistant.helpers import device_registry as dr, entity_registry as er

    device_reg = dr.async_get(hass)
    entity_reg = er.async_get(hass)

    hub = device_reg.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, entry.entry_id)},
        name=entry.title,
    )
    for eid in sensor_entity_ids:
        platform_part = eid.split(".")[1]
        entity_reg.async_get_or_create(
            "sensor",
            DOMAIN,
            platform_part,
            config_entry=entry,
            device_id=hub.id,
            suggested_object_id=platform_part,
        )


async def test_press_notification_contains_entity_ids(hass) -> None:
    entry = _make_entry(hass)
    sensor_ids = [
        "sensor.solar_production",
        "sensor.total_miner_consumption",
    ]
    await _register_hub_with_sensors(hass, entry, sensor_ids)

    button = AddToDashboardButton(entry)
    button.hass = hass

    with patch(PN_MODULE) as mock_pn:
        await button.async_press()

    mock_pn.assert_called_once()
    message: str = mock_pn.call_args.args[1]
    assert "sensor.solar_production" in message
    assert "sensor.total_miner_consumption" in message


async def test_press_notification_contains_title(hass) -> None:
    entry = _make_entry(hass)
    await _register_hub_with_sensors(hass, entry, ["sensor.solar_production"])

    button = AddToDashboardButton(entry)
    button.hass = hass

    with patch(PN_MODULE) as mock_pn:
        await button.async_press()

    message: str = mock_pn.call_args.args[1]
    assert "My Miner Hub" in message


async def test_press_notification_is_valid_yaml_card(hass) -> None:
    entry = _make_entry(hass)
    await _register_hub_with_sensors(hass, entry, ["sensor.solar_production"])

    button = AddToDashboardButton(entry)
    button.hass = hass

    with patch(PN_MODULE) as mock_pn:
        await button.async_press()

    message: str = mock_pn.call_args.args[1]
    assert "type: entities" in message
    assert "```yaml" in message


async def test_press_uses_stable_notification_id(hass) -> None:
    entry = _make_entry(hass)
    await _register_hub_with_sensors(hass, entry, ["sensor.solar_production"])

    button = AddToDashboardButton(entry)
    button.hass = hass

    with patch(PN_MODULE) as mock_pn:
        await button.async_press()

    assert mock_pn.call_args.kwargs["notification_id"] == f"{DOMAIN}_add_to_dashboard"


async def test_press_excludes_non_sensor_entities(hass) -> None:
    """The button itself (domain=button) must not appear in the card YAML."""
    from homeassistant.helpers import device_registry as dr, entity_registry as er

    entry = _make_entry(hass)
    device_reg = dr.async_get(hass)
    entity_reg = er.async_get(hass)

    hub = device_reg.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, entry.entry_id)},
        name=entry.title,
    )
    entity_reg.async_get_or_create(
        "sensor", DOMAIN, "solar_production",
        config_entry=entry, device_id=hub.id,
        suggested_object_id="solar_production",
    )
    # Register the button entity itself
    entity_reg.async_get_or_create(
        "button", DOMAIN, "add_to_dashboard",
        config_entry=entry, device_id=hub.id,
        suggested_object_id="add_to_dashboard",
    )

    button = AddToDashboardButton(entry)
    button.hass = hass

    with patch(PN_MODULE) as mock_pn:
        await button.async_press()

    message: str = mock_pn.call_args.args[1]
    assert "button." not in message
    assert "sensor.solar_production" in message
