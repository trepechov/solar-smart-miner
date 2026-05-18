---
date: 2026-05-18
topic: dashboard-card-entities
---

# Dashboard Card — Hub Entity Exposure

## Summary

Expose all data already gathered by the U11 coordinator as proper HA sensor entities, register a hub `DeviceInfo`, and let the operator add a standard HA Entities card to their dashboard showing solar/energy state and per-miner readings in one place. Also completes R4 by extending `MinerSnapshot` and coordinator reads to include hashrate and efficiency.

---

## Problem Frame

U11 delivers a coordinator that reads solar production, grid consumption, battery SOC, and full per-miner state every poll cycle. None of that data is visible in the HA UI beyond the single `TotalMinerConsumptionSensor`. The integration has no device registered, so it cannot be added to a dashboard as a card. This work surfaces the coordinator's data as real HA entities and ties them to a hub device.

---

## Requirements

**Hub device registration**

- R1. A `DeviceInfo` for the Solar Smart Miner hub is registered via the config entry; all entities attach to this device. The device name is the config entry title (set during setup wizard).

**Hub-level sensor entities**

- R2. A `Solar production` sensor (W, `POWER` device class) is exposed; value comes from `coordinator.data.energy.solar_production_w`; returns `None` when `solar_fault=True`.
- R3. A `Grid consumption` sensor (W, `POWER` device class) is exposed when a grid entity is configured; returns `None` when the underlying entity is unavailable.
- R4. A `Battery SOC` sensor (%, `BATTERY` device class) is exposed when a battery entity is configured; returns `None` when unconfigured or unavailable.
- R5. The existing `TotalMinerConsumptionSensor` is updated to attach to the hub `DeviceInfo`.

**Per-miner sensor entities**

One set of sensors is created per configured miner at config entry setup. Entities are named `{miner name} — {metric}`.

- R6. `{miner name} — Power draw` sensor (W, `POWER` device class); value from `MinerSnapshot.power_w`; returns `None` when miner is unavailable.
- R7. `{miner name} — Temperature` sensor (°C, `TEMPERATURE` device class); value from `MinerSnapshot.temperature_c`; returns `None` when unavailable.
- R8. `{miner name} — Power limit` sensor (W, `POWER` device class); value from `MinerSnapshot.power_limit_w`; returns `None` when unavailable.
- R9. `{miner name} — Hashrate` sensor (TH/s, no standard device class); value from `MinerSnapshot.hashrate_th`; returns `None` when unavailable.
- R10. `{miner name} — Efficiency` sensor (J/TH, no standard device class); value from `MinerSnapshot.efficiency_jth`; returns `None` when unavailable.

**Data model extension (completing R4 from origin doc)**

- R11. `MinerSnapshot` gains `hashrate_th: float | None = None` and `efficiency_jth: float | None = None`.
- R12. The coordinator's `_find_hass_miner_entities` and `_async_read_miners` are extended to discover and read hashrate (matched by unit `TH/s`) and efficiency (matched by unit `J/TH`) sensors from the miner's hass-miner device entities.

**Entity lifecycle**

- R13. All entities use `CoordinatorEntity` and update on every coordinator poll cycle.
- R14. Adding or removing a miner (via the options flow) takes effect after the config entry is reloaded; no hot-swap required.

---

## Scope Boundaries

### In scope

- Hub `DeviceInfo` registration
- Hub-level sensors: solar production, grid consumption, battery SOC, total miner consumption
- Per-miner sensors: power draw, temperature, power limit, hashrate, efficiency
- `MinerSnapshot` + coordinator extension for hashrate and efficiency (completes R4)

### Deferred to follow-up work (U7 remainder)

- Profile selector entity (`select` platform)
- Dry-run switch entity (`switch` platform)
- Last-decision text sensor
- Coordinator status sensor (running / safety override / fault)

### Out of scope

- Custom Lovelace card — the standard HA Entities card with device filter is sufficient
- Per-miner HA devices — all entities live under the hub device

---

## Key Decisions

- **All entities under the hub device:** Keeps the dashboard story simple — one card, one device filter. Per-miner sub-devices are not needed for this use case and add complexity without benefit at this stage.
- **Standard HA Entities card, no custom card:** No Lovelace frontend work required; the operator adds an Entities card filtered by the hub device in the HA UI.
- **Hashrate/efficiency matched by unit of measurement:** hass-miner does not assign standard HA device classes to these sensors; matching by `TH/s` and `J/TH` within the miner's device entities is the most robust heuristic without coupling to hass-miner's internal entity naming.
- **Reload on miner add/remove:** Entity creation at setup, not dynamically. HA handles stale entity IDs gracefully; reload is the standard pattern for config entry changes.

---

## Acceptance Examples

- AE1. After setup with solar, grid, battery, and two miners configured, the hub device appears in HA's device list; an Entities card filtered to that device shows: Solar production, Grid consumption, Battery SOC, Total miner consumption, and two sets of per-miner sensors (power draw, temperature, power limit, hashrate, efficiency).
- AE2. When a miner's hass-miner entities are unavailable (`is_available=False`), its five sensor entities show as `unavailable` in the HA UI; hub-level and other miner entities are unaffected.
- AE3. When no battery entity is configured, the Battery SOC entity is not created and does not appear on the card.
- AE4. When `solar_fault=True`, the Solar production entity returns `None` (shows as unavailable); the other hub and miner entities continue updating normally.

---

## Dependencies / Assumptions

- U11 coordinator is implemented and wired in `__init__.py` (already done).
- hass-miner must be installed and miners configured for per-miner entities to show live data; entities are created regardless but return `None` until hass-miner entities are discoverable.
- hass-miner exposes hashrate and efficiency sensors with units `TH/s` and `J/TH` respectively on the miner's device.

---

## Sources & References

- **Parent plan:** [docs/plans/2026-05-15-001-feat-solar-smart-miner-ha-integration-plan.md](docs/plans/2026-05-15-001-feat-solar-smart-miner-ha-integration-plan.md) — this work is a scoped slice of U7 plus R4 completion
- **Origin requirements:** [docs/brainstorms/solar-smart-miner-requirements.md](docs/brainstorms/solar-smart-miner-requirements.md) — R4 (hashrate/efficiency reads), R27 (HA entity conventions)
- **U11 plan:** [docs/plans/2026-05-18-001-feat-coordinator-sensor-reads-logging-plan.md](docs/plans/2026-05-18-001-feat-coordinator-sensor-reads-logging-plan.md) — data model and coordinator this work builds on
