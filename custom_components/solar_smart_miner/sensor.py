"""Sensor platform for Solar Smart Miner."""
from __future__ import annotations

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import PERCENTAGE, UnitOfPower, UnitOfTemperature
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .config_flow import CONF_BATTERY_ENTITY, CONF_GRID_ENTITY
from .const import CONTROL_MODE_PREVIEW, DOMAIN
from .coordinator import SolarMinerCoordinator
from .protocols import MinerSnapshot


def _hub_device_info(entry: ConfigEntry) -> DeviceInfo:
    return DeviceInfo(
        identifiers={(DOMAIN, entry.entry_id)},
        name=entry.title,
    )


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: SolarMinerCoordinator = entry.runtime_data
    entities: list[SensorEntity] = [
        TotalMinerConsumptionSensor(coordinator, entry),
        SolarProductionSensor(coordinator, entry),
        NetGridPowerSensor(coordinator, entry),
        AvailableForMinersSensor(coordinator, entry),
        DecisionLogSensor(coordinator, entry),
        AiAdviceSensor(coordinator, entry),
        LastActionSensor(coordinator, entry),
        ActivitySensor(coordinator, entry),
    ]

    if entry.data.get(CONF_GRID_ENTITY, ""):
        entities.append(GridConsumptionSensor(coordinator, entry))

    if entry.data.get(CONF_BATTERY_ENTITY, ""):
        entities.append(BatterySocSensor(coordinator, entry))

    async_add_entities(entities)

    # Miners come from hass-miner and can appear after startup (hass-miner may
    # load later, or a miner is added there), so add their sensors as they show up.
    known_miner_ids: set[str] = set()

    @callback
    def _add_new_miners() -> None:
        if coordinator.data is None:
            return
        new_entities = []
        for miner in coordinator.data.miners:
            if miner.miner_id in known_miner_ids:
                continue
            known_miner_ids.add(miner.miner_id)
            new_entities.extend(
                MinerSensor(coordinator, entry, miner.miner_id, miner.name, metric)
                for metric in _MINER_METRICS
            )
        if new_entities:
            async_add_entities(new_entities)

    _add_new_miners()
    entry.async_on_unload(coordinator.async_add_listener(_add_new_miners))


# ---------------------------------------------------------------------------
# Hub-level sensors
# ---------------------------------------------------------------------------

class TotalMinerConsumptionSensor(CoordinatorEntity[SolarMinerCoordinator], SensorEntity):
    _attr_has_entity_name = True
    _attr_device_class = SensorDeviceClass.POWER
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfPower.WATT

    def __init__(self, coordinator: SolarMinerCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.unique_id or entry.entry_id}_total_miner_consumption"
        self._attr_name = "Total miner consumption"
        self._attr_device_info = _hub_device_info(entry)

    @property
    def native_value(self) -> float | None:
        if self.coordinator.data is None:
            return None
        return self.coordinator.data.energy.miner_consumption_sum_w


class SolarProductionSensor(CoordinatorEntity[SolarMinerCoordinator], SensorEntity):
    _attr_has_entity_name = True
    _attr_device_class = SensorDeviceClass.POWER
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfPower.WATT

    def __init__(self, coordinator: SolarMinerCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.unique_id or entry.entry_id}_solar_production"
        self._attr_name = "Solar production"
        self._attr_device_info = _hub_device_info(entry)

    @property
    def native_value(self) -> float | None:
        if self.coordinator.data is None:
            return None
        return self.coordinator.data.energy.solar_production_w


class GridConsumptionSensor(CoordinatorEntity[SolarMinerCoordinator], SensorEntity):
    _attr_has_entity_name = True
    _attr_device_class = SensorDeviceClass.POWER
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfPower.WATT

    def __init__(self, coordinator: SolarMinerCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.unique_id or entry.entry_id}_grid_consumption"
        self._attr_name = "House consumption"
        self._attr_device_info = _hub_device_info(entry)

    @property
    def native_value(self) -> float | None:
        if self.coordinator.data is None:
            return None
        return self.coordinator.data.energy.grid_consumption_w


class NetGridPowerSensor(CoordinatorEntity[SolarMinerCoordinator], SensorEntity):
    """Grid balance: positive = exporting, negative = importing."""

    _attr_has_entity_name = True
    _attr_device_class = SensorDeviceClass.POWER
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfPower.WATT

    def __init__(self, coordinator: SolarMinerCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.unique_id or entry.entry_id}_grid_net"
        self._attr_name = "Grid export"
        self._attr_device_info = _hub_device_info(entry)

    @property
    def native_value(self) -> float | None:
        if self.coordinator.data is None:
            return None
        return self.coordinator.data.energy.grid_net_w


class AvailableForMinersSensor(CoordinatorEntity[SolarMinerCoordinator], SensorEntity):
    """Power the miners could draw without importing from the grid."""

    _attr_has_entity_name = True
    _attr_device_class = SensorDeviceClass.POWER
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfPower.WATT

    def __init__(self, coordinator: SolarMinerCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.unique_id or entry.entry_id}_available_for_miners"
        self._attr_name = "Available for miners"
        self._attr_device_info = _hub_device_info(entry)

    @property
    def native_value(self) -> float | None:
        if self.coordinator.data is None:
            return None
        return self.coordinator.data.energy.available_for_miners_w


class DecisionLogSensor(CoordinatorEntity[SolarMinerCoordinator], SensorEntity):
    """Latest decision summary; the full reasoning trace lives in attributes."""

    _attr_has_entity_name = True
    _attr_icon = "mdi:text-box-search-outline"
    # Refreshed every poll — keep the bulky attributes out of the recorder DB.
    _unrecorded_attributes = frozenset({"trace", "history", "proposals", "plans"})

    def __init__(self, coordinator: SolarMinerCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.unique_id or entry.entry_id}_decision_log"
        self._attr_name = "Decision log"
        self._attr_device_info = _hub_device_info(entry)

    @property
    def native_value(self) -> str | None:
        if self.coordinator.data is None or self.coordinator.data.decision is None:
            return None
        return self.coordinator.data.decision.summary[:255]

    @property
    def extra_state_attributes(self) -> dict | None:
        data = self.coordinator.data
        if data is None or data.decision is None:
            return None
        names = {m.miner_id: m.name for m in data.miners}
        return {
            "preview_only": self.coordinator.control_mode == CONTROL_MODE_PREVIEW,
            "trace": data.decision.trace,
            "proposals": {names.get(mid, mid): w for mid, w in data.decision.proposals.items()},
            "plans": {
                names.get(mid, mid): {
                    "action": plan.action,
                    "limit_w": plan.limit_w,
                    "method": plan.method,
                    "reason": plan.reason,
                }
                for mid, plan in data.decision.plans.items()
            },
            "history": data.decision_history,
        }


class AiAdviceSensor(CoordinatorEntity[SolarMinerCoordinator], SensorEntity):
    """The AI advisor's latest comment on the decision (advisory only: never applied)."""

    _attr_has_entity_name = True
    _attr_icon = "mdi:robot-outline"
    _unrecorded_attributes = frozenset({"response", "actions", "history"})

    def __init__(self, coordinator: SolarMinerCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.unique_id or entry.entry_id}_ai_advice"
        self._attr_name = "AI advice"
        self._attr_device_info = _hub_device_info(entry)

    @property
    def native_value(self) -> str | None:
        if not self.coordinator.ai_enabled:
            return "Off"
        advice = self.coordinator.data.ai_advice if self.coordinator.data else None
        if advice is None:
            return "Waiting for the first answer"
        if advice.error:
            return f"Error: {advice.error}"[:255]
        return (advice.summary or advice.text)[:255]

    @property
    def extra_state_attributes(self) -> dict | None:
        log = self.coordinator.ai_log
        attrs: dict = {
            "preview_only": self.coordinator.control_mode == CONTROL_MODE_PREVIEW,
            "history": list(log.history),
            "log_file": str(log.path),
        }
        advice = self.coordinator.data.ai_advice if self.coordinator.data else None
        if advice is not None:
            attrs.update(
                response=advice.text,
                actions=advice.actions,
                error=advice.error,
                model=advice.model,
                requested_at=advice.requested_at,
                latency_s=advice.latency_s,
            )
        return attrs


class LastActionSensor(CoordinatorEntity[SolarMinerCoordinator], SensorEntity):
    """The last command sent to a miner and how it ended; the recent ones are in `history`."""

    _attr_has_entity_name = True
    _attr_icon = "mdi:history"
    _unrecorded_attributes = frozenset({"history"})

    def __init__(self, coordinator: SolarMinerCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.unique_id or entry.entry_id}_last_action"
        self._attr_name = "Last action"
        self._attr_device_info = _hub_device_info(entry)

    @property
    def native_value(self) -> str:
        history = self.coordinator.action_log.history
        if not history:
            return "None yet"
        last = history[0]
        return f"{last['miner']}: {last['plan']} ({last['result']})"[:255]

    @property
    def extra_state_attributes(self) -> dict:
        log = self.coordinator.action_log
        return {"history": list(log.history), "log_file": str(log.path)}


class ActivitySensor(CoordinatorEntity[SolarMinerCoordinator], SensorEntity):
    """The farm's proposal now, and proposals and applied actions in one newest-first feed."""

    _attr_has_entity_name = True
    _attr_icon = "mdi:timeline-text-outline"
    _unrecorded_attributes = frozenset({"feed", "proposal", "fingerprint"})

    def __init__(self, coordinator: SolarMinerCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.unique_id or entry.entry_id}_activity"
        self._attr_name = "Activity"
        self._attr_device_info = _hub_device_info(entry)

    @property
    def native_value(self) -> str:
        if self.coordinator.data is None:
            return "Nothing yet"
        return self.coordinator.proposal_text()[:255]

    @property
    def extra_state_attributes(self) -> dict:
        fingerprint, _ = (
            self.coordinator._farm_proposal(self.coordinator.data) if self.coordinator.data else ("", "")
        )
        seen_proposal = False
        feed = []
        for entry in self.coordinator.activity:  # newest first
            current = False
            if entry["kind"] == "proposal" and not seen_proposal:
                seen_proposal = True  # only the newest proposal can still stand
                current = bool(fingerprint) and entry.get("fingerprint") == fingerprint
            feed.append({**entry, "current": current})
        return {
            "proposal": self.coordinator.proposal_text(),
            "fingerprint": fingerprint,
            "feed": feed,
        }


class BatterySocSensor(CoordinatorEntity[SolarMinerCoordinator], SensorEntity):
    _attr_has_entity_name = True
    _attr_device_class = SensorDeviceClass.BATTERY
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = PERCENTAGE

    def __init__(self, coordinator: SolarMinerCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.unique_id or entry.entry_id}_battery_soc"
        self._attr_name = "Battery SOC"
        self._attr_device_info = _hub_device_info(entry)

    @property
    def native_value(self) -> float | None:
        if self.coordinator.data is None:
            return None
        return self.coordinator.data.energy.battery_soc_pct


# ---------------------------------------------------------------------------
# Per-miner sensors
# ---------------------------------------------------------------------------

_MINER_METRICS: list[dict] = [
    {
        "key": "power_w",
        "label": "power draw",
        "device_class": SensorDeviceClass.POWER,
        "unit": UnitOfPower.WATT,
        "state_class": SensorStateClass.MEASUREMENT,
    },
    {
        "key": "temperature_c",
        "label": "temperature",
        "device_class": SensorDeviceClass.TEMPERATURE,
        "unit": UnitOfTemperature.CELSIUS,
        "state_class": SensorStateClass.MEASUREMENT,
    },
    {
        "key": "power_limit_w",
        "label": "power limit",
        "device_class": SensorDeviceClass.POWER,
        "unit": UnitOfPower.WATT,
        "state_class": SensorStateClass.MEASUREMENT,
    },
    {
        "key": "hashrate_th",
        "label": "hashrate",
        "device_class": None,
        "unit": "TH/s",
        "state_class": SensorStateClass.MEASUREMENT,
    },
    {
        "key": "efficiency_jth",
        "label": "efficiency",
        "device_class": None,
        "unit": "J/TH",
        "state_class": SensorStateClass.MEASUREMENT,
    },
]


class MinerSensor(CoordinatorEntity[SolarMinerCoordinator], SensorEntity):
    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: SolarMinerCoordinator,
        entry: ConfigEntry,
        miner_id: str,
        miner_name: str,
        metric: dict,
    ) -> None:
        super().__init__(coordinator)
        id_slug = miner_id.replace(".", "_")
        self._miner_id = miner_id
        self._metric_key: str = metric["key"]
        self._attr_unique_id = (
            f"{entry.unique_id or entry.entry_id}_{id_slug}_{metric['key']}"
        )
        self._attr_name = f"{miner_name} {metric['label']}"
        self._attr_device_class = metric["device_class"]
        self._attr_native_unit_of_measurement = metric["unit"]
        self._attr_state_class = metric["state_class"]
        self._attr_device_info = _hub_device_info(entry)

    @property
    def native_value(self) -> float | None:
        if self.coordinator.data is None:
            return None
        miner: MinerSnapshot | None = next(
            (m for m in self.coordinator.data.miners if m.miner_id == self._miner_id),
            None,
        )
        if miner is None or not miner.is_available:
            return None
        return getattr(miner, self._metric_key)
