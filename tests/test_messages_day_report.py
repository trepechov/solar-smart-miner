"""The day report in Home Assistant: the action, the button, the sensors (plan 2026-10-15-002 U6)."""
from __future__ import annotations

import pytest
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import entity_registry as er_module
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.solar_smart_miner.config_flow import (
    CONF_BATTERY_ENTITY,
    CONF_GRID_ENTITY,
    CONF_SOLAR_ENTITY,
)
from custom_components.solar_smart_miner.const import DOMAIN
from custom_components.solar_smart_miner.messages.day_report import TEST_LINE

SOLAR = "sensor.solar_power"
GRID = "sensor.house_power"
TELEGRAM = {"telegram_bot_token": "1:T", "telegram_chat_id": "42"}


def _kept_day(date: str, solar: float, forecast: float | None) -> dict:
    report = {
        "date": date, "day_label": "Monday 12 October 2026",
        "totals": {"solar": solar, "miners": 0.0, "import": 1.0, "export": 0.0, "house": solar + 1.0,
                   "miners_solar": 0.0, "miners_grid": 0.0, "house_grid": 1.0, "house_solar": solar},
        "solar_known": True, "solar_share_pct": None, "self_use_pct": 100, "light": None, "best_slice": None,
        "grid_topup": None, "miners": [], "hashes": 0.0, "j_per_th": None,
        "commands": {"total": 0, "start": 0, "stop": 0, "limit": 0}, "forecast_kwh": forecast, "incomplete": False,
    }
    return {"date": date, "report": report, "reported": True}


async def _setup(hass, options: dict | None = None) -> MockConfigEntry:
    hass.states.async_set(SOLAR, "0")
    hass.states.async_set(GRID, "400")
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_SOLAR_ENTITY: SOLAR, CONF_GRID_ENTITY: GRID, CONF_BATTERY_ENTITY: None},
        options={**TELEGRAM, **(options or {})},
    )
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


def _entity_id(hass, domain: str, suffix: str) -> str:
    return next(
        e.entity_id for e in er_module.async_get(hass).entities.values()
        if e.platform == DOMAIN and e.domain == domain and e.unique_id.endswith(suffix)
    )


async def test_the_action_sends_a_kept_day(hass, mock_telegram) -> None:
    entry = await _setup(hass)
    entry.runtime_data.day_report.state["kept_days"] = [_kept_day("2026-10-12", 42.1, 31.0)]

    await hass.services.async_call(DOMAIN, "send_day_report", {"date": "2026-10-12"}, blocking=True)
    await hass.async_block_till_done(wait_background_tasks=True)

    text = mock_telegram.await_args.args[3]
    assert text.startswith("📊 Farm day: Monday 12 October 2026") and "42.1 kWh" in text


async def test_the_action_refuses_a_day_it_doesnt_keep(hass, mock_telegram) -> None:
    await _setup(hass)

    with pytest.raises(ServiceValidationError, match="keeps the last 7 days"):
        await hass.services.async_call(DOMAIN, "send_day_report", {"date": "2026-09-01"}, blocking=True)
    mock_telegram.assert_not_awaited()


async def test_the_action_needs_telegram(hass, mock_telegram) -> None:
    entry = await _setup(hass, {"telegram_bot_token": ""})
    entry.runtime_data.day_report.state["kept_days"] = [_kept_day("2026-10-12", 42.1, None)]

    with pytest.raises(ServiceValidationError, match="Telegram isn't set up"):
        await hass.services.async_call(DOMAIN, "send_day_report", {"date": "2026-10-12"}, blocking=True)


async def test_with_two_farms_the_action_asks_which(hass, mock_telegram) -> None:
    first = await _setup(hass)
    second = await _setup(hass)
    second.runtime_data.day_report.state["kept_days"] = [_kept_day("2026-10-12", 7.0, None)]

    with pytest.raises(ServiceValidationError, match="config_entry_id"):
        await hass.services.async_call(DOMAIN, "send_day_report", {"date": "2026-10-12"}, blocking=True)

    await hass.services.async_call(
        DOMAIN, "send_day_report", {"date": "2026-10-12", "config_entry_id": second.entry_id, "test": True},
        blocking=True,
    )
    await hass.async_block_till_done(wait_background_tasks=True)
    assert mock_telegram.await_args.args[3].startswith(TEST_LINE)
    assert first.entry_id != second.entry_id


async def test_the_button_sends_today_so_far_as_a_test(hass, mock_telegram) -> None:
    await _setup(hass)

    await hass.services.async_call(
        "button", "press", {"entity_id": _entity_id(hass, "button", "_send_day_report")}, blocking=True
    )
    await hass.async_block_till_done(wait_background_tasks=True)

    assert mock_telegram.await_args.args[3].startswith(TEST_LINE + "\n\n📊 Farm day: ")


async def test_the_sensors_show_the_latest_report_and_the_forecast(hass, mock_telegram) -> None:
    entry = await _setup(hass, {"telegram_day_messages": False})
    coordinator = entry.runtime_data
    coordinator.day_report.state["kept_days"] = [_kept_day("2026-10-11", 20.0, 25.0), _kept_day("2026-10-12", 42.1, 31.0)]
    coordinator.async_update_listeners()
    await hass.async_block_till_done()

    report = hass.states.get(_entity_id(hass, "sensor", "_day_report"))
    assert report.state == "2026-10-12" and "42.1 kWh" in report.attributes["text"]
    forecast = hass.states.get(_entity_id(hass, "sensor", "_forecast_accuracy"))
    assert forecast.state == "136" and forecast.attributes["forecast_kwh"] == 31.0


async def test_without_a_forecast_the_accuracy_is_unknown(hass, mock_telegram) -> None:
    entry = await _setup(hass)
    entry.runtime_data.day_report.state["kept_days"] = [_kept_day("2026-10-12", 42.1, None)]
    entry.runtime_data.async_update_listeners()
    await hass.async_block_till_done()

    assert hass.states.get(_entity_id(hass, "sensor", "_forecast_accuracy")).state == "unknown"
