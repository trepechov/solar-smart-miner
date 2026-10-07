"""Select platform for Solar Smart Miner — active profile control."""
from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .config_flow import CONF_PROFILE
from .const import (
    CONF_CONTROL_MODE,
    CONTROL_MODE_LABELS,
    DEFAULT_PROFILE,
    DOMAIN,
    PROFILES,
    control_mode_of,
)

_LABEL_BY_NAME = {p["name"]: p["display_name"] for p in PROFILES}
_NAME_BY_LABEL = {label: name for name, label in _LABEL_BY_NAME.items()}
_MODE_BY_LABEL = {label: mode for mode, label in CONTROL_MODE_LABELS.items()}


def _hub_device_info(entry: ConfigEntry) -> DeviceInfo:
    return DeviceInfo(
        identifiers={(DOMAIN, entry.entry_id)},
        name=entry.title,
    )


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    async_add_entities([ProfileSelect(entry), ControlModeSelect(entry)])


class ProfileSelect(SelectEntity):
    _attr_has_entity_name = True
    _attr_name = "Profile"
    _attr_icon = "mdi:tune-variant"
    _attr_options = list(_LABEL_BY_NAME.values())

    def __init__(self, entry: ConfigEntry) -> None:
        self._entry = entry
        self._attr_unique_id = f"{entry.unique_id or entry.entry_id}_profile"
        self._attr_device_info = _hub_device_info(entry)

    @property
    def current_option(self) -> str | None:
        return _LABEL_BY_NAME.get(self._entry.options.get(CONF_PROFILE, DEFAULT_PROFILE))

    async def async_select_option(self, option: str) -> None:
        # Saving the option reloads the entry, so the next cycle uses the new profile.
        self.hass.config_entries.async_update_entry(
            self._entry,
            options={**self._entry.options, CONF_PROFILE: _NAME_BY_LABEL[option]},
        )


class ControlModeSelect(SelectEntity):
    """How proposals reach the miners: Manual (on a button press) or Automatic (every cycle)."""

    _attr_has_entity_name = True
    _attr_name = "Control mode"
    _attr_icon = "mdi:hand-back-right-outline"
    _attr_options = list(CONTROL_MODE_LABELS.values())

    def __init__(self, entry: ConfigEntry) -> None:
        self._entry = entry
        self._attr_unique_id = f"{entry.unique_id or entry.entry_id}_control_mode"
        self._attr_device_info = _hub_device_info(entry)

    @property
    def current_option(self) -> str | None:
        return CONTROL_MODE_LABELS[control_mode_of(self._entry.options)]

    async def async_select_option(self, option: str) -> None:
        self.hass.config_entries.async_update_entry(
            self._entry,
            options={**self._entry.options, CONF_CONTROL_MODE: _MODE_BY_LABEL[option]},
        )
