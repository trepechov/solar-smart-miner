# Solar Smart Miner — Claude Instructions

Home Assistant custom integration that controls ASIC Bitcoin miners based on solar production and battery state, using an AI agent (OpenRouter) to make start/stop decisions.

## Branch Strategy

**Work directly on `main`.** This is a solo project optimised for fast iteration — no feature branches, no PRs. Commit directly and push when the work is stable. Skip branch gymnastics.

## Versioning and Releases

HACS installs from **GitHub releases**, so Home Assistant only shows an update (Settings → Updates) when a new release is published; a plain push to `main` reaches nobody. Every change that should reach the farm goes out as a release:

- **Version** lives in `custom_components/solar_smart_miner/manifest.json` (`X.Y.Z`, semver). Never tag or release without bumping it; the tag must be `v` + that version (`release.yml` refuses a mismatch).
- **Which number:** always the patch number, the third one (`0.7.0 → 0.7.1 → 0.7.2`), whatever the release contains, fixes or new behaviour alike: we iterate in many small releases. Bump the minor or major number only when the owner asks for it.
- **How:** commit the work first, then `scripts/release.sh X.Y.Z`. It bumps the manifest, commits `chore(release): vX.Y.Z`, tags and pushes; `.github/workflows/release.yml` then publishes the release with notes generated from the commits.
- **When:** release once a batch is stable and tests pass, not after every commit. At the end of a session that changed integration code, say whether a release was cut, and if not, propose the version number.
- **Removing an entity:** add its (domain, unique-id suffix) to `RETIRED_ENTITIES` in `__init__.py`, so the update deletes it from the user's registry instead of leaving it unavailable.
- `.github/workflows/validate.yml` runs the HACS and hassfest checks on every push to `main`; fix a failure before releasing.

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
scripts/               # dev utilities (release.sh, install-git-hooks.sh, etc.)
.github/workflows/     # release.yml (tag → GitHub release), validate.yml (HACS + hassfest)
```

## Knowledge Base

`custom_components/solar_smart_miner/knowledge/` holds what we know about the site, the miners and the control rules as small, prioritised facts (P0 hard limit … P3 context) with a status (decided / verified / assumed / open / conflict), plus the alert scenarios. It is what the AI will later be given. **When a session settles or measures something, add or update the entry in the same commit** (put the evidence in `source`/`date`; change `status` rather than silently rewriting a statement; record disagreements as a `conflict`). Don't store secrets or tokens in it.

**The integration is for any farm; the owner's farm is the reference example.** Its miner count, models (S9), wattages, timings, voltage and cooling show how to reason, never a fixed rule. Before writing an entry, decide whether it is a **principle** (true on any farm) or a **measurement of the reference farm**. A `statement` holds the principle, worded against the configuration ("the configured power steps", "the ramp lock"), with no site wattages, miner counts, miner names or entity ids; the reference farm's numbers go in `note`/`source`. Values that depend on the miner type or the site (power steps, ramp and tuning time, voltage limit, temperature band) are settings, defaulting to the reference farm's values. Keep entries and prompts short: don't add text or code for cases that can't happen.

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
- **Power steps** (`const.DEFAULT_POWER_STEPS`, 900 to 2,500 W in 200 W steps): every limit change restarts a miner (about 4 minutes), and a wattage it has never run takes up to an hour to tune, so limits only move between fixed steps and are never arbitrary watts. Stopping a miner is a separate plan action (relay or pause switch), not a power limit.
- **Hub device** (`DeviceInfo` with `identifiers`) groups all integration entities under one HA dashboard card.
- **Sensor entities** subclass `CoordinatorEntity`; per-miner sensors are generated from a `MINER_METRICS` descriptor list.
- **hass-miner** is the sibling integration that talks to the physical miners; this integration reads its exposed entities.
