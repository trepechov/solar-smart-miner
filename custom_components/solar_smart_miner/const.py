from __future__ import annotations

DOMAIN = "solar_smart_miner"

DEFAULT_POLLING_INTERVAL = 15  # seconds
MIN_POLLING_INTERVAL = 1  # seconds; hass-miner itself refreshes every 10 s
# Below the target a miner may step up; from target to target + tolerance it holds;
# at or above target + tolerance it steps down one step.
DEFAULT_TEMP_TARGET = 60  # °C
DEFAULT_TEMP_TOLERANCE = 10  # °C above the target
DEFAULT_BATTERY_FLOOR = 20  # % SOC
DEFAULT_AI_TIMEOUT = 40  # seconds; OpenRouter call timeout (free models can be slow)
DEFAULT_AI_INTERVAL = 60  # seconds between AI advice requests
MIN_AI_INTERVAL = 10  # seconds
ASK_AI_COOLDOWN = 10  # seconds; minimum gap between "Ask AI now" presses
OPENROUTER_API_URL = "https://openrouter.ai/api/v1/chat/completions"

# Knowledge base in the AI prompt (kb.py). P0 and P1 are always sent; this caps the rest.
KB_PROMPT_BUDGET_CHARS = 6000

# Defaults are the reference farm's values (the owner's first farm); on another farm they are
# settings. Every limit change restarts a miner, and a wattage it has never run takes much
# longer to tune, so the limit only ever moves between these steps. W; each miner uses the ones
# inside its own range.
DEFAULT_POWER_STEPS = [900, 1100, 1300, 1500, 1700, 1900, 2100, 2300, 2500]
# Setting "restart time after a change" (it depends on the miner type): the longest every miner
# holds after a change while the changed one restarts; it ends earlier once that one has settled.
DEFAULT_RAMP_LOCK_MINUTES = 4
# Generic, not settings: a changed miner is done ramping early once it draws within this
# fraction of its new limit (its hashrate may still be settling), but not before
# RAMP_MIN_MINUTES: right after a change the old reading could otherwise look done.
RAMP_DONE_FRACTION = 0.05
RAMP_MIN_MINUTES = 1

# Solar-follow keeps the grid import inside a range, never at zero: at zero a
# throttled inverter hides how much more the panels could give. Below the minimum it takes one
# increment; inside the range it holds; above the maximum it steps down once the import has
# stayed that high for the step-down delay. Keep the range at least one power step wide, so a
# step from just outside lands inside.
DEFAULT_IMPORT_MIN_W = 200  # the minimum import (setting "import_min")
DEFAULT_IMPORT_MAX_W = 400
# A shortfall must last this long before a step down (rule.down-slowly-up-promptly); during
# sunrise it waits longer, since production is catching up. Minutes.
DEFAULT_STEP_DOWN_DELAY_MINUTES = 5
DEFAULT_MORNING_STEP_DOWN_DELAY_MINUTES = 30

# Sunrise and sunset are periods of changing production (transition.py). Fixed values, from the
# reference farm's history (2026-10-06 to 10-08: about +200 to +750 W per 15 minutes while the
# sun rose, under 100 W either way at midday without clouds, falling steadily in the evening);
# settings only if another farm proves them wrong.
TRANSITION_WINDOW_MIN = 15
TRANSITION_MIN_SPAN_MIN = 10  # the window must cover this much before it says anything
TRANSITION_CHANGE_W = 150  # production rising (sunrise) or falling (sunset) by more than this
SUNSET_GATE_MIN = 120  # sunset can only start this close to sun.sun's next setting

# One profile until Setup B (a battery). The battery profiles (requirements doc §6.3) add
# entries here and bring back the profile select (ProfileSelect, retired in 0.8.0) and the
# Configure field; the stored `profile` option is kept for that. Owner, 2026-10-09: Full
# power / Grid-agnostic, Grid-independent and Battery-focused are dropped.
PROFILES = [
    {
        "name": "solar_follow",
        "display_name": "Solar-follow",
        "description": "Aim for a small steady grid import: all the solar is used, and a little more.",
    },
]

PROFILE_NAMES = [p["name"] for p in PROFILES]
PROFILES_BY_NAME = {p["name"]: p for p in PROFILES}
DEFAULT_PROFILE = "solar_follow"
# Profiles of 0.7 and before; a stored one reads as Solar-follow (0.8.0).
LEGACY_PROFILES = ("solar_max", "grid_agnostic", "grid_independent", "battery_focused")

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
CONTROL_MODE_MANUAL = "manual"  # the owner applies the proposal with the Apply button
CONTROL_MODE_AUTO = "auto"  # the coordinator applies the proposal every cycle
CONTROL_MODES = [CONTROL_MODE_MANUAL, CONTROL_MODE_AUTO]
DEFAULT_CONTROL_MODE = CONTROL_MODE_MANUAL  # nothing is applied until the owner presses or chooses Auto
CONTROL_MODE_LABELS = {CONTROL_MODE_MANUAL: "Manual", CONTROL_MODE_AUTO: "Automatic"}
# Up to 0.7.4 "import_target" held the single import target (400 W by default); 0.7.4 read
# it as the minimum. Its value means something else now, so it is dropped, not migrated.
LEGACY_IMPORT_TARGET = "import_target"
LEGACY_CONTROL_MODE_PREVIEW = "preview"  # removed in 0.7.2; a stored value reads as Manual
# Settings that no longer exist; a stored value is dropped at setup. "tuning_settle_minutes" (up
# to 0.7): a window after a change with no step-up and the temperature ignored, merged into the
# settling after a restart in 0.8.0 (decision/pacing.py).
RETIRED_OPTIONS = ("tuning_settle_minutes",)


def control_mode_of(options) -> str:
    """The control mode in these options; anything not a current mode (an old "preview") is Manual."""
    mode = options.get(CONF_CONTROL_MODE)
    return mode if mode in CONTROL_MODES else DEFAULT_CONTROL_MODE


# How long an applied command may take to show in the miner's entity before it counts as failed.
# A relay start is slower: the miner has to boot before its limit entity is back.
APPLY_VERIFY_GRACE_S = 60
APPLY_VERIFY_GRACE_RELAY_START_S = 300

NO_ACTION_TEXT = "no action"
ACTIVITY_SIZE = 30  # proposals and applied actions kept for the activity feed
DECISION_HISTORY_SIZE = 20  # decision-log entries kept on the sensor
