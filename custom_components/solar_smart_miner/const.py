from __future__ import annotations

DOMAIN = "solar_smart_miner"
HASS_MINER_PLATFORM = "miner"  # domain of the hass-miner integration (github.com/Schnitzel/hass-miner)

DEFAULT_POLLING_INTERVAL = 300  # seconds; minimum 60
DEFAULT_TEMP_CEILING = 80  # °C
DEFAULT_BATTERY_FLOOR = 20  # % SOC
DEFAULT_AI_TIMEOUT = 10  # seconds; OpenRouter call timeout

PROFILES = [
    {
        "name": "battery_focused",
        "display_name": "Battery-focused",
        "description": (
            "Prioritise preserving battery SOC. Run at efficiency-optimal wattage "
            "and back off as battery drops toward the SOC floor."
        ),
        "parameters": {
            "priority": "battery_soc",
            "reduce_at_soc_pct": 60,
            "stop_at_soc_pct": 20,
            "grid_draw_allowed": True,
        },
    },
    {
        "name": "solar_max",
        "display_name": "Solar-max",
        "description": (
            "Run at maximum wattage during high solar production. "
            "Back off when production drops below consumption."
        ),
        "parameters": {
            "priority": "solar_production",
            "grid_draw_allowed": True,
        },
    },
    {
        "name": "grid_agnostic",
        "display_name": "Grid-agnostic",
        "description": (
            "Use solar surplus freely and supplement with grid without penalty. "
            "Optimise for maximum hashrate."
        ),
        "parameters": {
            "priority": "hashrate",
            "grid_draw_allowed": True,
        },
    },
    {
        "name": "grid_independent",
        "display_name": "Grid-independent",
        "description": (
            "Never draw net power from the grid. "
            "Cap miner wattage to (solar production − base household consumption)."
        ),
        "parameters": {
            "priority": "grid_independence",
            "grid_draw_allowed": False,
        },
    },
]

PROFILE_NAMES = [p["name"] for p in PROFILES]
DEFAULT_PROFILE = "solar_max"

# Mock solar mode — development only
CONF_MOCK_SOLAR_ENABLED = "mock_solar_enabled"
CONF_MOCK_SOLAR_ENTITY = "mock_solar_entity"

# Mock consumption mode — development only
CONF_MOCK_CONSUMPTION_ENABLED = "mock_consumption_enabled"
