"""Select platform for Solar Smart Miner: the control mode.

The profile select (ProfileSelect) was retired in 0.8.0 with the one-profile cut; it returns
with the battery profiles (see PROFILES in const.py).
"""
from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    CONF_CONTROL_MODE,
    CONTROL_MODE_LABELS,
    DOMAIN,
    control_mode_of,
)

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
    async_add_entities([ControlModeSelect(entry)])


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
