"""Wet food feeder profile (PLAF109 Polar).

Protocol observed directly from vendor cloud traffic, not inferred:

    -> service/sub  WET_FOOD_FEED_NOW_SERVICE {planId, feedingDuration}
    <- service/post ack {code: 0}
    <- event/post   WET_GRAIN_OUTPUT_EVENT {finished, feedingDuration, planId,
                                            plate, execTime}
    -> event/sub    ack {code: 0, execStep}
                    GRAIN_THAW -> GRAIN_START -> OPEN_DOOR -> GRAIN_END

Two behaviours differ fundamentally from an auger feeder:

* There is no quantity. The plate rotates and the door stays open for
  `feedingDuration` seconds.
* Feeding requires an existing `planId` - the device takes the plate from that
  plan, so it cannot feed ad-hoc.

The firmware also refuses to actuate unless the plate has homed
(`zeroState == SUCCESS`). It accepts the command and does nothing, which
presents to the user as an unexplained timeout - hence the explicit warning
and the zeroState diagnostic.
"""

from __future__ import annotations

import logging
from typing import Any

from ..const import (
    CAP_AUDIO_TEST,
    CAP_DISPENSE_PLATE,
    CAP_FEEDING_PLANS,
    CAP_PLATE,
    CMD_ATTR_PUSH_EVENT,
    CMD_DEVICE_CONFIG_SYNC,
    CMD_DEVICE_LOG_REPORT_EVENT,
    CMD_GET_SOME_ATTR_SERVICE,
    CMD_WET_FOOD_FEED_NOW_SERVICE,
    CMD_WET_GRAIN_FEEDING_PLAN_SERVICE,
    CMD_WET_GRAIN_OUTPUT_EVENT,
    DEFAULT_WET_FEEDING_DURATION,
    ZERO_STATE_SUCCESS,
)
from ..protocol.codec import build_wet_feed_now
from ..protocol.messages import WET_FIELDS
from .common import ack_event, merge_state, warn_if_failed

_LOGGER = logging.getLogger(__name__)


class WetFeeder:
    """Profile for plate-and-door wet food feeders."""

    PRODUCT_IDS = frozenset({"PLAF109"})
    MODEL_NAME = "Polar Wet Food Feeder"
    FIELDS = WET_FIELDS
    CAPABILITIES = frozenset({
        CAP_DISPENSE_PLATE,
        CAP_PLATE,
        CAP_AUDIO_TEST,
        CAP_FEEDING_PLANS,
    })

    def handlers(self) -> dict[str, Any]:
        return {
            CMD_WET_GRAIN_OUTPUT_EVENT: _handle_wet_output,
            CMD_WET_FOOD_FEED_NOW_SERVICE: _handle_feed_now_response,
            CMD_WET_GRAIN_FEEDING_PLAN_SERVICE: _handle_plan_response,
            CMD_GET_SOME_ATTR_SERVICE: _handle_some_attr_response,
            CMD_DEVICE_CONFIG_SYNC: _handle_config_sync,
            CMD_DEVICE_LOG_REPORT_EVENT: _handle_log_report,
        }

    async def dispense(self, device: Any, plan_id: int | None = None, **_: Any) -> None:
        """Feed now by referencing a plan."""
        plan_id = plan_id if plan_id is not None else _default_plan_id(device)
        if plan_id is None:
            _LOGGER.error(
                "Device %s: wet feeders feed by referencing a plan and none is "
                "known. Create a feeding plan before feeding manually.",
                device.serial,
            )
            return

        if device.state.get("zero_state") != ZERO_STATE_SUCCESS:
            _LOGGER.warning(
                "Device %s: plate not homed (zeroState=%s). The feeder will "
                "accept this command and do nothing - check for a jam or "
                "reseat the plate.",
                device.serial,
                device.state.get("zero_state"),
            )

        duration = _duration_for(device, plan_id)
        await device._publish(
            device.topics.service_sub, build_wet_feed_now(plan_id, duration)
        )


def _default_plan_id(device: Any) -> int | None:
    for plan in device.feeding_plans:
        if plan.get("planId") is not None:
            return int(plan["planId"])
    last = device.state.get("plan_id")
    return int(last) if last is not None else None


def _duration_for(device: Any, plan_id: int) -> int:
    for plan in device.feeding_plans:
        if plan.get("planId") == plan_id and plan.get("feedingDuration"):
            return int(plan["feedingDuration"])
    return int(device.state.get("feeding_duration") or DEFAULT_WET_FEEDING_DURATION)


async def _handle_wet_output(device: Any, payload: dict) -> None:
    """Feeding progress. execStep drives a 'currently feeding' state."""
    exec_step = payload.get("execStep", "")
    await ack_event(device, CMD_WET_GRAIN_OUTPUT_EVENT, payload, execStep=exec_step)
    await merge_state(device, payload, WET_FIELDS)


async def _handle_feed_now_response(device: Any, payload: dict) -> None:
    warn_if_failed(device, CMD_WET_FOOD_FEED_NOW_SERVICE, payload)


async def _handle_plan_response(device: Any, payload: dict) -> None:
    if warn_if_failed(device, CMD_WET_GRAIN_FEEDING_PLAN_SERVICE, payload):
        plans = payload.get("plans")
        if isinstance(plans, list):
            device.feeding_plans = plans


async def _handle_some_attr_response(device: Any, payload: dict) -> None:
    """Response to a targeted attribute read (e.g. zeroState)."""
    await merge_state(device, payload, WET_FIELDS)


async def _handle_config_sync(device: Any, payload: dict) -> None:
    """Vendor cloud pushes broker/API addresses with this. Nothing to do
    locally, but handle it so it is not logged as unknown on every connect."""
    _LOGGER.debug("Device %s config sync acknowledged", device.serial)


async def _handle_log_report(device: Any, payload: dict) -> None:
    _LOGGER.debug("Device %s log report: %s", device.serial, payload.get("msgId"))


# Attributes worth polling explicitly; zeroState is the one that explains a
# feeder that accepts commands but never actuates.
POLL_ATTRS = ["zeroState", "platePosition"]

__all__ = ["WetFeeder", "POLL_ATTRS", "CMD_ATTR_PUSH_EVENT"]
