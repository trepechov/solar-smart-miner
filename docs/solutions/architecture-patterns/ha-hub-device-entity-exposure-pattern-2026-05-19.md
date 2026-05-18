---
title: "HA Hub Device Pattern: Exposing Coordinator Data as Sensor and Button Entities"
date: 2026-05-19
category: architecture-patterns
module: solar_smart_miner / sensor + button + coordinator
problem_type: architecture_pattern
component: assistant
severity: medium
applies_when:
  - Adding a HA custom integration that reads data via a DataUpdateCoordinator and needs to expose it as UI-visible sensor entities
  - Grouping all integration entities under a single hub device so they appear as one card in the HA dashboard
  - Exposing per-item (e.g. per-miner) sensor entities driven by a shared coordinator snapshot
  - Reading state from a sibling HA integration without importing its code
  - Discovering sibling integration entities by unit of measurement when device class is absent
related_components:
  - coordinator
  - protocols
  - config_flow
tags:
  - home-assistant
  - sensor-entity
  - coordinator-entity
  - hub-device
  - device-info
  - hass-miner
  - entity-exposure
  - dashboard
---

# HA Hub Device Pattern: Exposing Coordinator Data as Sensor and Button Entities

## Context

The Solar Smart Miner coordinator (`SolarMinerCoordinator`) was already reading solar production, grid consumption, battery SOC, and per-miner telemetry (power draw, temperature, power limit, hashrate, efficiency) from Home Assistant's state machine on every poll cycle. However, none of this data was surfaced as HA entities — there was no registered hub device and the integration had no presence in the HA device list or dashboard.

The gap: coordinator data is rich and structured but invisible to the HA UI. Users had no way to see live miner or solar state, no entity history graphs, no dashboard card. The only sensor was a single `TotalMinerConsumptionSensor` with no device attachment.

This pattern documents how to close that gap: register a hub device, expose coordinator state as `CoordinatorEntity` sensor subclasses, use a parametric class for per-miner metrics, discover sibling integration entities via registry lookups, and provide a self-service button that generates a Lovelace card YAML.

## Guidance

### 1. Register a hub `DeviceInfo` shared by all entities

Define a single helper that produces a consistent `DeviceInfo` using the config entry ID as the stable identifier. Every entity (sensor and button) calls this helper — no entity registers a separate device.

```python
def _hub_device_info(entry: ConfigEntry) -> DeviceInfo:
    return DeviceInfo(
        identifiers={(DOMAIN, entry.entry_id)},
        name=entry.title,
    )
```

Use `entry.entry_id` (not `entry.unique_id`) as the identifier — `entry.entry_id` is always set while `entry.unique_id` may be `None`. Entity unique IDs use a fallback: `entry.unique_id or entry.entry_id`.

### 2. Subclass `CoordinatorEntity` for all sensors; attach device in `__init__`

All sensor classes inherit from both `CoordinatorEntity[SolarMinerCoordinator]` and `SensorEntity`. The hub device is attached by setting `self._attr_device_info = _hub_device_info(entry)` in `__init__`. HA registers the device automatically when any entity referencing it is added.

```python
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
```

### 3. Use a parametric class for per-miner metrics

Rather than five separate classes for power, temperature, limit, hashrate, and efficiency, define a metric descriptor list and a single parametric `MinerSensor` class. Each descriptor carries `key`, `label`, `device_class`, `unit`, and `state_class`. The class reads the correct field via `getattr`.

```python
_MINER_METRICS: list[dict] = [
    {"key": "power_w",        "label": "power draw",  "device_class": SensorDeviceClass.POWER,       "unit": UnitOfPower.WATT,          "state_class": SensorStateClass.MEASUREMENT},
    {"key": "temperature_c",  "label": "temperature", "device_class": SensorDeviceClass.TEMPERATURE,  "unit": UnitOfTemperature.CELSIUS,  "state_class": SensorStateClass.MEASUREMENT},
    {"key": "power_limit_w",  "label": "power limit", "device_class": SensorDeviceClass.POWER,       "unit": UnitOfPower.WATT,          "state_class": SensorStateClass.MEASUREMENT},
    {"key": "hashrate_th",    "label": "hashrate",    "device_class": None,                          "unit": "TH/s",                    "state_class": SensorStateClass.MEASUREMENT},
    {"key": "efficiency_jth", "label": "efficiency",  "device_class": None,                          "unit": "J/TH",                    "state_class": SensorStateClass.MEASUREMENT},
]

class MinerSensor(CoordinatorEntity[SolarMinerCoordinator], SensorEntity):
    _attr_has_entity_name = True

    def __init__(self, coordinator, entry, miner_ip, miner_name, metric):
        super().__init__(coordinator)
        ip_slug = miner_ip.replace(".", "_")
        self._miner_ip = miner_ip
        self._metric_key = metric["key"]
        self._attr_unique_id = f"{entry.unique_id or entry.entry_id}_{ip_slug}_{metric['key']}"
        self._attr_name = f"{miner_name} {metric['label']}"
        self._attr_device_class = metric["device_class"]
        self._attr_native_unit_of_measurement = metric["unit"]
        self._attr_state_class = metric["state_class"]
        self._attr_device_info = _hub_device_info(entry)

    @property
    def native_value(self):
        if self.coordinator.data is None:
            return None
        miner = next((m for m in self.coordinator.data.miners if m.ip == self._miner_ip), None)
        if miner is None or not miner.is_available:
            return None
        return getattr(miner, self._metric_key)
```

### 4. Discover sibling integration entities via device registry + unit of measurement

The coordinator cannot import hass-miner directly. Instead it reads the hass-miner device from the HA device registry using `connections=("ip", miner_ip)` as the primary lookup, falling back to scanning `configuration_url`. Entity discovery filters by `platform == HASS_MINER_PLATFORM` and matches by unit of measurement for non-standard sensors (TH/s, J/TH):

```python
HASS_MINER_PLATFORM = "miner"  # hass-miner's integration domain — NOT "hass_miner"

target_device = dr.async_get_device(connections={("ip", miner_ip)})
if target_device is None:
    for device in dr.devices.values():
        if device.configuration_url and miner_ip in device.configuration_url:
            target_device = device
            break

device_entities = [
    e for e in er.entities.values()
    if e.device_id == target_device.id and e.platform == HASS_MINER_PLATFORM
]
hashrate_entry = next(
    (e for e in device_entities if e.domain == "sensor" and e.unit_of_measurement == "TH/s"), None
)
efficiency_entry = next(
    (e for e in device_entities if e.domain == "sensor" and e.unit_of_measurement == "J/TH"), None
)
```

**Key pitfall:** the hass-miner platform name is `"miner"` (its integration domain), not `"hass_miner"`. Using the wrong string causes all entity lookups to silently return nothing.

### 5. Add a button entity that generates Lovelace card YAML via persistent notification

A `ButtonEntity` on the hub device queries the entity registry at press time, collects sensor entity IDs belonging to the hub device, and creates a persistent HA notification with ready-to-paste Lovelace YAML. No custom frontend work required.

```python
class AddToDashboardButton(ButtonEntity):
    _attr_has_entity_name = True
    _attr_name = "Add to dashboard"
    _attr_icon = "mdi:view-dashboard-variant"

    async def async_press(self) -> None:
        dr = device_registry.async_get(self.hass)
        er = entity_registry.async_get(self.hass)
        hub_device = dr.async_get_device(identifiers={(DOMAIN, self._entry.entry_id)})
        entity_ids = sorted(
            e.entity_id for e in er.entities.values()
            if e.device_id == hub_device.id and e.domain == "sensor" and e.platform == DOMAIN
        )
        lines = ["type: entities", f"title: {self._entry.title}", "entities:"]
        for eid in entity_ids:
            lines.append(f"  - {eid}")
        pn_create(self.hass, f"```yaml\n{chr(10).join(lines)}\n```", ...)
```

The button inherits from `ButtonEntity` directly (no `CoordinatorEntity` mixin) since it only needs the registry at press time, not coordinator data.

### 6. Reload the config entry after adding a miner to materialise new entities

Entity creation happens in `async_setup_entry`. When a miner is added via the options flow, schedule a reload to create the new miner's sensor entities without manual intervention:

```python
# In options flow async_step_add_miner, after updating entry.data:
self.hass.async_create_task(
    self.hass.config_entries.async_reload(self._config_entry.entry_id)
)
return self.async_create_entry(data=self._pending_options)
```

Use `async_create_task` (not `await`) — the options flow must return its result synchronously; the reload runs after the flow completes.

Also initialise `_pending_options` from `config_entry.options` at options flow construction time so that `add_miner` can save without first visiting `edit_settings`:

```python
def __init__(self, config_entry):
    self._config_entry = config_entry
    self._pending_options = dict(config_entry.options)  # not {} — preserves current settings
```

## Why This Matters

- **Coordinator data is only useful if it is visible.** Exposing coordinator state as entities unlocks HA's native features: history graphs, automations, dashboard cards, Logbook — all for free.
- **Hub device pattern keeps the dashboard story simple.** One device, one Entities card. Per-miner sub-devices would add navigation complexity without benefit for a small set of miners.
- **Registry-based sibling integration reads maintain clean integration boundaries.** The coordinator never imports hass-miner code. It reads HA's public registries, which is the correct HA architecture for one integration consuming another's state.
- **Unit-of-measurement matching is the right heuristic for non-standard sensors.** hass-miner does not assign standard HA device classes to hashrate or efficiency sensors. Unit strings (`TH/s`, `J/TH`) are stable and unique within a miner's entity set.
- **Reload on miner add avoids dynamic entity management complexity.** Using a config entry reload at setup time is the idiomatic, simpler approach compared to coordinating `async_add_entities` calls with stale entity cleanup.

## When to Apply

- Building a HA custom integration that reads data into a coordinator and needs to expose that data as dashboard-visible entities.
- Managing multiple child devices (miners, sensors, thermostats) that should all appear under a single hub device in the HA UI.
- Reading state from a sibling HA integration without a code dependency — use device and entity registry lookups.
- Sibling integration entities use non-standard device classes and must be discovered by unit of measurement.
- Adding items dynamically via an options flow where each new item requires new entities.
- Giving users a quick path to a dashboard card without writing custom Lovelace frontend.

## Examples

**Full `async_setup_entry` in sensor.py** — complete entity registration:

```python
async def async_setup_entry(hass, entry, async_add_entities):
    coordinator = entry.runtime_data
    entities = [
        TotalMinerConsumptionSensor(coordinator, entry),
        SolarProductionSensor(coordinator, entry),
    ]
    if entry.data.get(CONF_GRID_ENTITY, ""):
        entities.append(GridConsumptionSensor(coordinator, entry))
    if entry.data.get(CONF_BATTERY_ENTITY, ""):
        entities.append(BatterySocSensor(coordinator, entry))
    for miner_conf in entry.data.get(CONF_MINERS, []):
        miner_ip = miner_conf.get(CONF_MINER_IP, "")
        miner_name = miner_conf.get(CONF_MINER_NAME, miner_ip)
        for metric in _MINER_METRICS:
            entities.append(MinerSensor(coordinator, entry, miner_ip, miner_name, metric))
    async_add_entities(entities)
```

**Before (wrong platform name — silently returns no entities):**

```python
# Wrong: "hass_miner" is the package name, not the integration domain
device_entities = [e for e in er.entities.values()
    if e.device_id == target_device.id and e.platform == "hass_miner"]

# Wrong: scanning identifiers for "hass_miner" key
for device in dr.devices.values():
    if any(ident_domain == "hass_miner" and ident_value == miner_ip
           for ident_domain, ident_value in device.identifiers):
        target_device = device
```

**After (correct):**

```python
HASS_MINER_PLATFORM = "miner"  # integration domain, not the package name

device_entities = [e for e in er.entities.values()
    if e.device_id == target_device.id and e.platform == HASS_MINER_PLATFORM]

target_device = dr.async_get_device(connections={("ip", miner_ip)})
```

## Related

- [Hub entity exposure plan](../../plans/2026-05-18-002-feat-hub-device-entity-exposure-plan.md) — the implementation plan this pattern was derived from
- [Coordinator sensor reads plan](../../plans/2026-05-18-001-feat-coordinator-sensor-reads-logging-plan.md) — precursor work: reading hass-miner entity state into the coordinator
- [Dashboard card entities requirements](../../brainstorms/2026-05-18-dashboard-card-entities-requirements.md) — origin requirements (R1–R7)
- **Relevant source files:**
  - `custom_components/solar_smart_miner/sensor.py` — hub DeviceInfo, hub sensors, parametric MinerSensor, async_setup_entry
  - `custom_components/solar_smart_miner/button.py` — AddToDashboardButton, persistent notification YAML
  - `custom_components/solar_smart_miner/coordinator.py` — `_async_read_miners`, sibling integration entity discovery
  - `custom_components/solar_smart_miner/const.py` — `HASS_MINER_PLATFORM = "miner"`
  - `custom_components/solar_smart_miner/protocols.py` — `MinerSnapshot` dataclass
