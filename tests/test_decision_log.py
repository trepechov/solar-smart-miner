"""Tests for the decision log: every input of build_decision, written when the plans change."""
from __future__ import annotations

import inspect
import json

from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.solar_smart_miner.config_flow import (
    CONF_BATTERY_ENTITY,
    CONF_GRID_ENTITY,
    CONF_SOLAR_ENTITY,
)
from custom_components.solar_smart_miner.const import DOMAIN
from custom_components.solar_smart_miner.coordinator import SolarMinerCoordinator
from custom_components.solar_smart_miner.decision import build_decision
from custom_components.solar_smart_miner.decision_log import DecisionLog, build_record, load_record
from custom_components.solar_smart_miner.protocols import (
    CoordinatorSnapshot,
    Decision,
    EnergySnapshot,
    MinerPlan,
    MinerSnapshot,
)

SOLAR = "sensor.solar_power"
GRID = "sensor.grid_consumption"
LIMITS = {"min": 500.0, "max": 3500.0}


def _snapshot(available_w: float = 3000.0) -> CoordinatorSnapshot:
    return CoordinatorSnapshot(
        energy=EnergySnapshot(
            solar_production_w=5000.0, grid_net_w=available_w - 1300.0,
            miner_consumption_sum_w=1300.0, available_for_miners_w=available_w,
        ),
        miners=[
            MinerSnapshot(
                miner_id="m1", ip="m1", name="Miner 1", power_w=1290.0, power_limit_w=1300.0,
                min_power_w=500.0, max_power_w=3500.0, temperature_c=55.0, is_available=True,
                power_limit_entity_id="number.m1", switch_entity_id="switch.m1",
            )
        ],
    )


INPUTS = dict(profile="solar_max", temp_target=60.0, temp_tolerance=10.0, battery_floor=20.0,
              import_min_w=200.0, import_max_w=400.0)


def _lines(log: DecisionLog) -> list[dict]:
    return [json.loads(line) for line in log.path.read_text().splitlines()]


def test_a_record_replays_to_the_same_decision() -> None:
    snapshot = _snapshot()
    decision = build_decision(snapshot, **INPUTS)
    record = json.loads(json.dumps(build_record(snapshot, INPUTS, decision)))  # as read from the file

    again = _replay(record)

    assert again.summary == decision.summary == record["summary"]
    assert {k: p.fingerprint for k, p in again.plans.items()} == {
        k: MinerPlan(**p).fingerprint for k, p in record["plans"].items()
    }


def _replay(record) -> Decision:
    snapshot, inputs = load_record(record)
    return build_decision(snapshot, **inputs)


def test_a_line_from_an_older_version_still_loads() -> None:
    snapshot = _snapshot()
    record = build_record(snapshot, INPUTS, build_decision(snapshot, **INPUTS))
    record["energy"]["field_from_the_past"] = 1
    record["miners"][0]["gone_since"] = "0.6"

    loaded, inputs = load_record(record)

    assert loaded.miners[0].name == "Miner 1" and inputs == INPUTS


async def test_a_line_is_written_only_when_the_plans_change(hass) -> None:
    log = DecisionLog(hass)
    snapshot = _snapshot()
    hold = Decision(summary="budget 3,000 W", plans={"m1": MinerPlan("hold", limit_w=1300.0)})
    same_plans_other_text = Decision(summary="budget 3,010 W", plans=dict(hold.plans))
    step = Decision(summary="budget 4,000 W", plans={"m1": MinerPlan("set_limit", limit_w=1500.0)})

    for decision in (hold, same_plans_other_text, step, step):
        await log.async_record(snapshot, INPUTS, decision)

    assert [line["summary"] for line in _lines(log)] == ["budget 3,000 W", "budget 4,000 W"]


async def test_the_coordinator_logs_every_argument_of_the_decision(hass, add_hass_miner) -> None:
    add_hass_miner("192.168.1.10", name="Brod1", limit="1100", power="1100", temperature="55",
                   limit_attrs=LIMITS)
    hass.states.async_set(SOLAR, "5000")
    hass.states.async_set(GRID, "1500")
    entry = MockConfigEntry(
        domain=DOMAIN, data={CONF_SOLAR_ENTITY: SOLAR, CONF_GRID_ENTITY: GRID, CONF_BATTERY_ENTITY: None}
    )
    entry.add_to_hass(hass)
    coordinator = SolarMinerCoordinator(hass, entry)

    snapshot = await coordinator._async_update_data()

    (line,) = _lines(coordinator.decision_log)
    arguments = set(inspect.signature(build_decision).parameters) - {"snapshot"}
    assert set(line["inputs"]) == arguments  # a new argument must be logged too, or replays drift
    assert line["miners"][0]["switch_entity_id"] is None and "solar_fault" in line["energy"]
    assert _replay(line).summary == snapshot.decision.summary
