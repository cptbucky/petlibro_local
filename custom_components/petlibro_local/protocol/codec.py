"""JSON codec helpers for Petlibro MQTT messages."""

from __future__ import annotations

import datetime
import hashlib
import json
import time
import uuid
from typing import Any


def generate_msg_id() -> str:
    """Generate a 32-char message ID (SHA256 of random UUID)."""
    return hashlib.sha256(str(uuid.uuid4()).encode()).hexdigest()[:32]


def timestamp_now_ms() -> int:
    """Current time as milliseconds since epoch."""
    return int(time.time() * 1000)


def timezone_offset_hours() -> float:
    """Local timezone offset from UTC in hours."""
    now = datetime.datetime.now().astimezone()
    return now.utcoffset().total_seconds() / 3600


def build_response(cmd: str, msg_id: str | None = None, code: int = 0, **extra) -> str:
    """Build a JSON response payload to send to the device."""
    payload: dict[str, Any] = {
        "cmd": cmd,
        "ts": timestamp_now_ms(),
        "code": code,
    }
    if msg_id is not None:
        payload["msgId"] = msg_id
    else:
        payload["msgId"] = generate_msg_id()
    payload.update(extra)
    return json.dumps(payload)


def build_command(cmd: str, **kwargs) -> str:
    """Build a JSON command payload (server → device)."""
    payload: dict[str, Any] = {
        "cmd": cmd,
        "msgId": generate_msg_id(),
        "ts": timestamp_now_ms(),
    }
    payload.update(kwargs)
    return json.dumps(payload)


def build_ntp_response(calibrate: bool = False) -> str:
    """Build NTP response with current time and timezone.

    Deliberately does NOT send timezoneOffsetSeconds or the DST transition
    fields the vendor sends. Captured traffic (2026-08-06) shows executionTime
    is interpreted as UTC, so plan scheduling does not depend on the device's
    offset, and this process runs in a container whose clock is UTC - we would
    be telling the device it is on UTC while the vendor told it otherwise.
    Sending a worse answer than the one it already has helps nobody.
    """
    return json.dumps({
        "cmd": "NTP",
        "ts": timestamp_now_ms(),
        "code": 0,
        "calibrationTag": calibrate,
        "timezone": timezone_offset_hours(),
    })


def build_ntp_sync() -> str:
    """Build NTP_SYNC command to force device time recalibration."""
    return json.dumps({
        "cmd": "NTP_SYNC",
        "msgId": generate_msg_id(),
        "ts": timestamp_now_ms(),
        "timezone": timezone_offset_hours(),
    })


def build_manual_feed(portions: int) -> str:
    """Build manual feeding command (auger/dry feeders)."""
    return build_command("MANUAL_FEEDING_SERVICE", grainNum=portions)


def build_wet_feed_now(plan_id: int, feeding_duration: int) -> str:
    """Build immediate feed command for wet food feeders.

    Unlike the dry-feeder command this takes no quantity: a wet feeder rotates
    its plate and holds the door open for feeding_duration MINUTES - the wire
    carries minutes, not seconds (measured: a plan value of 120 held the door
    open from 21:00:06Z to 23:00:12Z). It also requires an existing plan_id - the device borrows that plan's plate, so
    there is no way to feed ad-hoc without a plan to reference.

    The device only actuates when its plate is homed (zeroState == SUCCESS);
    otherwise it accepts the command and silently does nothing.
    """
    return build_command(
        "WET_FOOD_FEED_NOW_SERVICE",
        planId=plan_id,
        feedingDuration=feeding_duration,
    )


def build_wet_feeding_plan(plans: list[dict], opt_code: str = "PLATE_POSTPONE") -> str:
    """Build the wet feeder plan sync command.

    Each plan carries planId, executionTime ("HH:MM"), executionDay
    ("YYYY-MM-DD"), plate and feedingDuration. opt_code selects the plan
    operation; PLATE_POSTPONE is the only value observed from the vendor cloud.
    """
    normalised = [{**p, "optCode": p.get("optCode", opt_code)} for p in plans]
    return build_command("WET_GRAIN_FEEDING_PLAN_SERVICE", plans=normalised)


def build_get_some_attrs(attr_keys: list[str]) -> str:
    """Build a targeted attribute read.

    Cheaper than ATTR_GET_SERVICE and the only way the vendor cloud polls
    zeroState before feeding.
    """
    return build_command("GET_SOME_ATTR_SERVICE", attrKeys=attr_keys)


def build_function_test(action: str = "AUDIO") -> str:
    """Build a device self-test command. AUDIO rings the feeder's bell."""
    return json.dumps({
        "cmd": "DEVICE_FUNCTION_TEST_SERVICE",
        "ts": timestamp_now_ms(),
        "action": action,
    })


def build_attr_get() -> str:
    """Build request for full device attribute snapshot."""
    return build_command("ATTR_GET_SERVICE")


def build_attr_set(**attrs) -> str:
    """Build attribute set command with sparse key-value pairs.

    Keys should be camelCase MQTT field names, e.g.:
        build_attr_set(lightSwitch=True, volume=50)
    """
    return build_command("ATTR_SET_SERVICE", **attrs)


def build_device_reboot() -> str:
    """Build device reboot command."""
    return build_command("DEVICE_REBOOT")


def build_restore() -> str:
    """Build factory restore command."""
    return build_command("RESTORE")


def build_feeding_plan(plans: list[dict]) -> str:
    """Build feeding plan service command.

    Each plan dict should have keys:
        planId, executionTime, repeatDay, enableAudio, audioTimes, grainNum, syncTime
    """
    return build_command("FEEDING_PLAN_SERVICE", plans=plans)


def parse_payload(raw: str | bytes) -> dict[str, Any]:
    """Parse an MQTT message payload from JSON."""
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", errors="replace")
    return json.loads(raw)
