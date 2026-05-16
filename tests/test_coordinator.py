"""Tests for SolarMinerCoordinator — mock solar substitution (U9)."""
from __future__ import annotations

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.solar_smart_miner.config_flow import (
    CONF_POLLING_INTERVAL,
    CONF_SOLAR_ENTITY,
)
from custom_components.solar_smart_miner.const import (
    CONF_MOCK_SOLAR_ENABLED,
    CONF_MOCK_SOLAR_ENTITY,
    DEFAULT_POLLING_INTERVAL,
    DOMAIN,
)
from custom_components.solar_smart_miner.coordinator import SolarMinerCoordinator
from custom_components.solar_smart_miner.protocols import EnergySnapshot

SOLAR_ENTITY = "sensor.solar_power"
FORECAST_ENTITY = "sensor.forecast_solar_power_production_now"


def _make_entry(hass, *, mock_enabled: bool = False, mock_entity: str | None = None):
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_SOLAR_ENTITY: SOLAR_ENTITY},
        options={
            CONF_POLLING_INTERVAL: DEFAULT_POLLING_INTERVAL,
            CONF_MOCK_SOLAR_ENABLED: mock_enabled,
            CONF_MOCK_SOLAR_ENTITY: mock_entity,
        },
        version=1,
    )
    entry.add_to_hass(hass)
    return entry


# ---------------------------------------------------------------------------
# Happy paths
# ---------------------------------------------------------------------------


async def test_coordinator_reads_real_solar_when_mock_disabled(hass) -> None:
    """Happy path: mock disabled → reads real solar entity."""
    hass.states.async_set(SOLAR_ENTITY, "2000")
    entry = _make_entry(hass, mock_enabled=False)
    coord = SolarMinerCoordinator(hass, entry)

    snapshot = await coord._async_update_data()

    assert isinstance(snapshot, EnergySnapshot)
    assert snapshot.solar_production_w == pytest.approx(2000.0)
    assert snapshot.mock_solar is False


async def test_coordinator_reads_mock_solar_when_enabled(hass) -> None:
    """Happy path: mock enabled + entity set → uses Forecast.Solar value, mock_solar=True."""
    hass.states.async_set(SOLAR_ENTITY, "2000")
    hass.states.async_set(FORECAST_ENTITY, "3500")
    entry = _make_entry(hass, mock_enabled=True, mock_entity=FORECAST_ENTITY)
    coord = SolarMinerCoordinator(hass, entry)

    snapshot = await coord._async_update_data()

    assert snapshot.solar_production_w == pytest.approx(3500.0)
    assert snapshot.mock_solar is True


# ---------------------------------------------------------------------------
# Edge / fallback cases
# ---------------------------------------------------------------------------


async def test_coordinator_falls_back_when_mock_entity_unavailable(hass) -> None:
    """Edge: mock enabled but Forecast.Solar entity is unavailable → falls back to real solar."""
    hass.states.async_set(SOLAR_ENTITY, "1800")
    hass.states.async_set(FORECAST_ENTITY, "unavailable")
    entry = _make_entry(hass, mock_enabled=True, mock_entity=FORECAST_ENTITY)
    coord = SolarMinerCoordinator(hass, entry)

    snapshot = await coord._async_update_data()

    assert snapshot.solar_production_w == pytest.approx(1800.0)
    assert snapshot.mock_solar is False


async def test_coordinator_falls_back_when_mock_entity_not_configured(hass) -> None:
    """Edge: mock enabled but entity is None → falls back to real solar."""
    hass.states.async_set(SOLAR_ENTITY, "1200")
    entry = _make_entry(hass, mock_enabled=True, mock_entity=None)
    coord = SolarMinerCoordinator(hass, entry)

    snapshot = await coord._async_update_data()

    assert snapshot.solar_production_w == pytest.approx(1200.0)
    assert snapshot.mock_solar is False


async def test_coordinator_raises_when_real_solar_unavailable(hass) -> None:
    """Error path: mock disabled and real solar entity unavailable → UpdateFailed raised."""
    from homeassistant.helpers.update_coordinator import UpdateFailed

    hass.states.async_set(SOLAR_ENTITY, "unavailable")
    entry = _make_entry(hass, mock_enabled=False)
    coord = SolarMinerCoordinator(hass, entry)

    with pytest.raises(UpdateFailed):
        await coord._async_update_data()
