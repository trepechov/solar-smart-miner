# Quick start: local dev environment

A Home Assistant dev instance with Solar Smart Miner and hass-miner, in about five minutes.

**You need** Docker and Docker Compose (on macOS, OrbStack is faster and supports host networking), and a
browser.

## 1. Start Home Assistant

```bash
docker compose -f docker-compose.dev.yml up --build
```

Open `http://localhost:8123`. (Or open the repo in VS Code, reopen in the Dev Container and run
`scripts/develop`.)

## 2. Install HACS, if it isn't there

```bash
mkdir -p config/custom_components
curl -L -o config/custom_components/hacs.zip https://github.com/hacs/integration/releases/latest/download/hacs.zip
unzip -q config/custom_components/hacs.zip -d config/custom_components/hacs
rm config/custom_components/hacs.zip
docker compose -f docker-compose.dev.yml restart
```

## 3. Install hass-miner and add your miners

1. In HACS, add `https://github.com/Schnitzel/hass-miner` as a custom repository (category Integration),
   install it, and restart.
2. **Add Integration → hass-miner**, with each miner's **explicit LAN IP**. UDP discovery doesn't cross
   Docker's bridge network; use host networking (OrbStack, or `network_mode: "host"` in
   `docker-compose.dev.yml`) if you need it.

## 4. Add Solar Smart Miner

**Add Integration → Solar Smart Miner**, map the solar entity (and optionally the house consumption and
battery sensors), optionally an OpenRouter key. Then under **Configure**: your power steps (about 200 W apart; the range
your miner model accepts), the temperature target and tolerance, and the **Farm** section. Keep the control
mode on **Manual** until you have gone through the first-run list in the README.

## 5. No solar hardware? Mock it

Install **Forecast.Solar** with your location and panel size, then in **Configure → Settings → Development**
turn on *Use Forecast.Solar as mock solar data* and pick its power-now sensor. The decision log shows
`[MOCK SOLAR]` while it is on.

## Tests

No HA instance or miner needed:

```bash
pip install -r requirements.txt
python -m pytest tests/
```

Without Docker: `python3.12 -m venv .venv && source .venv/bin/activate && pip install homeassistant -r requirements.txt`,
link `custom_components/solar_smart_miner` into `config/custom_components/` and run `hass -c config`
(Python 3.12).

## Iterating and deploying

Edit the files on your host and restart HA in the container. To try a build on a real HA without a release:

```bash
docker cp custom_components/solar_smart_miner <container>:/config/custom_components/ && docker restart <container>
# or over SSH
scp -r custom_components/solar_smart_miner/ user@ha-host:/config/custom_components/
```

Releases reach users through HACS (see CLAUDE.md, *Versioning and Releases*).

## Troubleshooting

- **"Invalid handler specified":** a file is missing in the container; check
  `docker logs <container> 2>&1 | grep solar_smart_miner` and copy again.
- **HA exits with code 100:** a Python import error; see `docker compose logs`.
- **Miner not found:** use its explicit IP, not discovery.
