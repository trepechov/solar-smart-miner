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

A Home Assistant custom integration that uses an AI agent to dynamically control ASIC miner power limits based on real-time solar production, household consumption, and battery state — with hard safety rails, dry-run mode, and Telegram notifications.

## What it does

Bitcoin ASIC miners are power-hungry and most efficient when run continuously at a fixed wattage. Homes with solar panels and batteries operate in a constantly shifting energy environment: production peaks midday, drops at night, batteries fill and drain. Without automation, miners either waste solar surplus or drain batteries unnecessarily.

Solar Smart Miner closes that gap. An AI agent runs on a configurable interval, reads your current energy state from Home Assistant, and adjusts each miner's power limit accordingly. The AI reasons with your chosen profile (e.g. maximise solar self-consumption, protect battery SOC, never draw from the grid) and logs its reasoning for every decision it makes.

## Features

- **AI-driven power control** — an agent powered by any OpenRouter-compatible model (including free Llama/Gemma) sets per-miner power limits based on live energy data
- **Four built-in profiles** — Battery-focused, Solar-max, Grid-agnostic, Grid-independent; switchable from the HA UI without restart
- **Hard safety layer** — temperature ceiling, battery SOC floor, and solar fault checks run before every AI decision and cannot be reasoned around
- **Dry-run mode** — the agent runs its full decision cycle and logs what it would do, without touching any miner; togglable from the HA UI
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
| **Settings** | Profile, power steps, tuning time, polling interval, temperature ceiling, battery floor, dry-run, Telegram, development mocks |

### AI advice (OpenRouter)

The model dropdown lists the **free** text models OpenRouter currently offers, fetched when you open the form (any other model id can be typed in). The default, `openrouter/free`, lets OpenRouter pick an available free model, so it keeps working when individual free models are retired.

The AI is **advisory only**: every few minutes it is sent the current readings and the rule-based proposal, and its short comment appears on the **AI advice** sensor (full text in the `response` attribute) and in the dashboard card. Nothing it says changes the miners. Press **Ask AI now** to ask on demand. The default interval is 60 s (minimum 10 s). Free models allow only a limited number of requests per day, so a short interval may need a paid model or a longer interval.

#### AI decision log

The AI answers in a fixed shape: a one-line summary plus, for each miner, an action (`increase`, `reduce` or `hold`) and a reason (`excess_energy`, `not_enough_energy`, `voltage_limit`, `temperature_limit`, `battery_low`, `no_change` or `other`). These are what it *would* do; nothing is applied.

Every request is appended to `<HA config>/solar_smart_miner/ai_log.jsonl`, one JSON object per line: the readings it was given (solar, grid, house, miners, battery), the actual PV output and the forecast (now / next hour / energy left today, if you set those sensors), each miner's state, the rule-based proposal, the exact prompt, and the AI's answer and actions. The file rotates at 5 MB and keeps two older files. Read it with `jq` or any text editor, e.g. `jq -c '{ts, pv: .inputs.pv_actual_w, fc: .inputs.forecast_now_w, ai: .ai.actions}' ai_log.jsonl`.

The last 20 entries are also on the **AI advice** sensor (`history` and `actions` attributes) and in the card from **Add to dashboard** under *AI log*.

## Profiles

| Profile | Behaviour |
|---|---|
| **Battery-focused** | Prioritise preserving battery SOC; run at efficiency-optimal wattage; back off as battery drops |
| **Solar-max** | Run at maximum wattage during high solar production; back off when production drops |
| **Grid-agnostic** | Use solar surplus freely and supplement with grid without penalty; optimise for hashrate |
| **Grid-independent** | Never draw net power from the grid; cap miner wattage to (production − base consumption) |

## Power steps, stopping and tuning

A miner re-tunes itself for 14 minutes to an hour after every power-limit change, and only reaches its best efficiency once that is done. So the controller never asks for an arbitrary wattage: limits move only between **power steps**, **900, 1100, 1300 and 1500 W** by default (Configure → Settings; each miner uses the steps inside its own range).

- **Starting from where the miner is.** The plan begins at each miner's current step. A shortfall of up to 150 W keeps the current step, and stepping up needs 100 W of spare power on top of the step's cost, so small wobbles in the budget change nothing.
- **Stopping.** When the budget is below the lowest step the miner is **stopped**, not just turned down to a minimum. How is configured per miner under Configure → Miner stop method: a **relay** switch (for miners cut off with a relay) or, when none is set, the miner's own **pause** switch (`switch.<miner>_active` from hass-miner). A stopped miner is started again when its lowest step plus the margin fits. A miner with neither is dropped to its lowest step instead.
- **Tuning.** hass-miner doesn't report the tuning state, so it is estimated: a miner is assumed to be tuning for the configured number of minutes (default 60) after its limit last changed. While tuning it is never stepped up; stepping down and stopping are still allowed. A limit that was already set when Home Assistant started counts as settled.
- **Filling order.** A running miner is stepped up to its top step before another miner is started.

These are plans only for now: the decision log and the AI advice show what would be done, and nothing is applied to the miners yet.

## Safety layer

The following overrides run before every AI decision and cannot be bypassed — not even by dry-run mode:

- **Temperature ceiling** — if any miner exceeds the configured board/chip temperature, it is dropped to its lowest power step
- **Battery SOC floor** — if battery drops below the configured %, all miners are stopped
- **Solar fault** — if the solar entity is unavailable, every miner is held as it is: a short sensor drop must not make the miners re-tune

## Decision log

The **Decision log** sensor shows what the controller read, how it reasoned and what it *would* set for each miner (a preview — nothing is applied yet). The state is the one-line summary; the `trace`, `proposals` and `history` attributes hold the detail. Use the **Add to dashboard** button for a ready-made card that also shows the AI advice.

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

Then restart Home Assistant. Run dry-run mode for at least 24 hours before switching to live control.

## Roadmap

- Event-triggered decisions (replace polling with HA state-change triggers)
- Weather forecast integration (pre-position miners based on next-day solar)
- Grid electricity price integration
- Decision history UI in the HA frontend

## License

MIT
