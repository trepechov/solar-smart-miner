"""Config flow for Solar Smart Miner.

Note: The plan specified ConfigSubentryFlow for miner management (introduced in HA 2025.2).
The installed pytest-homeassistant-custom-component pins HA to 2025.1.4, so miners are stored
as a list in config entry data instead. The production integration can be migrated to subentries
once the minimum HA version is bumped to 2025.2+.
"""
from __future__ import annotations

import hashlib
from ipaddress import AddressValueError, IPv4Address
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.selector import (
    EntitySelector,
    EntitySelectorConfig,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .const import (
    CONF_MOCK_SOLAR_ENABLED,
    CONF_MOCK_SOLAR_ENTITY,
    DEFAULT_BATTERY_FLOOR,
    DEFAULT_POLLING_INTERVAL,
    DEFAULT_PROFILE,
    DEFAULT_TEMP_CEILING,
    DOMAIN,
    PROFILE_NAMES,
)

CONF_SOLAR_ENTITY = "solar_production_entity"
CONF_GRID_ENTITY = "grid_consumption_entity"
CONF_OPENROUTER_KEY = "openrouter_api_key"
CONF_OPENROUTER_MODEL = "openrouter_model"
CONF_BATTERY_ENTITY = "battery_soc_entity"
CONF_TEMP_CEILING = "temp_ceiling"
CONF_BATTERY_FLOOR = "battery_floor"
CONF_PROFILE = "profile"
CONF_POLLING_INTERVAL = "polling_interval"
CONF_DRY_RUN = "dry_run"
CONF_TELEGRAM_TOKEN = "telegram_bot_token"
CONF_TELEGRAM_CHAT_ID = "telegram_chat_id"
CONF_MINERS = "miners"

CONF_MINER_NAME = "miner_name"
CONF_MINER_IP = "miner_ip"

DEFAULT_OPENROUTER_MODEL = "anthropic/claude-haiku-4-5"


def _stable_unique_id(solar_entity_id: str, grid_entity_id: str) -> str:
    return hashlib.sha256(f"{solar_entity_id}:{grid_entity_id}".encode()).hexdigest()[:16]


def _entity_exists(hass: HomeAssistant, entity_id: str) -> bool:
    return hass.states.get(entity_id) is not None


def _validate_ip(ip_str: str) -> bool:
    try:
        IPv4Address(ip_str)
        return True
    except (AddressValueError, ValueError):
        return False


def _step1_schema() -> vol.Schema:
    return vol.Schema(
        {
            vol.Required(CONF_SOLAR_ENTITY): EntitySelector(
                EntitySelectorConfig(domain="sensor")
            ),
            vol.Required(CONF_GRID_ENTITY): EntitySelector(
                EntitySelectorConfig(domain="sensor")
            ),
            vol.Required(CONF_OPENROUTER_KEY): TextSelector(
                TextSelectorConfig(type=TextSelectorType.PASSWORD)
            ),
            vol.Required(CONF_OPENROUTER_MODEL, default=DEFAULT_OPENROUTER_MODEL): TextSelector(
                TextSelectorConfig(type=TextSelectorType.TEXT)
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


def _step3_schema(
    temp_ceiling: float = DEFAULT_TEMP_CEILING,
    battery_floor: float = DEFAULT_BATTERY_FLOOR,
) -> vol.Schema:
    return vol.Schema(
        {
            vol.Required(CONF_TEMP_CEILING, default=temp_ceiling): NumberSelector(
                NumberSelectorConfig(
                    min=40, max=120, step=1, unit_of_measurement="°C", mode=NumberSelectorMode.BOX
                )
            ),
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
            vol.Required(CONF_PROFILE, default=profile): SelectSelector(
                SelectSelectorConfig(options=PROFILE_NAMES, mode=SelectSelectorMode.DROPDOWN)
            ),
            vol.Required(CONF_POLLING_INTERVAL, default=polling_interval): NumberSelector(
                NumberSelectorConfig(
                    min=60, max=3600, step=60, unit_of_measurement="s", mode=NumberSelectorMode.BOX
                )
            ),
        }
    )


def _miner_schema() -> vol.Schema:
    return vol.Schema(
        {
            vol.Required(CONF_MINER_NAME): TextSelector(
                TextSelectorConfig(type=TextSelectorType.TEXT)
            ),
            vol.Required(CONF_MINER_IP): TextSelector(
                TextSelectorConfig(type=TextSelectorType.TEXT)
            ),
        }
    )


def _options_schema(options: dict) -> vol.Schema:
    schema: dict = {
        vol.Required(CONF_DRY_RUN, default=options.get(CONF_DRY_RUN, False)): bool,
        vol.Required(
            CONF_PROFILE, default=options.get(CONF_PROFILE, DEFAULT_PROFILE)
        ): SelectSelector(
            SelectSelectorConfig(options=PROFILE_NAMES, mode=SelectSelectorMode.DROPDOWN)
        ),
        vol.Required(
            CONF_POLLING_INTERVAL,
            default=options.get(CONF_POLLING_INTERVAL, DEFAULT_POLLING_INTERVAL),
        ): NumberSelector(
            NumberSelectorConfig(
                min=60,
                max=3600,
                step=60,
                unit_of_measurement="s",
                mode=NumberSelectorMode.BOX,
            )
        ),
        vol.Required(
            CONF_TEMP_CEILING,
            default=options.get(CONF_TEMP_CEILING, DEFAULT_TEMP_CEILING),
        ): NumberSelector(
            NumberSelectorConfig(
                min=40,
                max=120,
                step=1,
                unit_of_measurement="°C",
                mode=NumberSelectorMode.BOX,
            )
        ),
        vol.Required(
            CONF_BATTERY_FLOOR,
            default=options.get(CONF_BATTERY_FLOOR, DEFAULT_BATTERY_FLOOR),
        ): NumberSelector(
            NumberSelectorConfig(
                min=0, max=80, step=1, unit_of_measurement="%", mode=NumberSelectorMode.BOX
            )
        ),
        vol.Optional(
            CONF_TELEGRAM_TOKEN, default=options.get(CONF_TELEGRAM_TOKEN, "")
        ): TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD)),
        vol.Optional(
            CONF_TELEGRAM_CHAT_ID, default=options.get(CONF_TELEGRAM_CHAT_ID, "")
        ): TextSelector(TextSelectorConfig(type=TextSelectorType.TEXT)),
        vol.Optional(
            CONF_MOCK_SOLAR_ENABLED,
            default=options.get(CONF_MOCK_SOLAR_ENABLED, False),
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
            grid = user_input[CONF_GRID_ENTITY]

            if not _entity_exists(self.hass, solar):
                errors[CONF_SOLAR_ENTITY] = "solar_entity_not_found"
            elif not _entity_exists(self.hass, grid):
                errors[CONF_GRID_ENTITY] = "grid_entity_not_found"

            if not errors:
                unique_id = _stable_unique_id(solar, grid)
                await self.async_set_unique_id(unique_id)
                self._abort_if_unique_id_configured()

                self._data = {
                    CONF_SOLAR_ENTITY: solar,
                    CONF_GRID_ENTITY: grid,
                    CONF_OPENROUTER_KEY: user_input[CONF_OPENROUTER_KEY],
                    CONF_OPENROUTER_MODEL: user_input[CONF_OPENROUTER_MODEL],
                    CONF_MINERS: [],
                }
                return await self.async_step_optional_sensors()

        return self.async_show_form(
            step_id="user",
            data_schema=_step1_schema(),
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
            self._options[CONF_TEMP_CEILING] = int(user_input[CONF_TEMP_CEILING])
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
            self._options[CONF_DRY_RUN] = False

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
        self._pending_options: dict[str, Any] = {}

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            self._pending_options = {
                CONF_DRY_RUN: user_input[CONF_DRY_RUN],
                CONF_PROFILE: user_input[CONF_PROFILE],
                CONF_POLLING_INTERVAL: int(user_input[CONF_POLLING_INTERVAL]),
                CONF_TEMP_CEILING: int(user_input[CONF_TEMP_CEILING]),
                CONF_BATTERY_FLOOR: int(user_input[CONF_BATTERY_FLOOR]),
            }
            token = user_input.get(CONF_TELEGRAM_TOKEN, "").strip()
            chat_id = user_input.get(CONF_TELEGRAM_CHAT_ID, "").strip()
            self._pending_options[CONF_TELEGRAM_TOKEN] = token if token else None
            self._pending_options[CONF_TELEGRAM_CHAT_ID] = chat_id if chat_id else None

            self._pending_options[CONF_MOCK_SOLAR_ENABLED] = user_input.get(
                CONF_MOCK_SOLAR_ENABLED, False
            )
            mock_entity = (user_input.get(CONF_MOCK_SOLAR_ENTITY) or "").strip()
            self._pending_options[CONF_MOCK_SOLAR_ENTITY] = mock_entity if mock_entity else None

            return await self.async_step_add_miner()

        return self.async_show_form(
            step_id="init",
            data_schema=_options_schema(self._config_entry.options),
        )

    async def async_step_add_miner(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Optional step to add a miner. Submitting empty name skips and saves."""
        errors: dict[str, str] = {}

        if user_input is not None:
            name = user_input.get(CONF_MINER_NAME, "").strip()
            ip = user_input.get(CONF_MINER_IP, "").strip()

            if not name and not ip:
                return self.async_create_entry(data=self._pending_options)

            if not _validate_ip(ip):
                errors[CONF_MINER_IP] = "invalid_ip"

            if not errors:
                miners: list[dict[str, str]] = list(
                    self._config_entry.data.get(CONF_MINERS, [])
                )
                miners.append({CONF_MINER_NAME: name, CONF_MINER_IP: ip})
                self.hass.config_entries.async_update_entry(
                    self._config_entry,
                    data={**self._config_entry.data, CONF_MINERS: miners},
                )
                return self.async_create_entry(data=self._pending_options)

        return self.async_show_form(
            step_id="add_miner",
            data_schema=_miner_schema(),
            errors=errors,
            description_placeholders={
                "tip": "Leave both fields blank to skip and save without adding a miner."
            },
        )
