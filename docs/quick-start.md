# Quick Start — Local Dev Environment

Get a working Home Assistant dev instance with Solar Smart Miner and hass-miner running in ~5 minutes.

## Prerequisites

- Docker and Docker Compose (or OrbStack on macOS — recommended for faster mounts)
- Browser

## Steps

### 1. Start Home Assistant

```bash
docker compose -f docker-compose.dev.yml up --build
```

Open `http://localhost:8123` in your browser.

### 2. Install HACS (if needed)

If HACS does not appear in `Settings → Devices & Services → Add Integration`:

```bash
mkdir -p config/custom_components
curl -L -o config/custom_components/hacs.zip https://github.com/hacs/integration/releases/latest/download/hacs.zip
unzip -q config/custom_components/hacs.zip -d config/custom_components/hacs
rm config/custom_components/hacs.zip
```

Restart the Home Assistant container:

```bash
docker compose -f docker-compose.dev.yml restart
```

### 3. Install hass-miner

In the Home Assistant UI:

1. Open HACS (from the sidebar)
2. Go to `Integrations`
3. If `hass-miner` is not visible:
   - Click `⋮` (settings) → `Custom repositories`
   - Add repository: `https://github.com/Schnitzel/hass-miner`
   - Set category to `Integration`
   - Save
4. Search for `hass-miner` and install it
5. Restart Home Assistant

### 4. Configure Miners

In the Home Assistant UI:

1. Go to `Settings → Devices & Services → Add Integration`
2. Search for and add `hass-miner`
3. Enter your miner's **explicit LAN IP address** (e.g., `192.168.1.50`)
4. Do not rely on UDP auto-discovery — it does not work through Docker bridge networking unless you use host networking (OrbStack or uncomment `network_mode: "host"` in `docker-compose.dev.yml`)

### 5. Add Solar Smart Miner

1. Go to `Settings → Devices & Services → Add Integration`
2. Search for and add `Solar Smart Miner`
3. Enter your OpenRouter API key and select a model
4. Map your solar production, consumption, and (optionally) battery SOC entities from your existing Home Assistant integrations
5. Select the miners managed by hass-miner
6. Set safety thresholds (temperature ceiling, battery SOC floor)
7. Choose a starting profile and enable dry-run mode initially

## Testing Without Hardware

Run unit tests locally (no HA instance, no miner needed):

```bash
pip install -r requirements.txt
pytest
```

## Editing and Iterating

1. Edit `.py` files in `custom_components/solar_smart_miner/` on your host
2. Restart Home Assistant inside the container to pick up changes
3. Check logs at `http://localhost:8123/config/logs`

## Syncing to Production

When ready to deploy to a real Home Assistant instance:

```bash
scp -r custom_components/solar_smart_miner/ ha-user@ha-host:/config/custom_components/
```

Restart Home Assistant on the production host. Run in dry-run mode for at least 24 hours before enabling live control.

## Troubleshooting

- **HACS not found after install:** Use a modern browser (Chrome/Firefox); Safari may have front-end rendering issues
- **Miner not found in hass-miner config:** Use explicit IP, not UDP discovery
- **Docker networking slow on macOS:** Switch to OrbStack for native filesystem mounts
- **HA exits with code 100:** Check `docker compose logs` for Python import errors; ensure HACS `hacs_frontend` module is present
