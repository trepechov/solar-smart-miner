"""Tests for the Solar Smart Miner config flow."""
from __future__ import annotations

from typing import Any

from homeassistant.config_entries import SOURCE_USER
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.solar_smart_miner.config_flow import (
    CONF_AI_ENABLED,
    CONF_AI_INTERVAL,
    CONF_BATTERY_ENTITY,
    CONF_BATTERY_FLOOR,
    CONF_DRY_RUN,
    CONF_GRID_ENTITY,
    CONF_OPENROUTER_KEY,
    CONF_OPENROUTER_MODEL,
    CONF_POLLING_INTERVAL,
    CONF_PROFILE,
    CONF_SOLAR_ENTITY,
    CONF_SOLAR_ENTITY_TYPE,
    CONF_TEMP_CEILING,
    DEFAULT_OPENROUTER_MODEL,
)
from custom_components.solar_smart_miner.const import (
    CONF_MOCK_SOLAR_ENABLED,
    CONF_MOCK_SOLAR_ENTITY,
    DEFAULT_BATTERY_FLOOR,
    DEFAULT_POLLING_INTERVAL,
    DEFAULT_PROFILE,
    DEFAULT_TEMP_CEILING,
    DOMAIN,
    SOLAR_ENTITY_TYPE_NET_EXPORT,
    SOLAR_ENTITY_TYPE_PRODUCTION,
)

SOLAR_ENTITY = "sensor.solar_power"
GRID_ENTITY = "sensor.grid_consumption"
API_KEY = "sk-or-test-key"
NET_METER_ENTITY = "sensor.grid_net_power"
HOUSE_ENTITY = "sensor.house_consumption"
BATTERY_ENTITY = "sensor.battery_soc"


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
    assert data[CONF_SOLAR_ENTITY_TYPE] == SOLAR_ENTITY_TYPE_PRODUCTION
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
    options_input: dict | None = None,
    sensors_input: dict | None = None,
    ai_input: dict | None = None,
) -> dict:
    """Drive the OptionsFlow through the menu and return the final result.

    Pass options_input to navigate the "edit_settings" path.
    Pass sensors_input to navigate the "edit_sensors" path.
    Pass ai_input to navigate the "edit_ai" path.
    """
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] == FlowResultType.MENU
    assert result["step_id"] == "init"

    if sensors_input is not None:
        step_id, user_input = "edit_sensors", sensors_input
    elif ai_input is not None:
        step_id, user_input = "edit_ai", ai_input
    else:
        step_id, user_input = "edit_settings", options_input
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": step_id}
    )
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == step_id
    return await hass.config_entries.options.async_configure(result["flow_id"], user_input)


def _make_entry(hass: HomeAssistant) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_SOLAR_ENTITY: SOLAR_ENTITY,
            CONF_GRID_ENTITY: GRID_ENTITY,
            CONF_OPENROUTER_KEY: API_KEY,
            CONF_OPENROUTER_MODEL: DEFAULT_OPENROUTER_MODEL,
            CONF_BATTERY_ENTITY: None,
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
    """Happy path: OptionsFlow sets dry_run=True via edit_settings."""
    entry = _make_entry(hass)

    result = await _get_options_flow_result(
        hass,
        entry,
        options_input={
            CONF_DRY_RUN: True,
            CONF_PROFILE: DEFAULT_PROFILE,
            CONF_POLLING_INTERVAL: DEFAULT_POLLING_INTERVAL,
            CONF_TEMP_CEILING: DEFAULT_TEMP_CEILING,
            CONF_BATTERY_FLOOR: DEFAULT_BATTERY_FLOOR,
        },
    )
    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_DRY_RUN] is True


async def test_options_flow_changes_profile(hass: HomeAssistant) -> None:
    """Happy path: OptionsFlow changes profile via edit_settings."""
    entry = _make_entry(hass)

    result = await _get_options_flow_result(
        hass,
        entry,
        options_input={
            CONF_DRY_RUN: False,
            CONF_PROFILE: "battery_focused",
            CONF_POLLING_INTERVAL: DEFAULT_POLLING_INTERVAL,
            CONF_TEMP_CEILING: DEFAULT_TEMP_CEILING,
            CONF_BATTERY_FLOOR: DEFAULT_BATTERY_FLOOR,
        },
    )
    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_PROFILE] == "battery_focused"


def _sensors_input(**overrides) -> dict:
    return {
        CONF_SOLAR_ENTITY: NET_METER_ENTITY,
        CONF_SOLAR_ENTITY_TYPE: SOLAR_ENTITY_TYPE_NET_EXPORT,
        CONF_GRID_ENTITY: HOUSE_ENTITY,
        CONF_BATTERY_ENTITY: BATTERY_ENTITY,
        **overrides,
    }


def _ai_input(**overrides) -> dict:
    return {
        CONF_AI_ENABLED: True,
        CONF_OPENROUTER_KEY: "sk-or-new-key",
        CONF_OPENROUTER_MODEL: "test/free:free",
        CONF_AI_INTERVAL: 600,
        **overrides,
    }


async def test_options_flow_edit_sensors_updates_entry_data(hass: HomeAssistant) -> None:
    """Entities chosen at setup can be changed afterwards."""
    entry = _make_entry(hass)
    for eid in (NET_METER_ENTITY, HOUSE_ENTITY, BATTERY_ENTITY):
        hass.states.async_set(eid, "100")

    result = await _get_options_flow_result(hass, entry, sensors_input=_sensors_input())

    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert entry.data[CONF_SOLAR_ENTITY] == NET_METER_ENTITY
    assert entry.data[CONF_SOLAR_ENTITY_TYPE] == SOLAR_ENTITY_TYPE_NET_EXPORT
    assert entry.data[CONF_GRID_ENTITY] == HOUSE_ENTITY
    assert entry.data[CONF_BATTERY_ENTITY] == BATTERY_ENTITY
    # Credentials and runtime options are left untouched.
    assert entry.data[CONF_OPENROUTER_KEY] == API_KEY
    assert entry.options[CONF_PROFILE] == DEFAULT_PROFILE


async def test_options_flow_edit_sensors_can_clear_battery(hass: HomeAssistant) -> None:
    entry = _make_entry(hass)
    hass.config_entries.async_update_entry(
        entry, data={**entry.data, CONF_BATTERY_ENTITY: BATTERY_ENTITY}
    )
    for eid in (NET_METER_ENTITY, HOUSE_ENTITY):
        hass.states.async_set(eid, "100")
    sensors_input = _sensors_input()
    del sensors_input[CONF_BATTERY_ENTITY]

    await _get_options_flow_result(hass, entry, sensors_input=sensors_input)

    assert entry.data[CONF_BATTERY_ENTITY] is None


async def test_options_flow_edit_sensors_rejects_missing_entity(hass: HomeAssistant) -> None:
    entry = _make_entry(hass)
    hass.states.async_set(HOUSE_ENTITY, "100")  # net meter entity does not exist

    result = await _get_options_flow_result(hass, entry, sensors_input=_sensors_input())

    assert result["type"] == FlowResultType.FORM
    assert result["errors"] == {CONF_SOLAR_ENTITY: "solar_entity_not_found"}
    assert entry.data[CONF_SOLAR_ENTITY] == SOLAR_ENTITY


async def test_options_flow_accepts_one_second_polling(hass: HomeAssistant) -> None:
    entry = _make_entry(hass)

    result = await _get_options_flow_result(
        hass,
        entry,
        options_input={
            CONF_DRY_RUN: False,
            CONF_PROFILE: DEFAULT_PROFILE,
            CONF_POLLING_INTERVAL: 1,
            CONF_TEMP_CEILING: DEFAULT_TEMP_CEILING,
            CONF_BATTERY_FLOOR: DEFAULT_BATTERY_FLOOR,
        },
    )

    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_POLLING_INTERVAL] == 1


async def test_options_flow_telegram_credentials_stored(hass: HomeAssistant) -> None:
    """Happy path: Telegram credentials provided → stored in options."""
    entry = _make_entry(hass)

    result = await _get_options_flow_result(
        hass,
        entry,
        options_input={
            CONF_DRY_RUN: False,
            CONF_PROFILE: DEFAULT_PROFILE,
            CONF_POLLING_INTERVAL: DEFAULT_POLLING_INTERVAL,
            CONF_TEMP_CEILING: DEFAULT_TEMP_CEILING,
            CONF_BATTERY_FLOOR: DEFAULT_BATTERY_FLOOR,
            "telegram_bot_token": "bot123:ABC",
            "telegram_chat_id": "-100123456",
        },
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
        options_input={
            CONF_DRY_RUN: False,
            CONF_PROFILE: DEFAULT_PROFILE,
            CONF_POLLING_INTERVAL: DEFAULT_POLLING_INTERVAL,
            CONF_TEMP_CEILING: DEFAULT_TEMP_CEILING,
            CONF_BATTERY_FLOOR: DEFAULT_BATTERY_FLOOR,
            "telegram_bot_token": "",
            "telegram_chat_id": "",
        },
    )
    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["data"].get("telegram_bot_token") is None
    assert result["data"].get("telegram_chat_id") is None


# ---------------------------------------------------------------------------
# OptionsFlow — mock solar (U9)
# ---------------------------------------------------------------------------

FORECAST_SOLAR_ENTITY = "sensor.forecast_solar_power_production_now"


async def test_options_flow_mock_solar_enabled_stores_entity(hass: HomeAssistant) -> None:
    """Happy path: mock solar enabled with entity → stored correctly in options."""
    entry = _make_entry(hass)
    hass.states.async_set(FORECAST_SOLAR_ENTITY, "3500")

    result = await _get_options_flow_result(
        hass,
        entry,
        options_input={
            CONF_DRY_RUN: False,
            CONF_PROFILE: DEFAULT_PROFILE,
            CONF_POLLING_INTERVAL: DEFAULT_POLLING_INTERVAL,
            CONF_TEMP_CEILING: DEFAULT_TEMP_CEILING,
            CONF_BATTERY_FLOOR: DEFAULT_BATTERY_FLOOR,
            CONF_MOCK_SOLAR_ENABLED: True,
            CONF_MOCK_SOLAR_ENTITY: FORECAST_SOLAR_ENTITY,
        },
    )
    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_MOCK_SOLAR_ENABLED] is True
    assert result["data"][CONF_MOCK_SOLAR_ENTITY] == FORECAST_SOLAR_ENTITY


async def test_options_flow_mock_solar_disabled_by_default(hass: HomeAssistant) -> None:
    """Happy path: mock solar not submitted → defaults to disabled, entity stored as None."""
    entry = _make_entry(hass)

    result = await _get_options_flow_result(
        hass,
        entry,
        options_input={
            CONF_DRY_RUN: False,
            CONF_PROFILE: DEFAULT_PROFILE,
            CONF_POLLING_INTERVAL: DEFAULT_POLLING_INTERVAL,
            CONF_TEMP_CEILING: DEFAULT_TEMP_CEILING,
            CONF_BATTERY_FLOOR: DEFAULT_BATTERY_FLOOR,
        },
    )
    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["data"].get(CONF_MOCK_SOLAR_ENABLED) is False
    assert result["data"].get(CONF_MOCK_SOLAR_ENTITY) is None


async def test_options_flow_mock_solar_no_entity_selected_stored_as_none(hass: HomeAssistant) -> None:
    """Edge case: mock solar enabled but no entity selected → entity stored as None."""
    entry = _make_entry(hass)

    # Omitting CONF_MOCK_SOLAR_ENTITY simulates the user not selecting an entity in the HA UI.
    # EntitySelector rejects empty strings, so real users can only submit a valid entity ID or nothing.
    result = await _get_options_flow_result(
        hass,
        entry,
        options_input={
            CONF_DRY_RUN: False,
            CONF_PROFILE: DEFAULT_PROFILE,
            CONF_POLLING_INTERVAL: DEFAULT_POLLING_INTERVAL,
            CONF_TEMP_CEILING: DEFAULT_TEMP_CEILING,
            CONF_BATTERY_FLOOR: DEFAULT_BATTERY_FLOOR,
            CONF_MOCK_SOLAR_ENABLED: True,
        },
    )
    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_MOCK_SOLAR_ENABLED] is True
    assert result["data"].get(CONF_MOCK_SOLAR_ENTITY) is None


async def test_house_entity_is_optional_in_setup(hass: HomeAssistant) -> None:
    """A net grid meter alone is enough; no house consumption sensor needed."""
    hass.states.async_set(SOLAR_ENTITY, "1500")
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_SOLAR_ENTITY: SOLAR_ENTITY,
            CONF_SOLAR_ENTITY_TYPE: SOLAR_ENTITY_TYPE_NET_EXPORT,
            CONF_OPENROUTER_KEY: API_KEY,
            CONF_OPENROUTER_MODEL: DEFAULT_OPENROUTER_MODEL,
        },
    )

    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "optional_sensors"


async def test_openrouter_key_is_optional_in_setup(hass: HomeAssistant) -> None:
    """The controller works rule-based without an AI key."""
    hass.states.async_set(SOLAR_ENTITY, "1500")
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_SOLAR_ENTITY: SOLAR_ENTITY,
            CONF_SOLAR_ENTITY_TYPE: SOLAR_ENTITY_TYPE_PRODUCTION,
            CONF_OPENROUTER_MODEL: DEFAULT_OPENROUTER_MODEL,
        },
    )

    assert result["step_id"] == "optional_sensors"


async def test_options_flow_edit_sensors_can_clear_house_entity(hass: HomeAssistant) -> None:
    entry = _make_entry(hass)
    hass.states.async_set(NET_METER_ENTITY, "100")
    sensors_input = _sensors_input()
    del sensors_input[CONF_GRID_ENTITY]

    result = await _get_options_flow_result(hass, entry, sensors_input=sensors_input)

    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert entry.data[CONF_GRID_ENTITY] is None


async def test_options_flow_edit_ai_updates_key_model_and_schedule(hass: HomeAssistant) -> None:
    entry = _make_entry(hass)

    result = await _get_options_flow_result(hass, entry, ai_input=_ai_input())

    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert entry.data[CONF_OPENROUTER_KEY] == "sk-or-new-key"
    assert entry.data[CONF_OPENROUTER_MODEL] == "test/free:free"
    assert entry.options[CONF_AI_ENABLED] is True
    assert entry.options[CONF_AI_INTERVAL] == 600
    # Other runtime options survive.
    assert entry.options[CONF_PROFILE] == DEFAULT_PROFILE
    assert entry.options[CONF_POLLING_INTERVAL] == DEFAULT_POLLING_INTERVAL


async def test_options_flow_edit_ai_blank_key_clears_it(hass: HomeAssistant) -> None:
    entry = _make_entry(hass)
    ai_input = _ai_input()
    del ai_input[CONF_OPENROUTER_KEY]

    await _get_options_flow_result(hass, entry, ai_input=ai_input)

    assert entry.data[CONF_OPENROUTER_KEY] == ""


async def test_options_flow_edit_ai_accepts_any_model_id(hass: HomeAssistant) -> None:
    """Paid or unlisted models can be typed in; the dropdown is only a suggestion."""
    entry = _make_entry(hass)

    await _get_options_flow_result(
        hass, entry, ai_input=_ai_input(**{CONF_OPENROUTER_MODEL: "anthropic/claude-haiku-4-5"})
    )

    assert entry.data[CONF_OPENROUTER_MODEL] == "anthropic/claude-haiku-4-5"


async def test_options_flow_edit_ai_form_masks_key_and_lists_free_models(hass: HomeAssistant) -> None:
    entry = _make_entry(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "edit_ai"}
    )

    schema = {str(k): v for k, v in result["data_schema"].schema.items()}
    assert schema[CONF_OPENROUTER_KEY].config["type"] == "password"
    model_values = {o["value"] for o in schema[CONF_OPENROUTER_MODEL].config["options"]}
    assert {"openrouter/free", "test/free:free"} <= model_values


async def test_options_flow_edit_settings_keeps_ai_options(hass: HomeAssistant) -> None:
    """Saving the runtime settings must not drop options owned by another step."""
    entry = _make_entry(hass)
    hass.config_entries.async_update_entry(
        entry, options={**entry.options, CONF_AI_ENABLED: False, CONF_AI_INTERVAL: 1800}
    )

    await _get_options_flow_result(
        hass,
        entry,
        options_input={
            CONF_DRY_RUN: False,
            CONF_PROFILE: DEFAULT_PROFILE,
            CONF_POLLING_INTERVAL: 30,
            CONF_TEMP_CEILING: DEFAULT_TEMP_CEILING,
            CONF_BATTERY_FLOOR: DEFAULT_BATTERY_FLOOR,
        },
    )

    assert entry.options[CONF_POLLING_INTERVAL] == 30
    assert entry.options[CONF_AI_ENABLED] is False
    assert entry.options[CONF_AI_INTERVAL] == 1800


async def test_options_flow_edit_sensors_saves_and_clears_reference_sensors(hass: HomeAssistant) -> None:
    from custom_components.solar_smart_miner.config_flow import (
        CONF_FORECAST_NEXT_HOUR_ENTITY,
        CONF_FORECAST_NOW_ENTITY,
        CONF_FORECAST_REMAINING_ENTITY,
        CONF_PV_ENTITY,
    )

    entry = _make_entry(hass)
    refs = {
        CONF_PV_ENTITY: "sensor.pv",
        CONF_FORECAST_NOW_ENTITY: "sensor.fc_now",
        CONF_FORECAST_NEXT_HOUR_ENTITY: "sensor.fc_next",
        CONF_FORECAST_REMAINING_ENTITY: "sensor.fc_left",
    }
    for eid in (NET_METER_ENTITY, HOUSE_ENTITY, *refs.values()):
        hass.states.async_set(eid, "100")

    await _get_options_flow_result(hass, entry, sensors_input={**_sensors_input(), **refs})
    assert {k: entry.data[k] for k in refs} == refs

    await _get_options_flow_result(hass, entry, sensors_input=_sensors_input())
    assert all(entry.data[k] is None for k in refs)


# ---------------------------------------------------------------------------
# Power steps, tuning time and the miner stop method
# ---------------------------------------------------------------------------


def test_parse_power_steps() -> None:
    from custom_components.solar_smart_miner.config_flow import parse_power_steps

    assert parse_power_steps("900, 1100, 1300,1500") == [900, 1100, 1300, 1500]
    assert parse_power_steps("1500 900  1100 900") == [900, 1100, 1500]  # sorted, de-duplicated
    for bad in ("", "abc", "900, x", "50", "20000", "12.5"):
        assert parse_power_steps(bad) is None, bad


async def test_settings_save_power_steps_and_tuning_time(hass: HomeAssistant) -> None:
    from custom_components.solar_smart_miner.config_flow import CONF_POWER_STEPS, CONF_TUNING_SETTLE

    entry = _make_entry(hass)
    result = await _get_options_flow_result(
        hass,
        entry,
        options_input={
            CONF_DRY_RUN: False,
            CONF_PROFILE: DEFAULT_PROFILE,
            CONF_POLLING_INTERVAL: DEFAULT_POLLING_INTERVAL,
            CONF_TEMP_CEILING: 80,
            CONF_BATTERY_FLOOR: 20,
            CONF_POWER_STEPS: "1000, 1200 1400",
            CONF_TUNING_SETTLE: 20,
        },
    )

    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert entry.options[CONF_POWER_STEPS] == [1000, 1200, 1400]
    assert entry.options[CONF_TUNING_SETTLE] == 20


async def test_settings_default_to_the_agreed_steps(hass: HomeAssistant) -> None:
    from custom_components.solar_smart_miner.config_flow import CONF_POWER_STEPS, CONF_TUNING_SETTLE

    entry = _make_entry(hass)
    await _get_options_flow_result(hass, entry, options_input={})

    assert entry.options[CONF_POWER_STEPS] == [900, 1100, 1300, 1500]
    assert entry.options[CONF_TUNING_SETTLE] == 60


async def test_settings_reject_unusable_power_steps_and_keep_the_form(hass: HomeAssistant) -> None:
    from custom_components.solar_smart_miner.config_flow import CONF_POWER_STEPS

    entry = _make_entry(hass)
    result = await _get_options_flow_result(
        hass, entry, options_input={CONF_POWER_STEPS: "fast, faster"}
    )

    assert result["type"] == FlowResultType.FORM
    assert result["errors"] == {CONF_POWER_STEPS: "invalid_power_steps"}
    assert CONF_POWER_STEPS not in entry.options


async def _open_miners_step(hass, entry):
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert "edit_miners" in result["menu_options"]
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "edit_miners"}
    )
    assert result["step_id"] == "edit_miners"
    return result


async def test_miners_step_lists_miners_by_name_and_stores_a_relay(hass, add_hass_miner) -> None:
    from custom_components.solar_smart_miner.config_flow import (
        CONF_MINER,
        CONF_MINER_RELAYS,
        CONF_RELAY_ENTITY,
    )

    add_hass_miner("192.168.1.101", name="Brod1")
    add_hass_miner("192.168.1.102", name="Brod2")
    hass.states.async_set("switch.relay_brod2", "on")
    entry = _make_entry(hass)

    form = await _open_miners_step(hass, entry)
    choices = form["data_schema"].schema[CONF_MINER].config["options"]
    assert {(c["value"], c["label"]) for c in choices} == {
        ("192.168.1.101", "Brod1"),
        ("192.168.1.102", "Brod2"),
    }
    assert form["description_placeholders"]["current"].startswith("none")

    result = await hass.config_entries.options.async_configure(
        form["flow_id"], {CONF_MINER: "192.168.1.102", CONF_RELAY_ENTITY: "switch.relay_brod2"}
    )

    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert entry.options[CONF_MINER_RELAYS] == {"192.168.1.102": "switch.relay_brod2"}
    # Everything else in the options is kept.
    assert entry.options[CONF_PROFILE] == DEFAULT_PROFILE


async def test_miners_step_clears_a_relay_and_shows_the_current_ones(hass, add_hass_miner) -> None:
    from custom_components.solar_smart_miner.config_flow import (
        CONF_MINER,
        CONF_MINER_RELAYS,
    )

    add_hass_miner("192.168.1.101", name="Brod1")
    entry = _make_entry(hass)
    hass.config_entries.async_update_entry(
        entry,
        options={**entry.options, CONF_MINER_RELAYS: {"192.168.1.101": "switch.r1"}},
    )

    form = await _open_miners_step(hass, entry)
    assert "Brod1 → switch.r1" in form["description_placeholders"]["current"]

    await hass.config_entries.options.async_configure(form["flow_id"], {CONF_MINER: "192.168.1.101"})

    assert entry.options[CONF_MINER_RELAYS] == {}
