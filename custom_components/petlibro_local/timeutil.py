"""Conversions between a plan's wire time and the user's clock.

A feeding plan's `executionTime` is **UTC**. Measured 2026-08-06 from captured
vendor traffic: a plan reading `executionTime: "21:00"` began its feed cycle at
21:00:01Z, which was 22:00 BST.

Users think in local time, so the integration converts at its edges - local on
the way in, local on the way out, UTC on the wire.

The subtlety that made this wrong for a long time: these helpers must use **Home
Assistant's configured timezone**, not the process's. Home Assistant commonly
runs in a container with no TZ set, where `datetime.now().astimezone()` reports
UTC regardless of what the user configured. The integration used exactly that,
so its local->UTC conversion silently became a no-op and every scheduled feed
landed an hour out in BST - which users worked around by entering times in UTC.
`dt_util.DEFAULT_TIME_ZONE` is what Home Assistant sets from `core.config`, and
is the only correct source here.
"""

from __future__ import annotations

import datetime

from homeassistant.util import dt as dt_util


def _parse_hhmm(value: str) -> tuple[int, int] | None:
    try:
        hour, minute = (int(part) for part in value.split(":")[:2])
    except (ValueError, AttributeError):
        return None
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return None
    return hour, minute


def local_to_utc_hhmm(local_time: str) -> str:
    """Convert a local "HH:MM" to the UTC "HH:MM" that goes on the wire."""
    parsed = _parse_hhmm(local_time)
    if parsed is None:
        return local_time
    hour, minute = parsed
    local_dt = datetime.datetime.combine(
        dt_util.now().date(),
        datetime.time(hour, minute),
        tzinfo=dt_util.DEFAULT_TIME_ZONE,
    )
    utc_dt = local_dt.astimezone(datetime.timezone.utc)
    return f"{utc_dt.hour:02}:{utc_dt.minute:02}"


def utc_to_local_hhmm(utc_time: str) -> str:
    """Convert a wire "HH:MM" (UTC) to the user's local "HH:MM"."""
    parsed = _parse_hhmm(utc_time)
    if parsed is None:
        return utc_time
    hour, minute = parsed
    utc_dt = datetime.datetime.combine(
        dt_util.utcnow().date(),
        datetime.time(hour, minute),
        tzinfo=datetime.timezone.utc,
    )
    local_dt = utc_dt.astimezone(dt_util.DEFAULT_TIME_ZONE)
    return f"{local_dt.hour:02}:{local_dt.minute:02}"


def utc_to_local_12h(utc_time: str) -> str:
    """Convert a wire "HH:MM" (UTC) to a local "h:MM AM/PM" for display."""
    local = utc_to_local_hhmm(utc_time)
    parsed = _parse_hhmm(local)
    if parsed is None:
        return utc_time
    hour, minute = parsed
    return datetime.time(hour, minute).strftime("%-I:%M %p")
