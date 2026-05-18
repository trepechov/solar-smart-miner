---
title: "feat: Coordinator full sensor reads and snapshot logging"
type: feat
status: completed
date: 2026-05-18
origin: docs/brainstorms/solar-smart-miner-requirements.md
parent_plan: docs/plans/2026-05-15-001-feat-solar-smart-miner-ha-integration-plan.md
---

# feat: Coordinator full sensor reads and snapshot logging

## Summary

Implement the first real iteration of `SolarMinerCoordinator`: extend it to read all configured sensors — solar production, grid consumption, battery SOC, and per-miner state — on the configured 60–3600 s interval, then log a human-readable snapshot to the HA log after every poll. This unit delivers visible confirmation that data is flowing before any AI or safety logic is layered on top. It corresponds to **U11** in the parent plan.

The coordinator currently reads only solar production (or a mock substitute) and is never actually started — `__init__.py` does not construct it. This plan closes both gaps.

---

## Problem Frame

The current coordinator stub reads one entity and is never instantiated by `async_setup_entry`. Grid consumption, battery SOC, and miner sensors are unconfigured. There is no way to confirm that sensor data flows correctly without running the full AI decision pipeline. This plan installs the "read and log" foundation that makes the system observable without any AI involvement.

---

## Requirements

Carried from the origin document via the parent plan:
- **R1** — Read solar production entity
- **R2** — Read grid consumption entity
- **R3** — Read battery SOC entity (optional; skip when not configured)
- **R4** — Read per-miner sensors from hass-miner entities
- **R9** — Polling interval configurable (60–3600 s, default 300 s)

---

## Scope Boundaries

### In scope

- Extend `EnergySnapshot` and add `MinerSnapshot`, `CoordinatorSnapshot` data classes
- Full sensor read methods in the coordinator
- Human-readable INFO log after every poll cycle
- Wire the coordinator into `__init__.py`

### Deferred to follow-up work

- Safety layer evaluation (U4 in parent plan)
- AI decision engine (U5 in parent plan)
- Power limit application to miners (U6 in parent plan)
- HA entity platform files exposing coordinator data (U7 in parent plan)
- `_async_apply_power_limit` method (remainder of U11 in parent plan — lands when U4/U5 are ready to wire in)

---

## Key Technical Decisions

- **`solar_fault=True` instead of `UpdateFailed`:** When the solar entity is unavailable, the coordinator sets `solar_fault=True` in the snapshot and continues rather than raising `UpdateFailed`. The existing test `test_coordinator_raises_when_real_solar_unavailable` must be updated to assert `solar_fault=True` and no exception. Raising `UpdateFailed` would mark the whole config entry as failed; the new behavior lets the coordinator keep polling while the safety layer (U4) acts on `solar_fault` later.

- **`CoordinatorSnapshot` as the coordinator's data type:** `coordinator.data` changes type from `EnergySnapshot` to `CoordinatorSnapshot` (wraps `EnergySnapshot` + `list[MinerSnapshot]`). The coordinator class annotation becomes `DataUpdateCoordinator[CoordinatorSnapshot]`.

- **Miner entity discovery via entity registry + device IP match:** For each configured miner IP, filter the entity registry by `platform == "hass_miner"` and match the associated device's connection address to the IP. No heuristic name-pattern guessing. If no matching entity is found, the miner is marked `is_available=False` with a WARNING log — never raises. This keeps the coordinator resilient when hass-miner is not installed or a miner is temporarily absent.

- **INFO-level snapshot log after every cycle:** One line per poll at INFO level so operators can confirm data flow in HA's system log without enabling DEBUG. Example format:
  ```
  Snapshot — solar: 2450.0W | grid: 1200.0W | battery: 78.0% | miners: [M1@192.168.1.10: 600.0W 65°C, M2@192.168.1.11: unavailable]
  ```
  None values render as `n/a`.

---

## Implementation Units

### U1. Extend `protocols.py` with full data model

**Goal:** Bring `protocols.py` from its U9 stub to the complete data model needed for the full sensor read cycle: extended `EnergySnapshot`, new `MinerSnapshot`, new `CoordinatorSnapshot`.

**Requirements:** R1, R2, R3, R4

**Dependencies:** None

**Files:**
- Modify: `custom_components/solar_smart_miner/protocols.py`
- Create: `tests/test_protocols.py`

**Approach:**

*Extend `EnergySnapshot`:*
- Add `grid_consumption_w: float | None = None`
- Add `battery_soc_pct: float | None = None`
- Add `solar_fault: bool = False`
- All new fields have defaults so `EnergySnapshot(solar_production_w=2000.0)` continues to construct without modification — backward-compatible with existing tests.

*Add `MinerSnapshot` dataclass:*
- `miner_id: str` — stable ID derived from miner IP (e.g., `"miner_192_168_1_10"`)
- `name: str` — display name from config entry
- `ip: str` — configured IP address
- `power_w: float | None` — current power draw from hass-miner sensor
- `temperature_c: float | None` — board temperature from hass-miner sensor
- `power_limit_w: float | None` — current applied power limit from hass-miner number entity
- `min_power_w: float | None` — miner's minimum wattage (from number entity attributes)
- `max_power_w: float | None` — miner's maximum wattage (from number entity attributes)
- `is_available: bool` — False when required hass-miner entities are not found in the registry
- `power_limit_entity_id: str | None = None` — cached entity ID for later use by `_async_apply_power_limit`

*Add `CoordinatorSnapshot` dataclass:*
- `energy: EnergySnapshot`
- `miners: list[MinerSnapshot]`

**Test scenarios:**
- `EnergySnapshot(solar_production_w=2000.0)` constructs; `grid_consumption_w is None`, `battery_soc_pct is None`, `solar_fault is False`
- `EnergySnapshot(solar_production_w=None, solar_fault=True)` constructs correctly
- `MinerSnapshot` with all fields set constructs correctly; `power_limit_entity_id` defaults to `None`
- `CoordinatorSnapshot` with populated energy and non-empty miner list constructs correctly
- `CoordinatorSnapshot` with empty `miners=[]` constructs correctly

**Verification:**
- All new data classes importable in tests without runtime errors
- Existing `EnergySnapshot` usages in `coordinator.py` and `test_coordinator.py` construct without changes

---

### U2. Extend coordinator with full sensor reads and snapshot logging

**Goal:** Replace the U9 single-sensor stub in `_async_update_data()` with three private read methods that together cover all configured energy entities and miners, then log a human-readable snapshot to the HA log on every cycle.

**Requirements:** R1, R2, R3, R4, R9

**Dependencies:** U1

**Files:**
- Modify: `custom_components/solar_smart_miner/coordinator.py`
- Modify: `tests/test_coordinator.py`

**Approach:**

*`_async_read_energy() -> EnergySnapshot`:*
- Absorbs the existing U9 mock-solar path (relocated from `_async_update_data`, logic unchanged)
- After resolving the solar entity (real or mock), reads its state via `_parse_state_float`
- When solar state is `None` (unavailable or unknown): sets `solar_fault=True`, `solar_production_w=None` — does **not** raise `UpdateFailed`
- Reads `CONF_GRID_ENTITY` from `entry.data`; `grid_consumption_w=None` when unavailable (no fault flag — grid reads are best-effort)
- Reads `entry.data.get(CONF_BATTERY_ENTITY)` when present; `battery_soc_pct=None` when unconfigured or unavailable; skips entirely when entity key absent

*`_find_hass_miner_entities(ip: str) -> dict`:*
- Helper that returns a dict of discovered entity IDs for a miner at the given IP
- Iterates entity registry filtered by `platform == "hass_miner"` 
- For each entity, resolves its device via the device registry; checks `("ip", ip)` in `device.connections`
- Separates power sensor (`device_class == POWER`, domain `sensor`), temperature sensor (`device_class == TEMPERATURE`, domain `sensor`), and power limit entity (domain `number`)
- Returns `{power_entity_id, temp_entity_id, power_limit_entity_id}` — any may be `None` when not found

*`_async_read_miners() -> list[MinerSnapshot]`:*
- Iterates `entry.data.get(CONF_MINERS, [])` (list of `{miner_name, miner_ip}` dicts)
- For each miner, calls `_find_hass_miner_entities(ip)`
- When entity IDs are found: reads power sensor and temperature sensor via `_parse_state_float`; reads `min_value` and `max_value` from the power limit entity's state attributes; marks `is_available=True`
- When required entities not found: marks `is_available=False`, logs `WARNING "Miner {name} ({ip}): hass-miner entities not found — is hass-miner installed and this miner configured?"`, continues
- Returns `[]` when `CONF_MINERS` is absent or empty

*`_async_update_data() -> CoordinatorSnapshot`:*
- Calls `_async_read_energy()` then `_async_read_miners()`
- Logs one INFO line: human-readable snapshot (format described in Key Technical Decisions)
- Returns `CoordinatorSnapshot(energy=..., miners=...)`
- Class annotation: `class SolarMinerCoordinator(DataUpdateCoordinator[CoordinatorSnapshot])`

*Test update:*
- Rename `test_coordinator_raises_when_real_solar_unavailable` → `test_coordinator_sets_solar_fault_when_solar_unavailable`
- Assert: `snapshot.energy.solar_fault is True`, `snapshot.energy.solar_production_w is None`, no exception raised

**Test scenarios:**
- Happy path: solar=2000, grid=1500, battery=75 → all snapshot fields populated correctly
- Happy path: no battery entity configured (`CONF_BATTERY_ENTITY` absent from entry data) → `battery_soc_pct is None`, no error
- Edge case: solar entity state `"unavailable"` → `solar_fault=True`, `solar_production_w=None`, no exception raised
- Edge case: grid entity state `"unknown"` → `grid_consumption_w=None`, no exception
- Happy path: two miners configured, both found in entity registry → `len(snapshot.miners) == 2`, both `is_available=True`, `power_w` and `temperature_c` populated
- Edge case: one miner's power entity not found in registry → that `MinerSnapshot.is_available=False`, WARNING logged, other miner unaffected
- Happy path: no miners configured (`CONF_MINERS=[]`) → `snapshot.miners == []`, no error
- Log: assert `_LOGGER.info` called once per cycle; message contains `"Snapshot"` and the solar value
- Backward-compat: existing mock-solar tests (`test_coordinator_reads_real_solar_when_mock_disabled`, etc.) still pass after refactor (mock-solar path preserved in `_async_read_energy`)

**Verification:**
- `pytest tests/test_coordinator.py` passes with no hardware
- Running in the dev container produces one INFO log line per poll cycle showing all configured sensor values

---

### U3. Wire coordinator lifecycle in `__init__.py`

**Goal:** Make the coordinator actually run: construct it in `async_setup_entry`, complete the first refresh before forwarding platform setup, and store it on `entry.runtime_data`.

**Requirements:** R9 (polling interval applies from the first cycle)

**Dependencies:** U1, U2

**Files:**
- Modify: `custom_components/solar_smart_miner/__init__.py`

**Approach:**
- `async_setup_entry`: construct `SolarMinerCoordinator(hass, entry)` → `await coordinator.async_config_entry_first_refresh()` → `entry.runtime_data = coordinator` → `await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)`
- `async_unload_entry`: `return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)` — no explicit coordinator teardown; `DataUpdateCoordinator` manages its own HA event loop registration
- No other changes; `PLATFORMS` list already defined

**Test scenarios:**
- Integration: after `async_setup_entry`, `entry.runtime_data` is a `SolarMinerCoordinator` instance
- Integration: `entry.runtime_data.data` is a `CoordinatorSnapshot` with `energy` and `miners` attributes immediately after setup — entities never see `None` coordinator data on first load
- Integration: `async_config_entry_first_refresh()` completes before any platform is forwarded (platforms set up after the `await`)

**Verification:**
- Integration loads in the dev container without errors
- HA log shows the first snapshot INFO line immediately on startup
- Subsequent INFO lines appear every `polling_interval` seconds

---

## System-Wide Impact

- **`coordinator.data` type change:** All consumers of `entry.runtime_data.data` must update field access from `.solar_production_w` to `.energy.solar_production_w`. Currently no consumers exist outside the coordinator and its tests, so the blast radius is limited to those two files.
- **Test behavior change:** `test_coordinator_raises_when_real_solar_unavailable` currently passes by asserting `UpdateFailed`. After U2 this assertion must flip — coordinator no longer raises; it sets `solar_fault=True`. The test must be updated in the same commit as the coordinator change to keep the suite green.

---

## Sources & References

- **Parent plan (U11):** [docs/plans/2026-05-15-001-feat-solar-smart-miner-ha-integration-plan.md](docs/plans/2026-05-15-001-feat-solar-smart-miner-ha-integration-plan.md)
- **Origin document:** [docs/brainstorms/solar-smart-miner-requirements.md](docs/brainstorms/solar-smart-miner-requirements.md)
- HA `DataUpdateCoordinator` docs: `developers.home-assistant.io/docs/integration_fetching_data/`
- Entity registry API: `homeassistant.helpers.entity_registry.async_get(hass).entities`
- Device registry API: `homeassistant.helpers.device_registry.async_get(hass).async_get(device_id)`
