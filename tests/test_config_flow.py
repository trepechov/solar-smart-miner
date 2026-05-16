"""Tests for the Solar Smart Miner config flow."""
from __future__ import annotations

from typing import Any

from homeassistant.config_entries import SOURCE_USER
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.solar_smart_miner.config_flow import (
    CONF_BATTERY_ENTITY,
    CONF_BATTERY_FLOOR,
    CONF_DRY_RUN,
    CONF_GRID_ENTITY,
    CONF_MINER_IP,
    CONF_MINER_NAME,
    CONF_MINERS,
    CONF_OPENROUTER_KEY,
    CONF_OPENROUTER_MODEL,
    CONF_POLLING_INTERVAL,
    CONF_PROFILE,
    CONF_SOLAR_ENTITY,
    CONF_TEMP_CEILING,
    DEFAULT_OPENROUTER_MODEL,
)
from custom_components.solar_smart_miner.const import (
    DEFAULT_BATTERY_FLOOR,
    DEFAULT_POLLING_INTERVAL,
    DEFAULT_PROFILE,
    DEFAULT_TEMP_CEILING,
    DOMAIN,
)

SOLAR_ENTITY = "sensor.solar_power"
GRID_ENTITY = "sensor.grid_consumption"
API_KEY = "sk-or-test-key"
MINER_IP = "192.168.1.100"
MINER_NAME = "ASIC 1"


def _mock_solar_state(hass: HomeAssistant) -> None:
    hass.states.async_set(SOLAR_ENTITY, "1500")
    hass.states.async_set(GRID_ENTITY, "200")


async def _complete_config_flow(
    hass: HomeAssistant,
    *,
    battery_entity: str = "",
    temp_ceiling: int = DEFAULT_TEMP_CEILING,
    battery_floor: int = DEFAULT_BATTERY_FLOOR,
    profile: str = DEFAULT_PROFILE,
    polling_interval: int = DEFAULT_POLLING_INTERVAL,
) -> dict:
    """Drive through all four steps of the config flow and return the final result."""
    _mock_solar_state(hass)

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "user"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_SOLAR_ENTITY: SOLAR_ENTITY,
            CONF_GRID_ENTITY: GRID_ENTITY,
            CONF_OPENROUTER_KEY: API_KEY,
            CONF_OPENROUTER_MODEL: DEFAULT_OPENROUTER_MODEL,
        },
    )
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "optional_sensors"

    step2_input: dict[str, Any] = {}
    if battery_entity:
        step2_input[CONF_BATTERY_ENTITY] = battery_entity
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        step2_input,
    )
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "safety_thresholds"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_TEMP_CEILING: temp_ceiling, CONF_BATTERY_FLOOR: battery_floor},
    )
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "runtime_settings"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_PROFILE: profile, CONF_POLLING_INTERVAL: polling_interval},
    )
    return result


# ---------------------------------------------------------------------------
# ConfigFlow — happy paths
# ---------------------------------------------------------------------------


async def test_complete_flow_creates_entry(hass: HomeAssistant) -> None:
    """Happy path: full flow produces a config entry with correct data structure."""
    result = await _complete_config_flow(hass)

    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["title"] == "Solar Smart Miner"

    data = result["data"]
    assert data[CONF_SOLAR_ENTITY] == SOLAR_ENTITY
    assert data[CONF_GRID_ENTITY] == GRID_ENTITY
    assert data[CONF_OPENROUTER_KEY] == API_KEY
    assert data[CONF_OPENROUTER_MODEL] == DEFAULT_OPENROUTER_MODEL
    assert data[CONF_MINERS] == []
    assert data[CONF_BATTERY_ENTITY] is None

    options = result["options"]
    assert options[CONF_PROFILE] == DEFAULT_PROFILE
    assert options[CONF_POLLING_INTERVAL] == DEFAULT_POLLING_INTERVAL
    assert options[CONF_TEMP_CEILING] == DEFAULT_TEMP_CEILING
    assert options[CONF_BATTERY_FLOOR] == DEFAULT_BATTERY_FLOOR
    assert options[CONF_DRY_RUN] is False


async def test_battery_entity_blank_is_none(hass: HomeAssistant) -> None:
    """Happy path: step 2 battery SOC left blank → battery_soc_entity is None."""
    result = await _complete_config_flow(hass, battery_entity="")
    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_BATTERY_ENTITY] is None


async def test_battery_entity_provided(hass: HomeAssistant) -> None:
    """Happy path: battery SOC entity is stored when provided."""
    hass.states.async_set("sensor.battery_soc", "80")
    _mock_solar_state(hass)

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_SOLAR_ENTITY: SOLAR_ENTITY,
            CONF_GRID_ENTITY: GRID_ENTITY,
            CONF_OPENROUTER_KEY: API_KEY,
            CONF_OPENROUTER_MODEL: DEFAULT_OPENROUTER_MODEL,
        },
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_BATTERY_ENTITY: "sensor.battery_soc"},
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_TEMP_CEILING: DEFAULT_TEMP_CEILING, CONF_BATTERY_FLOOR: DEFAULT_BATTERY_FLOOR},
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_PROFILE: DEFAULT_PROFILE, CONF_POLLING_INTERVAL: DEFAULT_POLLING_INTERVAL},
    )
    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_BATTERY_ENTITY] == "sensor.battery_soc"


# ---------------------------------------------------------------------------
# ConfigFlow — edge cases / error paths
# ---------------------------------------------------------------------------


async def test_solar_entity_not_found_shows_error(hass: HomeAssistant) -> None:
    """Edge case: solar entity does not exist in HA state → validation error."""
    _mock_solar_state(hass)

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_SOLAR_ENTITY: "sensor.nonexistent_solar",
            CONF_GRID_ENTITY: GRID_ENTITY,
            CONF_OPENROUTER_KEY: API_KEY,
            CONF_OPENROUTER_MODEL: DEFAULT_OPENROUTER_MODEL,
        },
    )
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "user"
    assert "solar_production_entity" in result["errors"]
    assert result["errors"]["solar_production_entity"] == "solar_entity_not_found"


async def test_grid_entity_not_found_shows_error(hass: HomeAssistant) -> None:
    """Edge case: grid entity does not exist in HA state → validation error."""
    _mock_solar_state(hass)

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_SOLAR_ENTITY: SOLAR_ENTITY,
            CONF_GRID_ENTITY: "sensor.nonexistent_grid",
            CONF_OPENROUTER_KEY: API_KEY,
            CONF_OPENROUTER_MODEL: DEFAULT_OPENROUTER_MODEL,
        },
    )
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "user"
    assert "grid_consumption_entity" in result["errors"]
    assert result["errors"]["grid_consumption_entity"] == "grid_entity_not_found"


async def test_already_configured_aborts(hass: HomeAssistant) -> None:
    """Edge case: same solar + grid pair configured twice → already_configured."""
    _mock_solar_state(hass)

    result = await _complete_config_flow(hass)
    assert result["type"] == FlowResultType.CREATE_ENTRY

    result2 = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    result2 = await hass.config_entries.flow.async_configure(
        result2["flow_id"],
        {
            CONF_SOLAR_ENTITY: SOLAR_ENTITY,
            CONF_GRID_ENTITY: GRID_ENTITY,
            CONF_OPENROUTER_KEY: "other-key",
            CONF_OPENROUTER_MODEL: DEFAULT_OPENROUTER_MODEL,
        },
    )
    assert result2["type"] == FlowResultType.ABORT
    assert result2["reason"] == "already_configured"


async def test_unique_id_is_stable(hass: HomeAssistant) -> None:
    """The same entity pair always produces the same unique ID."""
    from custom_components.solar_smart_miner.config_flow import _stable_unique_id

    uid1 = _stable_unique_id(SOLAR_ENTITY, GRID_ENTITY)
    uid2 = _stable_unique_id(SOLAR_ENTITY, GRID_ENTITY)
    assert uid1 == uid2
    assert len(uid1) == 16

    uid_different = _stable_unique_id(SOLAR_ENTITY, "sensor.other_grid")
    assert uid1 != uid_different


# ---------------------------------------------------------------------------
# OptionsFlow — happy paths
# ---------------------------------------------------------------------------


async def _get_options_flow_result(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    options_input: dict,
    miner_input: dict | None = None,
) -> dict:
    """Drive the OptionsFlow init step and optional add_miner step."""
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "init"

    result = await hass.config_entries.options.async_configure(
        result["flow_id"], options_input
    )

    if miner_input is not None:
        assert result["type"] == FlowResultType.FORM
        assert result["step_id"] == "add_miner"
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], miner_input
        )

    return result


def _make_entry(hass: HomeAssistant, miners: list | None = None) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_SOLAR_ENTITY: SOLAR_ENTITY,
            CONF_GRID_ENTITY: GRID_ENTITY,
            CONF_OPENROUTER_KEY: API_KEY,
            CONF_OPENROUTER_MODEL: DEFAULT_OPENROUTER_MODEL,
            CONF_BATTERY_ENTITY: None,
            CONF_MINERS: miners or [],
        },
        options={
            CONF_DRY_RUN: False,
            CONF_PROFILE: DEFAULT_PROFILE,
            CONF_POLLING_INTERVAL: DEFAULT_POLLING_INTERVAL,
            CONF_TEMP_CEILING: DEFAULT_TEMP_CEILING,
            CONF_BATTERY_FLOOR: DEFAULT_BATTERY_FLOOR,
        },
        version=1,
    )
    entry.add_to_hass(hass)
    return entry


async def test_options_flow_sets_dry_run(hass: HomeAssistant) -> None:
    """Happy path: OptionsFlow sets dry_run=True; option updated."""
    entry = _make_entry(hass)

    result = await _get_options_flow_result(
        hass,
        entry,
        {
            CONF_DRY_RUN: True,
            CONF_PROFILE: DEFAULT_PROFILE,
            CONF_POLLING_INTERVAL: DEFAULT_POLLING_INTERVAL,
            CONF_TEMP_CEILING: DEFAULT_TEMP_CEILING,
            CONF_BATTERY_FLOOR: DEFAULT_BATTERY_FLOOR,
        },
        miner_input={CONF_MINER_NAME: "", CONF_MINER_IP: ""},
    )
    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_DRY_RUN] is True


async def test_options_flow_changes_profile(hass: HomeAssistant) -> None:
    """Happy path: OptionsFlow changes profile; takes effect on next poll."""
    entry = _make_entry(hass)

    result = await _get_options_flow_result(
        hass,
        entry,
        {
            CONF_DRY_RUN: False,
            CONF_PROFILE: "battery_focused",
            CONF_POLLING_INTERVAL: DEFAULT_POLLING_INTERVAL,
            CONF_TEMP_CEILING: DEFAULT_TEMP_CEILING,
            CONF_BATTERY_FLOOR: DEFAULT_BATTERY_FLOOR,
        },
        miner_input={CONF_MINER_NAME: "", CONF_MINER_IP: ""},
    )
    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_PROFILE] == "battery_focused"


async def test_options_flow_add_miner_stores_in_data(hass: HomeAssistant) -> None:
    """Happy path: add miner via OptionsFlow → stored in config entry data."""
    entry = _make_entry(hass)

    result = await _get_options_flow_result(
        hass,
        entry,
        {
            CONF_DRY_RUN: False,
            CONF_PROFILE: DEFAULT_PROFILE,
            CONF_POLLING_INTERVAL: DEFAULT_POLLING_INTERVAL,
            CONF_TEMP_CEILING: DEFAULT_TEMP_CEILING,
            CONF_BATTERY_FLOOR: DEFAULT_BATTERY_FLOOR,
        },
        miner_input={CONF_MINER_NAME: MINER_NAME, CONF_MINER_IP: MINER_IP},
    )
    assert result["type"] == FlowResultType.CREATE_ENTRY
    miners = entry.data[CONF_MINERS]
    assert len(miners) == 1
    assert miners[0][CONF_MINER_NAME] == MINER_NAME
    assert miners[0][CONF_MINER_IP] == MINER_IP


async def test_options_flow_add_miner_invalid_ip(hass: HomeAssistant) -> None:
    """Edge case: invalid IP in add_miner step → error, no miner stored."""
    entry = _make_entry(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            CONF_DRY_RUN: False,
            CONF_PROFILE: DEFAULT_PROFILE,
            CONF_POLLING_INTERVAL: DEFAULT_POLLING_INTERVAL,
            CONF_TEMP_CEILING: DEFAULT_TEMP_CEILING,
            CONF_BATTERY_FLOOR: DEFAULT_BATTERY_FLOOR,
        },
    )
    assert result["step_id"] == "add_miner"

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {CONF_MINER_NAME: MINER_NAME, CONF_MINER_IP: "not-an-ip"},
    )
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "add_miner"
    assert "miner_ip" in result["errors"]
    assert result["errors"]["miner_ip"] == "invalid_ip"
    assert entry.data[CONF_MINERS] == []


async def test_options_flow_skip_miner_add(hass: HomeAssistant) -> None:
    """Happy path: leaving both miner fields blank saves without adding a miner."""
    entry = _make_entry(hass)

    result = await _get_options_flow_result(
        hass,
        entry,
        {
            CONF_DRY_RUN: False,
            CONF_PROFILE: DEFAULT_PROFILE,
            CONF_POLLING_INTERVAL: DEFAULT_POLLING_INTERVAL,
            CONF_TEMP_CEILING: DEFAULT_TEMP_CEILING,
            CONF_BATTERY_FLOOR: DEFAULT_BATTERY_FLOOR,
        },
        miner_input={CONF_MINER_NAME: "", CONF_MINER_IP: ""},
    )
    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert entry.data[CONF_MINERS] == []


async def test_options_flow_telegram_credentials_stored(hass: HomeAssistant) -> None:
    """Happy path: Telegram credentials provided → stored in options."""
    entry = _make_entry(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            CONF_DRY_RUN: False,
            CONF_PROFILE: DEFAULT_PROFILE,
            CONF_POLLING_INTERVAL: DEFAULT_POLLING_INTERVAL,
            CONF_TEMP_CEILING: DEFAULT_TEMP_CEILING,
            CONF_BATTERY_FLOOR: DEFAULT_BATTERY_FLOOR,
            "telegram_bot_token": "bot123:ABC",
            "telegram_chat_id": "-100123456",
        },
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {CONF_MINER_NAME: "", CONF_MINER_IP: ""},
    )
    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["data"]["telegram_bot_token"] == "bot123:ABC"
    assert result["data"]["telegram_chat_id"] == "-100123456"


async def test_options_flow_telegram_blank_stored_as_none(hass: HomeAssistant) -> None:
    """Edge case: empty Telegram fields → stored as None, not empty string."""
    entry = _make_entry(hass)

    result = await _get_options_flow_result(
        hass,
        entry,
        {
            CONF_DRY_RUN: False,
            CONF_PROFILE: DEFAULT_PROFILE,
            CONF_POLLING_INTERVAL: DEFAULT_POLLING_INTERVAL,
            CONF_TEMP_CEILING: DEFAULT_TEMP_CEILING,
            CONF_BATTERY_FLOOR: DEFAULT_BATTERY_FLOOR,
            "telegram_bot_token": "",
            "telegram_chat_id": "",
        },
        miner_input={CONF_MINER_NAME: "", CONF_MINER_IP: ""},
    )
    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["data"].get("telegram_bot_token") is None
    assert result["data"].get("telegram_chat_id") is None
