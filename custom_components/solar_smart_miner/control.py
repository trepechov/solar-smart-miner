"""Turns a MinerPlan into HA service calls and checks that they took.

The one place that touches the miners. It is reached from the Apply button (trigger
"manual", control mode Manual) and from the coordinator's cycle (trigger "auto", control
mode Automatic). Every command is checked before it is sent
(guards), sent with blocking service calls, then watched until the miner's entity shows
the expected value or the grace time runs out. Each step is reported to `on_event`
(the action log) and a failure raises the `control.apply-failed` notification.
"""
from __future__ import annotations

import logging
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

import voluptuous as vol
from homeassistant.components.persistent_notification import async_create as pn_create
from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.util import dt as dt_util

from .const import (
    APPLY_VERIFY_GRACE_RELAY_START_S,
    APPLY_VERIFY_GRACE_S,
    CONTROL_MODE_AUTO,
    CONTROL_MODE_MANUAL,
    DOMAIN,
)
from .decision import _describe_plan, _ladder
from .protocols import (
    ACTION_HOLD,
    ACTION_SET_LIMIT,
    ACTION_START,
    ACTION_STOP,
    STOP_METHOD_RELAY,
    MinerPlan,
    MinerSnapshot,
)

_LOGGER = logging.getLogger(__name__)

TRIGGER_MANUAL = "manual"
TRIGGER_AUTO = "auto"

# Command and event results.
RESULT_OK = "ok"  # the miner's entity shows the new value
RESULT_PENDING = "pending"  # sent, not yet seen on the miner
RESULT_REFUSED = "refused"  # a guard said no; nothing was sent
RESULT_FAILED = "failed"  # the call raised, or the value never showed up

_LIMIT_TOLERANCE_W = 0.5  # the number entity reads back the limit as a float

# Which control modes allow which trigger.
_MODES_FOR_TRIGGER = {TRIGGER_MANUAL: {CONTROL_MODE_MANUAL}, TRIGGER_AUTO: {CONTROL_MODE_AUTO}}

_STAGE_SWITCH = "switch"  # waiting for a stop / start switch to reach its state
_STAGE_LIMIT = "limit"  # waiting for the power limit to read back


@dataclass
class CommandResult:
    status: str
    reason: str = ""
    calls: list[dict] = field(default_factory=list)
    command_id: str = ""
    notified: bool = False  # the caller already told the owner (a notification), so don't raise


@dataclass
class CommandEvent:
    """One line of the action log: a command being sent, refused, or finished."""

    command_id: str
    trigger: str
    miner_id: str
    miner_name: str
    plan: MinerPlan
    status: str
    reason: str = ""
    calls: list[dict] = field(default_factory=list)
    ts: str = ""


@dataclass
class PendingCommand:
    command_id: str
    trigger: str
    miner_id: str
    miner_name: str
    plan: MinerPlan
    stage: str
    entity_id: str  # the entity being watched now
    expected: str | float  # "on" / "off", or the power limit in W
    deadline: float  # time.monotonic()
    limit_entity_id: str | None = None  # a start: the number to set once the switch is on
    calls: list[dict] = field(default_factory=list)


def _state_float(state) -> float | None:
    if state is None or state.state in (STATE_UNAVAILABLE, STATE_UNKNOWN):
        return None
    try:
        return float(state.state)
    except (TypeError, ValueError):
        return None


class MinerController:
    def __init__(
        self,
        hass: HomeAssistant,
        *,
        get_mode: Callable[[], str],
        on_limit_applied: Callable[[str, float], None] | None = None,
        on_event: Callable[[CommandEvent], Awaitable[None]] | None = None,
    ) -> None:
        self._hass = hass
        self._get_mode = get_mode
        self._on_limit_applied = on_limit_applied
        self._on_event = on_event
        self._pending: dict[str, PendingCommand] = {}  # miner id -> command being verified

    def is_pending(self, miner_id: str) -> bool:
        return miner_id in self._pending

    # --- guards -----------------------------------------------------------

    def mode_refusal(self, trigger: str) -> str | None:
        if self._get_mode() not in _MODES_FOR_TRIGGER.get(trigger, set()):
            return f"the control mode ({self._get_mode()}) doesn't allow {trigger} applying"
        return None

    def _entity_problem(self, entity_id: str | None, what: str) -> str | None:
        if not entity_id:
            return f"no {what} entity"
        state = self._hass.states.get(entity_id)
        if state is None or state.state == STATE_UNAVAILABLE:
            return f"{what} entity {entity_id} is unavailable"
        return None

    def refusal(
        self, miner: MinerSnapshot, plan: MinerPlan, trigger: str, steps: list[float]
    ) -> str | None:
        """Why this plan must not run now, or None. The guards, in order."""
        if (reason := self.mode_refusal(trigger)) is not None:
            return reason
        if plan.action == ACTION_HOLD:
            return "nothing to apply (hold)"
        if miner.miner_id in self._pending:
            return "the previous command for this miner is still being checked"
        if plan.action == ACTION_SET_LIMIT:
            if (problem := self._entity_problem(miner.power_limit_entity_id, "power limit")):
                return problem
        elif plan.action in (ACTION_STOP, ACTION_START):
            if (problem := self._entity_problem(plan.target_entity_id, "switch")):
                return problem
            if plan.action == ACTION_START and not miner.power_limit_entity_id:
                return "no power limit entity to set after the start"
        else:
            return f"unknown action {plan.action}"
        if plan.action in (ACTION_SET_LIMIT, ACTION_START):
            return self._limit_refusal(miner, plan.limit_w, steps)
        return None

    @staticmethod
    def _limit_refusal(miner: MinerSnapshot, limit_w: float | None, steps: list[float]) -> str | None:
        """P0 rule.miner-range: only the miner's own power steps. Refuse, never clamp: a limit
        off the ladder is a bug upstream and clamping would hide it."""
        if limit_w is None:
            return "the plan has no power limit"
        if limit_w not in _ladder(miner, steps):
            return f"{limit_w:g} W is not one of this miner's power steps"
        if miner.min_power_w is not None and limit_w < miner.min_power_w:
            return f"{limit_w:g} W is below the miner's minimum {miner.min_power_w:g} W"
        if miner.max_power_w is not None and limit_w > miner.max_power_w:
            return f"{limit_w:g} W is above the miner's maximum {miner.max_power_w:g} W"
        return None

    async def async_refuse(
        self, miner: MinerSnapshot, plan: MinerPlan, trigger: str, reason: str
    ) -> CommandResult:
        """Record a press that was turned away before it reached the guards."""
        result = CommandResult(RESULT_REFUSED, reason, command_id=uuid.uuid4().hex[:8])
        await self._emit(result.command_id, trigger, miner.miner_id, miner.name, plan, result)
        return result

    # --- apply ------------------------------------------------------------

    async def async_apply(
        self, miner: MinerSnapshot, plan: MinerPlan, *, trigger: str, steps: list[float]
    ) -> CommandResult:
        command_id = uuid.uuid4().hex[:8]
        reason = self.refusal(miner, plan, trigger, steps)
        if reason is not None:
            result = CommandResult(RESULT_REFUSED, reason, command_id=command_id)
            await self._emit(command_id, trigger, miner.miner_id, miner.name, plan, result)
            return result

        if plan.action == ACTION_SET_LIMIT:
            call = self._limit_call(miner.power_limit_entity_id, plan.limit_w)
            pending = PendingCommand(
                command_id, trigger, miner.miner_id, miner.name, plan, _STAGE_LIMIT,
                miner.power_limit_entity_id, plan.limit_w, self._deadline(APPLY_VERIFY_GRACE_S),
            )
        else:
            on = plan.action == ACTION_START
            call = {
                "service": "switch.turn_on" if on else "switch.turn_off",
                "entity_id": plan.target_entity_id,
            }
            grace = (
                APPLY_VERIFY_GRACE_RELAY_START_S
                if on and plan.method == STOP_METHOD_RELAY
                else APPLY_VERIFY_GRACE_S
            )
            pending = PendingCommand(
                command_id, trigger, miner.miner_id, miner.name, plan, _STAGE_SWITCH,
                plan.target_entity_id, "on" if on else "off", self._deadline(grace),
                limit_entity_id=miner.power_limit_entity_id if on else None,
            )

        # Registered before the first await, so a second press can't slip past the guard.
        self._pending[miner.miner_id] = pending
        if error := await self._send(call):
            del self._pending[miner.miner_id]
            result = CommandResult(RESULT_FAILED, error, [call], command_id)
            await self._emit(command_id, trigger, miner.miner_id, miner.name, plan, result)
            self._notify_failure(pending, error)
            return result

        pending.calls.append(call)
        if plan.action in (ACTION_SET_LIMIT, ACTION_START) and self._on_limit_applied:
            # The miner re-tunes now: the next cycle already counts it as tuning.
            self._on_limit_applied(miner.miner_id, plan.limit_w)
        result = CommandResult(RESULT_PENDING, "sent, waiting for the miner", [call], command_id)
        await self._emit(command_id, trigger, miner.miner_id, miner.name, plan, result)
        return result

    @staticmethod
    def _limit_call(entity_id: str | None, limit_w: float | None) -> dict:
        return {"service": "number.set_value", "entity_id": entity_id, "value": limit_w}

    @staticmethod
    def _deadline(grace_s: float) -> float:
        return time.monotonic() + grace_s

    async def _send(self, call: dict) -> str | None:
        """Make a service call; the error text if it failed. Blocking, so a hass-miner error shows."""
        domain, service = call["service"].split(".")
        data = {k: v for k, v in call.items() if k != "service"}
        try:
            await self._hass.services.async_call(domain, service, data, blocking=True)
        except (HomeAssistantError, vol.Invalid) as err:
            _LOGGER.warning("%s failed: %s", call["service"], err)
            return str(err) or type(err).__name__
        return None

    # --- verification -----------------------------------------------------

    async def async_check_pending(self) -> None:
        """Called every coordinator cycle: finish the commands whose result has shown up."""
        for pending in list(self._pending.values()):
            if self._reached(pending):
                if pending.stage == _STAGE_SWITCH and pending.limit_entity_id:
                    await self._set_limit_after_start(pending)
                else:
                    await self._finish(pending, RESULT_OK, "the miner shows the new value")
            elif time.monotonic() >= pending.deadline:
                await self._finish(
                    pending, RESULT_FAILED, f"{pending.entity_id} never reached {pending.expected}"
                )

    def _reached(self, pending: PendingCommand) -> bool:
        state = self._hass.states.get(pending.entity_id)
        if state is None:
            return False
        if pending.stage == _STAGE_SWITCH:
            return state.state == pending.expected
        value = _state_float(state)
        return value is not None and abs(value - pending.expected) < _LIMIT_TOLERANCE_W

    async def _set_limit_after_start(self, pending: PendingCommand) -> None:
        """The switch is on: set the limit the plan wanted, once the number is back."""
        limit_state = self._hass.states.get(pending.limit_entity_id)
        current = _state_float(limit_state)
        if current is None:
            # Still booting: keep waiting for the number, but not past the deadline.
            if time.monotonic() >= pending.deadline:
                await self._finish(
                    pending, RESULT_FAILED, f"started, but {pending.limit_entity_id} never came back"
                )
            return
        limit_w = pending.plan.limit_w
        if abs(current - limit_w) < _LIMIT_TOLERANCE_W:
            await self._finish(pending, RESULT_OK, "started, and it already has the limit")
            return
        call = self._limit_call(pending.limit_entity_id, limit_w)
        pending.calls.append(call)
        if error := await self._send(call):
            await self._finish(pending, RESULT_FAILED, f"started, but setting the limit failed: {error}")
            return
        pending.stage = _STAGE_LIMIT
        pending.entity_id = pending.limit_entity_id
        pending.expected = limit_w
        pending.deadline = self._deadline(APPLY_VERIFY_GRACE_S)
        if self._on_limit_applied:
            self._on_limit_applied(pending.miner_id, limit_w)

    async def _finish(self, pending: PendingCommand, status: str, reason: str) -> None:
        self._pending.pop(pending.miner_id, None)
        result = CommandResult(status, reason, pending.calls, pending.command_id)
        await self._emit(
            pending.command_id, pending.trigger, pending.miner_id, pending.miner_name,
            pending.plan, result,
        )
        if status == RESULT_FAILED:
            self._notify_failure(pending, reason)

    def _notify_failure(self, pending: PendingCommand, reason: str) -> None:
        """control.apply-failed: one notification per miner, replaced rather than stacked."""
        pn_create(
            self._hass,
            f"{pending.miner_name}: the command ({_describe_plan(pending.plan)}) didn't take: {reason}.\n\n"
            "Check the miner and the Solar Smart Miner action log.",
            title="Solar Smart Miner: a command didn't take",
            notification_id=f"{DOMAIN}_apply_failed_{pending.miner_id.replace('.', '_')}",
        )

    async def _emit(
        self, command_id: str, trigger: str, miner_id: str, miner_name: str,
        plan: MinerPlan, result: CommandResult,
    ) -> None:
        if self._on_event is None:
            return
        await self._on_event(
            CommandEvent(
                command_id, trigger, miner_id, miner_name, plan, result.status, result.reason,
                list(result.calls), dt_util.now().isoformat(timespec="seconds"),
            )
        )
