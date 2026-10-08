"""Config flow for Solar Smart Miner.

Miners are not configured here: the coordinator manages every miner set up in
hass-miner automatically.
"""
from __future__ import annotations

import hashlib
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    EntitySelector,
    EntitySelectorConfig,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .ai import async_free_models
from .const import (
    CONF_CONTROL_MODE,
    CONF_MOCK_CONSUMPTION_ENABLED,
    CONF_MOCK_SOLAR_ENABLED,
    CONF_MOCK_SOLAR_ENTITY,
    DEFAULT_AI_INTERVAL,
    CONTROL_MODE_LABELS,
    CONTROL_MODES,
    DEFAULT_BATTERY_FLOOR,
    DEFAULT_CONTROL_MODE,
    DEFAULT_IMPORT_TARGET_W,
    DEFAULT_MORNING_STEP_DOWN_DELAY_MINUTES,
    DEFAULT_POLLING_INTERVAL,
    DEFAULT_POWER_STEPS,
    DEFAULT_PROFILE,
    DEFAULT_STEP_DOWN_DELAY_MINUTES,
    DEFAULT_TEMP_TARGET,
    DEFAULT_TEMP_TOLERANCE,
    DEFAULT_TUNING_SETTLE_MINUTES,
    DOMAIN,
    HASS_MINER_PLATFORM,
    MIN_AI_INTERVAL,
    MIN_POLLING_INTERVAL,
    PROFILES,
    SOLAR_ENTITY_TYPE_NET_EXPORT,
    SOLAR_ENTITY_TYPE_NET_IMPORT,
    SOLAR_ENTITY_TYPE_PRODUCTION,
    control_mode_of,
)

CONF_SOLAR_ENTITY = "solar_production_entity"
CONF_SOLAR_ENTITY_TYPE = "solar_entity_type"
CONF_GRID_ENTITY = "grid_consumption_entity"  # house consumption, miners included
CONF_OPENROUTER_KEY = "openrouter_api_key"
CONF_OPENROUTER_MODEL = "openrouter_model"
CONF_BATTERY_ENTITY = "battery_soc_entity"
# Reference sensors: shown to the AI and written to its log, never used by the rules.
CONF_PV_ENTITY = "pv_power_entity"
CONF_FORECAST_NOW_ENTITY = "forecast_power_now_entity"
CONF_FORECAST_NEXT_HOUR_ENTITY = "forecast_power_next_hour_entity"
CONF_FORECAST_REMAINING_ENTITY = "forecast_energy_remaining_entity"
REFERENCE_ENTITY_KEYS = (
    CONF_BATTERY_ENTITY,
    CONF_PV_ENTITY,
    CONF_FORECAST_NOW_ENTITY,
    CONF_FORECAST_NEXT_HOUR_ENTITY,
    CONF_FORECAST_REMAINING_ENTITY,
)
CONF_TEMP_TARGET = "temp_target"
CONF_TEMP_TOLERANCE = "temp_tolerance"
CONF_BATTERY_FLOOR = "battery_floor"
CONF_IMPORT_TARGET = "import_target"  # W: Solar-max's grid import floor
CONF_STEP_DOWN_DELAY = "step_down_delay_minutes"  # a shortfall must last this long first
CONF_MORNING_STEP_DOWN_DELAY = "morning_step_down_delay_minutes"  # the same while the sun rises
CONF_PROFILE = "profile"
CONF_POLLING_INTERVAL = "polling_interval"
CONF_TELEGRAM_TOKEN = "telegram_bot_token"
CONF_TELEGRAM_CHAT_ID = "telegram_chat_id"
CONF_AI_ENABLED = "ai_enabled"
CONF_AI_INTERVAL = "ai_interval"
CONF_POWER_STEPS = "power_steps"  # list[int] in options; typed as "900, 1100, 1300, 1500"
CONF_TUNING_SETTLE = "tuning_settle_minutes"
CONF_MINER_RELAYS = "miner_relays"  # options: {miner id (its IP): relay switch entity id}
CONF_MINER = "miner"  # form field: which miner the relay below belongs to
CONF_RELAY_ENTITY = "relay_entity"

# Routes to whichever free model is up, so it survives free models being rotated out.
DEFAULT_OPENROUTER_MODEL = "openrouter/free"


def _stable_unique_id(solar_entity_id: str, grid_entity_id: str) -> str:
    return hashlib.sha256(f"{solar_entity_id}:{grid_entity_id}".encode()).hexdigest()[:16]


def _entity_exists(hass: HomeAssistant, entity_id: str) -> bool:
    return hass.states.get(entity_id) is not None


def _validate_entities(hass: HomeAssistant, user_input: dict[str, Any]) -> dict[str, str]:
    errors: dict[str, str] = {}
    house = (user_input.get(CONF_GRID_ENTITY) or "").strip()
    if not _entity_exists(hass, user_input[CONF_SOLAR_ENTITY]):
        errors[CONF_SOLAR_ENTITY] = "solar_entity_not_found"
    elif house and not _entity_exists(hass, house):
        errors[CONF_GRID_ENTITY] = "grid_entity_not_found"
    return errors


async def _async_model_options(hass: HomeAssistant, current_model: str | None) -> list[SelectOptionDict]:
    """Free OpenRouter models for the dropdown; the current choice is always listed."""
    models = await async_free_models(async_get_clientsession(hass))
    if current_model and current_model not in {model_id for model_id, _ in models}:
        models.insert(0, (current_model, current_model))
    return [
        SelectOptionDict(value=model_id, label=label if label == model_id else f"{label} ({model_id})")
        for model_id, label in models
    ]


_PROFILE_SELECTOR = SelectSelector(
    SelectSelectorConfig(
        options=[SelectOptionDict(value=p["name"], label=p["display_name"]) for p in PROFILES],
        mode=SelectSelectorMode.DROPDOWN,
    )
)

_CONTROL_MODE_SELECTOR = SelectSelector(
    SelectSelectorConfig(
        options=[SelectOptionDict(value=m, label=CONTROL_MODE_LABELS[m]) for m in CONTROL_MODES],
        mode=SelectSelectorMode.DROPDOWN,
    )
)

_SOLAR_ENTITY_TYPE_SELECTOR = SelectSelector(
    SelectSelectorConfig(
        options=[
            SelectOptionDict(
                value=SOLAR_ENTITY_TYPE_PRODUCTION, label="Solar production (always ≥ 0)"
            ),
            SelectOptionDict(
                value=SOLAR_ENTITY_TYPE_NET_EXPORT,
                label="Net grid power (+ export / − import)",
            ),
            SelectOptionDict(
                value=SOLAR_ENTITY_TYPE_NET_IMPORT,
                label="Net grid power (+ import / − export)",
            ),
        ],
        mode=SelectSelectorMode.DROPDOWN,
    )
)

_POLLING_SELECTOR = NumberSelector(
    NumberSelectorConfig(
        min=MIN_POLLING_INTERVAL,
        max=3600,
        step=1,
        unit_of_measurement="s",
        mode=NumberSelectorMode.BOX,
    )
)


def parse_power_steps(text: str) -> list[int] | None:
    """"900, 1100 1300" -> [900, 1100, 1300]; None if it isn't a list of plausible watts."""
    try:
        values = sorted({int(part) for part in text.replace(",", " ").split()})
    except ValueError:
        return None
    if not values or any(not 100 <= v <= 10000 for v in values):
        return None
    return values


def _format_power_steps(steps: list[int] | None) -> str:
    return ", ".join(str(step) for step in (steps or DEFAULT_POWER_STEPS))


def _model_selector(options: list[SelectOptionDict]) -> SelectSelector:
    # custom_value: any OpenRouter model id works, not just the free ones listed.
    return SelectSelector(
        SelectSelectorConfig(options=options, custom_value=True, mode=SelectSelectorMode.DROPDOWN)
    )


def _sensor_fields(current: dict[str, Any]) -> dict:
    """Solar / house entity pickers. Both are suggested (not defaulted) so house can be cleared."""
    return {
        vol.Required(
            CONF_SOLAR_ENTITY, default=current.get(CONF_SOLAR_ENTITY, vol.UNDEFINED)
        ): EntitySelector(EntitySelectorConfig(domain="sensor")),
        vol.Required(
            CONF_SOLAR_ENTITY_TYPE,
            default=current.get(CONF_SOLAR_ENTITY_TYPE, SOLAR_ENTITY_TYPE_PRODUCTION),
        ): _SOLAR_ENTITY_TYPE_SELECTOR,
        vol.Optional(
            CONF_GRID_ENTITY, description={"suggested_value": current.get(CONF_GRID_ENTITY)}
        ): EntitySelector(EntitySelectorConfig(domain="sensor")),
    }


def _ai_key_field(current: dict[str, Any]) -> dict:
    return {
        vol.Optional(
            CONF_OPENROUTER_KEY, description={"suggested_value": current.get(CONF_OPENROUTER_KEY)}
        ): TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD)),
    }


def _model_field(current: dict[str, Any], model_options: list[SelectOptionDict]) -> dict:
    return {
        vol.Required(
            CONF_OPENROUTER_MODEL,
            default=current.get(CONF_OPENROUTER_MODEL) or DEFAULT_OPENROUTER_MODEL,
        ): _model_selector(model_options),
    }


def _step1_schema(
    model_options: list[SelectOptionDict], current: dict[str, Any] | None = None
) -> vol.Schema:
    """Initial setup: sensors + OpenRouter credentials."""
    current = current or {}
    return vol.Schema(
        {**_sensor_fields(current), **_ai_key_field(current), **_model_field(current, model_options)}
    )


def _sensors_schema(current: dict[str, Any]) -> vol.Schema:
    """Options: entities only. The optional fields can be cleared."""
    return vol.Schema(
        {
            **_sensor_fields(current),
            **{
                vol.Optional(key, description={"suggested_value": current.get(key)}): EntitySelector(
                    EntitySelectorConfig(domain="sensor")
                )
                for key in REFERENCE_ENTITY_KEYS
            },
        }
    )


def _ai_schema(
    data: dict[str, Any], options: dict[str, Any], model_options: list[SelectOptionDict]
) -> vol.Schema:
    return vol.Schema(
        {
            vol.Required(CONF_AI_ENABLED, default=options.get(CONF_AI_ENABLED, True)): bool,
            **_ai_key_field(data),
            **_model_field(data, model_options),
            vol.Required(
                CONF_AI_INTERVAL, default=options.get(CONF_AI_INTERVAL, DEFAULT_AI_INTERVAL)
            ): NumberSelector(
                NumberSelectorConfig(
                    min=MIN_AI_INTERVAL,
                    max=86400,
                    step=1,
                    unit_of_measurement="s",
                    mode=NumberSelectorMode.BOX,
                )
            ),
        }
    )


def _step2_schema() -> vol.Schema:
    return vol.Schema(
        {
            vol.Optional(CONF_BATTERY_ENTITY): EntitySelector(
                EntitySelectorConfig(domain="sensor")
            ),
        }
    )


def _temperature_fields(current: dict[str, Any]) -> dict:
    return {
        vol.Required(
            CONF_TEMP_TARGET, default=current.get(CONF_TEMP_TARGET, DEFAULT_TEMP_TARGET)
        ): NumberSelector(
            NumberSelectorConfig(
                min=30, max=100, step=1, unit_of_measurement="°C", mode=NumberSelectorMode.BOX
            )
        ),
        vol.Required(
            CONF_TEMP_TOLERANCE, default=current.get(CONF_TEMP_TOLERANCE, DEFAULT_TEMP_TOLERANCE)
        ): NumberSelector(
            NumberSelectorConfig(
                min=1, max=30, step=1, unit_of_measurement="°C", mode=NumberSelectorMode.BOX
            )
        ),
    }


def _step3_schema(battery_floor: float = DEFAULT_BATTERY_FLOOR) -> vol.Schema:
    return vol.Schema(
        {
            **_temperature_fields({}),
            vol.Required(CONF_BATTERY_FLOOR, default=battery_floor): NumberSelector(
                NumberSelectorConfig(
                    min=0, max=80, step=1, unit_of_measurement="%", mode=NumberSelectorMode.BOX
                )
            ),
        }
    )


def _step4_schema(
    profile: str = DEFAULT_PROFILE,
    polling_interval: int = DEFAULT_POLLING_INTERVAL,
) -> vol.Schema:
    return vol.Schema(
        {
            vol.Required(CONF_PROFILE, default=profile): _PROFILE_SELECTOR,
            vol.Required(CONF_POLLING_INTERVAL, default=polling_interval): _POLLING_SELECTOR,
        }
    )


def _options_schema(options: dict) -> vol.Schema:
    schema: dict = {
        vol.Required(
            CONF_CONTROL_MODE, default=control_mode_of(options)
        ): _CONTROL_MODE_SELECTOR,
        vol.Required(
            CONF_PROFILE, default=options.get(CONF_PROFILE, DEFAULT_PROFILE)
        ): _PROFILE_SELECTOR,
        vol.Required(
            CONF_POLLING_INTERVAL,
            default=options.get(CONF_POLLING_INTERVAL, DEFAULT_POLLING_INTERVAL),
        ): _POLLING_SELECTOR,
        **_temperature_fields(options),
        vol.Required(
            CONF_BATTERY_FLOOR,
            default=options.get(CONF_BATTERY_FLOOR, DEFAULT_BATTERY_FLOOR),
        ): NumberSelector(
            NumberSelectorConfig(
                min=0, max=80, step=1, unit_of_measurement="%", mode=NumberSelectorMode.BOX
            )
        ),
        vol.Required(
            CONF_IMPORT_TARGET, default=options.get(CONF_IMPORT_TARGET, DEFAULT_IMPORT_TARGET_W)
        ): NumberSelector(
            NumberSelectorConfig(
                min=0, max=3000, step=50, unit_of_measurement="W", mode=NumberSelectorMode.BOX
            )
        ),
        vol.Required(
            CONF_STEP_DOWN_DELAY,
            default=options.get(CONF_STEP_DOWN_DELAY, DEFAULT_STEP_DOWN_DELAY_MINUTES),
        ): NumberSelector(
            NumberSelectorConfig(
                min=0, max=120, step=1, unit_of_measurement="min", mode=NumberSelectorMode.BOX
            )
        ),
        vol.Required(
            CONF_MORNING_STEP_DOWN_DELAY,
            default=options.get(CONF_MORNING_STEP_DOWN_DELAY, DEFAULT_MORNING_STEP_DOWN_DELAY_MINUTES),
        ): NumberSelector(
            NumberSelectorConfig(
                min=0, max=240, step=1, unit_of_measurement="min", mode=NumberSelectorMode.BOX
            )
        ),
        vol.Required(
            CONF_POWER_STEPS, default=_format_power_steps(options.get(CONF_POWER_STEPS))
        ): TextSelector(TextSelectorConfig(type=TextSelectorType.TEXT)),
        vol.Required(
            CONF_TUNING_SETTLE,
            default=options.get(CONF_TUNING_SETTLE, DEFAULT_TUNING_SETTLE_MINUTES),
        ): NumberSelector(
            NumberSelectorConfig(
                min=0, max=240, step=1, unit_of_measurement="min", mode=NumberSelectorMode.BOX
            )
        ),
        # Suggested, not defaulted: a blank field is stored as None, and None as a default
        # fails the text selector, so the form could never be saved again.
        vol.Optional(
            CONF_TELEGRAM_TOKEN, description={"suggested_value": options.get(CONF_TELEGRAM_TOKEN)}
        ): TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD)),
        vol.Optional(
            CONF_TELEGRAM_CHAT_ID,
            description={"suggested_value": options.get(CONF_TELEGRAM_CHAT_ID)},
        ): TextSelector(TextSelectorConfig(type=TextSelectorType.TEXT)),
        vol.Optional(
            CONF_MOCK_SOLAR_ENABLED,
            default=options.get(CONF_MOCK_SOLAR_ENABLED, False),
        ): bool,
        vol.Optional(
            CONF_MOCK_CONSUMPTION_ENABLED,
            default=options.get(CONF_MOCK_CONSUMPTION_ENABLED, False),
        ): bool,
    }
    # EntitySelector rejects empty strings, so only include a default when an entity is already set.
    _mock_entity = options.get(CONF_MOCK_SOLAR_ENTITY) or ""
    if _mock_entity:
        schema[vol.Optional(CONF_MOCK_SOLAR_ENTITY, default=_mock_entity)] = EntitySelector(
            EntitySelectorConfig(domain="sensor")
        )
    else:
        schema[vol.Optional(CONF_MOCK_SOLAR_ENTITY)] = EntitySelector(
            EntitySelectorConfig(domain="sensor")
        )
    return vol.Schema(schema)


class SolarSmartMinerConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    def __init__(self) -> None:
        self._data: dict[str, Any] = {}
        self._options: dict[str, Any] = {}

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> SolarSmartMinerOptionsFlow:
        return SolarSmartMinerOptionsFlow(config_entry)

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}

        if user_input is not None:
            solar = user_input[CONF_SOLAR_ENTITY]
            grid = (user_input.get(CONF_GRID_ENTITY) or "").strip()
            errors = _validate_entities(self.hass, user_input)

            if not errors:
                unique_id = _stable_unique_id(solar, grid)
                await self.async_set_unique_id(unique_id)
                self._abort_if_unique_id_configured()

                self._data = {
                    CONF_SOLAR_ENTITY: solar,
                    CONF_SOLAR_ENTITY_TYPE: user_input.get(
                        CONF_SOLAR_ENTITY_TYPE, SOLAR_ENTITY_TYPE_PRODUCTION
                    ),
                    CONF_GRID_ENTITY: grid or None,
                    CONF_OPENROUTER_KEY: (user_input.get(CONF_OPENROUTER_KEY) or "").strip(),
                    CONF_OPENROUTER_MODEL: user_input[CONF_OPENROUTER_MODEL],
                }
                return await self.async_step_optional_sensors()

        return self.async_show_form(
            step_id="user",
            data_schema=_step1_schema(await _async_model_options(self.hass, None)),
            errors=errors,
        )

    async def async_step_optional_sensors(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            battery = (user_input.get(CONF_BATTERY_ENTITY) or "").strip()
            self._data[CONF_BATTERY_ENTITY] = battery if battery else None
            return await self.async_step_safety_thresholds()

        return self.async_show_form(
            step_id="optional_sensors",
            data_schema=_step2_schema(),
        )

    async def async_step_safety_thresholds(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            self._options[CONF_TEMP_TARGET] = int(user_input[CONF_TEMP_TARGET])
            self._options[CONF_TEMP_TOLERANCE] = int(user_input[CONF_TEMP_TOLERANCE])
            self._options[CONF_BATTERY_FLOOR] = int(user_input[CONF_BATTERY_FLOOR])
            return await self.async_step_runtime_settings()

        return self.async_show_form(
            step_id="safety_thresholds",
            data_schema=_step3_schema(),
        )

    async def async_step_runtime_settings(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            self._options[CONF_PROFILE] = user_input[CONF_PROFILE]
            self._options[CONF_POLLING_INTERVAL] = int(user_input[CONF_POLLING_INTERVAL])
            self._options[CONF_CONTROL_MODE] = DEFAULT_CONTROL_MODE

            return self.async_create_entry(
                title="Solar Smart Miner",
                data=self._data,
                options=self._options,
            )

        return self.async_show_form(
            step_id="runtime_settings",
            data_schema=_step4_schema(),
        )


class SolarSmartMinerOptionsFlow(OptionsFlow):
    def __init__(self, config_entry: ConfigEntry) -> None:
        self._config_entry = config_entry
        # Pre-fill with current options so a step can save without dropping the others.
        self._pending_options: dict[str, Any] = dict(config_entry.options)

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        return self.async_show_menu(
            step_id="init",
            menu_options=["edit_sensors", "edit_miners", "edit_ai", "edit_settings"],
        )

    async def async_step_edit_settings(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            steps = parse_power_steps(user_input[CONF_POWER_STEPS])
            if steps is None:
                errors[CONF_POWER_STEPS] = "invalid_power_steps"
        if user_input is not None and not errors:
            self._pending_options.update(
                {
                    CONF_POWER_STEPS: steps,
                    CONF_TUNING_SETTLE: int(user_input[CONF_TUNING_SETTLE]),
                    CONF_CONTROL_MODE: user_input[CONF_CONTROL_MODE],
                    CONF_PROFILE: user_input[CONF_PROFILE],
                    CONF_POLLING_INTERVAL: int(user_input[CONF_POLLING_INTERVAL]),
                    CONF_TEMP_TARGET: int(user_input[CONF_TEMP_TARGET]),
                    CONF_TEMP_TOLERANCE: int(user_input[CONF_TEMP_TOLERANCE]),
                    CONF_BATTERY_FLOOR: int(user_input[CONF_BATTERY_FLOOR]),
                    CONF_IMPORT_TARGET: int(user_input[CONF_IMPORT_TARGET]),
                    CONF_STEP_DOWN_DELAY: int(user_input[CONF_STEP_DOWN_DELAY]),
                    CONF_MORNING_STEP_DOWN_DELAY: int(user_input[CONF_MORNING_STEP_DOWN_DELAY]),
                }
            )
            self._pending_options.pop("temp_ceiling", None)  # replaced by target + tolerance
            self._pending_options.pop("dry_run", None)  # replaced by the control mode
            token = (user_input.get(CONF_TELEGRAM_TOKEN) or "").strip()
            chat_id = (user_input.get(CONF_TELEGRAM_CHAT_ID) or "").strip()
            self._pending_options[CONF_TELEGRAM_TOKEN] = token if token else None
            self._pending_options[CONF_TELEGRAM_CHAT_ID] = chat_id if chat_id else None

            self._pending_options[CONF_MOCK_SOLAR_ENABLED] = user_input.get(
                CONF_MOCK_SOLAR_ENABLED, False
            )
            mock_entity = (user_input.get(CONF_MOCK_SOLAR_ENTITY) or "").strip()
            self._pending_options[CONF_MOCK_SOLAR_ENTITY] = mock_entity if mock_entity else None
            self._pending_options[CONF_MOCK_CONSUMPTION_ENABLED] = user_input.get(
                CONF_MOCK_CONSUMPTION_ENABLED, False
            )

            return self.async_create_entry(data=self._pending_options)

        return self.async_show_form(
            step_id="edit_settings",
            data_schema=_options_schema(
                {**self._config_entry.options, **(user_input or {})}
                if errors
                else self._config_entry.options
            ),
            errors=errors,
        )

    def _miner_choices(self) -> list[SelectOptionDict]:
        """Every miner set up in hass-miner, named as the controller names it."""
        from .coordinator import miner_display_name

        return [
            SelectOptionDict(value=miner_id, label=name)
            for miner_id, name in miner_display_name(self.hass)
        ]

    async def async_step_edit_miners(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """How a miner is stopped: a relay switch, or (left empty) its own pause switch."""
        relays: dict[str, str] = dict(self._pending_options.get(CONF_MINER_RELAYS) or {})
        choices = self._miner_choices()

        if user_input is not None:
            relay = (user_input.get(CONF_RELAY_ENTITY) or "").strip()
            if relay:
                relays[user_input[CONF_MINER]] = relay
            else:
                relays.pop(user_input[CONF_MINER], None)
            self._pending_options[CONF_MINER_RELAYS] = relays
            return self.async_create_entry(data=self._pending_options)

        names = {c["value"]: c["label"] for c in choices}
        current = (
            "\n".join(f"{names.get(mid, mid)} → {entity}" for mid, entity in relays.items())
            or "none: every miner is stopped with its own pause switch"
        )
        return self.async_show_form(
            step_id="edit_miners",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_MINER): SelectSelector(
                        SelectSelectorConfig(options=choices, mode=SelectSelectorMode.DROPDOWN)
                    ),
                    vol.Optional(CONF_RELAY_ENTITY): EntitySelector(
                        EntitySelectorConfig(domain=["switch", "input_boolean"])
                    ),
                }
            ),
            description_placeholders={"current": current},
        )

    async def async_step_edit_sensors(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Change the solar / house / battery entities chosen during setup."""
        errors: dict[str, str] = {}

        if user_input is not None:
            errors = _validate_entities(self.hass, user_input)
            if not errors:
                house = (user_input.get(CONF_GRID_ENTITY) or "").strip()
                reference = {
                    key: (user_input.get(key) or "").strip() or None
                    for key in REFERENCE_ENTITY_KEYS
                }
                self.hass.config_entries.async_update_entry(
                    self._config_entry,
                    data={
                        **self._config_entry.data,
                        CONF_SOLAR_ENTITY: user_input[CONF_SOLAR_ENTITY],
                        CONF_SOLAR_ENTITY_TYPE: user_input[CONF_SOLAR_ENTITY_TYPE],
                        CONF_GRID_ENTITY: house or None,
                        **reference,
                    },
                )
                # Options are unchanged; the data update above triggers the reload.
                return self.async_create_entry(data=self._pending_options)

        return self.async_show_form(
            step_id="edit_sensors",
            data_schema=_sensors_schema(dict(self._config_entry.data)),
            errors=errors,
        )

    async def async_step_edit_ai(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """OpenRouter key (hidden), model and how often the AI is asked."""
        data = dict(self._config_entry.data)

        if user_input is not None:
            self._pending_options.update(
                {
                    CONF_AI_ENABLED: user_input[CONF_AI_ENABLED],
                    CONF_AI_INTERVAL: int(user_input[CONF_AI_INTERVAL]),
                }
            )
            # One update for both so the integration reloads once.
            self.hass.config_entries.async_update_entry(
                self._config_entry,
                data={
                    **data,
                    CONF_OPENROUTER_KEY: (user_input.get(CONF_OPENROUTER_KEY) or "").strip(),
                    CONF_OPENROUTER_MODEL: user_input[CONF_OPENROUTER_MODEL],
                },
                options=self._pending_options,
            )
            return self.async_create_entry(data=self._pending_options)

        model_options = await _async_model_options(self.hass, data.get(CONF_OPENROUTER_MODEL))
        return self.async_show_form(
            step_id="edit_ai",
            data_schema=_ai_schema(data, self._config_entry.options, model_options),
        )
