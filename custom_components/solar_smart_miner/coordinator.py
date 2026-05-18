"""DataUpdateCoordinator for Solar Smart Miner — U11.

Implements full entity reads (solar, grid, battery, per-miner sensors) and
a dry-run-aware power limit apply method. Safety layer and AI agent are wired
in U3 on top of this foundation.
"""
from __future__ import annotations

import logging
from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry, entity_registry
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .config_flow import (
    CONF_BATTERY_ENTITY,
    CONF_DRY_RUN,
    CONF_GRID_ENTITY,
    CONF_MINER_IP,
    CONF_MINERS,
    CONF_POLLING_INTERVAL,
    CONF_SOLAR_ENTITY,
)
from .const import (
    CONF_MOCK_CONSUMPTION_ENABLED,
    CONF_MOCK_SOLAR_ENABLED,
    CONF_MOCK_SOLAR_ENTITY,
    DEFAULT_POLLING_INTERVAL,
    DOMAIN,
)
from .protocols import CoordinatorSnapshot, EnergySnapshot, MinerSnapshot

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


class SolarMinerCoordinator(DataUpdateCoordinator[CoordinatorSnapshot]):
    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        interval = int(entry.options.get(CONF_POLLING_INTERVAL, DEFAULT_POLLING_INTERVAL))
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=interval),
        )
        self._entry = entry

    async def _async_read_energy(self) -> EnergySnapshot:
        options = self._entry.options
        data = self._entry.data

        solar_w: float | None = None
        mock_solar = False
        solar_fault = False

        if options.get(CONF_MOCK_SOLAR_ENABLED, False):
            mock_entity_id = options.get(CONF_MOCK_SOLAR_ENTITY) or ""
            if mock_entity_id:
                state = self.hass.states.get(mock_entity_id)
                val = _parse_state_float(state)
                if val is not None:
                    solar_w = val
                    mock_solar = True
                    _LOGGER.info("[MOCK SOLAR] Using %s: %.1f W", mock_entity_id, val)
                else:
                    _LOGGER.warning(
                        "[MOCK SOLAR] Entity %s unavailable; falling back to real solar",
                        mock_entity_id,
                    )
            else:
                _LOGGER.warning(
                    "[MOCK SOLAR] Enabled but no entity configured; falling back to real solar"
                )

        if not mock_solar:
            solar_entity_id = data.get(CONF_SOLAR_ENTITY, "")
            solar_w = _parse_state_float(self.hass.states.get(solar_entity_id))
            solar_fault = solar_w is None
            if solar_fault:
                _LOGGER.warning(
                    "Solar entity %r unavailable; solar_fault=True", solar_entity_id
                )

        grid_entity_id = data.get(CONF_GRID_ENTITY, "")
        grid_w = _parse_state_float(self.hass.states.get(grid_entity_id)) if grid_entity_id else None

        battery_entity_id = (data.get(CONF_BATTERY_ENTITY) or "").strip()
        battery_pct: float | None = None
        if battery_entity_id:
            battery_pct = _parse_state_float(self.hass.states.get(battery_entity_id))

        return EnergySnapshot(
            solar_production_w=solar_w,
            grid_consumption_w=grid_w,
            battery_soc_pct=battery_pct,
            solar_fault=solar_fault,
            mock_solar=mock_solar,
        )

    async def _async_read_miners(self) -> list[MinerSnapshot]:
        dr = device_registry.async_get(self.hass)
        er = entity_registry.async_get(self.hass)

        miners_conf = self._entry.data.get(CONF_MINERS, [])
        snapshots: list[MinerSnapshot] = []

        for miner_conf in miners_conf:
            miner_ip: str = miner_conf.get(CONF_MINER_IP, "")
            miner_id: str = miner_ip  # IP is the stable identifier

            # Locate the hass_miner device for this IP via identifiers or configuration_url
            target_device = None
            for device in dr.devices.values():
                if any(
                    ident_domain == "hass_miner" and ident_value == miner_ip
                    for ident_domain, ident_value in device.identifiers
                ):
                    target_device = device
                    break
                if device.configuration_url and miner_ip in device.configuration_url:
                    target_device = device
                    break

            if target_device is None:
                _LOGGER.warning(
                    "No hass_miner device found for IP %s; marking unavailable", miner_ip
                )
                snapshots.append(
                    MinerSnapshot(
                        miner_id=miner_id,
                        ip=miner_ip,
                        power_w=None,
                        power_limit_w=None,
                        min_power_w=None,
                        max_power_w=None,
                        temperature_c=None,
                        is_available=False,
                        power_limit_entity_id=None,
                    )
                )
                continue

            device_entities = [
                e
                for e in er.entities.values()
                if e.device_id == target_device.id and e.platform == "hass_miner"
            ]

            power_entry = next(
                (
                    e
                    for e in device_entities
                    if e.domain == "sensor" and e.original_device_class == "power"
                ),
                None,
            )
            temp_entry = next(
                (
                    e
                    for e in device_entities
                    if e.domain == "sensor" and e.original_device_class == "temperature"
                ),
                None,
            )
            limit_entry = next(
                (e for e in device_entities if e.domain == "number"),
                None,
            )

            power_state = self.hass.states.get(power_entry.entity_id) if power_entry else None
            temp_state = self.hass.states.get(temp_entry.entity_id) if temp_entry else None
            limit_state = self.hass.states.get(limit_entry.entity_id) if limit_entry else None

            power_w = _parse_state_float(power_state)
            temperature_c = _parse_state_float(temp_state)
            power_limit_w = _parse_state_float(limit_state)
            min_power_w = (
                float(limit_state.attributes["min"])
                if limit_state and "min" in limit_state.attributes
                else None
            )
            max_power_w = (
                float(limit_state.attributes["max"])
                if limit_state and "max" in limit_state.attributes
                else None
            )

            is_available = power_entry is not None and power_w is not None
            if not is_available:
                _LOGGER.warning("Miner %s: power entity unavailable", miner_id)

            snapshots.append(
                MinerSnapshot(
                    miner_id=miner_id,
                    ip=miner_ip,
                    power_w=power_w,
                    power_limit_w=power_limit_w,
                    min_power_w=min_power_w,
                    max_power_w=max_power_w,
                    temperature_c=temperature_c,
                    is_available=is_available,
                    power_limit_entity_id=limit_entry.entity_id if limit_entry else None,
                )
            )

        return snapshots

    async def _async_apply_power_limit(
        self, snapshot: MinerSnapshot, limit_w: float, dry_run: bool
    ) -> None:
        if snapshot.power_limit_entity_id is None:
            _LOGGER.warning(
                "No power limit entity for miner %s; skipping", snapshot.miner_id
            )
            return

        clamped = limit_w
        if snapshot.min_power_w is not None:
            clamped = max(clamped, snapshot.min_power_w)
        if snapshot.max_power_w is not None:
            clamped = min(clamped, snapshot.max_power_w)

        if dry_run:
            _LOGGER.info(
                "[DRY RUN] Would set miner %s power limit to %.1f W",
                snapshot.miner_id,
                clamped,
            )
            return

        await self.hass.services.async_call(
            "number",
            "set_value",
            {"entity_id": snapshot.power_limit_entity_id, "value": clamped},
        )

    def _sum_miner_power_w(self, miners: list[MinerSnapshot]) -> float | None:
        values = [m.power_w for m in miners if m.power_w is not None]
        return sum(values) if values else None

    async def _async_update_data(self) -> CoordinatorSnapshot:
        energy = await self._async_read_energy()
        miners = await self._async_read_miners()

        miner_sum = self._sum_miner_power_w(miners)
        energy.miner_consumption_sum_w = miner_sum

        if self._entry.options.get(CONF_MOCK_CONSUMPTION_ENABLED, False):
            if miner_sum is not None:
                energy.grid_consumption_w = miner_sum
                energy.mock_consumption = True
                _LOGGER.info("[MOCK CONSUMPTION] Using miner sum: %.1f W", miner_sum)
            else:
                _LOGGER.warning(
                    "[MOCK CONSUMPTION] Miner sum unavailable; keeping real grid entity read"
                )

        return CoordinatorSnapshot(energy=energy, miners=miners)
