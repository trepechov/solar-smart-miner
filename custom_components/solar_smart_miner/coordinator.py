"""DataUpdateCoordinator for Solar Smart Miner.

Reads solar/grid/battery entities and every miner configured in hass-miner,
derives the grid balance, and builds the decision (see decision.py).
Plans are applied only through control.py, when the control mode allows it.
"""
from __future__ import annotations

import logging
import time
from collections import deque
from datetime import datetime, timedelta
from pathlib import Path

import yaml
from homeassistant.components.persistent_notification import async_create as pn_create
from homeassistant.components.persistent_notification import async_dismiss as pn_dismiss
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry, entity_registry
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.util import dt as dt_util

from .action_log import ActionLog
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
    CONF_IMPORT_MAX,
    CONF_IMPORT_MIN,
    CONF_LOW_VOLTAGE,
    CONF_VOLTAGE_DEBOUNCE,
    CONF_VOLTAGE_ENTITY,
    CONF_MORNING_STEP_DOWN_DELAY,
    CONF_OPENROUTER_KEY,
    CONF_OPENROUTER_MODEL,
    CONF_POLLING_INTERVAL,
    CONF_MINER_RELAYS,
    CONF_POWER_STEPS,
    CONF_PROFILE,
    CONF_RAMP_LOCK,
    CONF_PV_ENTITY,
    CONF_SOLAR_ENTITY,
    CONF_SOLAR_ENTITY_TYPE,
    CONF_STEP_DOWN_DELAY,
    CONF_TEMP_TARGET,
    CONF_TEMP_TOLERANCE,
    DEFAULT_OPENROUTER_MODEL,
)
from .const import (
    CONTROL_MODE_AUTO,
    CONF_FARM_BASE_LOAD,
    CONF_MOCK_CONSUMPTION_ENABLED,
    CONF_MOCK_SOLAR_ENABLED,
    CONF_MOCK_SOLAR_ENTITY,
    ACTIVITY_SIZE,
    NO_ACTION_TEXT,
    ASK_AI_COOLDOWN,
    DECISION_HISTORY_SIZE,
    DEFAULT_AI_INTERVAL,
    DEFAULT_BATTERY_FLOOR,
    DEFAULT_IMPORT_MAX_W,
    DEFAULT_IMPORT_MIN_W,
    DEFAULT_LOW_VOLTAGE_V,
    DEFAULT_VOLTAGE_DEBOUNCE_S,
    DEFAULT_MORNING_STEP_DOWN_DELAY_MINUTES,
    DEFAULT_POLLING_INTERVAL,
    DEFAULT_POWER_STEPS,
    DEFAULT_PROFILE,
    DEFAULT_STEP_DOWN_DELAY_MINUTES,
    DEFAULT_TEMP_TARGET,
    DEFAULT_TEMP_TOLERANCE,
    DEFAULT_RAMP_LOCK_MINUTES,
    DOMAIN,
    FARM_FILE,
    HASS_MINER_PLATFORM,
    METER_GRACE_MIN,
    METER_LOST_ALERT_MIN,
    SUNSET_GATE_MIN,
    MIN_AI_INTERVAL,
    SOLAR_ENTITY_TYPE_NET_IMPORT,
    SOLAR_ENTITY_TYPE_PRODUCTION,
    control_mode_of,
)
from .control import (
    RESULT_FAILED,
    RESULT_PENDING,
    RESULT_REFUSED,
    TRIGGER_AUTO,
    TRIGGER_MANUAL,
    CommandEvent,
    CommandResult,
    MinerController,
)
from .decision import HOLD_ALL_SUMMARY, _describe_plan, build_decision, describe_proposal
from .decision.rules import HELD_DOWN_REASONS
from .decision.safety import estimated_import_w
from .decision_log import DecisionLog
from . import jsonl_log
from .kb import (
    Fact,
    describe_farm,
    format_facts,
    load_facts,
    load_farm_facts,
    select_facts,
    situation,
    write_farm_header,
)
from .transition import Transition
from .protocols import (
    ACTION_HOLD,
    ACTION_SET_LIMIT,
    ACTION_STOP,
    AiAdvice,
    CoordinatorSnapshot,
    EnergySnapshot,
    MinerPlan,
    MinerSnapshot,
)

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


def _is_reduction(miner: MinerSnapshot, plan: MinerPlan) -> bool:
    """A stop or step down: applied before step-ups and starts, so the house never draws both."""
    if plan.action == ACTION_STOP:
        return True
    return (
        plan.action == ACTION_SET_LIMIT
        and miner.power_limit_w is not None
        and plan.limit_w < miner.power_limit_w
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
        self.action_log = ActionLog(hass)
        self.decision_log = DecisionLog(hass)
        # Proposals and applied actions, newest first: what the card's activity log shows.
        self.activity: deque[dict] = deque(maxlen=ACTIVITY_SIZE)
        self._proposed: str | None = None  # fingerprint of the last recorded farm proposal
        self._ai_advice: AiAdvice | None = None
        self._ai_busy = False
        self._ai_last_request: float | None = None  # time.monotonic()
        # miner id -> (power limit last seen, time.monotonic() when it changed or None)
        self._limit_seen: dict[str, tuple[float | None, float | None]] = {}
        self._stopped_seen: dict[str, bool] = {}  # miner id -> stopped at the last read
        # time.monotonic() of a change no miner can be named for (a failed send, a restart of HA)
        self._last_change: float | None = None
        self._last_unnamed_change: float | None = None  # the same, kept after the ramp lock
        self._ramp_done: list[str] = []  # names of changed miners already at their new power
        self._import_high_since: float | None = None  # time.monotonic() the import went above the band
        self._import_high_frozen: float | None = None  # time.monotonic() the meter went (grace)
        self._grid_unknown_since: float | None = None  # time.monotonic() the grid balance went unknown
        self._meter_alerted = False  # the sensor.meter-lost notification is up
        self._estimate_used = False  # a change was made on the estimate during this outage
        self._rules_failed = False  # the "rules failed" notification is up
        self._voltage_low_since: float | None = None  # time.monotonic() it went low (outside locks)
        # miner id -> time.monotonic() a Safety or Limits rule brought it down (HELD_DOWN_REASONS)
        self._brought_down: dict[str, float] = {}
        self.transition = Transition()  # sunrise and sunset, from how production changes
        self._building: CoordinatorSnapshot | None = None  # the snapshot of the cycle being worked out
        # miner id -> (plan fingerprint, reason) of the last automatic refusal, so it isn't repeated
        self._auto_refused: dict[str, tuple[str, str]] = {}
        self.controller = MinerController(
            hass,
            get_mode=lambda: self.control_mode,
            on_limit_applied=self._mark_limit_changed,
            on_event=self._async_record_action,
            get_ramp_lock_minutes=self._ramp_lock_minutes,
        )
        self.knowledge: list[Fact] = []  # the knowledge base, loaded by async_load_knowledge

    @property
    def control_mode(self) -> str:
        """manual or auto: whether the proposal waits for the Apply button or is applied every cycle."""
        return control_mode_of(self._entry.options)

    async def async_load_knowledge(self) -> None:
        """Read the knowledge base, then the farm's own facts after it (farm.yaml, created with
        its explanation on first setup). Without either the AI still works, just knows less."""
        try:
            self.knowledge = await self.hass.async_add_executor_job(load_facts)
        except (OSError, yaml.YAMLError, KeyError, TypeError) as err:
            _LOGGER.error("Knowledge base not loaded, the AI gets no facts: %s", err)
            self.knowledge = []
        farm_file = Path(self.hass.config.path(jsonl_log.LOG_DIR, FARM_FILE))
        try:
            await self.hass.async_add_executor_job(write_farm_header, farm_file)
        except OSError as err:
            _LOGGER.warning("Could not create %s: %s", farm_file, err)
        try:
            self.knowledge += await self.hass.async_add_executor_job(load_farm_facts, farm_file)
        except Exception:  # noqa: BLE001 - the user's file must never stop setup
            _LOGGER.exception("Farm file %s not loaded", farm_file)

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
            voltage_v=_reference(CONF_VOLTAGE_ENTITY, _parse_state_float),
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
            since_limit, limit_changed = self._minutes_since_limit_change(miner_id, power_limit_w)
            was_running = self._stopped_seen.get(miner_id) is False
            if is_stopped and self.controller.restarting(miner_id):
                # A limit change restarts the miner, and hass-miner shows its pause switch off
                # for a minute or two meanwhile: it is restarting at its new limit, not stopped.
                is_stopped = False
            stop_changed = self._stopped_seen.get(miner_id, is_stopped) != is_stopped
            self._stopped_seen[miner_id] = is_stopped
            if not is_available and not is_stopped:
                _LOGGER.warning("Miner %s (%s): power entity unavailable", name, miner_id)

            snapshot = MinerSnapshot(
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
                minutes_since_limit_change=since_limit,
            )
            # A change seen on the miner settles like a command (by us or by hand).
            if stop_changed:
                self.controller.note_change(snapshot, stopping=is_stopped)
            elif limit_changed:
                self.controller.note_change(snapshot, stopping=False, restarting=was_running)
            snapshots.append(snapshot)

        return snapshots

    def _minutes_since_limit_change(
        self, miner_id: str, limit_w: float | None
    ) -> tuple[float | None, bool]:
        """Minutes since the miner's power limit was seen to change (None: never seen to), and
        whether it changed in this reading.

        hass-miner doesn't expose the tuning state, so this stands in for it: a miner
        settles a few minutes after a change (a step it never ran takes longer). A limit
        that was already set when we started watching has an unknown age, which counts as
        settled.
        """
        now = time.monotonic()
        last_limit, changed = self._limit_seen.get(miner_id, (None, None))
        seen = False
        if limit_w is not None:
            if last_limit is not None and last_limit != limit_w:
                changed = now
                seen = True
            self._limit_seen[miner_id] = (limit_w, changed)
        return (None if changed is None else (now - changed) / 60), seen

    def _mark_limit_changed(self, miner_id: str, limit_w: float) -> None:
        """An applied command re-tunes the miner now: restart its tuning clock at once.

        The last limit reading is kept, so the number still showing the old value for a
        few seconds doesn't read as a second change; the new value, once it shows, does.
        """
        last_limit, _ = self._limit_seen.get(miner_id, (None, None))
        self._limit_seen[miner_id] = (last_limit, time.monotonic())

    def _minutes_since_change(self, miners: list[MinerSnapshot]) -> float | None:
        """Minutes since the last change still settling, for the ramp lock (0 while one is checked).

        A changed miner that has restarted and draws close to its new limit is done (its
        hashrate may still be settling): the readings are true again, so the next miner may
        change. The controller's settling records decide it (Settling.done).
        """
        self._ramp_done = []
        if any(self.controller.is_pending(m.miner_id) for m in miners):
            return 0.0
        now = time.monotonic()
        ramp_lock = self._ramp_lock_minutes()
        ages: list[float] = []
        for m in miners:
            record = self.controller.settling.get(m.miner_id)
            if record is None or (age := record.age_min(now)) >= ramp_lock:
                continue
            if record.done(m, now):
                self._ramp_done.append(m.name)
                continue
            ages.append(age)
        if self._last_change is not None:
            age = (now - self._last_change) / 60
            if age < ramp_lock:
                ages.append(age)
            else:
                self._last_change = None  # over: "None" means no change is settling
        return min(ages, default=None)

    def _minutes_since_last_change(self) -> float | None:
        """Minutes since the last change of any kind (sent, seen, a failed send, a restart of HA),
        however long ago: the step-down delay is counted again from it."""
        times = [t for t in (self.controller.last_change_at, self._last_unnamed_change) if t is not None]
        return (time.monotonic() - max(times)) / 60 if times else None

    def _seconds_voltage_low(
        self, energy: EnergySnapshot, threshold_v: float, since_change: float | None
    ) -> float | None:
        """Seconds the voltage has stayed below the threshold, counted only outside a ramp lock:
        after each change the count starts again, so a lagging reading can't stop a second
        miner right after the first (None while it isn't low, or there is no voltage sensor)."""
        if energy.voltage_v is None or energy.voltage_v >= threshold_v or since_change is not None:
            self._voltage_low_since = None
            return None
        if self._voltage_low_since is None:
            self._voltage_low_since = time.monotonic()
        return time.monotonic() - self._voltage_low_since

    def _held_down(self, delay_minutes: float) -> list[str]:
        """Miners a Safety or Limits rule brought down within the step-down delay."""
        now = time.monotonic()
        for miner_id, when in list(self._brought_down.items()):
            if now - when >= delay_minutes * 60:
                del self._brought_down[miner_id]
        return sorted(self._brought_down)

    def _minutes_meter_lost(self, energy: EnergySnapshot, can_estimate: bool) -> float | None:
        """Minutes the grid balance has been unknown (None while it is known), and the
        sensor.meter-lost alert: raised at METER_LOST_ALERT_MIN, dismissed when it is back."""
        now = time.monotonic()
        if energy.grid_net_w is not None:
            if self._meter_alerted:
                pn_dismiss(self.hass, f"{DOMAIN}_meter_lost")
                self._meter_alerted = False
            self._grid_unknown_since = None
            self._estimate_used = False
            return None
        if self._grid_unknown_since is None:
            self._grid_unknown_since = now
        lost = (now - self._grid_unknown_since) / 60
        if lost >= METER_LOST_ALERT_MIN and not self._meter_alerted:
            self._meter_alerted = True
            pn_create(
                self.hass,
                f"The grid meter has read nothing for {lost:.0f} minutes. "
                + (
                    "The import is estimated from the miners' draw, the house load and the actual "
                    "PV; on it the miners can step down once, and otherwise hold."
                    if can_estimate
                    else "Every miner holds: an estimate needs the house load besides the miners "
                    "(Configure → Farm) and an actual PV sensor (Configure → Sensors)."
                )
                + " Check the meter and its connection.",
                title="Solar Smart Miner: grid meter lost",
                notification_id=f"{DOMAIN}_meter_lost",
            )
        return lost

    def _minutes_import_high(
        self, import_w: float | None, max_w: float, since_change: float | None
    ) -> float | None:
        """Minutes the grid import has stayed above the maximum (None while it isn't).

        Counted again from the last change: a step down that didn't cover the shortfall waits
        the delay again before the next one. While the import can't be read (a meter gap) the
        count is frozen, neither reset nor advanced; after the grace it follows the estimate.
        """
        now = time.monotonic()
        if import_w is None:
            if self._import_high_since is None:
                return None
            if self._import_high_frozen is None:
                self._import_high_frozen = now
            lasted = (self._import_high_frozen - self._import_high_since) / 60
            return lasted if since_change is None else min(lasted, since_change)
        if self._import_high_frozen is not None:
            if self._import_high_since is not None:
                self._import_high_since += now - self._import_high_frozen  # the gap doesn't count
            self._import_high_frozen = None
        if import_w <= max_w:
            self._import_high_since = None
            return None
        if self._import_high_since is None:
            self._import_high_since = now
        lasted = (now - self._import_high_since) / 60
        return lasted if since_change is None else min(lasted, since_change)

    def _update_transition(self, energy: EnergySnapshot, since_change: float | None, sun) -> None:
        """Feed the sunrise / sunset helper. Production is read only outside a ramp lock and
        while there is some import (a throttled inverter shows the load, not the sun)."""
        production = None
        if since_change is None and energy.grid_net_w is not None and energy.grid_net_w < 0:
            production = energy.grid_net_w + (energy.miner_consumption_sum_w or 0.0)
        setting = dt_util.parse_datetime(str(sun.attributes.get("next_setting") or "")) if sun else None
        self.transition.update(
            time.monotonic(),
            production,
            sun_up=None if sun is None else sun.state == "above_horizon",
            sun_rising=None if sun is None else bool(sun.attributes.get("rising")),
            minutes_to_setting=None if setting is None else (setting - dt_util.now()).total_seconds() / 60,
        )

    def _notify_rules_failed(self, decision) -> None:
        """The rules raised and every miner holds (decision.HOLD_ALL_SUMMARY): say so once, and
        take it back once a decision works again."""
        failed = decision.summary == HOLD_ALL_SUMMARY
        if failed and not self._rules_failed:
            pn_create(
                self.hass,
                "The decision rules failed, so every miner holds as it is (safety included). "
                "The Home Assistant log has the error; the decision log card shows it too.",
                title="Solar Smart Miner: the rules failed",
                notification_id=f"{DOMAIN}_rules_failed",
            )
        elif not failed and self._rules_failed:
            pn_dismiss(self.hass, f"{DOMAIN}_rules_failed")
        self._rules_failed = failed

    async def async_seed_transition(self) -> None:
        """After a reload during sunset, it stays sunset: the last decision line says so."""
        last = await self.decision_log.async_read_tail(1)
        if not last:
            return
        sun = self.hass.states.get("sun.sun")
        try:
            age_s = (dt_util.now() - datetime.fromisoformat(str(last[-1].get("ts")))).total_seconds()
        except (TypeError, ValueError):
            return
        recent = 0 <= age_s <= SUNSET_GATE_MIN * 60  # sunset lasts at most the gate before sunset
        if recent and (last[-1].get("inputs") or {}).get("sunset") and not (sun and sun.attributes.get("rising")):
            self.transition.sunset = True

    def seed_activity(self) -> None:
        """After a restart or reload, start from the action log: the feed of applied actions, and
        the ramp lock of the last command sent, so a reload can't send the next one too soon."""
        for entry in reversed(self.action_log.history):
            self.activity.appendleft({"kind": "applied", **entry})
        try:
            sent = datetime.fromisoformat(self.action_log.last_sent_ts or "")
            age_s = (dt_util.now() - sent).total_seconds()
        except (TypeError, ValueError):
            return
        if age_s >= 0:
            self._last_change = self._last_unnamed_change = time.monotonic() - age_s

    @staticmethod
    def _farm_proposal(snapshot: CoordinatorSnapshot) -> tuple[str, str]:
        """(fingerprint, text) of the whole farm's proposal: the plans that would change something."""
        if snapshot.decision is None:
            return "", NO_ACTION_TEXT
        miners = {m.miner_id: m for m in snapshot.miners}
        steps = [  # in the order Apply sends them: reductions first
            (miners[mid], plan)
            for mid, plan in snapshot.decision.plans.items()
            if plan.action != ACTION_HOLD and mid in miners
        ]
        steps.sort(key=lambda pair: not _is_reduction(*pair))
        fingerprint = ";".join(
            f"{m.miner_id}:{plan.fingerprint}" for m, plan in sorted(steps, key=lambda p: p[0].miner_id)
        )
        text = " · ".join(f"{m.name} {describe_proposal(m, plan)}" for m, plan in steps)
        return fingerprint, text or NO_ACTION_TEXT

    def _record_proposals(self, snapshot: CoordinatorSnapshot) -> None:
        """Add a feed entry whenever the farm's proposal changes (a first "no action" is not news)."""
        if snapshot.decision is None:
            return
        fingerprint, text = self._farm_proposal(snapshot)
        previous, self._proposed = self._proposed, fingerprint
        if fingerprint == previous or (previous is None and not fingerprint):
            return
        self.activity.appendleft(
            {
                "kind": "proposal",
                "time": dt_util.now().strftime("%H:%M:%S"),
                "plan": text,
                "fingerprint": fingerprint,
            }
        )

    def proposal_text(self) -> str:
        """The farm's current proposal in words, for the card."""
        return self._farm_proposal(self.data)[1] if self.data is not None else NO_ACTION_TEXT

    async def _async_record_action(self, event: CommandEvent) -> None:
        if event.status == RESULT_PENDING and self._grid_unknown_since is not None:
            self._estimate_used = True  # one change per meter outage on an estimated import
        if event.status == RESULT_PENDING and event.plan.reason in HELD_DOWN_REASONS:
            self._brought_down[event.miner_id] = time.monotonic()
        if event.status == RESULT_FAILED and event.calls:
            # Tried to: the miner may restart. A failed send waits out the whole ramp lock, so
            # Automatic doesn't retry it every cycle.
            self._last_change = self._last_unnamed_change = time.monotonic()
        # During a cycle, the plan came from the snapshot being built, not the previous one.
        await self.action_log.async_record(event, self._building or self.data)
        self.activity.appendleft({"kind": "applied", **self.action_log.history[0]})
        self.async_update_listeners()  # the "Last action" sensor and the buttons' availability

    def _ramp_lock_minutes(self) -> float:
        """The restart time after a change (a setting: it depends on the miner type)."""
        return float(self._entry.options.get(CONF_RAMP_LOCK, DEFAULT_RAMP_LOCK_MINUTES))

    def _power_steps(self) -> list[float]:
        return list(self._entry.options.get(CONF_POWER_STEPS) or DEFAULT_POWER_STEPS)

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

    # --- applying the proposed plans --------------------------------------

    def _plan_for(self, miner_id: str) -> tuple[MinerSnapshot, MinerPlan] | None:
        data = self.data
        if data is None or data.decision is None:
            return None
        miner = next((m for m in data.miners if m.miner_id == miner_id), None)
        plan = data.decision.plans.get(miner_id)
        return (miner, plan) if miner is not None and plan is not None else None

    def shown_fingerprint(self, miner_id: str) -> str | None:
        """Fingerprint of the plan on screen for this miner right now."""
        pair = self._plan_for(miner_id)
        return pair[1].fingerprint if pair else None

    def can_apply(self, miner_id: str) -> bool:
        """Whether an Apply for this miner would be allowed to start: Manual mode, a plan that
        does something, and no earlier command still being checked."""
        pair = self._plan_for(miner_id)
        return (
            pair is not None
            and self.controller.mode_refusal(TRIGGER_MANUAL) is None
            and pair[1].action != ACTION_HOLD
            and not self.controller.is_pending(miner_id)
        )

    def can_apply_any(self) -> bool:
        data = self.data
        return data is not None and any(self.can_apply(m.miner_id) for m in data.miners)

    def _notify_changed(self, notification_id: str, message: str, title: str | None = None) -> None:
        pn_create(
            self.hass,
            message,
            title=title or "Solar Smart Miner: the proposal changed",
            notification_id=f"{DOMAIN}_{notification_id}",
        )

    async def _async_refresh_for_apply(self) -> str | None:
        """Read everything again before applying; why that failed, or None."""
        await self.async_refresh()
        if not self.last_update_success or self.data is None:
            return "the latest update failed, so the readings may be stale"
        return None

    async def async_apply_shown(
        self, miner_id: str, fingerprint: str, trigger: str = TRIGGER_MANUAL
    ) -> CommandResult:
        """Apply the plan the owner was looking at, if it is still the plan.

        Refreshes first; if the plan for this miner is no longer the one with this
        fingerprint, nothing runs and a notification says so. No silent substitution.
        """
        shown = self._plan_for(miner_id)
        if shown is None:
            return CommandResult(RESULT_REFUSED, "there is no plan for this miner")
        miner, shown_plan = shown
        if (reason := self.controller.mode_refusal(trigger)) is not None:
            return await self.controller.async_refuse(miner, shown_plan, trigger, reason)
        shown_text = (
            _describe_plan(shown_plan) if shown_plan.fingerprint == fingerprint else fingerprint
        )

        if (reason := await self._async_refresh_for_apply()) is not None:
            return await self.controller.async_refuse(miner, shown_plan, trigger, reason)
        now = self._plan_for(miner_id)
        if now is None:
            return await self.controller.async_refuse(
                miner, shown_plan, trigger, "the miner is gone after the refresh"
            )
        miner, plan = now
        if plan.fingerprint != fingerprint:
            reason = f"changed: from {shown_text} to {_describe_plan(plan)}"
            result = await self.controller.async_refuse(miner, plan, trigger, reason)
            self._notify_changed(
                f"apply_changed_{miner_id.replace('.', '_')}",
                f"{miner.name}: the proposal changed from {shown_text} to "
                f"{_describe_plan(plan)}. Check it and press Apply again.",
            )
            result.notified = True
            return result
        return await self.controller.async_apply(
            miner, plan, trigger=trigger, steps=self._power_steps()
        )

    async def _async_refuse_all(
        self, miner_ids, trigger: str, reason: str
    ) -> dict[str, CommandResult]:
        results = {}
        for mid in miner_ids:
            if pair := self._plan_for(mid):
                results[mid] = await self.controller.async_refuse(*pair, trigger, reason)
        return results

    async def async_apply_all(self, trigger: str = TRIGGER_MANUAL) -> dict[str, CommandResult]:
        """Apply every actionable plan that is unchanged after one refresh, reductions first.

        Stops and step-downs go before step-ups and starts, so the house never briefly
        draws both. Plans that changed, or that a guard refused, are reported together.
        """
        if self.data is None or self.data.decision is None:
            return {}
        shown = {
            mid: plan.fingerprint
            for mid, plan in self.data.decision.plans.items()
            if plan.action != ACTION_HOLD
        }
        if not shown:
            return {}
        if (reason := self.controller.mode_refusal(trigger)) is not None:
            return await self._async_refuse_all(shown, trigger, reason)
        if (reason := await self._async_refresh_for_apply()) is not None:
            return await self._async_refuse_all(shown, trigger, reason)

        # The proposal is one bundle: the plans are worked out together, so if any
        # of them changed after the refresh none is applied.
        now = {
            mid: plan.fingerprint
            for mid, plan in self.data.decision.plans.items()
            if plan.action != ACTION_HOLD
        }
        if now != shown:
            text = self.proposal_text()
            results = await self._async_refuse_all(
                {**shown, **now}, trigger, f"changed: now {text}"
            )
            for result in results.values():
                result.notified = True
            self._notify_changed(
                "apply_changed",
                f"The proposal changed (now: {text}). Check it and press Apply again.",
            )
            return results

        results: dict[str, CommandResult] = {}
        todo = [pair for mid in shown if (pair := self._plan_for(mid))]

        for miner, plan in sorted(todo, key=lambda pair: not _is_reduction(*pair)):  # stable: reductions first
            results[miner.miner_id] = await self.controller.async_apply(
                miner, plan, trigger=trigger, steps=self._power_steps()
            )

        names = {m.miner_id: m.name for m in self.data.miners}
        skipped = [
            f"{names.get(mid, mid)}: {r.reason}" for mid, r in results.items() if r.status == RESULT_REFUSED
        ]
        for result in results.values():
            result.notified = result.status == RESULT_REFUSED  # the notification below covers it
        if skipped:
            self._notify_changed(
                "apply_all_skipped",
                "These were not applied:\n\n- " + "\n- ".join(skipped),
                title="Solar Smart Miner: Apply all skipped some miners",
            )
        return results

    async def _async_apply_auto(self, snapshot: CoordinatorSnapshot) -> None:
        """Automatic mode: apply this cycle's plans, reductions first.

        The ramp lock in the decision paces it: after any change every plan is hold for a few
        minutes. A plan the guards refused is not tried or logged again until the plan or the
        reason changes.
        """
        if self.control_mode != CONTROL_MODE_AUTO or snapshot.decision is None:
            return
        miners = {m.miner_id: m for m in snapshot.miners}
        todo = []
        for mid, plan in snapshot.decision.plans.items():
            if plan.action == ACTION_HOLD or mid not in miners:
                self._auto_refused.pop(mid, None)
            else:
                todo.append((miners[mid], plan))
        steps = self._power_steps()
        for miner, plan in sorted(todo, key=lambda pair: not _is_reduction(*pair)):
            mid = miner.miner_id
            if self.controller.is_pending(mid):
                continue
            reason = self.controller.refusal(miner, plan, TRIGGER_AUTO, steps)
            if reason is not None and self._auto_refused.get(mid) == (plan.fingerprint, reason):
                continue
            try:
                result = await self.controller.async_apply(miner, plan, trigger=TRIGGER_AUTO, steps=steps)
            except Exception:  # noqa: BLE001 - one miner must not stop the update
                _LOGGER.exception("Automatic apply for %s failed", miner.name)
                continue
            if result.status == RESULT_REFUSED:
                self._auto_refused[mid] = (plan.fingerprint, result.reason)
            else:
                self._auto_refused.pop(mid, None)

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
            "temp_target": float(options.get(CONF_TEMP_TARGET, DEFAULT_TEMP_TARGET)),
            "temp_tolerance": float(options.get(CONF_TEMP_TOLERANCE, DEFAULT_TEMP_TOLERANCE)),
            "battery_floor": float(options.get(CONF_BATTERY_FLOOR, DEFAULT_BATTERY_FLOOR)),
        }
        now = situation(self.hass.states.get("sun.sun"), self.transition.name)
        facts = select_facts(self.knowledge, now)
        messages = build_messages(
            snapshot, **settings, knowledge=format_facts(facts, now),
            farm=describe_farm(self._entry.options), recent=self._recent_changes(),
        )
        record = build_record(
            snapshot,
            messages,
            **settings,
            knowledge={"situation": now, "facts": [f.id for f in facts]},
        )
        self._ai_busy = True
        self._ai_last_request = time.monotonic()
        self._entry.async_create_background_task(
            self.hass, self._async_run_ai(messages, record), name=f"{DOMAIN}_ai_advice"
        )

    def _recent_changes(self) -> list[str]:
        """The last commands, newest first, for the AI: when, which miner, what, the result."""
        lines = [
            f"{e.get('time') or '?'} {e.get('miner')}: {e.get('plan')} ({e.get('result')})"
            for e in list(self.action_log.history)[:5]
        ]
        return [f"(now {dt_util.now():%H:%M:%S})", *lines] if lines else []

    async def _async_run_ai(self, messages: list[dict[str, str]], record: dict) -> None:
        data = self._entry.data
        try:
            advice = await async_ask(
                async_get_clientsession(self.hass),
                api_key=(data.get(CONF_OPENROUTER_KEY) or "").strip(),
                model=(data.get(CONF_OPENROUTER_MODEL) or DEFAULT_OPENROUTER_MODEL).strip(),
                messages=messages,
                steps=self._power_steps(),
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
        try:
            await self.controller.async_check_pending({m.miner_id: m for m in miners})
        except Exception:  # noqa: BLE001 - the decision and safety must still run
            _LOGGER.exception("Checking the pending commands failed")

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
        import_min = float(options.get(CONF_IMPORT_MIN, DEFAULT_IMPORT_MIN_W))
        import_max = float(options.get(CONF_IMPORT_MAX, DEFAULT_IMPORT_MAX_W))
        since_change = self._minutes_since_change(miners)
        sun = self.hass.states.get("sun.sun")
        self._update_transition(energy, since_change, sun)
        low_voltage = float(options.get(CONF_LOW_VOLTAGE, DEFAULT_LOW_VOLTAGE_V))
        step_down_delay = float(options.get(CONF_STEP_DOWN_DELAY, DEFAULT_STEP_DOWN_DELAY_MINUTES))
        base_load = options.get(CONF_FARM_BASE_LOAD)
        base_load = float(base_load) if base_load not in (None, "") else None
        meter_lost = self._minutes_meter_lost(energy, estimated_import_w(energy, base_load) is not None)
        if energy.grid_net_w is not None:
            import_now = -energy.grid_net_w
        elif meter_lost is not None and meter_lost >= METER_GRACE_MIN:
            import_now = estimated_import_w(energy, base_load)
        else:
            import_now = None
        # Everything the decision is given besides the readings; logged with them for replays.
        inputs = {
            "profile": options.get(CONF_PROFILE, DEFAULT_PROFILE),
            "temp_target": float(options.get(CONF_TEMP_TARGET, DEFAULT_TEMP_TARGET)),
            "temp_tolerance": float(options.get(CONF_TEMP_TOLERANCE, DEFAULT_TEMP_TOLERANCE)),
            "battery_floor": float(options.get(CONF_BATTERY_FLOOR, DEFAULT_BATTERY_FLOOR)),
            "power_steps": self._power_steps(),
            "import_min_w": import_min,
            "import_max_w": import_max,
            "minutes_since_change": since_change,
            "ramp_lock_minutes": self._ramp_lock_minutes(),
            "ramp_done": list(self._ramp_done),
            "minutes_import_high": self._minutes_import_high(
                import_now, import_max, self._minutes_since_last_change()
            ),
            "step_down_delay_minutes": step_down_delay,
            "morning_step_down_delay_minutes": float(
                options.get(CONF_MORNING_STEP_DOWN_DELAY, DEFAULT_MORNING_STEP_DOWN_DELAY_MINUTES)
            ),
            "sun_up": None if sun is None else sun.state == "above_horizon",
            "sunrise": self.transition.sunrise,
            "sunset": self.transition.sunset,
            "meter_lost_minutes": meter_lost,
            "base_load_w": base_load,
            "estimate_used": self._estimate_used,
            "voltage_low_seconds": self._seconds_voltage_low(energy, low_voltage, since_change),
            "voltage_debounce_s": float(options.get(CONF_VOLTAGE_DEBOUNCE, DEFAULT_VOLTAGE_DEBOUNCE_S)),
            "low_voltage_v": low_voltage,
            "held_down": self._held_down(step_down_delay),
        }
        decision = build_decision(snapshot, **inputs)
        try:
            await self.decision_log.async_record(snapshot, inputs, decision)
        except Exception:  # noqa: BLE001 - a log line must never stop the cycle
            _LOGGER.exception("Writing the decision log failed")
        self._notify_rules_failed(decision)
        # Only record changes, so the history reads as a log of what shifted.
        if not self._history or self._history[0]["summary"] != decision.summary:
            self._history.appendleft(
                {"time": dt_util.now().strftime("%H:%M:%S"), "summary": decision.summary}
            )
        snapshot.decision = decision
        self._record_proposals(snapshot)
        self._building = snapshot
        try:
            await self._async_apply_auto(snapshot)
        finally:
            self._building = None
        snapshot.decision_history = list(self._history)
        self._maybe_request_ai(snapshot)
        # After the request: a very fast answer can already be in by now.
        snapshot.ai_advice = self._ai_advice

        _LOGGER.debug("Decision cycle:\n  %s", "\n  ".join(decision.trace))
        return snapshot

