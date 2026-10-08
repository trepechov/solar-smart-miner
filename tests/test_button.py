"""Tests for button platform — AddToDashboardButton."""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.solar_smart_miner.button import AddToDashboardButton, async_setup_entry
from custom_components.solar_smart_miner.config_flow import (
    CONF_GRID_ENTITY,
    CONF_POLLING_INTERVAL,
    CONF_SOLAR_ENTITY,
)
from custom_components.solar_smart_miner.const import (
    CONF_MOCK_CONSUMPTION_ENABLED,
    CONF_MOCK_SOLAR_ENABLED,
    DEFAULT_POLLING_INTERVAL,
    DOMAIN,
)

SOLAR_ENTITY = "sensor.solar_power"
GRID_ENTITY = "sensor.grid_consumption"

PN_MODULE = "custom_components.solar_smart_miner.button.pn_create"


def _make_entry(hass):
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="My Miner Hub",
        data={
            CONF_SOLAR_ENTITY: SOLAR_ENTITY,
            CONF_GRID_ENTITY: GRID_ENTITY,
        },
        options={
            CONF_POLLING_INTERVAL: DEFAULT_POLLING_INTERVAL,
            CONF_MOCK_SOLAR_ENABLED: False,
            CONF_MOCK_CONSUMPTION_ENABLED: False,
        },
        version=1,
    )
    entry.add_to_hass(hass)
    return entry


# ---------------------------------------------------------------------------
# Entity construction
# ---------------------------------------------------------------------------

async def test_button_name(hass) -> None:
    entry = _make_entry(hass)
    button = AddToDashboardButton(entry)
    assert button.name == "Add to dashboard"


async def test_button_unique_id(hass) -> None:
    entry = _make_entry(hass)
    button = AddToDashboardButton(entry)
    assert button.unique_id == f"{entry.entry_id}_add_to_dashboard"


async def test_button_device_info_uses_hub_identifier(hass) -> None:
    entry = _make_entry(hass)
    button = AddToDashboardButton(entry)
    assert (DOMAIN, entry.entry_id) in button.device_info["identifiers"]


async def test_button_icon(hass) -> None:
    entry = _make_entry(hass)
    button = AddToDashboardButton(entry)
    assert button.icon == "mdi:view-dashboard-variant"


# ---------------------------------------------------------------------------
# async_setup_entry registers the button
# ---------------------------------------------------------------------------

async def test_async_setup_entry_registers_one_button(hass) -> None:
    hass.states.async_set(SOLAR_ENTITY, "2000")
    hass.states.async_set(GRID_ENTITY, "1500")
    entry = _make_entry(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    button_states = [s for s in hass.states.async_all() if "add_to_dashboard" in s.entity_id]
    assert len(button_states) == 1


# ---------------------------------------------------------------------------
# async_press — no sensor entities registered yet
# ---------------------------------------------------------------------------

async def test_press_notifies_no_entities_when_hub_device_missing(hass) -> None:
    entry = _make_entry(hass)
    button = AddToDashboardButton(entry)
    button.hass = hass

    with patch(PN_MODULE) as mock_pn:
        await button.async_press()

    mock_pn.assert_not_called()


async def test_press_notifies_no_entities_when_no_sensors_registered(hass) -> None:
    """Hub device exists but no sensor entities have been registered yet."""
    from homeassistant.helpers import device_registry as dr

    entry = _make_entry(hass)
    # Create the hub device without any entities
    device_reg = dr.async_get(hass)
    device_reg.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, entry.entry_id)},
        name=entry.title,
    )

    button = AddToDashboardButton(entry)
    button.hass = hass

    with patch(PN_MODULE) as mock_pn:
        await button.async_press()

    mock_pn.assert_called_once()
    call_args = mock_pn.call_args
    assert "No sensor entities found" in call_args.args[1]
    assert call_args.kwargs["notification_id"] == f"{DOMAIN}_add_to_dashboard"


# ---------------------------------------------------------------------------
# async_press — sensor entities present
# ---------------------------------------------------------------------------

async def _register_hub_with_sensors(hass, entry, sensor_entity_ids: list[str]) -> None:
    """Create the hub device and register sensor entities for it."""
    from homeassistant.helpers import device_registry as dr, entity_registry as er

    device_reg = dr.async_get(hass)
    entity_reg = er.async_get(hass)

    hub = device_reg.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, entry.entry_id)},
        name=entry.title,
    )
    for eid in sensor_entity_ids:
        platform_part = eid.split(".")[1]
        entity_reg.async_get_or_create(
            "sensor",
            DOMAIN,
            platform_part,
            config_entry=entry,
            device_id=hub.id,
            suggested_object_id=platform_part,
        )


async def test_press_notification_contains_entity_ids(hass) -> None:
    entry = _make_entry(hass)
    sensor_ids = [
        "sensor.solar_production",
        "sensor.total_miner_consumption",
    ]
    await _register_hub_with_sensors(hass, entry, sensor_ids)

    button = AddToDashboardButton(entry)
    button.hass = hass

    with patch(PN_MODULE) as mock_pn:
        await button.async_press()

    mock_pn.assert_called_once()
    message: str = mock_pn.call_args.args[1]
    assert "sensor.solar_production" in message
    assert "sensor.total_miner_consumption" in message


async def test_press_notification_contains_title(hass) -> None:
    entry = _make_entry(hass)
    await _register_hub_with_sensors(hass, entry, ["sensor.solar_production"])

    button = AddToDashboardButton(entry)
    button.hass = hass

    with patch(PN_MODULE) as mock_pn:
        await button.async_press()

    message: str = mock_pn.call_args.args[1]
    assert "My Miner Hub" in message


async def test_press_notification_is_valid_yaml_card(hass) -> None:
    entry = _make_entry(hass)
    await _register_hub_with_sensors(hass, entry, ["sensor.solar_production"])

    button = AddToDashboardButton(entry)
    button.hass = hass

    with patch(PN_MODULE) as mock_pn:
        await button.async_press()

    message: str = mock_pn.call_args.args[1]
    assert "type: entities" in message
    assert "```yaml" in message


async def test_press_uses_stable_notification_id(hass) -> None:
    entry = _make_entry(hass)
    await _register_hub_with_sensors(hass, entry, ["sensor.solar_production"])

    button = AddToDashboardButton(entry)
    button.hass = hass

    with patch(PN_MODULE) as mock_pn:
        await button.async_press()

    assert mock_pn.call_args.kwargs["notification_id"] == f"{DOMAIN}_add_to_dashboard"


async def test_press_excludes_non_sensor_entities(hass) -> None:
    """The button itself (domain=button) must not appear in the card YAML."""
    from homeassistant.helpers import device_registry as dr, entity_registry as er

    entry = _make_entry(hass)
    device_reg = dr.async_get(hass)
    entity_reg = er.async_get(hass)

    hub = device_reg.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, entry.entry_id)},
        name=entry.title,
    )
    entity_reg.async_get_or_create(
        "sensor", DOMAIN, "solar_production",
        config_entry=entry, device_id=hub.id,
        suggested_object_id="solar_production",
    )
    # Register the button entity itself
    entity_reg.async_get_or_create(
        "button", DOMAIN, "add_to_dashboard",
        config_entry=entry, device_id=hub.id,
        suggested_object_id="add_to_dashboard",
    )

    button = AddToDashboardButton(entry)
    button.hass = hass

    with patch(PN_MODULE) as mock_pn:
        await button.async_press()

    message: str = mock_pn.call_args.args[1]
    assert "button." not in message
    assert "sensor.solar_production" in message


async def test_press_includes_the_selects_and_decision_log_card(hass) -> None:
    from homeassistant.helpers import device_registry as dr, entity_registry as er
    import yaml

    entry = _make_entry(hass)
    hub = dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, entry.entry_id)},
        name=entry.title,
    )
    entity_reg = er.async_get(hass)
    for domain, uid in (
        ("sensor", "x_solar_production"),
        ("select", "x_control_mode"),
        ("sensor", "x_decision_log"),
    ):
        entity_reg.async_get_or_create(
            domain, DOMAIN, uid, config_entry=entry, device_id=hub.id,
            suggested_object_id=uid,
        )

    button = AddToDashboardButton(entry)
    button.hass = hass
    with patch(PN_MODULE) as mock_pn:
        await button.async_press()

    message: str = mock_pn.call_args.args[1]
    card = yaml.safe_load(message.split("```yaml\n")[1].split("\n```")[0])
    assert card["type"] == "vertical-stack"
    entities_card, log_card = card["cards"]
    assert entities_card["entities"] == ["select.x_control_mode", "sensor.x_solar_production"]
    assert log_card["type"] == "markdown"
    assert "sensor.x_decision_log" in log_card["content"]


# ---------------------------------------------------------------------------
# Ask AI now
# ---------------------------------------------------------------------------


async def test_ask_ai_button_asks_the_coordinator(hass) -> None:
    from unittest.mock import MagicMock

    from custom_components.solar_smart_miner.button import AskAiButton

    entry = _make_entry(hass)
    entry.runtime_data = MagicMock()
    button = AskAiButton(entry)
    button.hass = hass

    await button.async_press()

    entry.runtime_data.async_ask_ai_now.assert_called_once_with()


async def test_ask_ai_button_registered_with_hub_device(hass) -> None:
    hass.states.async_set(SOLAR_ENTITY, "2000")
    hass.states.async_set(GRID_ENTITY, "1500")
    entry = _make_entry(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    states = [s for s in hass.states.async_all("button") if "ask_ai" in s.entity_id]
    assert len(states) == 1


async def test_dashboard_card_shows_ai_advice_in_markdown_not_entity_list(hass) -> None:
    from homeassistant.components import persistent_notification as pn

    hass.states.async_set(SOLAR_ENTITY, "2000")
    hass.states.async_set(GRID_ENTITY, "1500")
    entry = _make_entry(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    button_id = next(s.entity_id for s in hass.states.async_all("button") if "add_to_dashboard" in s.entity_id)

    await hass.services.async_call(
        "button", "press", {"entity_id": button_id}, blocking=True
    )

    message = pn._async_get_or_create_notifications(hass)[f"{DOMAIN}_add_to_dashboard"]["message"]
    ai_entity = next(s.entity_id for s in hass.states.async_all("sensor") if "ai_advice" in s.entity_id)
    assert f"state_attr('{ai_entity}', 'response')" in message
    assert f"  - {ai_entity}" not in message


async def test_dashboard_card_has_an_ai_log_of_recent_answers_with_actions(hass) -> None:
    from homeassistant.components import persistent_notification as pn

    hass.states.async_set(SOLAR_ENTITY, "2000")
    hass.states.async_set(GRID_ENTITY, "1500")
    entry = _make_entry(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    button_id = next(s.entity_id for s in hass.states.async_all("button") if "add_to_dashboard" in s.entity_id)

    await hass.services.async_call("button", "press", {"entity_id": button_id}, blocking=True)

    message = pn._async_get_or_create_notifications(hass)[f"{DOMAIN}_add_to_dashboard"]["message"]
    ai_entity = next(s.entity_id for s in hass.states.async_all("sensor") if "ai_advice" in s.entity_id)
    assert "AI log (latest first)" in message
    assert f"state_attr('{ai_entity}', 'history')" in message
    assert "{{ a.miner }} {{ a.action }} ({{ a.reason }})" in message


async def test_dashboard_card_renders_the_ai_log(hass) -> None:
    """The markdown card is a Jinja template: render it for real, not just grep it."""
    import yaml
    from homeassistant.helpers.template import Template

    from custom_components.solar_smart_miner.button import _decision_log_card

    hass.states.async_set("sensor.log", "Solar-max", {"trace": ["READ"], "history": []})
    hass.states.async_set(
        "sensor.ai",
        "Sun is setting",
        {
            "response": "Sun is setting | Brod1: reduce (not_enough_energy)",
            "history": [
                {
                    "time": "18:30:15",
                    "summary": "Sun is setting",
                    "error": None,
                    "actions": [{"miner": "Brod1", "action": "reduce", "reason": "not_enough_energy"}],
                },
                {"time": "18:29:15", "summary": "", "error": "Rate limited by OpenRouter", "actions": []},
            ],
        },
    )
    card = yaml.safe_load("\n".join(_decision_log_card("sensor.log", "sensor.ai")))[0]

    text = Template(card["content"], hass).async_render()

    assert "AI log (latest first)" in text
    assert "`18:30:15` Sun is setting · Brod1 reduce (not_enough_energy)" in text
    assert "`18:29:15` Rate limited by OpenRouter" in text


# ---------------------------------------------------------------------------
# Proposed actions section
# ---------------------------------------------------------------------------


async def _generated_card(hass, add_hass_miner, *, mode: str | None, miner_name: str = "Brod1") -> dict:
    import yaml
    from homeassistant.components import persistent_notification as pn

    from custom_components.solar_smart_miner.const import CONF_CONTROL_MODE

    add_hass_miner("192.168.1.10", name=miner_name, limit="1100", power="1100", temperature="55")
    hass.states.async_set(SOLAR_ENTITY, "2000")
    hass.states.async_set(GRID_ENTITY, "1500")
    entry = _make_entry(hass)
    if mode is not None:
        hass.config_entries.async_update_entry(entry, options={**entry.options, CONF_CONTROL_MODE: mode})
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    button_id = next(s.entity_id for s in hass.states.async_all("button") if "add_to_dashboard" in s.entity_id)

    await hass.services.async_call("button", "press", {"entity_id": button_id}, blocking=True)

    message = pn._async_get_or_create_notifications(hass)[f"{DOMAIN}_add_to_dashboard"]["message"]
    return yaml.safe_load(message.split("```yaml\n")[1].split("\n```")[0])


def _entity_id_ending(hass, domain: str, suffix: str) -> str:
    return next(s.entity_id for s in hass.states.async_all(domain) if s.entity_id.endswith(suffix))


async def test_card_is_an_activity_log_with_one_confirmed_apply_button_under_it(hass, add_hass_miner) -> None:
    card = await _generated_card(hass, add_hass_miner, mode="manual")

    assert [c["type"] for c in card["cards"]] == ["entities", "markdown", "button", "markdown"]
    main, activity, button, _ = card["cards"]
    activity_id = _entity_id_ending(hass, "sensor", "activity")
    apply_id = _entity_id_ending(hass, "button", "apply_proposal")
    assert activity["title"] == "Activity log"
    assert f"state_attr('{activity_id}', 'proposal')" in activity["content"]
    assert f"state_attr('{activity_id}', 'feed')" in activity["content"]
    assert button["entity"] == apply_id and button["show_state"] is False
    assert button["name"] == "Apply proposal"
    assert button["tap_action"] == {
        "action": "perform-action",
        "perform_action": "button.press",
        "target": {"entity_id": apply_id},
        "confirmation": {"text": "Apply the proposal shown above?"},
    }
    # Nothing the log shows repeats in the main list; there are no per-miner buttons.
    assert activity_id not in main["entities"]
    assert not any("brod1_apply" in str(c) for c in card["cards"])


async def test_activity_log_renders_the_proposal_and_the_feed(hass, add_hass_miner) -> None:
    """The markdown card is a Jinja template: render it for real against a feed."""
    from homeassistant.helpers.template import Template

    card = await _generated_card(hass, add_hass_miner, mode="manual")
    activity = card["cards"][1]
    activity_id = _entity_id_ending(hass, "sensor", "activity")
    hass.states.async_set(
        activity_id,
        "x",
        {
            "proposal": "Brod2 1,300 W (from 1,500 W) \u00b7 Brod1 1,500 W (from 900 W)",
            "feed": [
                {"kind": "applied", "time": "11:32:10", "miner": "Brod2", "plan": "1,300 W",
                 "result": "ok", "reason": "the miner shows the new value"},
                {"kind": "applied", "time": "11:31:02", "miner": "Brod1", "plan": "1,500 W",
                 "result": "failed", "reason": "never reached 1500"},
                {"kind": "proposal", "time": "11:30:00", "current": True,
                 "plan": "Brod2 1,300 W (from 1,500 W) \u00b7 Brod1 1,500 W (from 900 W)"},
                {"kind": "proposal", "time": "11:00:00", "current": False, "plan": "no action"},
            ],
        },
    )

    text = Template(activity["content"], hass).async_render()

    assert "**Proposal now:** Brod2 1,300 W (from 1,500 W) \u00b7 Brod1 1,500 W (from 900 W)" in text
    assert "`11:32:10` **Brod2** applied 1,300 W: **ok**" in text
    assert "`11:31:02` **Brod1** applied 1,500 W: **failed** (never reached 1500)" in text
    assert "`11:30:00` Proposal: Brod2 1,300 W (from 1,500 W) \u00b7 Brod1 1,500 W (from 900 W) **◀ current**" in text
    assert "`11:00:00` Proposal: no action\n" in text + "\n"
    assert text.index("11:32:10") < text.index("11:00:00")  # newest first


async def test_decision_log_title_follows_the_control_mode(hass, add_hass_miner) -> None:
    manual = await _generated_card(hass, add_hass_miner, mode="manual")

    assert manual["cards"][-1]["title"] == "Decision log (manual apply)"


async def test_decision_log_title_says_automatic_in_automatic_mode(hass, add_hass_miner) -> None:
    auto = await _generated_card(hass, add_hass_miner, mode="auto")

    assert auto["cards"][-1]["title"] == "Decision log (applied automatically)"


async def test_decision_log_title_says_manual_by_default(hass, add_hass_miner) -> None:
    default = await _generated_card(hass, add_hass_miner, mode=None)

    assert default["cards"][-1]["title"] == "Decision log (manual apply)"


async def test_card_without_apply_entities_has_no_proposed_section(hass) -> None:
    from homeassistant.helpers import device_registry as dr, entity_registry as er
    import yaml

    entry = _make_entry(hass)
    hub = dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id, identifiers={(DOMAIN, entry.entry_id)}, name=entry.title
    )
    for domain, uid in (("sensor", "x_solar_production"), ("sensor", "x_decision_log")):
        er.async_get(hass).async_get_or_create(
            domain, DOMAIN, uid, config_entry=entry, device_id=hub.id, suggested_object_id=uid
        )
    button = AddToDashboardButton(entry)
    button.hass = hass
    with patch(PN_MODULE) as mock_pn:
        await button.async_press()

    card = yaml.safe_load(mock_pn.call_args.args[1].split("```yaml\n")[1].split("\n```")[0])

    assert [c["type"] for c in card["cards"]] == ["entities", "markdown"]
    assert card["cards"][1]["title"] == "Decision log (manual apply)"
