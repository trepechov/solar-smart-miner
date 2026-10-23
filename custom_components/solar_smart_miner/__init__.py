from __future__ import annotations

import logging

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv, entity_registry
from homeassistant.helpers.typing import ConfigType

from .const import (  # noqa: F401
    CONF_CONTROL_MODE,
    DOMAIN,
    LEGACY_CONTROL_MODE_PREVIEW,
    DEFAULT_PROFILE,
    LEGACY_IMPORT_TARGET,
    LEGACY_PROFILES,
    RETIRED_OPTIONS,
    control_mode_of,
)
from .config_flow import CONF_PROFILE, CONF_UNMANAGED_MINERS
from .coordinator import SolarMinerCoordinator

_LOGGER = logging.getLogger(__name__)

# number/switch added in U7.
PLATFORMS: list[str] = ["button", "select", "sensor"]

# (domain, unique-id suffix) of entities an older version created and this one no longer does.
# v0.6.0 had a proposal sensor and an Apply button per miner; the proposal is farm-level now.
# 0.8.0 has one profile, so the profile select has nothing to choose (it returns with the
# battery profiles; see PROFILES in const.py).
RETIRED_ENTITIES: tuple[tuple[str, str], ...] = (
    ("button", "_apply"),
    ("sensor", "_proposed_action"),
    ("select", "_profile"),
)


CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

SERVICE_SEND_DAY_REPORT = "send_day_report"
SEND_DAY_REPORT_SCHEMA = vol.Schema({
    vol.Optional("date"): cv.date,
    vol.Optional("test", default=False): cv.boolean,
    vol.Optional("config_entry_id"): cv.string,
})


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """The actions, registered once for every farm (config entry)."""

    async def send_day_report(call: ServiceCall) -> None:
        entry_id = call.data.get("config_entry_id")
        loaded = [
            e for e in hass.config_entries.async_entries(DOMAIN)
            if e.state is ConfigEntryState.LOADED and (entry_id is None or e.entry_id == entry_id)
        ]
        if not loaded:
            raise ServiceValidationError("No Solar Smart Miner farm is loaded" + (f" with id {entry_id}." if entry_id else "."))
        if len(loaded) > 1:
            raise ServiceValidationError("There are several farms: say which one with config_entry_id.")
        day = call.data.get("date")
        await loaded[0].runtime_data.async_send_day_report(
            day.isoformat() if day else None, test=call.data["test"]
        )

    hass.services.async_register(DOMAIN, SERVICE_SEND_DAY_REPORT, send_day_report, schema=SEND_DAY_REPORT_SCHEMA)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    # First, before the reload listener exists: rewriting the options later would reload at once.
    _migrate_control_mode(hass, entry)
    _drop_legacy_import_target(hass, entry)
    _drop_retired_options(hass, entry)
    _migrate_profile(hass, entry)
    _remove_retired_entities(hass, entry)
    _remove_unmanaged_miner_entities(hass, entry)
    coordinator = SolarMinerCoordinator(hass, entry)
    await coordinator.ai_log.async_load_history()
    await coordinator.action_log.async_load_history()
    coordinator.seed_activity()
    await coordinator.async_seed_transition()
    await coordinator.async_load_day_report()
    await coordinator.async_load_knowledge()
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_reload_on_change))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    await entry.runtime_data.async_save_day_report()
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def _async_reload_on_change(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Apply any settings change (entities, polling interval, profile) by reloading."""
    await hass.config_entries.async_reload(entry.entry_id)


def _remove_retired_entities(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Drop registry entries an update left behind, so they don't linger as unavailable."""
    er = entity_registry.async_get(hass)
    for entity in entity_registry.async_entries_for_config_entry(er, entry.entry_id):
        if any(
            entity.domain == domain and entity.unique_id.endswith(suffix)
            for domain, suffix in RETIRED_ENTITIES
        ):
            er.async_remove(entity.entity_id)


def _remove_unmanaged_miner_entities(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """A miner left out under Managed miners loses its sensors rather than keeping them
    unavailable; ticked again, they come back with the same ids (and so their history)."""
    from .sensor import miner_sensor_unique_ids

    er = entity_registry.async_get(hass)
    for miner_id in entry.options.get(CONF_UNMANAGED_MINERS) or []:
        for unique_id in miner_sensor_unique_ids(entry, miner_id):
            if entity_id := er.async_get_entity_id("sensor", DOMAIN, unique_id):
                er.async_remove(entity_id)


def _migrate_control_mode(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Preview mode is gone (0.7.2): a stored "preview" becomes Manual, which still applies
    nothing without a press. Done once, so Configure shows the mode in use."""
    if entry.options.get(CONF_CONTROL_MODE) != LEGACY_CONTROL_MODE_PREVIEW:
        return
    hass.config_entries.async_update_entry(
        entry, options={**entry.options, CONF_CONTROL_MODE: control_mode_of({})}
    )
    _LOGGER.info("Control mode Preview no longer exists; switched to Manual")


def _drop_legacy_import_target(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """The old single import target (0.7.4 and before) must not set the import range: drop
    it, so the minimum and maximum come from their own settings (or the defaults)."""
    if LEGACY_IMPORT_TARGET not in entry.options:
        return
    options = {k: v for k, v in entry.options.items() if k != LEGACY_IMPORT_TARGET}
    hass.config_entries.async_update_entry(entry, options=options)
    _LOGGER.info("Dropped the old grid import target; the import range settings apply")


def _drop_retired_options(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Settings that no longer exist (RETIRED_OPTIONS) are removed from the stored options."""
    if not any(key in entry.options for key in RETIRED_OPTIONS):
        return
    options = {k: v for k, v in entry.options.items() if k not in RETIRED_OPTIONS}
    hass.config_entries.async_update_entry(entry, options=options)
    _LOGGER.info("Dropped settings that no longer exist: %s", ", ".join(RETIRED_OPTIONS))


def _migrate_profile(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """One profile since 0.8.0: a stored older one becomes Solar-follow, once. Solar-max was
    its forerunner (same rules); the others are dropped (owner, 2026-10-09)."""
    stored = entry.options.get(CONF_PROFILE)
    if stored not in LEGACY_PROFILES:
        return
    hass.config_entries.async_update_entry(
        entry, options={**entry.options, CONF_PROFILE: DEFAULT_PROFILE}
    )
    _LOGGER.info("Profile %s no longer exists; switched to Solar-follow", stored)
