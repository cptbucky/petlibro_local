"""Fallback profile for a model with no dedicated profile.

An unrecognised feeder still parses and reports fine - the message shapes are
shared - so it gets the auger profile's handlers and plan format, which is
better than no behaviour at all.

What it deliberately does **not** get is a way to dispense. The PLAF109 taught
us that a feeder silently drops a command it does not implement: sending
MANUAL_FEEDING_SERVICE to a non-auger model looks exactly like success, and was
verified against an invented NONEXISTENT_PROBE_SERVICE as a control. Offering a
Dispense button on a model we have never seen therefore risks a control that
appears to work, reports no error, and never feeds an animal.

So this profile omits CAP_DISPENSE_PORTIONS and provides no `dispense`. A user
whose feeder lands here gets sensors and diagnostics, and `manual_feed` logs a
clear message naming the model rather than emitting a command into the void.
Adding a real profile is a small change; a feed that silently never happens is
not something the user can debug.
"""

from __future__ import annotations

from typing import Any

from ..const import CAP_FEEDING_PLANS
from ..protocol.codec import build_feeding_plan
from .dry import (
    _handle_detection_event,
    _handle_feeding_plan_response,
    _handle_get_feeding_plan,
    _handle_grain_output,
    _handle_manual_feeding_response,
)
from ..const import (
    CMD_DETECTION_EVENT,
    CMD_FEEDING_PLAN_SERVICE,
    CMD_GET_FEEDING_PLAN_EVENT,
    CMD_GRAIN_OUTPUT_EVENT,
    CMD_MANUAL_FEEDING_SERVICE,
)


class UnknownFeeder:
    """Conservative profile for an unrecognised product id."""

    #: Claims nothing. `get_profile` selects this only when no profile matches.
    PRODUCT_IDS: frozenset[str] = frozenset()
    MODEL_NAME = "Unrecognised Petlibro feeder"
    FIELDS: dict[str, str] = {}

    #: No CAP_DISPENSE_PORTIONS: see the module docstring. No CAP_SD_CARD or
    #: CAP_DETECTION either - those describe camera hardware we have no reason
    #: to assume is present, and they would add permanently unavailable
    #: entities.
    CAPABILITIES = frozenset({CAP_FEEDING_PLANS})

    def build_plans(self, plans: list[dict]) -> str:
        """Use the auger plan format, the only one shared across known models."""
        return build_feeding_plan(plans)

    def handlers(self) -> dict[str, Any]:
        """Parse what the shared message shapes allow.

        Reading a device is safe; commanding one we do not understand is not.
        """
        return {
            CMD_GRAIN_OUTPUT_EVENT: _handle_grain_output,
            CMD_GET_FEEDING_PLAN_EVENT: _handle_get_feeding_plan,
            CMD_FEEDING_PLAN_SERVICE: _handle_feeding_plan_response,
            CMD_MANUAL_FEEDING_SERVICE: _handle_manual_feeding_response,
            CMD_DETECTION_EVENT: _handle_detection_event,
        }
