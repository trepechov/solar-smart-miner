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

The project uses a dev container for local development against a real HA instance. The test suite uses `pytest` with `pytest-homeassistant-custom-component`; no real miner or solar hardware is required to run tests.

```bash
# Run tests
pytest
```

## Roadmap

- Event-triggered decisions (replace polling with HA state-change triggers)
- Weather forecast integration (pre-position miners based on next-day solar)
- Grid electricity price integration
- Decision history UI in the HA frontend

## License

MIT
