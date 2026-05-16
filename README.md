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

## Configuration

The integration is configured entirely through the Home Assistant UI config flow:

1. Enter your OpenRouter API key and select a model
2. Map your solar production entity (required), grid consumption entity (required), and battery SOC entity (optional)
3. Select the miners to manage (discovered from hass-miner)
4. Set safety thresholds (temperature ceiling, battery SOC floor)
5. Choose a starting profile and whether to begin in dry-run mode
6. Optionally enter your Telegram bot token and chat ID

## Profiles

| Profile | Behaviour |
|---|---|
| **Battery-focused** | Prioritise preserving battery SOC; run at efficiency-optimal wattage; back off as battery drops |
| **Solar-max** | Run at maximum wattage during high solar production; back off when production drops |
| **Grid-agnostic** | Use solar surplus freely and supplement with grid without penalty; optimise for hashrate |
| **Grid-independent** | Never draw net power from the grid; cap miner wattage to (production − base consumption) |

## Safety layer

The following overrides run before every AI decision and cannot be bypassed — not even by dry-run mode:

- **Temperature ceiling** — if any miner exceeds the configured board/chip temperature, it is stopped or throttled immediately
- **Battery SOC floor** — if battery drops below the configured %, all miners stop immediately
- **Solar fault** — if the solar entity enters an unavailable or error state, all miners stop until it recovers

## Decision log

Every agent decision — including the model's reasoning, the energy snapshot it saw, and the power limit it chose — is written to the HA log. In dry-run mode this is the primary output; in live mode it runs alongside actual miner changes.

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

```bash
scp -r custom_components/solar_smart_miner/ ha-user@ha-host:/config/custom_components/
```

Then restart Home Assistant on the production host. Run dry-run mode for at least 24 hours before switching to live control.

## Roadmap

- Event-triggered decisions (replace polling with HA state-change triggers)
- Weather forecast integration (pre-position miners based on next-day solar)
- Grid electricity price integration
- Decision history UI in the HA frontend

## License

MIT
