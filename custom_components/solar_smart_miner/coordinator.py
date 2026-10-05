"""DataUpdateCoordinator for Solar Smart Miner.

Reads solar/grid/battery entities and every miner configured in hass-miner,
derives the grid balance, and builds a preview decision (see decision.py).
Safety layer and AI agent are wired in U3 on top of this foundation.
"""
from __future__ import annotations

import logging
from collections import deque
from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry, entity_registry
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.util import dt as dt_util

from .config_flow import (
    CONF_BATTERY_ENTITY,
    CONF_BATTERY_FLOOR,
    CONF_GRID_ENTITY,
    CONF_POLLING_INTERVAL,
    CONF_PROFILE,
    CONF_SOLAR_ENTITY,
    CONF_SOLAR_ENTITY_TYPE,
    CONF_TEMP_CEILING,
)
from .const import (
    CONF_MOCK_CONSUMPTION_ENABLED,
    CONF_MOCK_SOLAR_ENABLED,
    CONF_MOCK_SOLAR_ENTITY,
    DECISION_HISTORY_SIZE,
    DEFAULT_BATTERY_FLOOR,
    DEFAULT_POLLING_INTERVAL,
    DEFAULT_PROFILE,
    DEFAULT_TEMP_CEILING,
    DOMAIN,
    HASS_MINER_PLATFORM,
    SOLAR_ENTITY_TYPE_NET_IMPORT,
    SOLAR_ENTITY_TYPE_PRODUCTION,
)
from .decision import build_decision
from .protocols import CoordinatorSnapshot, EnergySnapshot, MinerSnapshot

_LOGGER = logging.getLogger(__name__)

# hass-miner unique_id suffixes (unique_id = "<mac>-<key>"). Board-level entities
# use "<mac>-<n>-board_..." and so never match these.
_UID_POWER = "-miner_consumption"
_UID_TEMPERATURE = "-temperature"
_UID_POWER_LIMIT = "-power_limit"
_UID_HASHRATE = "-hashrate"
_UID_EFFICIENCY = "-efficiency"


def _parse_state_float(state_obj) -> float | None:
    if state_obj is None:
        return None
    if state_obj.state in (STATE_UNAVAILABLE, STATE_UNKNOWN):
        return None
    try:
        return float(state_obj.state)
    except (ValueError, TypeError):
        return None


def _parse_power_w(state_obj) -> float | None:
    """Like _parse_state_float, but converts kW/MW readings to W."""
    value = _parse_state_float(state_obj)
    if value is None:
        return None
    unit = state_obj.attributes.get("unit_of_measurement")
    if unit == "kW":
        return value * 1000
    if unit == "MW":
        return value * 1_000_000
    return value


def _find_entity(entities, domain: str, suffix: str):
    return next(
        (e for e in entities if e.domain == domain and e.unique_id.endswith(suffix)),
        None,
    )


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
        self._history: deque[dict[str, str]] = deque(maxlen=DECISION_HISTORY_SIZE)

    async def _async_read_energy(self) -> EnergySnapshot:
        options = self._entry.options
        data = self._entry.data

        solar_w: float | None = None
        grid_net_w: float | None = None
        mock_solar = False
        solar_fault = False

        if options.get(CONF_MOCK_SOLAR_ENABLED, False):
            mock_entity_id = options.get(CONF_MOCK_SOLAR_ENTITY) or ""
            if mock_entity_id:
                val = _parse_power_w(self.hass.states.get(mock_entity_id))
                if val is not None:
                    solar_w = val
                    mock_solar = True
                    _LOGGER.debug("[MOCK SOLAR] Using %s: %.1f W", mock_entity_id, val)
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
            reading = _parse_power_w(self.hass.states.get(solar_entity_id))
            solar_fault = reading is None
            if solar_fault:
                _LOGGER.warning(
                    "Solar entity %r unavailable; solar_fault=True", solar_entity_id
                )
            else:
                entity_type = data.get(CONF_SOLAR_ENTITY_TYPE, SOLAR_ENTITY_TYPE_PRODUCTION)
                if entity_type == SOLAR_ENTITY_TYPE_PRODUCTION:
                    solar_w = reading
                elif entity_type == SOLAR_ENTITY_TYPE_NET_IMPORT:
                    grid_net_w = -reading
                else:
                    grid_net_w = reading

        grid_entity_id = data.get(CONF_GRID_ENTITY, "")
        house_w = _parse_power_w(self.hass.states.get(grid_entity_id)) if grid_entity_id else None
        if grid_entity_id and house_w is None:
            _LOGGER.debug("House consumption entity %r has no numeric state", grid_entity_id)

        battery_entity_id = (data.get(CONF_BATTERY_ENTITY) or "").strip()
        battery_pct: float | None = None
        if battery_entity_id:
            battery_pct = _parse_state_float(self.hass.states.get(battery_entity_id))

        return EnergySnapshot(
            solar_production_w=solar_w,
            grid_consumption_w=house_w,
            battery_soc_pct=battery_pct,
            solar_fault=solar_fault,
            mock_solar=mock_solar,
            grid_net_w=grid_net_w,
        )

    async def _async_read_miners(self) -> list[MinerSnapshot]:
        """Read every miner configured in hass-miner (one config entry per miner)."""
        dr = device_registry.async_get(self.hass)
        er = entity_registry.async_get(self.hass)
        snapshots: list[MinerSnapshot] = []

        for hm_entry in self.hass.config_entries.async_entries(HASS_MINER_PLATFORM):
            if hm_entry.disabled_by is not None:
                continue
            miner_ip: str = hm_entry.data.get("ip", "")
            miner_id = miner_ip or hm_entry.entry_id
            entities = entity_registry.async_entries_for_config_entry(er, hm_entry.entry_id)
            power_entry = _find_entity(entities, "sensor", _UID_POWER)

            # hass-miner entries can accumulate empty duplicate devices, so take
            # the name from the device that actually owns the miner's entities.
            device = dr.async_get(power_entry.device_id) if power_entry and power_entry.device_id else None
            name = (device.name_by_user or device.name) if device else None
            name = name or hm_entry.title or miner_id
            temp_entry = _find_entity(entities, "sensor", _UID_TEMPERATURE)
            limit_entry = _find_entity(entities, "number", _UID_POWER_LIMIT)
            hashrate_entry = _find_entity(entities, "sensor", _UID_HASHRATE)
            efficiency_entry = _find_entity(entities, "sensor", _UID_EFFICIENCY)

            def _state(reg_entry):
                return self.hass.states.get(reg_entry.entity_id) if reg_entry else None

            limit_state = _state(limit_entry)
            min_power_w = (
                limit_state.attributes.get("min") if limit_state else None
            ) or hm_entry.data.get("min_power")
            max_power_w = (
                limit_state.attributes.get("max") if limit_state else None
            ) or hm_entry.data.get("max_power")

            power_w = _parse_power_w(_state(power_entry))
            is_available = power_w is not None
            if not is_available:
                _LOGGER.warning("Miner %s (%s): power entity unavailable", name, miner_id)

            snapshots.append(
                MinerSnapshot(
                    miner_id=miner_id,
                    ip=miner_ip,
                    name=name,
                    power_w=power_w,
                    power_limit_w=_parse_state_float(limit_state),
                    min_power_w=float(min_power_w) if min_power_w is not None else None,
                    max_power_w=float(max_power_w) if max_power_w is not None else None,
                    temperature_c=_parse_state_float(_state(temp_entry)),
                    is_available=is_available,
                    power_limit_entity_id=limit_entry.entity_id if limit_entry else None,
                    hashrate_th=_parse_state_float(_state(hashrate_entry)),
                    efficiency_jth=_parse_state_float(_state(efficiency_entry)),
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

    @staticmethod
    def _derive_balance(energy: EnergySnapshot) -> None:
        """Fill whichever of solar production / grid balance wasn't measured.

        House consumption includes the miners, so grid = solar - house, and the
        power miners could use without importing is grid + current miner draw.
        """
        house = energy.grid_consumption_w
        if energy.solar_production_w is not None:
            if house is not None:
                energy.grid_net_w = energy.solar_production_w - house
        elif energy.grid_net_w is not None and house is not None:
            energy.solar_production_w = max(energy.grid_net_w + house, 0.0)

        if energy.grid_net_w is not None:
            energy.available_for_miners_w = energy.grid_net_w + (
                energy.miner_consumption_sum_w or 0.0
            )

    async def _async_update_data(self) -> CoordinatorSnapshot:
        energy = await self._async_read_energy()
        miners = await self._async_read_miners()

        miner_sum = self._sum_miner_power_w(miners)
        energy.miner_consumption_sum_w = miner_sum

        if self._entry.options.get(CONF_MOCK_CONSUMPTION_ENABLED, False):
            if miner_sum is not None:
                energy.grid_consumption_w = miner_sum
                energy.mock_consumption = True
                _LOGGER.debug("[MOCK CONSUMPTION] Using miner sum: %.1f W", miner_sum)
            else:
                _LOGGER.warning(
                    "[MOCK CONSUMPTION] Miner sum unavailable; keeping real grid entity read"
                )

        self._derive_balance(energy)

        snapshot = CoordinatorSnapshot(energy=energy, miners=miners)
        options = self._entry.options
        decision = build_decision(
            snapshot,
            profile=options.get(CONF_PROFILE, DEFAULT_PROFILE),
            temp_ceiling=float(options.get(CONF_TEMP_CEILING, DEFAULT_TEMP_CEILING)),
            battery_floor=float(options.get(CONF_BATTERY_FLOOR, DEFAULT_BATTERY_FLOOR)),
        )
        # Only record changes, so the history reads as a log of what shifted.
        if not self._history or self._history[0]["summary"] != decision.summary:
            self._history.appendleft(
                {"time": dt_util.now().strftime("%H:%M:%S"), "summary": decision.summary}
            )
        snapshot.decision = decision
        snapshot.decision_history = list(self._history)

        _LOGGER.debug("Decision cycle:\n  %s", "\n  ".join(decision.trace))
        return snapshot
