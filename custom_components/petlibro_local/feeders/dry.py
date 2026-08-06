"""Auger (dry kibble) feeder profile.

The original behaviour of this integration, unchanged - moved out of
PetlibroDevice so that model-specific commands live with the model rather than
in a shared class that has to ask what it is.

Also the fallback for unrecognised product ids, which preserves the previous
default for anyone running a model without a dedicated profile.
"""

from __future__ import annotations

import logging
from typing import Any

from ..const import (
    CAP_DETECTION,
    CAP_DISPENSE_PORTIONS,
    CAP_FEEDING_PLANS,
    CAP_SD_CARD,
    CAP_FEEDING_AUDIO,
    CAP_BUTTON_LOCK,
    CMD_DETECTION_EVENT,
    CMD_FEEDING_PLAN_SERVICE,
    CMD_GET_FEEDING_PLAN_EVENT,
    CMD_GRAIN_OUTPUT_EVENT,
    CMD_MANUAL_FEEDING_SERVICE,
)
from ..protocol.codec import build_feeding_plan, build_manual_feed, build_response
from .common import ack_event, merge_state, warn_if_failed

_LOGGER = logging.getLogger(__name__)


class DryFeeder:
    """Profile for auger feeders that dispense a quantity of kibble."""

    PRODUCT_IDS = frozenset({"PLAF203", "PLAF203S"})
    MODEL_NAME = "Granary Smart Feeder"
    FIELDS: dict[str, str] = {}  # shared maps already describe auger fields
    CAPABILITIES = frozenset({
        CAP_DISPENSE_PORTIONS,
        CAP_FEEDING_PLANS,
        CAP_DETECTION,
        # Camera models record to onboard storage and report sdCardState,
        # sdCardTotalCapacity and sdCardUsedCapacity. Plate feeders never do.
        CAP_SD_CARD,
        # enableAudio, autoChangeMode and disableHardwareButton are all
        # reported by this model and none by the plate feeder.
        CAP_FEEDING_AUDIO,
        CAP_BUTTON_LOCK,
    })

    def build_plans(self, plans: list[dict]) -> str:
        return build_feeding_plan(plans)

    def handlers(self) -> dict[str, Any]:
        return {
            CMD_GRAIN_OUTPUT_EVENT: _handle_grain_output,
            CMD_GET_FEEDING_PLAN_EVENT: _handle_get_feeding_plan,
            CMD_FEEDING_PLAN_SERVICE: _handle_feeding_plan_response,
            CMD_MANUAL_FEEDING_SERVICE: _handle_manual_feeding_response,
            CMD_DETECTION_EVENT: _handle_detection_event,
        }

    async def dispense(self, device: Any, portions: int = 1, **_: Any) -> None:
        """Dispense `portions` of kibble."""
        await device._publish(
            device.topics.service_sub, build_manual_feed(portions)
        )


async def _handle_grain_output(device: Any, payload: dict) -> None:
    """Grain dispensing event from device."""
    await device._publish(
        device.topics.service_sub,
        build_response(
            CMD_GRAIN_OUTPUT_EVENT,
            payload.get("msgId"),
            execStep=payload.get("execStep", ""),
        ),
    )
    await merge_state(device, payload)


async def _handle_get_feeding_plan(device: Any, payload: dict) -> None:
    """Device requesting current feeding plans - respond with stored plans."""
    await device._publish(
        device.topics.service_sub,
        build_response(
            CMD_GET_FEEDING_PLAN_EVENT,
            payload.get("msgId"),
            plans=[dict(plan) for plan in device.feeding_plans],
        ),
    )


async def _handle_feeding_plan_response(device: Any, payload: dict) -> None:
    warn_if_failed(device, CMD_FEEDING_PLAN_SERVICE, payload)


async def _handle_manual_feeding_response(device: Any, payload: dict) -> None:
    warn_if_failed(device, CMD_MANUAL_FEEDING_SERVICE, payload)


async def _handle_detection_event(device: Any, payload: dict) -> None:
    """Motion or sound detection event from device camera."""
    detection_type = payload.get("type", "UNKNOWN")
    ts = payload.get("ts")
    _LOGGER.debug(
        "Device %s detection: type=%s ts=%s", device.serial, detection_type, ts
    )
    await ack_event(device, CMD_DETECTION_EVENT, payload)
    device.state["detection_type"] = detection_type
    device.state["detection_ts"] = ts
    device._notify_state_changed()


__all__ = ["DryFeeder"]
