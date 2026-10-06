from __future__ import annotations

DOMAIN = "solar_smart_miner"

DEFAULT_POLLING_INTERVAL = 15  # seconds
MIN_POLLING_INTERVAL = 1  # seconds; hass-miner itself refreshes every 10 s
# Below the target a miner may step up; from target to target + tolerance it holds;
# at or above target + tolerance it steps down one step.
DEFAULT_TEMP_TARGET = 65  # °C
DEFAULT_TEMP_TOLERANCE = 10  # °C above the target
DEFAULT_BATTERY_FLOOR = 20  # % SOC
DEFAULT_AI_TIMEOUT = 40  # seconds; OpenRouter call timeout (free models can be slow)
DEFAULT_AI_INTERVAL = 60  # seconds between AI advice requests
MIN_AI_INTERVAL = 10  # seconds
ASK_AI_COOLDOWN = 10  # seconds; minimum gap between "Ask AI now" presses
OPENROUTER_API_URL = "https://openrouter.ai/api/v1/chat/completions"

# Knowledge base in the AI prompt (kb.py). P0 and P1 are always sent; this caps the rest.
KB_PROMPT_BUDGET_CHARS = 6000
KB_NIGHT_ELEVATION = -3  # deg; sun at or below this is night
KB_TRANSITION_ELEVATION = 15  # deg; below this the sun is rising or setting

# Miners re-tune every time the power limit changes (14 min to an hour), so the limit
# only ever moves between these steps. W; each miner uses the ones inside its own range.
DEFAULT_POWER_STEPS = [900, 1100, 1300, 1500]
DEFAULT_TUNING_SETTLE_MINUTES = 60  # after a limit change the miner is "tuning": no step up
HOLD_TOLERANCE_W = 150  # a shortfall this small keeps the current step (avoids re-tuning)
UP_MARGIN_W = 100  # spare power needed beyond a step's cost before moving up to it

# Solar-max (Solar-follow) aims for a small steady grid import, not for zero: at zero a
# throttled inverter hides how much more the panels could give. With the tolerance and
# margin above, a 400 W target steps up at <= 100 W import and down above 550 W.
DEFAULT_IMPORT_TARGET_W = 400
# The meter within this of 0 W is what a throttled inverter looks like (situation.curtailed).
METER_NEAR_ZERO_W = 100

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
PROFILES_BY_NAME = {p["name"]: p for p in PROFILES}
DEFAULT_PROFILE = "solar_max"

# Mock solar mode — development only
CONF_MOCK_SOLAR_ENABLED = "mock_solar_enabled"
CONF_MOCK_SOLAR_ENTITY = "mock_solar_entity"

# Mock consumption mode — development only
CONF_MOCK_CONSUMPTION_ENABLED = "mock_consumption_enabled"

HASS_MINER_PLATFORM = "miner"  # domain of the hass-miner integration (github.com/Schnitzel/hass-miner)

# What the configured "solar" entity actually measures.
SOLAR_ENTITY_TYPE_PRODUCTION = "production"  # PV output, >= 0
SOLAR_ENTITY_TYPE_NET_EXPORT = "grid_net_export"  # grid meter: + export, - import
SOLAR_ENTITY_TYPE_NET_IMPORT = "grid_net_import"  # grid meter: + import, - export
SOLAR_ENTITY_TYPES = [
    SOLAR_ENTITY_TYPE_PRODUCTION,
    SOLAR_ENTITY_TYPE_NET_EXPORT,
    SOLAR_ENTITY_TYPE_NET_IMPORT,
]

# Control mode: whether the integration may touch the miners (control.py).
CONF_CONTROL_MODE = "control_mode"
CONTROL_MODE_PREVIEW = "preview"  # decisions are only shown
CONTROL_MODE_MANUAL = "manual"  # the owner applies each proposed action with a button
CONTROL_MODE_AUTO = "auto"  # reserved for automatic applying; not offered yet
CONTROL_MODES = [CONTROL_MODE_PREVIEW, CONTROL_MODE_MANUAL]
DEFAULT_CONTROL_MODE = CONTROL_MODE_PREVIEW  # an upgrade changes nothing until the owner chooses
CONTROL_MODE_LABELS = {CONTROL_MODE_PREVIEW: "Preview", CONTROL_MODE_MANUAL: "Manual"}

# How long an applied command may take to show in the miner's entity before it counts as failed.
# A relay start is slower: the miner has to boot before its limit entity is back.
APPLY_VERIFY_GRACE_S = 60
APPLY_VERIFY_GRACE_RELAY_START_S = 300

ACTIVITY_SIZE = 30  # proposals and applied actions kept for the activity feed
DECISION_HISTORY_SIZE = 20  # decision-log entries kept on the sensor
