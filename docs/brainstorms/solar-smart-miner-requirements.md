---
date: 2026-05-15
topic: solar-smart-miner
---

# Solar Smart Miner — Home Assistant Integration

## Summary

A Home Assistant custom integration that uses a single AI agent to dynamically control ASIC miner power limits based on real-time solar, consumption, and battery data — with user-configurable profiles, hard safety rails the AI cannot override, dry-run mode for safe onboarding, and Telegram notifications for every action taken.

---

## Problem Frame

Bitcoin ASIC miners are power-hungry and most efficient when run continuously at a fixed wattage. But homes with solar panels and batteries operate in a constantly shifting energy environment: production peaks midday, drops at night, batteries fill and drain, and grid draw has a cost. Without automation, miners either run at a fixed setting (wasting solar surplus or draining batteries) or require constant manual adjustment.

Home Assistant already has mature integrations for most solar hardware brands and, via hass-miner, for controlling ASIC miners. Nothing connects them. Users with autotuning-capable miners — where power draw can be dialled anywhere from minimum to maximum wattage — are leaving significant value on the table: they could be running harder when the sun is out and backing off when it isn't, all without touching anything.

The target user is a technically-capable homeowner who already runs HA, has at least one solar or solar+battery setup, and wants their miners to behave intelligently without writing automations from scratch.

---

## Actors

- A1. **Miner operator** — installs and configures the integration, selects a profile, monitors decisions via dry-run before going live, receives Telegram notifications
- A2. **AI decision agent** — runs on a configurable interval, reads the current energy state, consults the active profile, and outputs a target power limit per miner
- A3. **Safety layer** — hard-coded constraint checker that runs before any AI decision is applied; overrides the agent when temperature, battery, or solar fault thresholds are breached
- A4. **hass-miner integration** — exposes miner controls (power limit, start/stop) and sensors (hashrate, power draw, efficiency, temperatures)
- A5. **HA solar integration** — exposes solar production, grid consumption, and battery SOC as standard HA entities (Solarman, SolarEdge, Fronius, Enphase, Huawei, or any other that surfaces entities)
- A6. **Telegram bot** — delivers action and override notifications to the operator

---

## Key Flows

- F1. **Normal decision cycle**
  - **Trigger:** Polling interval elapses (default: 5 minutes, configurable)
  - **Actors:** A2, A3, A4, A5
  - **Steps:**
    1. Agent reads solar production, grid consumption, and battery SOC from HA entities (A5)
    2. Agent reads miner power draw, hashrate, efficiency, and temperatures (A4)
    3. Safety layer evaluates hard constraints — temperature ceiling, battery floor, solar fault (A3)
    4. If any constraint is breached, safety layer sets the decision (stop or reduce); agent output is discarded
    5. If no constraint breached, agent reasons with current energy state + active profile and outputs target power limit
    6. Decision and reasoning are written to the decision log
    7. If dry-run: log only, no change applied to miners
    8. If live: power limit is applied to each configured miner via hass-miner
  - **Outcome:** Miner power limit reflects the optimal level for current conditions
  - **Covered by:** R1–R13, R18–R22

- F2. **Safety override**
  - **Trigger:** Any miner temperature exceeds ceiling, battery SOC drops below floor, or solar integration reports a fault
  - **Actors:** A3, A4, A6
  - **Steps:**
    1. Safety layer detects breach during step 3 of F1
    2. Safety layer sets decision: reduce to minimum wattage or stop completely
    3. Decision and override reason are logged
    4. Change is applied immediately (safety overrides ignore dry-run mode)
    5. Telegram notification is sent to operator (A6)
  - **Outcome:** Miner is protected; operator is informed
  - **Covered by:** R5–R7, R20

- F3. **Profile switch**
  - **Trigger:** Operator changes active profile via HA UI
  - **Actors:** A1, A2
  - **Steps:**
    1. Operator selects new profile in the integration config or a dedicated HA entity
    2. Profile context is updated for the agent
    3. Next polling cycle uses the new profile
  - **Outcome:** Agent behaviour reflects the new profile on the next cycle
  - **Covered by:** R14–R18

---

## Requirements

**Data ingestion**

- R1. The integration reads a configurable HA entity for solar production (watts)
- R2. The integration reads a configurable HA entity for grid consumption (watts)
- R3. The integration optionally reads a configurable HA entity for battery state of charge (%); battery-related profile logic is skipped when no battery entity is configured
- R4. The integration reads miner sensors from hass-miner per configured miner: power draw (W), hashrate (TH/s), efficiency (J/TH), and board/chip temperatures

**Safety layer**

- R5. If any miner's reported temperature exceeds the configured ceiling, the integration stops or throttles that miner immediately, bypassing the AI decision — this override also applies in dry-run mode
- R6. If battery SOC drops below the configured floor %, the integration stops all miners immediately, bypassing the AI decision
- R7. If the solar integration entity enters an unavailable or fault state, the integration stops all miners until the entity recovers
- R8. Safety thresholds (temperature ceiling °C, battery floor %) are user-configurable; sensible defaults are provided

**AI decision engine**

- R9. A single AI agent runs on a configurable polling interval (default: 5 minutes)
- R10. The agent receives as input: current solar production, grid consumption, battery SOC (if present), per-miner power draw and hashrate, active profile name and its parameters
- R11. The agent outputs a target power limit in watts for each configured miner
- R12. The agent's reasoning for each decision is captured and written to the decision log alongside the output
- R13. The agent respects hass-miner's reported min/max power range per miner; outputs are clamped to that range

**Dry-run mode**

- R14. In dry-run mode, the agent runs the full decision cycle but does not apply power limit changes to miners
- R15. In dry-run mode, all decisions and reasoning are still written to the decision log
- R16. Safety overrides (R5–R7) apply and are enacted even in dry-run mode
- R17. Dry-run mode is togglable from the HA UI without restarting the integration

**Profiles**

- R18. Battery-focused: prioritise preserving battery SOC; use efficiency-optimal wattage; reduce power as battery drops; stop before hitting battery floor
- R19. Solar-max: during periods of high solar production (production significantly exceeds consumption), run miners at maximum rated wattage; back off when production drops
- R20. Grid-agnostic: use solar surplus freely; supplement with grid without penalty; optimise for hashrate given available power
- R21. Grid-independent: never draw net power from the grid; miner wattage is capped to (production − base consumption); stop mining if the surplus disappears
- R22. The active profile is selectable by the operator via HA UI (config flow or a select entity); only one profile is active at a time

**Notifications**

- R23. When the agent applies a power limit change to any miner, a Telegram message is sent stating the old and new wattage and the brief reason
- R24. When a safety override fires, a Telegram message is sent stating which threshold was breached and what action was taken
- R25. Telegram bot token and chat ID are user-configurable; Telegram is optional — the integration functions fully without it

**Development, testing, and distribution**

- R26. The integration is written in Python (required by HA); it targets the current stable HA release
- R27. The integration follows HA custom component conventions: `custom_components/solar_smart_miner/` structure, `manifest.json`, UI-based config flow
- R28. A `hacs.json` manifest is included; the integration is structured for distribution via HACS
- R29. Versioning follows semantic versioning; releases are created via GitHub Releases (HACS installs from these)
- R30. The test suite uses `pytest` with `pytest-homeassistant-custom-component`; mock fixtures cover solar sensor entities, miner entities, and hass-miner state
- R31. The repository includes a `devcontainer.json` (or equivalent HA dev container config) for local live development against a real HA instance

---

## Acceptance Examples

- AE1. **Covers R14, R15, R12.** Given dry-run mode is enabled and solar production is 3 kW with battery at 75%, when the polling interval elapses, the agent logs a target power limit and its reasoning but the miner's reported wattage does not change.

- AE2. **Covers R5, R16.** Given dry-run mode is enabled and a miner reports a board temperature above the configured ceiling, when the safety layer detects the breach, the miner is stopped (the change is applied despite dry-run) and a Telegram notification is sent.

- AE3. **Covers R21, R13.** Given the grid-independent profile is active, solar production is 1.2 kW, and base household consumption is 0.8 kW, when the polling cycle runs, the agent outputs a power limit no higher than 400 W (clamped to the miner's min if lower).

- AE4. **Covers R3, R18.** Given the battery-focused profile is active and no battery SOC entity is configured, when the polling cycle runs, the agent applies battery-focused logic as best it can from production/consumption data alone and does not error.

- AE5. **Covers R17.** Given the integration is running live and the operator toggles dry-run mode on, the next polling cycle logs a decision but applies no change to miners, without requiring an HA restart.

---

## Success Criteria

- A miner's power limit adjusts automatically in response to solar and battery state changes without any manual intervention from the operator
- An operator can run dry-run mode for at least one full day, review the decision log, and trust that going live will behave as observed
- A friend with a different solar brand and no battery can install the integration via HACS, configure it in under 15 minutes using the HA UI, and reach a working dry-run state
- Safety overrides fire reliably and are never blocked by AI decisions or dry-run mode
- The test suite can be run locally in under 2 minutes with no real miner or solar hardware required

---

## Scope Boundaries

### Deferred for later

- Event-triggered decisions (replace polling with HA state-change triggers — v2 upgrade path)
- Weather forecast integration (pre-position miners based on next-day solar forecast)
- Grid electricity price integration (factor buy/sell rates into power decisions)
- Multiple simultaneous profiles (per-miner profile assignment)
- Agent decision history UI in the HA frontend (beyond the decision log)
- Non-autotuning miner support (on/off only control for miners without power limit capability)

### Outside this product's identity

- Mining pool or coin-profitability switching (this product manages energy, not mining strategy)
- Cloud or remote management beyond Telegram (not a cloud product)
- Direct solar vendor API integration (we read HA state only; solar vendor integration is HA's problem)
- General HA energy management beyond miner control (this is not a whole-home energy orchestrator)

---

## Key Decisions

- **OpenRouter as the LLM provider:** Single API key gives access to both free and paid models (Llama, Gemma, Claude, GPT-4, etc.); uses the OpenAI-compatible API format; users choose their model at install time — ideal for a community plugin where cost tolerance varies
- **Python only:** HA custom integrations must be Python; no choice here
- **hass-miner as the miner layer:** We read and write via hass-miner entities rather than talking directly to miner APIs; this reduces scope and reuses the pyasic library already maintained there
- **Autotuning-only for v1:** Miners without power limit support are excluded; the core value proposition depends on graduated control, not just on/off
- **Polling-based for v1:** Simpler, more debuggable, and meaningful dry-run experience; event-triggered is the natural v2 upgrade
- **Safety layer is hard-coded, not AI-driven:** Temperature, battery floor, and solar fault responses are deterministic overrides; the AI cannot reason around them
- **Single active profile:** Profiles configure the AI's priorities; only one is active at a time; per-miner profiles are deferred
- **HA entities as the data bus:** Solar and consumption data come from existing HA entity state, not direct hardware APIs; the operator maps their entities during config

---

## Dependencies / Assumptions

- hass-miner must be installed, configured, and reachable for at least one miner before this integration is useful
- At least one HA solar integration must be present and exposing production/consumption entities
- All miners managed by this integration must report `supports_autotuning = true` via hass-miner
- An OpenRouter API key is required for the AI agent; the operator selects a model at install time (free options like Llama/Gemma are available for cost-sensitive setups)
- hass-miner's power limit number entity correctly reflects and enforces the miner's actual min/max range

---

## Outstanding Questions

### Resolve Before Planning

*(none)*

### Deferred to Planning

- **[Affects R3, R4] [Technical]** How does the config flow handle optional vs. required entity mapping? (e.g., battery SOC is optional; solar production is required)
- **[Affects R4] [Technical]** Is miner configuration one config entry per miner, or a single entry listing N miners?
- **[Affects R30] [Needs research]** What level of HA core mocking does `pytest-homeassistant-custom-component` provide out of the box for entity state reads and service calls?
