"""Wet food feeder profile (PLAF109 Polar).

Protocol observed directly from vendor cloud traffic, not inferred:

    -> service/sub  WET_FOOD_FEED_NOW_SERVICE {planId, feedingDuration}
    <- service/post ack {code: 0}
    <- event/post   WET_GRAIN_OUTPUT_EVENT {finished, feedingDuration, planId,
                                            plate, execTime}
    -> event/sub    ack {code: 0, execStep}
                    GRAIN_THAW -> GRAIN_START -> OPEN_DOOR -> CLOSE_DOOR
                                                           -> GRAIN_END

Two behaviours differ fundamentally from an auger feeder:

* There is no quantity. The plate rotates and the door stays open for
  `feedingDuration` minutes.
* Feeding requires an existing `planId` - the device takes the plate from that
  plan, so it cannot feed ad-hoc.

The firmware also refuses to actuate unless the plate has homed
(`zeroState == SUCCESS`). It accepts the command and does nothing, which
presents to the user as an unexplained timeout - hence the explicit warning
and the zeroState diagnostic.

Plan ownership, established by testing against a device:

* An unreferenced planId is rejected with code 2050. A feed cannot be
  fabricated, because the command carries no plate and the device resolves one
  from the stored plan.
* Plans are push-only and replace the list wholesale. There is no plan-fetch
  command for this model - GET_FEEDING_PLAN_EVENT never appears.
* Locally-issued planIds ARE accepted (code 0), so plans need not come from the
  vendor cloud.
* The ack echoes the planIds now stored, which is the only read-back available:
  it confirms which plans exist, though not their plate or duration.

Together these mean the integration must own the plan list outright. It cannot
merge with, or borrow from, plans created by the vendor cloud.
"""

from __future__ import annotations

import datetime
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
    CODE_ERROR_PLAN_NOT_FOUND,
    CMD_PET_DETECT_EVENT,
    CMD_MACHINE_INFRARED_EVENT,
    CAP_PET_PRESENCE,
    CAP_TEMPERATURE,
    PET_DETECT_NEAR,
    DEFAULT_WET_FEEDING_DURATION,
    WET_FEEDING_MAX_MINUTES,
    WET_FEEDING_MIN_MINUTES,
    ZERO_STATE_SUCCESS,
)
from ..protocol.codec import build_wet_feed_now, build_wet_feeding_plan
from ..exceptions import NoPlanForPlate, PlateNotHomed
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
        # This one is refrigerated and reports its cabinet temperature on
        # every heartbeat. No CAP_SD_CARD: it has no camera and never sends
        # an sdCard* attribute.
        CAP_TEMPERATURE,
    })

    def build_plans(self, plans: list[dict]) -> str:
        """A wet feeder uses its own plan command; the auger one is dropped
        silently by the firmware, so plans would never reach the device.

        Dates are recomputed on every push: the device ignores repeatDay and
        will not run a plan whose executionDay has passed.
        """
        return build_wet_feeding_plan(refresh_execution_days(plans))

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
        rotate to the plate, open the door, shut it again - and reports
        progress through WET_GRAIN_OUTPUT_EVENT.execStep (GRAIN_THAW,
        GRAIN_START, OPEN_DOOR, CLOSE_DOOR, GRAIN_END). No quantity appears
        anywhere in it.
        """
        if not 1 <= plate <= self.PLATE_COUNT:
            _LOGGER.error(
                "Device %s: plate %s out of range (1-%s)",
                device.serial, plate, self.PLATE_COUNT,
            )
            return

        plan = _plan_for_plate(device, plate)
        if plan is None:
            known = [
                p["plate"] for p in device.feeding_plans if p.get("plate") is not None
            ]
            raise NoPlanForPlate(plate, known)

        zero_state = device.state.get("zero_state")
        # Only refuse on a known-bad state. zero_state is None until the device
        # reports one, and refusing then would block a feed that would work.
        if zero_state is not None and zero_state != ZERO_STATE_SUCCESS:
            raise PlateNotHomed(zero_state)

        # Clamp: the plan may predate the units fix, or have come from the
        # vendor cloud, and feedingDuration is minutes - an unclamped 14400
        # would ask a refrigerated feeder to stand open for ten days.
        raw = int(plan.get("feedingDuration") or DEFAULT_WET_FEEDING_DURATION)
        duration = max(WET_FEEDING_MIN_MINUTES, min(WET_FEEDING_MAX_MINUTES, raw))
        if duration != raw:
            _LOGGER.warning(
                "Device %s: plan %s asked for %s minutes, clamped to %s "
                "(maximum %s minutes)",
                device.serial, plan.get("planId"), raw, duration,
                WET_FEEDING_MAX_MINUTES,
            )
        await device._publish(
            device.topics.service_sub,
            build_wet_feed_now(int(plan["planId"]), duration),
        )


def next_execution_day(
    execution_time: str,
    repeat_day: list[int] | None,
    now: datetime.datetime | None = None,
) -> str:
    """Date on which a plan should next run, as YYYY-MM-DD.

    The device treats executionDay as an absolute date and ignores repeatDay:
    a plan whose date has passed simply never runs again. Confirmed by three
    plans carrying repeatDay [1..7] failing to fire the following day.

    The vendor cloud worked around this by re-pushing the same planId with an
    advanced date (optCode PLATE_POSTPONE), so a local integration has to do
    the same. This computes the next date matching the requested weekdays whose
    time has not already passed.

    `execution_time` and `now` are both UTC, matching the stored plan. Measured
    2026-08-06: a plan reading executionTime "21:00" started its feed at
    21:00:01Z, so the device schedules against UTC and the caller converts.
    """
    now = now or datetime.datetime.now(datetime.timezone.utc)
    try:
        hh, mm = (int(x) for x in execution_time.split(":")[:2])
    except (ValueError, AttributeError):
        hh, mm = 0, 0

    active = {d for d in (repeat_day or []) if 1 <= d <= 7} or set(range(1, 8))

    for offset in range(8):
        day = now.date() + datetime.timedelta(days=offset)
        if day.isoweekday() not in active:
            continue
        if offset == 0 and (hh, mm) <= (now.hour, now.minute):
            continue  # already passed today
        return day.isoformat()
    return (now.date() + datetime.timedelta(days=1)).isoformat()


def refresh_execution_days(plans: list[dict]) -> list[dict]:
    """Advance every plan to its next occurrence.

    Called before each push and once a day, because a plan left on a past date
    is dead rather than merely late.
    """
    out = []
    for plan in plans:
        p = dict(plan)
        if p.get("executionTime"):
            p["executionDay"] = next_execution_day(
                p["executionTime"], p.get("repeatDay")
            )
        out.append(p)
    return out


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
    code = payload.get("code")
    if code not in (None, 0):
        _LOGGER.warning(
            "Device %s did not accept the feed (code=%s, planId=%s)",
            device.serial, code, payload.get("planId"),
        )
    if code == CODE_ERROR_PLAN_NOT_FOUND:
        # Worth its own message: the generic "code=2050" tells a user nothing,
        # and the cause is specific and fixable.
        _LOGGER.error(
            "Device %s rejected the feed: planId %s is not stored on the "
            "device. A feed references an existing plan and the device reads "
            "the plate from it, so the plan must be pushed before it can be "
            "served.",
            device.serial,
            payload.get("planId"),
        )
        return
    warn_if_failed(device, CMD_WET_FOOD_FEED_NOW_SERVICE, payload)


async def _handle_plan_response(device: Any, payload: dict) -> None:
    """Acknowledgement of a plan push.

    The ack echoes only {planId, syncTime} - not plate or duration - so it must
    NOT replace the stored plans. Doing so would strip the plate off every plan
    and leave serve_plate unable to resolve one.
    """
    if not warn_if_failed(device, CMD_WET_GRAIN_FEEDING_PLAN_SERVICE, payload):
        return
    acked = {p.get("planId") for p in payload.get("plans", []) if isinstance(p, dict)}
    known = {p.get("planId") for p in device.feeding_plans}
    if acked and acked != known:
        _LOGGER.warning(
            "Device %s stored plans %s but we hold %s", device.serial, acked, known
        )


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
