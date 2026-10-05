"""DataUpdateCoordinator for Solar Smart Miner.

Reads solar/grid/battery entities and every miner configured in hass-miner,
derives the grid balance, and builds a preview decision (see decision.py).
Safety layer and AI agent are wired in U3 on top of this foundation.
"""
from __future__ import annotations

import logging
import time
from collections import deque
from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry, entity_registry
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.util import dt as dt_util

from .ai import async_ask, build_messages
from .ai_log import AiLog, build_record, complete_record
from .config_flow import (
    CONF_AI_ENABLED,
    CONF_AI_INTERVAL,
    CONF_BATTERY_ENTITY,
    CONF_BATTERY_FLOOR,
    CONF_FORECAST_NEXT_HOUR_ENTITY,
    CONF_FORECAST_NOW_ENTITY,
    CONF_FORECAST_REMAINING_ENTITY,
    CONF_GRID_ENTITY,
    CONF_OPENROUTER_KEY,
    CONF_OPENROUTER_MODEL,
    CONF_POLLING_INTERVAL,
    CONF_MINER_RELAYS,
    CONF_POWER_STEPS,
    CONF_PROFILE,
    CONF_PV_ENTITY,
    CONF_SOLAR_ENTITY,
    CONF_SOLAR_ENTITY_TYPE,
    CONF_TEMP_CEILING,
    CONF_TUNING_SETTLE,
    DEFAULT_OPENROUTER_MODEL,
)
from .const import (
    CONF_MOCK_CONSUMPTION_ENABLED,
    CONF_MOCK_SOLAR_ENABLED,
    CONF_MOCK_SOLAR_ENTITY,
    ASK_AI_COOLDOWN,
    DECISION_HISTORY_SIZE,
    DEFAULT_AI_INTERVAL,
    DEFAULT_BATTERY_FLOOR,
    DEFAULT_POLLING_INTERVAL,
    DEFAULT_POWER_STEPS,
    DEFAULT_PROFILE,
    DEFAULT_TEMP_CEILING,
    DEFAULT_TUNING_SETTLE_MINUTES,
    DOMAIN,
    HASS_MINER_PLATFORM,
    MIN_AI_INTERVAL,
    SOLAR_ENTITY_TYPE_NET_IMPORT,
    SOLAR_ENTITY_TYPE_PRODUCTION,
)
from .decision import build_decision
from .protocols import AiAdvice, CoordinatorSnapshot, EnergySnapshot, MinerSnapshot

_LOGGER = logging.getLogger(__name__)

# hass-miner unique_id suffixes (unique_id = "<mac>-<key>"). Board-level entities
# use "<mac>-<n>-board_..." and so never match these.
_UID_POWER = "-miner_consumption"
_UID_TEMPERATURE = "-temperature"
_UID_POWER_LIMIT = "-power_limit"
_UID_HASHRATE = "-hashrate"
_UID_EFFICIENCY = "-efficiency"
_UID_ACTIVE = "-active"  # the switch that pauses / resumes mining


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


def _parse_energy_kwh(state_obj) -> float | None:
    """Like _parse_state_float, but converts Wh/MWh readings to kWh."""
    value = _parse_state_float(state_obj)
    if value is None:
        return None
    unit = state_obj.attributes.get("unit_of_measurement")
    if unit == "Wh":
        return value / 1000
    if unit == "MWh":
        return value * 1000
    return value


def _find_entity(entities, domain: str, suffix: str):
    return next(
        (e for e in entities if e.domain == domain and e.unique_id.endswith(suffix)),
        None,
    )


def miner_display_name(hass: HomeAssistant) -> list[tuple[str, str]]:
    """(miner id, name) for every enabled hass-miner entry, named like the controller does."""
    dr = device_registry.async_get(hass)
    er = entity_registry.async_get(hass)
    result = []
    for hm_entry in hass.config_entries.async_entries(HASS_MINER_PLATFORM):
        if hm_entry.disabled_by is not None:
            continue
        miner_id = hm_entry.data.get("ip", "") or hm_entry.entry_id
        entities = entity_registry.async_entries_for_config_entry(er, hm_entry.entry_id)
        power_entry = _find_entity(entities, "sensor", _UID_POWER)
        device = (
            dr.async_get(power_entry.device_id) if power_entry and power_entry.device_id else None
        )
        name = (device.name_by_user or device.name) if device else None
        result.append((miner_id, name or hm_entry.title or miner_id))
    return result


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
        self.ai_log = AiLog(hass)
        self._ai_advice: AiAdvice | None = None
        self._ai_busy = False
        self._ai_last_request: float | None = None  # time.monotonic()
        # miner id -> (power limit last seen, time.monotonic() when it changed or None)
        self._limit_seen: dict[str, tuple[float | None, float | None]] = {}

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
                    # Inverters can report a small negative standby draw at night.
                    solar_w = max(reading, 0.0)
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

        def _reference(key: str, parse):
            entity_id = (data.get(key) or "").strip()
            return parse(self.hass.states.get(entity_id)) if entity_id else None

        return EnergySnapshot(
            pv_power_w=_reference(CONF_PV_ENTITY, _parse_power_w),
            forecast_now_w=_reference(CONF_FORECAST_NOW_ENTITY, _parse_power_w),
            forecast_next_hour_w=_reference(CONF_FORECAST_NEXT_HOUR_ENTITY, _parse_power_w),
            forecast_remaining_kwh=_reference(CONF_FORECAST_REMAINING_ENTITY, _parse_energy_kwh),
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
            active_entry = _find_entity(entities, "switch", _UID_ACTIVE)
            relay_entity_id = (self._entry.options.get(CONF_MINER_RELAYS) or {}).get(miner_id) or None

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
            switch_state = _state(active_entry)
            relay_state = self.hass.states.get(relay_entity_id) if relay_entity_id else None
            is_stopped = (switch_state is not None and switch_state.state == "off") or (
                relay_state is not None and relay_state.state == "off"
            )
            power_limit_w = _parse_state_float(limit_state)
            if not is_available and not is_stopped:
                _LOGGER.warning("Miner %s (%s): power entity unavailable", name, miner_id)

            snapshots.append(
                MinerSnapshot(
                    miner_id=miner_id,
                    ip=miner_ip,
                    name=name,
                    power_w=power_w,
                    power_limit_w=power_limit_w,
                    min_power_w=float(min_power_w) if min_power_w is not None else None,
                    max_power_w=float(max_power_w) if max_power_w is not None else None,
                    temperature_c=_parse_state_float(_state(temp_entry)),
                    is_available=is_available,
                    power_limit_entity_id=limit_entry.entity_id if limit_entry else None,
                    hashrate_th=_parse_state_float(_state(hashrate_entry)),
                    efficiency_jth=_parse_state_float(_state(efficiency_entry)),
                    switch_entity_id=active_entry.entity_id if active_entry else None,
                    relay_entity_id=relay_entity_id,
                    is_stopped=is_stopped,
                    minutes_since_limit_change=self._minutes_since_limit_change(
                        miner_id, power_limit_w
                    ),
                )
            )

        return snapshots

    def _minutes_since_limit_change(self, miner_id: str, limit_w: float | None) -> float | None:
        """Minutes since the miner's power limit was seen to change (None: never seen to).

        hass-miner doesn't expose the tuning state, so this stands in for it: a miner
        re-tunes for up to an hour after a change. A limit that was already set when we
        started watching has an unknown age, which counts as settled.
        """
        now = time.monotonic()
        last_limit, changed = self._limit_seen.get(miner_id, (None, None))
        if limit_w is not None:
            if last_limit is not None and last_limit != limit_w:
                changed = now
            self._limit_seen[miner_id] = (limit_w, changed)
        return None if changed is None else (now - changed) / 60

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

    # --- AI advisor (advisory only: nothing here changes the miners) -----------

    @property
    def ai_enabled(self) -> bool:
        key = (self._entry.data.get(CONF_OPENROUTER_KEY) or "").strip()
        return bool(key) and self._entry.options.get(CONF_AI_ENABLED, True)

    def _maybe_request_ai(self, snapshot: CoordinatorSnapshot) -> None:
        if not self.ai_enabled or self._ai_busy:
            return
        interval = max(
            int(self._entry.options.get(CONF_AI_INTERVAL, DEFAULT_AI_INTERVAL)), MIN_AI_INTERVAL
        )
        last = self._ai_last_request
        if last is None or time.monotonic() - last >= interval:
            self._start_ai_request(snapshot)

    def _start_ai_request(self, snapshot: CoordinatorSnapshot) -> None:
        options = self._entry.options
        settings = {
            "profile": options.get(CONF_PROFILE, DEFAULT_PROFILE),
            "temp_ceiling": float(options.get(CONF_TEMP_CEILING, DEFAULT_TEMP_CEILING)),
            "battery_floor": float(options.get(CONF_BATTERY_FLOOR, DEFAULT_BATTERY_FLOOR)),
        }
        messages = build_messages(snapshot, **settings)
        record = build_record(snapshot, messages, **settings)
        self._ai_busy = True
        self._ai_last_request = time.monotonic()
        self._entry.async_create_background_task(
            self.hass, self._async_run_ai(messages, record), name=f"{DOMAIN}_ai_advice"
        )

    async def _async_run_ai(self, messages: list[dict[str, str]], record: dict) -> None:
        data = self._entry.data
        try:
            advice = await async_ask(
                async_get_clientsession(self.hass),
                api_key=(data.get(CONF_OPENROUTER_KEY) or "").strip(),
                model=(data.get(CONF_OPENROUTER_MODEL) or DEFAULT_OPENROUTER_MODEL).strip(),
                messages=messages,
            )
        finally:
            self._ai_busy = False
        if advice.error:
            _LOGGER.warning("AI advice failed (%s): %s", advice.model, advice.error)
        else:
            _LOGGER.debug("AI advice from %s in %.1f s", advice.model, advice.latency_s)
        self._ai_advice = advice
        await self.ai_log.async_append(complete_record(record, advice))
        if self.data is not None:
            self.data.ai_advice = advice
            self.async_update_listeners()

    def async_ask_ai_now(self) -> None:
        """Request advice immediately (the "Ask AI now" button)."""
        if not self.ai_enabled:
            raise HomeAssistantError(
                "AI advice is off: set an OpenRouter API key and enable it under "
                "Configure → AI (OpenRouter)."
            )
        if self.data is None or self._ai_busy:
            return
        last = self._ai_last_request
        if last is not None and time.monotonic() - last < ASK_AI_COOLDOWN:
            return
        self._start_ai_request(self.data)

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
            power_steps=list(options.get(CONF_POWER_STEPS) or DEFAULT_POWER_STEPS),
            tuning_settle_minutes=float(
                options.get(CONF_TUNING_SETTLE, DEFAULT_TUNING_SETTLE_MINUTES)
            ),
        )
        # Only record changes, so the history reads as a log of what shifted.
        if not self._history or self._history[0]["summary"] != decision.summary:
            self._history.appendleft(
                {"time": dt_util.now().strftime("%H:%M:%S"), "summary": decision.summary}
            )
        snapshot.decision = decision
        snapshot.decision_history = list(self._history)
        self._maybe_request_ai(snapshot)
        # After the request: a very fast answer can already be in by now.
        snapshot.ai_advice = self._ai_advice

        _LOGGER.debug("Decision cycle:\n  %s", "\n  ".join(decision.trace))
        return snapshot
