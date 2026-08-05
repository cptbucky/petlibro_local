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


# Two years ahead covers the next two transitions in every zone that has them.
_DST_SEARCH_DAYS = 730
_DAY_SECONDS = 86400


def _utc_offset_seconds(ts: float) -> int:
    """UTC offset of the local timezone at `ts`, in seconds.

    Uses `time.localtime`, which consults the tz database for the timestamp
    given. `datetime.astimezone()` cannot be used here: with no argument it
    attaches a *fixed* offset taken from the current moment, so it reports
    today's offset for every future date and would never see a transition.
    """
    return time.localtime(ts).tm_gmtoff or 0


def next_dst_transitions(
    now_ts: float | None = None, count: int = 2
) -> list[tuple[int, int]]:
    """Find the next `count` UTC-offset changes in the local timezone.

    Returns `(transition_unix_seconds, offset_after_seconds)` pairs, earliest
    first. Zones without DST return an empty list.
    """
    start = int(time.time() if now_ts is None else now_ts)
    end = start + _DST_SEARCH_DAYS * _DAY_SECONDS
    current = _utc_offset_seconds(start)

    found: list[tuple[int, int]] = []
    cursor = start
    while cursor < end and len(found) < count:
        probe = min(cursor + _DAY_SECONDS, end)
        if _utc_offset_seconds(probe) == current:
            cursor = probe
            continue
        # Offset changed somewhere in this day; bisect to the exact second.
        low, high = cursor, probe
        while high - low > 1:
            mid = (low + high) // 2
            if _utc_offset_seconds(mid) == current:
                low = mid
            else:
                high = mid
        current = _utc_offset_seconds(high)
        found.append((high, current))
        cursor = high
    return found


def timezone_payload(now_ts: float | None = None) -> dict[str, Any]:
    """The timezone fields the firmware needs to interpret plan times.

    A wet plan's `executionTime` is wall-clock time in the *device's* timezone,
    and the device learns that timezone from the NTP response - there is no
    other channel for it. Sending only `timezone` leaves the firmware on UTC,
    which silently shifts every scheduled feed by the local offset: the user
    enters 17:00 and the feed runs at 17:00 UTC.

    The DST fields matter for the same reason one step later. The vendor
    preloads the next two transitions with the offset that follows each, so the
    device adjusts itself when the clocks change. Without them a correct offset
    today drifts by an hour at the next transition.

    Offsets come from the system timezone, matching the rest of this module.
    """
    offset = _utc_offset_seconds(time.time() if now_ts is None else now_ts)
    hours = offset / 3600
    payload: dict[str, Any] = {
        "timezoneOffsetSeconds": offset,
        # The vendor sends a whole number here; keep the wire shape identical
        # rather than emitting 1.0 where it emits 1.
        "timezone": int(hours) if offset % 3600 == 0 else hours,
    }

    transitions = next_dst_transitions(now_ts, count=2)
    if transitions:
        ts, after = transitions[0]
        payload["nextDSTTransitionTs"] = ts * 1000
        payload["nextDSTOffsetSeconds"] = after
    if len(transitions) > 1:
        ts, after = transitions[1]
        payload["secondNextDSTTransitionTs"] = ts * 1000
        payload["secondNextDSTOffsetSeconds"] = after
    return payload


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

    The timezone block is what makes scheduled feeds fire at the right
    wall-clock time - see `timezone_payload`.
    """
    return json.dumps({
        "cmd": "NTP",
        "ts": timestamp_now_ms(),
        "code": 0,
        "calibrationTag": calibrate,
        **timezone_payload(),
    })


def build_ntp_sync() -> str:
    """Build NTP_SYNC command to force device time recalibration."""
    return json.dumps({
        "cmd": "NTP_SYNC",
        "msgId": generate_msg_id(),
        "ts": timestamp_now_ms(),
        **timezone_payload(),
    })


def build_manual_feed(portions: int) -> str:
    """Build manual feeding command (auger/dry feeders)."""
    return build_command("MANUAL_FEEDING_SERVICE", grainNum=portions)


def build_wet_feed_now(plan_id: int, feeding_duration: int) -> str:
    """Build immediate feed command for wet food feeders.

    Unlike the dry-feeder command this takes no quantity: a wet feeder rotates
    its plate and holds the door open for feeding_duration seconds. It also
    requires an existing plan_id - the device borrows that plan's plate, so
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
