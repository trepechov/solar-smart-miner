# Solar Smart Miner

```
           .   .   *   .   .
         .   *           *   .
       .      \  \ * /  /      .
      .    *---  ( ☀ )  ---*    .
       .      /  / * \  \      .
         .   *           *   .
           .   .   *   .   .

   ┌────────┐  ┌────────┐  ┌────────┐
   │▓▓│░░│▓▓│  │▓▓│░░│▓▓│  │▓▓│░░│▓▓│
   │░░│▓▓│░░│  │░░│▓▓│░░│  │░░│▓▓│░░│
   │▓▓│░░│▓▓│  │▓▓│░░│▓▓│  │▓▓│░░│▓▓│
   └────────┘  └────────┘  └────────┘
        \          |          /
         \         |         /
          `--------+--------'
                   |
    ╔══════════════╧═════════════╗
    ║  ╔──────────────────────╗  ║
    ║  │   ◉              ◉   │  ║
    ║  │       ╰──────╯       │  ║
    ║  ╚──────────────────────╝  ║
    ║    ♯ ♯ ♯  B T C  ♯ ♯ ♯     ║
    ║   █████████████████████    ║
    ╚════════════════════════════╝
           ⚡ clean energy ⚡
```

A Home Assistant custom integration that steers ASIC miner power limits from real-time solar production, household consumption, and battery state, using a rule-based controller with an AI advisor — with hard safety rails, a confirm-each-change control mode, and Telegram notifications.

## What it does

Bitcoin ASIC miners are power-hungry and most efficient when run continuously at a fixed wattage. Homes with solar panels and batteries operate in a constantly shifting energy environment: production peaks midday, drops at night, batteries fill and drain. Without automation, miners either waste solar surplus or drain batteries unnecessarily.

Solar Smart Miner closes that gap. On every update a rule-based controller reads your current energy state from Home Assistant and proposes a power step (or a stop/start) for each miner, which you apply with a button. An AI advisor reviews the same inputs and the proposal on a configurable interval; it reasons with your chosen profile (e.g. maximise solar self-consumption, protect battery SOC, never draw from the grid) and logs its reasoning for every decision it makes.

## Features

- **Rule-based power control with an AI advisor**: the rules propose per-miner power steps from live energy data; an agent on any OpenRouter-compatible model (including free Llama/Gemma) comments on each proposal, and gains authority only in later, evidence-gated stages
- **Four built-in profiles** — Battery-focused, Solar-max, Grid-agnostic, Grid-independent; switchable from the HA UI without restart
- **Hard safety layer** — temperature ceiling, battery SOC floor, and solar fault checks run before every AI decision and cannot be reasoned around
- **Manual and Automatic control** — in Manual (the default) the farm gets one proposal and you press **Apply proposal**, and only then is a miner touched; in Automatic the proposal is applied every cycle, paced by the ramp lock; switchable from the HA UI
- **Telegram notifications** — every power limit change and every safety override sends a message with the reason
- **HACS-ready** — distributed as a standard HA custom component; install and configure entirely through the Home Assistant UI

## Requirements

- Home Assistant (current stable release)
- [hass-miner](https://github.com/Schnitzel/hass-miner) installed and configured for at least one autotuning-capable ASIC miner
- At least one HA solar integration exposing production and consumption entities (Solarman, SolarEdge, Fronius, Enphase, Huawei, or any other)
- An [OpenRouter](https://openrouter.ai) API key (free models available)
- Telegram bot token + chat ID (optional; integration works without it)

## Installation

### Via HACS (recommended)

1. Add this repository as a custom repository in HACS
2. Search for **Solar Smart Miner** and install
3. Restart Home Assistant
4. Go to **Settings → Devices & Services → Add Integration** and search for Solar Smart Miner

### Manual

Copy the `custom_components/solar_smart_miner/` directory into your HA `config/custom_components/` folder and restart Home Assistant.

**Docker-based HA:**
```bash
docker cp custom_components/solar_smart_miner <container-name>:/config/custom_components/
docker restart <container-name>
```

**SSH-accessible HA:**
```bash
scp -r custom_components/solar_smart_miner/ ha-user@ha-host:/config/custom_components/
```

## Configuration

### First setup

The integration is set up through the Home Assistant UI config flow:

1. Map your solar entity: either a solar **production** sensor, or a **net grid meter** (set the sign convention: + export or + import). Optionally add a house-consumption sensor (miners included) — it isn't needed when you have a net grid meter.
2. Optionally paste an [OpenRouter](https://openrouter.ai) API key and pick a model. Without a key the controller still runs, rule-based.
3. Optionally add a battery SOC sensor.
4. Set safety thresholds (temperature ceiling, battery SOC floor).
5. Choose a starting profile and polling interval.

Miners are not configured here: every miner set up in [hass-miner](https://github.com/Schnitzel/hass-miner) is picked up automatically.

### Changing settings later

Open **Settings → Devices & services → Solar Smart Miner → Configure**. Saving any section reloads the integration; no restart is needed.

| Section | What you can change |
|---|---|
| **Sensors** | Solar / net-meter entity and what it measures, house consumption (optional), battery SOC (optional), and reference sensors for the AI log: actual PV output and the solar forecast (all optional) |
| **Miner stop method** | Per miner: the relay switch that cuts it off (empty = use the miner's own pause switch) |
| **AI (OpenRouter)** | Turn the AI on or off, API key (shown hidden), model, seconds between AI requests |
| **Settings** | Profile, power steps, tuning time, polling interval, target temperature and tolerance, battery floor, control mode, Telegram, development mocks |

### AI advice (OpenRouter)

The model dropdown lists the **free** text models OpenRouter currently offers, fetched when you open the form (any other model id can be typed in). The default, `openrouter/free`, lets OpenRouter pick an available free model, so it keeps working when individual free models are retired.

The AI is **advisory only**: every few minutes it is sent the current readings and the rule-based proposal, and its short comment appears on the **AI advice** sensor (full text in the `response` attribute) and in the dashboard card. Nothing it says changes the miners. Press **Ask AI now** to ask on demand. The default interval is 60 s (minimum 10 s). Free models allow only a limited number of requests per day, so a short interval may need a paid model or a longer interval.

#### AI decision log

The AI answers in a fixed shape: a one-line summary plus, for each miner, an action (`increase`, `reduce` or `hold`) and a reason (`excess_energy`, `not_enough_energy`, `voltage_limit`, `temperature_limit`, `battery_low`, `no_change` or `other`). These are what it *would* do; nothing is applied.

Every request is appended to `<HA config>/solar_smart_miner/ai_log.jsonl`, one JSON object per line: the readings it was given (solar, grid, house, miners, battery), the actual PV output and the forecast (now / next hour / energy left today, if you set those sensors), each miner's state, the rule-based proposal, the exact prompt, and the AI's answer and actions. The file rotates at 5 MB and keeps two older files. Read it with `jq` or any text editor, e.g. `jq -c '{ts, pv: .inputs.pv_actual_w, fc: .inputs.forecast_now_w, ai: .ai.actions}' ai_log.jsonl`.

The last 20 entries are also on the **AI advice** sensor (`history` and `actions` attributes) and in the card from **Add to dashboard** under *AI log*.

## Profiles

> **Being replaced.** These four are from the first versions. The focus is **Solar-follow** for setups without a battery: a small steady draw from the grid proves all solar is used (Solar-max already works this way). Battery profiles are not designed yet. See [decision-making requirements §6](docs/brainstorms/2026-10-05-decision-making-requirements.md).

| Profile | Behaviour |
|---|---|
| **Battery-focused** | Prioritise preserving battery SOC; run at efficiency-optimal wattage; back off as battery drops |
| **Solar-max** | Follow the solar on a small steady grid import (see [Solar-max: steering on the import](#solar-max-steering-on-the-import)) |
| **Grid-agnostic** | Use solar surplus freely and supplement with grid without penalty; optimise for hashrate |
| **Grid-independent** | Never draw net power from the grid; cap miner wattage to (production − base consumption) |

### Solar-max: steering on the import

Without a battery the inverters hold their output to the load (zero export), so at 0 W on the meter you can't tell 500 W of sun from 5 kW. A small steady **import** is the only proof all the solar is used. Solar-max steers on it:

- **Below the minimum import** (Configure → Settings, 200 W by default): the solar covers the house and the inverters are probably holding back, so it adds one increment: it **starts a stopped miner** at its lowest step first, otherwise it raises the weakest running miner one step. The ramp lock then waits 4 minutes, and the import shows whether the sun carried it. This is also how the miners start in the morning. Nothing starts while the sun is below the horizon (`sun.sun`).
- **Between the minimum and the maximum import** (400 W by default): hold. Keep the range at least one power step wide, so a step from just outside lands inside.
- **Above the maximum:** step down, as far as brings the import back under it, but only once the import has stayed that high for the **step-down delay** (5 minutes), so a passing cloud costs no restart. While the sun is rising (until solar noon) the delay is the **morning step-down delay** (30 minutes): a miner started early may import for a while, the sun catches up.

The solar forecast is shown in the decision log and the AI log for reference only; it never decides.

## Power steps, stopping and tuning

Every power-limit change restarts a miner: it draws almost nothing for 2 to 4 minutes while it loads. A miner tunes itself the first time it runs a wattage (up to an hour) and keeps those settings, so going back to a step it has run before is only the restart and settles in about 5 minutes. So the controller never asks for an arbitrary wattage: limits move only between **power steps**, **900 to 2,500 W in 200 W steps** by default (Configure → Settings; each miner uses the steps inside its own range).

- **One miner per proposal.** Apart from safety, a proposal changes one miner. If several restarted together the farm's load would drop to almost 0 W, the zero-export inverters would throttle down, and the grid would cover the gap when the miners came back. A shortfall goes to the hungriest miner that can take all of it and keep running; if none can, the lowest-power miner is stopped. Spare power starts a stopped miner at its lowest step first, otherwise it raises the weakest running miner.
- **Even load (secondary).** Once every miner runs, their limits are kept within one step of each other, so no miner runs much hotter than the others. Spare power raises the weakest (of equals, the coolest) no further than its even share. When nothing else needs to change and two limits are two or more steps apart, the weakest steps up one step, or, if it can't, the hungriest (of equals, the hottest) steps down one. The import range, temperature, tuning and ramp lock rules always come first.
- **A change may skip steps.** That one change goes straight to the step that fits (1,500 → 1,100 W is one restart, not two).
- **Ramp lock.** After any change (a command sent, or a limit, stop or start seen on a miner, also by hand) every miner holds for up to 4 minutes, and while a command is still being checked. The readings are misleading while a miner restarts. It ends early once the changed miner draws within 5% of its new limit (after at least 1 minute), even while its hashrate is still settling, so on a rising morning the next miner can follow sooner. Safety doesn't wait.
- **Starting from where the miner is.** The plan begins at each miner's current step. A shortfall of up to 150 W keeps the current step, and stepping up needs 100 W of spare power on top of the step's cost, so small wobbles in the budget change nothing.
- **Stopping.** When the budget is below the lowest step the miner is **stopped**, not just turned down to a minimum. How is configured per miner under Configure → Miner stop method: a **relay** switch (for miners cut off with a relay) or, when none is set, the miner's own **pause** switch (`switch.<miner>_active` from hass-miner). A stopped miner is started again when its lowest step plus the margin fits. A miner with neither is dropped to its lowest step instead. A limit change restarts a miner and hass-miner shows its pause switch off for a minute or two; within the ramp lock that reads as restarting, not stopped.
- **Tuning.** 5 minutes by default: every configured step has been tuned before, so a change only needs to settle. Only if you add a step a miner has never run, raise the tuning time (Configure → Settings) for its first tuning: for that long after its limit changed a miner is not stepped up and its temperature is ignored. hass-miner doesn't report the tuning state, so it is estimated from the time since the limit last changed.

The rules work out one plan per miner every cycle and bundle them into one proposal. In **Manual** mode you apply it with a button; in **Automatic** mode it is applied at the end of the cycle (see [Control mode](#control-mode)).

## Safety layer

The following overrides run before every AI decision and cannot be bypassed — not even by pressing Apply:

- **Temperature band** — a miner at or above the target plus the tolerance (60 °C + 10 °C by default) steps down one step; between the target and target + tolerance it holds even with spare energy. A low temperature is never a reason to step up
- **Battery SOC floor** — if battery drops below the configured %, all miners are stopped
- **Solar fault** — if the solar entity is unavailable, every miner is held as it is: a short sensor drop must not make the miners re-tune

## Control mode

The **Control mode** select (also under Configure → Settings) says how a proposal reaches the miners:

| Mode | What happens |
|---|---|
| **Manual** (default) | The farm gets one **proposal** (what every miner that would change should do) and one **Apply proposal** button. Nothing is sent to a miner until you press it (the dashboard card asks to confirm). |
| **Automatic** | The proposal is applied at the end of every cycle, through the same code path as the button. The Apply button is greyed out; switch to Manual to act by hand. |

Preview mode is gone (0.7.2): an install that was on Preview comes up in Manual, which still sends nothing until you press.

How Automatic is paced:

- **The ramp lock.** A miner needs about 4 to 5 minutes to settle after a change, and changing anything sooner starts a change loop. So after any change (a command sent, even one that failed, or a miner seen stopping or starting) every miner holds for 4 minutes, and while a command is still being checked. The lock survives a reload or an HA restart: it is restored from the action log.
- **One miner per proposal**, as in Manual; only safety changes several at once.
- **A refused plan is logged once** (for example a miner whose entity is unavailable) and is not retried until the plan or the reason changes.
- **Turn the fixed-hour schedule automation off first.** The integration doesn't check for it yet; if it still runs, it and Automatic both act on the miners.

How Apply works:

- **One proposal for the whole farm.** The plans come from one shared power budget, so they are applied as a bundle: if any part changed since you looked, nothing is sent. Normally the bundle changes one miner (see [Power steps](#power-steps-stopping-and-tuning)); only safety changes several.
- **What you saw is what runs.** On press the integration reads everything again. If the proposal changed in the meantime, nothing is sent and a notification says "the proposal changed, check again". A failed update refuses too: stale readings are worse than no action.
- **Only the rule plan is applied**, never the AI answer. The AI's view of each miner is recorded in the action log.
- **Guards.** A limit that is not one of the miner's power steps is refused, never clamped. A second press for a miner is refused while its previous command is still being checked.
- **Apply proposal** sends stops and step-downs first, then step-ups and starts, so the house never briefly draws both. The proposal text lists the steps in that order.
- **Every command is checked.** The miner's entity must show the new value within 60 s (300 s for a relay start, the miner has to boot). A start is two steps: switch on, then set the limit once the miner is back. If it doesn't take, you get a notification (**a command didn't take**), one per miner.
- **Schedule automations still run.** If you still have the 07:00 / 19:00 pause-and-resume automation, it can undo an applied action. Turn it off before choosing Automatic.

### Activity log card

**Add to dashboard** builds the card for this. Under the settings list it has an **Activity log**: the line **Proposal now** (for example `Brod2 1,300 W (from 1,500 W) · Brod1 1,500 W (from 900 W)`, or `no action`), then a newest-first feed of proposals (`Proposal: … ◀ current`) and applied actions (`Brod1 applied 1,500 W: ok`). Directly under it is the **Apply proposal** button, which asks to confirm. A proposal is marked **◀ current** only while it is still what the rules propose. The feed is kept in memory (30 entries); after a restart it starts again from the applied actions in the action log. It is also on the **Activity** sensor (`proposal` and `feed` attributes).

### Action log

Every command is written to `<config>/solar_smart_miner/actions.jsonl` (rotated at 5 MB, two backups): one line when it is sent or refused and one when its outcome is known, joined by `command_id`. A line holds the plan, the miner before and after, the energy picture, the rule summary and the AI's view of that miner. The **Last action** sensor shows the latest command; its `history` attribute holds the last 20.

A command is `ok` only once the miner did it: after a limit change it restarts (no power for a minute or two) and mines again at its new limit; after a stop it no longer mines. A value hass-miner only echoes ends in `failed` and a notification.

Every time the proposal changes, `<config>/solar_smart_miner/decisions.jsonl` gets one line with everything the rules were given (all readings and settings) and what they decided. `scripts/replay.py` runs such a file through newer code and lists the moments that would now be decided differently, and reports collisions (a change undone within 15 minutes, a start on a restarting miner) from the action log or from Home Assistant's recorder history.

### First-run checklist

1. Control mode **Manual** (the default)
2. Midday, a step-down the rules propose: press Apply proposal, watch the number change, check `actions.jsonl` has a `pending` then an `ok` line, and that the next cycle treats the miner as tuning.
3. A step-up on the same miner after it has settled.
4. A stop with the pause method, then a start: note whether the limit can be set while paused and how long until it is back.
5. Let a plan change between looking and pressing (change the profile): confirm the "proposal changed" notification.
6. Pull the network or switch the miner off and press: confirm `failed` after the grace time and the notification.
7. A proposal with two miners moving in opposite directions: the reduction goes first.
8. Turn the fixed-hour schedule automation off, then switch to **Automatic**: the next proposal is applied within one cycle, the card title says "applied automatically", `actions.jsonl` lines carry `"trigger": "auto"`, and nothing else is sent for 4 minutes after a change.
9. Write what you saw into the knowledge base (`miners.yaml`, `alerts.yaml`).

## Decision log

The **Decision log** sensor shows what the controller read, how it reasoned and what it proposes for each miner (applied when you press Apply in Manual mode, or every cycle in Automatic). The state is the one-line summary; the `trace`, `proposals` and `history` attributes hold the detail. Use the **Add to dashboard** button for a ready-made card that also shows the AI advice.

## Development

### Quick start

1. Run the local HA dev container:
   ```bash
docker compose -f docker-compose.dev.yml up --build
```
2. Open `http://localhost:8123`.
3. Install HACS if needed by placing the downloaded HACS release in `config/custom_components/hacs`.
4. In HACS, add repository `https://github.com/Schnitzel/hass-miner` as an Integration repo if `hass-miner` is not visible.
5. Install `hass-miner`, restart HA, then add the integration via `Settings → Devices & Services → Add Integration`.
6. Configure your miner with explicit IPs; do not rely on UDP discovery unless using host networking / OrbStack.

### Prerequisites

- [OrbStack](https://orbstack.dev) (recommended on macOS) or Docker Desktop — OrbStack provides native filesystem mount speeds and `--network=host` support; Docker Desktop's bridge networking silently breaks UDP device discovery and has slower mounts
- VS Code with the [Dev Containers](https://marketplace.visualstudio.com/items?itemName=ms-vscode-remote.remote-containers) extension
- Python 3.12 if using the venv alternative below

### Dev container (recommended)

1. Open this repository in VS Code and reopen in Dev Container when prompted
2. Inside the container, run:
   ```bash
   scripts/develop
   ```
3. Home Assistant starts at `http://localhost:8123` with the integration pre-installed
4. Edit `.py` files on your host — changes are reflected via volume mount; restart HA inside the container to pick them up

### Local Docker startup

If you prefer to run Home Assistant directly from the repo without the Dev Container, use the provided compose file:

```bash
docker compose -f docker-compose.dev.yml up --build
```

Then open `http://localhost:8123`.

If HACS is not present in `Settings → Devices & Services → Add Integration` after startup, install it manually in `config/custom_components`:

```bash
mkdir -p config/custom_components
curl -L -o config/custom_components/hacs.zip https://github.com/hacs/integration/releases/latest/download/hacs.zip
unzip -q config/custom_components/hacs.zip -d config/custom_components/hacs
rm config/custom_components/hacs.zip
```

Restart Home Assistant after installing HACS.

### Installing hass-miner in dev

Once HACS is working, install `hass-miner` from the HACS UI:

1. Open HACS and go to `Integrations`
2. If `hass-miner` is not visible, add a custom repository:
   - Repository: `https://github.com/Schnitzel/hass-miner`
   - Category: `Integration`
3. Install `hass-miner`
4. Restart Home Assistant and add the integration via `Settings → Devices & Services → Add Integration`

### Miner connectivity in dev

Configure the integration with the miner's explicit LAN IP address. **Do not use UDP miner discovery** — `pyasic`'s UDP broadcast does not traverse Docker bridge NAT and will fail silently. Outbound TCP to the miner (port 4028 for CGMiner RPC, port 80 for HTTP admin) works through bridge networking when an explicit IP is set.

When OrbStack is the container runtime, `--network=host` is available for any scenario requiring full LAN network parity.

### Running tests (no hardware required)

```bash
pip install -r requirements.txt
pytest
```

All hass-miner entity state is mocked; no container, running HA instance, or physical miner is needed.

### Python venv alternative

For faster iteration without container overhead:

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install homeassistant -r requirements.txt
mkdir -p config/custom_components
ln -s "$(pwd)/custom_components/solar_smart_miner" config/custom_components/solar_smart_miner
hass -c config
```

HA constraint: Python 3.12 (do not use 3.11 or 3.13).

### Syncing to a real HA instance

**Docker-based HA:**
```bash
docker cp custom_components/solar_smart_miner <container-name>:/config/custom_components/
docker restart <container-name>
```

**SSH-accessible HA:**
```bash
scp -r custom_components/solar_smart_miner/ ha-user@ha-host:/config/custom_components/
```

Then restart Home Assistant. Start in Manual (the default), walk through the [first-run checklist](#first-run-checklist) on the real miners, and only then switch to Automatic.

## Roadmap

- Event-triggered decisions (replace polling with HA state-change triggers)
- Weather forecast integration (pre-position miners based on next-day solar)
- Grid electricity price integration
- Decision history UI in the HA frontend

## License

MIT
