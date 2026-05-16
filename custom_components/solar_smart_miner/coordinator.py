"""DataUpdateCoordinator for Solar Smart Miner.

U9 stub: implements mock solar substitution via Forecast.Solar.
Full coordinator logic (all entity reads, safety layer, AI agent) lands in U3.
"""
from __future__ import annotations

import logging
from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import (
    CONF_MOCK_SOLAR_ENABLED,
    CONF_MOCK_SOLAR_ENTITY,
    DEFAULT_POLLING_INTERVAL,
    DOMAIN,
)
from .config_flow import CONF_POLLING_INTERVAL, CONF_SOLAR_ENTITY
from .protocols import EnergySnapshot

_LOGGER = logging.getLogger(__name__)


def _parse_state_float(state_obj) -> float | None:
    if state_obj is None:
        return None
    if state_obj.state in (STATE_UNAVAILABLE, STATE_UNKNOWN):
        return None
    try:
        return float(state_obj.state)
    except (ValueError, TypeError):
        return None


class SolarMinerCoordinator(DataUpdateCoordinator[EnergySnapshot]):
    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        interval = int(entry.options.get(CONF_POLLING_INTERVAL, DEFAULT_POLLING_INTERVAL))
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=interval),
        )
        self._entry = entry

    async def _async_update_data(self) -> EnergySnapshot:
        options = self._entry.options

        if options.get(CONF_MOCK_SOLAR_ENABLED, False):
            mock_entity_id = options.get(CONF_MOCK_SOLAR_ENTITY) or ""
            if mock_entity_id:
                state = self.hass.states.get(mock_entity_id)
                solar_w = _parse_state_float(state)
                if solar_w is not None:
                    _LOGGER.info(
                        "[MOCK SOLAR] Using %s: %.1f W",
                        mock_entity_id,
                        solar_w,
                    )
                    return EnergySnapshot(solar_production_w=solar_w, mock_solar=True)
                _LOGGER.warning(
                    "[MOCK SOLAR] Entity %s unavailable; falling back to real solar",
                    mock_entity_id,
                )
            else:
                _LOGGER.warning(
                    "[MOCK SOLAR] Enabled but no entity configured; falling back to real solar"
                )

        solar_entity_id = self._entry.data.get(CONF_SOLAR_ENTITY, "")
        state = self.hass.states.get(solar_entity_id)
        solar_w = _parse_state_float(state)
        if solar_w is None:
            raise UpdateFailed(f"Solar entity {solar_entity_id!r} unavailable")
        return EnergySnapshot(solar_production_w=solar_w, mock_solar=False)
