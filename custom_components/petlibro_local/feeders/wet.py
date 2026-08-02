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
    CMD_PET_DETECT_EVENT,
    CMD_MACHINE_INFRARED_EVENT,
    CAP_PET_PRESENCE,
    PET_DETECT_NEAR,
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
    #: Physical carousel plates. Bounds the Serve Plate entities.
    PLATE_COUNT = 3
    CAPABILITIES = frozenset({
        CAP_DISPENSE_PLATE,
        CAP_PLATE,
        CAP_AUDIO_TEST,
        CAP_FEEDING_PLANS,
        CAP_PET_PRESENCE,
    })

    def handlers(self) -> dict[str, Any]:
        return {
            CMD_WET_GRAIN_OUTPUT_EVENT: _handle_wet_output,
            CMD_WET_FOOD_FEED_NOW_SERVICE: _handle_feed_now_response,
            CMD_WET_GRAIN_FEEDING_PLAN_SERVICE: _handle_plan_response,
            CMD_GET_SOME_ATTR_SERVICE: _handle_some_attr_response,
            CMD_DEVICE_CONFIG_SYNC: _handle_config_sync,
            CMD_DEVICE_LOG_REPORT_EVENT: _handle_log_report,
            CMD_PET_DETECT_EVENT: _handle_pet_detect,
            CMD_MACHINE_INFRARED_EVENT: _handle_infrared,
        }

    async def serve_plate(self, device: Any, plate: int) -> None:
        """Serve one of the carousel's plates.

        Deliberately not named `dispense`: this is not an auger turning N
        times. The firmware runs a multi-second sequence - pause refrigeration,
        rotate to the plate, open the door - and reports progress through
        WET_GRAIN_OUTPUT_EVENT.execStep (GRAIN_THAW, GRAIN_START, OPEN_DOOR,
        GRAIN_END). No quantity appears anywhere in it.
        """
        if not 1 <= plate <= self.PLATE_COUNT:
            _LOGGER.error(
                "Device %s: plate %s out of range (1-%s)",
                device.serial, plate, self.PLATE_COUNT,
            )
            return

        plan = _plan_for_plate(device, plate)
        if plan is None:
            _LOGGER.error(
                "Device %s: no feeding plan covers plate %s. The feed command "
                "references a plan and the device reads the plate from it, so "
                "a plan for this plate must exist before it can be served.",
                device.serial, plate,
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

        duration = int(plan.get("feedingDuration") or DEFAULT_WET_FEEDING_DURATION)
        await device._publish(
            device.topics.service_sub,
            build_wet_feed_now(int(plan["planId"]), duration),
        )


def _plan_for_plate(device: Any, plate: int) -> dict | None:
    """Find the plan that owns a plate.

    WET_FOOD_FEED_NOW_SERVICE takes a planId, not a plate number - the device
    reads the plate off the referenced plan. Confirmed against vendor traffic
    carrying two plans at once: planId 44142243 -> plate 1 and planId 44142244
    -> plate 3, each with its own execution time and duration.

    Note the vendor replaces the whole plan list on every edit (an empty
    "plans":[] push immediately followed by the new set), so plans are a set to
    be pushed wholesale rather than patched individually.
    """
    for plan in device.feeding_plans:
        if plan.get("plate") == plate and plan.get("planId") is not None:
            return plan
    return None


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


async def _handle_pet_detect(device: Any, payload: dict) -> None:
    """Pet approached or left the bowl, per the infrared sensor."""
    await ack_event(device, CMD_PET_DETECT_EVENT, payload)
    device.state["pet_present"] = payload.get("type") == PET_DETECT_NEAR
    await merge_state(device, payload, WET_FIELDS)


async def _handle_infrared(device: Any, payload: dict) -> None:
    """Raw infrared beam state, the signal behind PET_DETECT_EVENT."""
    await ack_event(device, CMD_MACHINE_INFRARED_EVENT, payload)
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

__all__ = ["WetFeeder", "POLL_ATTRS"]
