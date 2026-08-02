"""Behaviour shared between feeder profiles.

Plain functions rather than a base class: profiles call what they need, so
sharing never forces a model to inherit behaviour it does not have.
"""

from __future__ import annotations

import logging
from typing import Any

from ..const import CODE_OK
from ..protocol.codec import build_response
from ..protocol.messages import normalize_payload

_LOGGER = logging.getLogger(__name__)


async def ack_event(device: Any, cmd: str, payload: dict, **extra: Any) -> None:
    """Acknowledge a device event on the event channel."""
    await device._publish(
        device.topics.event_sub,
        build_response(cmd, payload.get("msgId"), **extra),
    )


async def merge_state(
    device: Any, payload: dict, extra_fields: dict[str, str] | None = None
) -> None:
    """Normalise a payload into device state and notify listeners.

    `extra_fields` carries the profile's model-specific field mappings, which
    take precedence over the shared ones.
    """
    device.state.update(normalize_payload(payload, extra_fields))
    device._notify_state_changed()


def warn_if_failed(device: Any, cmd: str, payload: dict) -> bool:
    """Log a non-zero response code. Returns True if the command succeeded."""
    code = payload.get("code", -1)
    if code != CODE_OK:
        _LOGGER.warning(
            "Device %s %s failed: code=%s msg=%s",
            device.serial,
            cmd,
            code,
            payload.get("msg", ""),
        )
        return False
    return True
