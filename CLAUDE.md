# Solar Smart Miner — Claude Instructions

Home Assistant custom integration that controls ASIC Bitcoin miners based on solar production and battery state, using an AI agent (OpenRouter) to make start/stop decisions.

## Branch Strategy

**Work directly on `main`.** This is a solo project optimised for fast iteration — no feature branches, no PRs. Commit directly and push when the work is stable. Skip branch gymnastics.

## Project Structure

```
custom_components/solar_smart_miner/   # HA integration source
  __init__.py          # platform setup and entry point
  config_flow.py       # setup wizard and options flow
  coordinator.py       # DataUpdateCoordinator — fetches solar/grid/battery/miner state,
                       #   builds the decision preview, schedules AI advice requests
  decision.py          # rule-based decision preview: power steps, stop/start plans, tuning
                       #   awareness (plans are never applied yet)
  ai.py                # OpenRouter client: prompt, chat completion, JSON answer parsing, free-model list
  kb.py                # loads the knowledge base and picks the facts each AI request gets
  ai_log.py            # JSONL log of every AI request (inputs, rule proposal, AI actions) + widget history
  sensor.py            # sensor entity platform (hub + per-miner sensors, decision log, AI advice)
  select.py            # profile select entity
  button.py            # button entity platform (Add to Dashboard, Ask AI now)
  protocols.py         # typed protocols for hass-miner entity reads
  const.py             # constants and configuration keys
  knowledge/           # knowledge base: facts by priority/status + alert catalogue (YAML);
                       #   format in its README, checked by tests/test_knowledge.py;
                       #   sent to the AI by kb.py (P0/P1 always, the rest by situation)
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

## Knowledge Base

`custom_components/solar_smart_miner/knowledge/` holds what we know about the site, the miners and the control rules as small, prioritised facts (P0 hard limit … P3 context) with a status (decided / verified / assumed / open / conflict), plus the alert scenarios. It is what the AI will later be given. **When a session settles or measures something, add or update the entry in the same commit** (put the evidence in `source`/`date`; change `status` rather than silently rewriting a statement; record disagreements as a `conflict`). Don't store secrets or tokens in it.

## Testing Policy

**Every change ships with tests, in the same commit.** New behaviour gets new tests; a fix gets a test that fails without it; a changed behaviour gets its old tests updated, never deleted to make them pass. Check this before committing: if a changed file has no matching test change, say why or add one.

**Tests run automatically after every commit** via the versioned `.githooks/post-commit` hook (it prints the result; a failure shows a red warning, so fix it in the next commit before pushing). The hook is enabled per clone with `scripts/install-git-hooks.sh` (sets `core.hooksPath`), so run that once after cloning. It tests the working tree, not the commit, so commit everything related together.

Tests never touch the network or the real HA config dir: `tests/conftest.py` has autouse fixtures for OpenRouter, the model list and the AI log folder.

## Running Tests

```bash
python -m pytest tests/
```

Uses `pytest-homeassistant-custom-component` — keep the HA test harness version pinned in `pyproject.toml`.

## Key Concepts

- **Coordinator** (`coordinator.py`) reads hass-miner entities from `hass.states` by matching unit-of-measurement when device class is absent. All entity data flows through a `MinerSnapshot` dataclass.
- **Power steps** (`const.DEFAULT_POWER_STEPS`): miners re-tune for up to an hour after each limit change, so limits only move between fixed steps and are never arbitrary watts. Stopping a miner is a separate plan action (relay or pause switch), not a power limit.
- **Hub device** (`DeviceInfo` with `identifiers`) groups all integration entities under one HA dashboard card.
- **Sensor entities** subclass `CoordinatorEntity`; per-miner sensors are generated from a `MINER_METRICS` descriptor list.
- **hass-miner** is the sibling integration that talks to the physical miners; this integration reads its exposed entities.
