# Solar Smart Miner — Claude Instructions

Home Assistant custom integration that controls ASIC Bitcoin miners based on solar production and battery state, using an AI agent (OpenRouter) to make start/stop decisions.

## Branch Strategy

**Work directly on `main`.** This is a solo project optimised for fast iteration — no feature branches, no PRs. Commit directly and push when the work is stable. Skip branch gymnastics.

## Project Structure

```
custom_components/solar_smart_miner/   # HA integration source
  __init__.py          # platform setup and entry point
  config_flow.py       # setup wizard and options flow
  coordinator.py       # DataUpdateCoordinator — fetches solar/grid/battery/miner state
  sensor.py            # sensor entity platform (hub + per-miner sensors)
  button.py            # button entity platform (Add to Dashboard)
  protocols.py         # typed protocols for hass-miner entity reads
  const.py             # constants and configuration keys
  manifest.json        # HACS/HA integration manifest

tests/                 # pytest test suite
docs/
  brainstorms/         # requirements exploration docs
  plans/               # implementation plans with status tracking
  solutions/           # documented solutions to past problems (bugs, best practices,
                       #   architectural patterns), organised by category with YAML
                       #   frontmatter (module, tags, problem_type). Useful when
                       #   implementing or debugging in documented areas.
scripts/               # dev utilities (validate-frontmatter.py, etc.)
```

## Running Tests

```bash
python -m pytest tests/
```

Uses `pytest-homeassistant-custom-component` — keep the HA test harness version pinned in `pyproject.toml`.

## Key Concepts

- **Coordinator** (`coordinator.py`) reads hass-miner entities from `hass.states` by matching unit-of-measurement when device class is absent. All entity data flows through a `MinerSnapshot` dataclass.
- **Hub device** (`DeviceInfo` with `identifiers`) groups all integration entities under one HA dashboard card.
- **Sensor entities** subclass `CoordinatorEntity`; per-miner sensors are generated from a `MINER_METRICS` descriptor list.
- **hass-miner** is the sibling integration that talks to the physical miners; this integration reads its exposed entities.
