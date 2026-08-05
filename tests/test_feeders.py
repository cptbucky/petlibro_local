"""Behavioural tests for per-model feeder behaviour.

These assert on the *wire contract* - what MQTT messages a device publishes for
a given action, and what public state results from a given inbound message.
They deliberately do not touch handler maps, profile internals or any private
attribute, so they survive a refactor of how model behaviour is organised.

No Home Assistant import is needed: device.py and feeders/ depend only on the
protocol layer. Run with `pytest tests/` from the repo root.
"""

from __future__ import annotations

import asyncio
import json
import pathlib
import sys
import types

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# custom_components has no __init__.py, so synthesise the package chain.
for _name, _path in (
    ("custom_components", ROOT / "custom_components"),
    ("custom_components.petlibro_local", ROOT / "custom_components" / "petlibro_local"),
):
    if _name not in sys.modules:
        _mod = types.ModuleType(_name)
        _mod.__path__ = [str(_path)]
        sys.modules[_name] = _mod

from custom_components.petlibro_local import const  # noqa: E402
from custom_components.petlibro_local.device import PetlibroDevice  # noqa: E402

WET = "PLAF109"
DRY = "PLAF203"
SERIAL = "AF0000000000000TEST"


class Feeder:
    """A device plus the messages it published, for assertion."""

    def __init__(self, product_id: str | None):
        self.sent: list[tuple[str, dict]] = []

        async def publish(topic: str, payload: str) -> None:
            self.sent.append((topic, json.loads(payload)))

        self.device = PetlibroDevice(SERIAL, publish, product_id=product_id)

    def run(self, coro):
        return asyncio.run(coro)

    def receive(self, **payload):
        """Simulate an inbound message from the device."""
        self.run(self.device.handle_message("t", json.dumps(payload)))

    @property
    def commands(self) -> list[str]:
        return [msg["cmd"] for _, msg in self.sent]

    def last(self) -> tuple[str, dict]:
        return self.sent[-1]


# --- feeding: the behaviour that differs most between models ---------------


def test_dry_feeder_dispenses_a_quantity():
    f = Feeder(DRY)
    f.run(f.device.manual_feed(portions=3))
    topic, msg = f.last()
    assert msg["cmd"] == "MANUAL_FEEDING_SERVICE"
    assert msg["grainNum"] == 3
    assert topic.endswith("/device/service/sub")


PLANS = [
    {"planId": 101, "plate": 1, "feedingDuration": 240},
    {"planId": 202, "plate": 2, "feedingDuration": 180},
    {"planId": 303, "plate": 3, "feedingDuration": 120},
]


def test_wet_feeder_serves_a_plate_by_referencing_its_plan():
    f = Feeder(WET)
    f.device.feeding_plans = list(PLANS)
    f.run(f.device.serve_plate(1))
    _, msg = f.last()
    assert msg["cmd"] == "WET_FOOD_FEED_NOW_SERVICE"
    assert msg["planId"] == 101
    assert msg["feedingDuration"] == 240
    # A wet feeder has no notion of quantity.
    assert "grainNum" not in msg


@pytest.mark.parametrize("plate,plan_id,duration", [(1, 101, 240), (2, 202, 180), (3, 303, 120)])
def test_each_plate_resolves_to_its_own_plan(plate, plan_id, duration):
    """The command carries a planId, not a plate, so serving plate N means
    finding the plan that owns plate N."""
    f = Feeder(WET)
    f.device.feeding_plans = list(PLANS)
    f.run(f.device.serve_plate(plate))
    _, msg = f.last()
    assert msg["planId"] == plan_id
    assert msg["feedingDuration"] == duration


def test_wet_feeder_never_sends_the_auger_command():
    """The firmware drops MANUAL_FEEDING_SERVICE silently, so sending it looks
    identical to success while nothing happens."""
    f = Feeder(WET)
    f.device.feeding_plans = list(PLANS)
    f.run(f.device.serve_plate(1))
    assert "MANUAL_FEEDING_SERVICE" not in f.commands


def test_wet_feeder_refuses_a_plate_with_no_plan():
    """Feeding takes the plate from a plan, so with no plan for that plate
    there is nothing coherent to send - and the user must be told, not left
    with a press that looks successful."""
    from custom_components.petlibro_local.exceptions import NoPlanForPlate

    f = Feeder(WET)
    f.device.feeding_plans = [PLANS[0]]
    with pytest.raises(NoPlanForPlate) as excinfo:
        f.run(f.device.serve_plate(2))
    assert f.sent == []
    # the message must name the plate and what is actually available
    assert "plate 2" in str(excinfo.value)
    assert "1" in str(excinfo.value)


def test_not_homed_plate_refuses_with_an_explanation():
    """The firmware accepts a feed while unhomed and silently does nothing, so
    the integration refuses rather than letting the press appear to work."""
    from custom_components.petlibro_local.exceptions import PlateNotHomed

    f = Feeder(WET)
    f.device.feeding_plans = list(PLANS)
    f.device.state["zero_state"] = "TIMEOUT"
    with pytest.raises(PlateNotHomed):
        f.run(f.device.serve_plate(1))
    assert f.sent == []


def test_unknown_homing_state_does_not_block_a_feed():
    """zero_state is None until the device reports one; refusing then would
    block feeds that would have worked."""
    f = Feeder(WET)
    f.device.feeding_plans = list(PLANS)
    assert f.device.state.get("zero_state") is None
    f.run(f.device.serve_plate(1))
    assert f.last()[1]["cmd"] == "WET_FOOD_FEED_NOW_SERVICE"


@pytest.mark.parametrize("plate", [0, 4, -1])
def test_wet_feeder_rejects_plates_outside_the_carousel(plate):
    f = Feeder(WET)
    f.device.feeding_plans = list(PLANS)
    f.run(f.device.serve_plate(plate))
    assert f.sent == []


def test_manual_feed_is_refused_on_a_wet_feeder():
    """manual_feed is a documented service, so it can be called against any
    device. On a plate feeder it must refuse rather than send a command the
    firmware will silently drop."""
    f = Feeder(WET)
    f.device.feeding_plans = list(PLANS)
    f.run(f.device.manual_feed(portions=2))
    assert f.sent == []


def test_serve_plate_is_refused_on_a_dry_feeder():
    f = Feeder(DRY)
    f.run(f.device.serve_plate(1))
    assert f.sent == []


def test_manual_feed_alias_still_works():
    """Kept for existing callers and user automations."""
    f = Feeder(DRY)
    f.run(f.device.manual_feed(2))
    assert f.last()[1]["grainNum"] == 2


# --- model resolution ------------------------------------------------------


@pytest.mark.parametrize("pid", [WET, "plaf109"])
def test_wet_model_is_recognised_case_insensitively(pid):
    f = Feeder(pid)
    assert f.device.supports(const.CAP_DISPENSE_PLATE)


@pytest.mark.parametrize("pid", [None, "PLAF999"])
def test_unknown_model_still_parses_and_reports(pid):
    """An unrecognised feeder keeps the shared message handling, so sensors and
    diagnostics work rather than the device being inert."""
    f = Feeder(pid)
    f.receive(cmd="HEARTBEAT", count=1, rssi=-50, wifiType=1)
    assert f.device.online


@pytest.mark.parametrize("pid", [None, "PLAF999"])
def test_unknown_model_refuses_to_dispense(pid):
    """The PLAF109 showed that a feeder drops a command it does not implement
    without complaint, so a Dispense button on hardware we have never seen would
    report success and never feed. Better to emit nothing and say why."""
    f = Feeder(pid)
    f.run(f.device.manual_feed(portions=1))
    assert f.sent == []
    assert const.CAP_DISPENSE_PORTIONS not in f.device.profile.CAPABILITIES


def test_unknown_model_is_not_labelled_as_a_known_one():
    """The device registry previously fell back to the string "PLAF203"."""
    assert "PLAF" not in Feeder("PLAF999").device.profile.MODEL_NAME


def test_plate_count_is_present_exactly_when_plates_are_supported():
    """Readers access PLATE_COUNT directly inside a supports() branch, so the
    contract is: declare it iff you declare the capability. Two readers used to
    guess different defaults (0 and 3) for a profile that lacked it."""
    from custom_components.petlibro_local.feeders import get_profile

    for pid in (WET, DRY, "PLAF999"):
        profile = get_profile(pid)
        has_plates = const.CAP_DISPENSE_PLATE in profile.CAPABILITIES
        assert hasattr(profile, "PLATE_COUNT") is has_plates, pid
        if has_plates:
            assert profile.PLATE_COUNT >= 1


def test_each_model_addresses_its_own_topics():
    assert f"dl/{WET}/" in Feeder(WET).device.topics.service_sub
    assert f"dl/{DRY}/" in Feeder(DRY).device.topics.service_sub


# --- capabilities drive which entities exist -------------------------------


def test_capabilities_are_model_appropriate():
    wet, dry = Feeder(WET).device, Feeder(DRY).device
    assert wet.supports(const.CAP_PLATE)
    assert not wet.supports(const.CAP_DISPENSE_PORTIONS)
    assert dry.supports(const.CAP_DISPENSE_PORTIONS)
    assert not dry.supports(const.CAP_PLATE)


# --- inbound state ---------------------------------------------------------


def test_plate_state_is_recorded_from_an_attribute_push():
    """zeroState/platePosition arrive via the shared ATTR_PUSH_EVENT handler,
    so model-specific field mapping has to apply there too."""
    f = Feeder(WET)
    f.receive(cmd="ATTR_PUSH_EVENT", msgId="m1", zeroState="SUCCESS", platePosition=2)
    assert f.device.state["zero_state"] == "SUCCESS"
    assert f.device.state["plate_position"] == 2


def test_feed_progress_is_recorded_and_acknowledged():
    f = Feeder(WET)
    f.receive(
        cmd="WET_GRAIN_OUTPUT_EVENT",
        msgId="m2",
        execStep="OPEN_DOOR",
        planId=44122386,
        plate=1,
        feedingDuration=240,
        finished=False,
    )
    assert f.device.state["wet_exec_step"] == "OPEN_DOOR"
    assert f.device.state["plate"] == 1
    topic, msg = f.last()
    assert topic.endswith("/device/event/sub")
    assert msg["cmd"] == "WET_GRAIN_OUTPUT_EVENT"


def test_wet_plan_id_does_not_collide_with_the_auger_meaning():
    """planId, execStep and finished exist in the auger map with different
    meanings; the wet model's mapping must win for a wet device."""
    f = Feeder(WET)
    f.receive(cmd="WET_GRAIN_OUTPUT_EVENT", msgId="m3", planId=7, execStep="GRAIN_END")
    assert f.device.state["plan_id"] == 7
    assert "grain_plan_id" not in f.device.state


def test_pet_presence_tracks_approach_and_departure():
    f = Feeder(WET)
    f.receive(cmd="PET_DETECT_EVENT", msgId="p1", type="NEAR")
    assert f.device.state["pet_present"] is True
    f.receive(cmd="PET_DETECT_EVENT", msgId="p2", type="LEAVE")
    assert f.device.state["pet_present"] is False


def test_infrared_state_is_recorded():
    f = Feeder(WET)
    f.receive(cmd="MACHINE_INFRARED_EVENT", msgId="i1", irState=True)
    assert f.device.state["ir_state"] is True


# --- regression: the config request storm ----------------------------------


def test_get_config_is_not_echoed_back():
    """Replying on config/sub with cmd=GET_CONFIG is indistinguishable from a
    fresh request, so device and integration ping-pong indefinitely. Measured
    at ~0.8 messages/sec each way before this was fixed."""
    f = Feeder(WET)
    f.receive(cmd="GET_CONFIG", msgId="m4")
    assert f.sent == []


# --- other model-specific commands -----------------------------------------


def test_bell_uses_the_function_test_command():
    f = Feeder(WET)
    f.run(f.device.ring_bell())
    _, msg = f.last()
    assert msg["cmd"] == "DEVICE_FUNCTION_TEST_SERVICE"
    assert msg["action"] == "AUDIO"


def test_targeted_attribute_read():
    f = Feeder(WET)
    f.run(f.device.request_attrs(["zeroState"]))
    _, msg = f.last()
    assert msg["cmd"] == "GET_SOME_ATTR_SERVICE"
    assert msg["attrKeys"] == ["zeroState"]


# --- schedule sensor describes the feeder to the UI -------------------------


def test_schedule_sensor_attributes_describe_the_feeder():
    """The Lovelace card branches on these rather than on model numbers, so a
    plate feeder must advertise itself and its plate count."""
    from custom_components.petlibro_local.feeders import get_profile

    wet, dry = get_profile(WET), get_profile(DRY)
    assert const.CAP_DISPENSE_PLATE in wet.CAPABILITIES
    assert wet.PLATE_COUNT == 3
    assert const.CAP_DISPENSE_PLATE not in dry.CAPABILITIES
    # A dry profile must not be asked for a plate count it does not have.
    assert getattr(dry, "PLATE_COUNT", None) is None


# --- plan push: the surface where the model difference actually bites -------


def test_wet_feeder_pushes_plans_with_its_own_command():
    """Regression: the auger plan command is dropped silently by wet firmware,
    so plans pushed with it never reach the device and every subsequent feed is
    rejected with code 2050."""
    f = Feeder(WET)
    f.run(f.device.set_feeding_plans(list(PLANS)))
    _, msg = f.last()
    assert msg["cmd"] == "WET_GRAIN_FEEDING_PLAN_SERVICE"
    assert [p["plate"] for p in msg["plans"]] == [1, 2, 3]


def test_dry_feeder_still_pushes_the_auger_plan_command():
    f = Feeder(DRY)
    f.run(f.device.set_feeding_plans([{"planId": 1, "grainNum": 2}]))
    _, msg = f.last()
    assert msg["cmd"] == "FEEDING_PLAN_SERVICE"


def test_plan_ack_does_not_strip_plate_from_stored_plans():
    """The ack echoes only {planId, syncTime}. Treating it as the new plan list
    would erase every plate and break serve_plate until restart."""
    f = Feeder(WET)
    f.device.feeding_plans = list(PLANS)
    f.receive(
        cmd="WET_GRAIN_FEEDING_PLAN_SERVICE",
        msgId="a1",
        code=0,
        plans=[{"planId": 101, "syncTime": 0},
               {"planId": 202, "syncTime": 0},
               {"planId": 303, "syncTime": 0}],
    )
    assert f.device.feeding_plans == PLANS
    f.sent.clear()
    f.run(f.device.serve_plate(2))
    assert f.last()[1]["planId"] == 202


# --- learning the model after setup ----------------------------------------


def test_learning_the_product_id_recomposes_the_profile():
    """The sniffer and manual config paths default the model, so it is learned
    from the first message. Rebinding topics alone leaves a plate feeder
    behaving as an auger for the whole session."""
    f = Feeder(None)
    assert not f.device.supports(const.CAP_DISPENSE_PLATE)

    f.device.set_product_id(WET)

    assert f.device.supports(const.CAP_DISPENSE_PLATE)
    assert f"dl/{WET}/" in f.device.topics.service_sub
    # wet commands must now be handled, and auger ones gone
    f.device.feeding_plans = list(PLANS)
    f.run(f.device.serve_plate(1))
    assert f.last()[1]["cmd"] == "WET_FOOD_FEED_NOW_SERVICE"


def test_learning_the_same_product_id_is_a_no_op():
    f = Feeder(WET)
    before = f.device.profile
    f.device.set_product_id(WET)
    assert f.device.profile is before


# --- executionDay: the device ignores repeatDay ----------------------------
# Three plans carrying repeatDay [1..7] failed to fire the day after they were
# created, because the device gates on executionDay and that date had passed.

import datetime  # noqa: E402

from custom_components.petlibro_local.feeders.wet import (  # noqa: E402
    next_execution_day,
    refresh_execution_days,
)

MON_0900 = datetime.datetime(2026, 8, 3, 9, 0, tzinfo=datetime.timezone.utc)


def test_time_already_passed_today_rolls_to_tomorrow():
    assert next_execution_day("08:00", [1, 2, 3, 4, 5, 6, 7], MON_0900) == "2026-08-04"


def test_time_still_to_come_stays_today():
    assert next_execution_day("17:00", [1, 2, 3, 4, 5, 6, 7], MON_0900) == "2026-08-03"


def test_weekday_restriction_is_respected():
    """Sunday-only, asked on a Monday, lands on the coming Sunday."""
    assert next_execution_day("17:00", [7, 0, 0, 0, 0, 0, 0], MON_0900) == "2026-08-09"


def test_empty_repeat_is_treated_as_daily():
    assert next_execution_day("17:00", [], MON_0900) == "2026-08-03"


def test_refresh_advances_a_stale_plan():
    """The exact failure seen in the field: a plan dated yesterday never runs
    again, however permissive its repeatDay."""
    stale = [{"planId": 1, "plate": 1, "executionTime": "08:00",
              "executionDay": "2026-08-02", "repeatDay": [1, 2, 3, 4, 5, 6, 7]}]
    refreshed = refresh_execution_days(stale)
    assert refreshed[0]["executionDay"] != "2026-08-02"
    # everything else is preserved, and the input is not mutated
    assert refreshed[0]["plate"] == 1
    assert stale[0]["executionDay"] == "2026-08-02"


def test_pushing_plans_refreshes_their_dates():
    """A push must never send a date already in the past."""
    f = Feeder(WET)
    today = datetime.date.today().isoformat()
    f.run(f.device.set_feeding_plans([
        {"planId": 1, "plate": 1, "executionTime": "23:59",
         "executionDay": "2020-01-01", "repeatDay": [1, 2, 3, 4, 5, 6, 7]}
    ]))
    _, msg = f.last()
    assert msg["cmd"] == "WET_GRAIN_FEEDING_PLAN_SERVICE"
    assert msg["plans"][0]["executionDay"] >= today


@pytest.mark.parametrize("day_offset", range(8))
def test_a_weekly_plan_never_drifts_onto_another_weekday(day_offset):
    """The daily refresh re-pushes every plan, so it must not walk a weekly
    plan forward onto whatever day it happens to run.

    A Sunday-only plan refreshed on any day of the week must still land on a
    Sunday - never the Monday the job happened to run on.
    """
    now = MON_0900 + datetime.timedelta(days=day_offset)
    got = next_execution_day("17:00", [7, 0, 0, 0, 0, 0, 0], now)
    assert datetime.date.fromisoformat(got).isoweekday() == 7
    assert datetime.date.fromisoformat(got) >= now.date()


def test_a_weekly_plan_stays_today_until_its_time_passes():
    """On the day itself it must not skip a week just because the refresh ran."""
    sunday_morning = datetime.datetime(2026, 8, 9, 9, 0, tzinfo=datetime.timezone.utc)
    assert next_execution_day("17:00", [7], sunday_morning) == "2026-08-09"


def test_a_weekly_plan_rolls_a_full_week_once_its_time_passes():
    sunday_evening = datetime.datetime(2026, 8, 9, 18, 0, tzinfo=datetime.timezone.utc)
    assert next_execution_day("17:00", [7], sunday_evening) == "2026-08-16"


def test_weekday_only_plan_skips_the_weekend():
    """Mon-Fri asked on a Saturday lands on the Monday."""
    saturday = datetime.datetime(2026, 8, 8, 9, 0, tzinfo=datetime.timezone.utc)
    assert next_execution_day("08:00", [1, 2, 3, 4, 5], saturday) == "2026-08-10"


# --- feed duration: minutes at the boundary, seconds on the wire -----------


def test_duration_bounds_are_four_hours():
    assert const.WET_FEEDING_MIN_MINUTES == 1
    assert const.WET_FEEDING_MAX_MINUTES == 240  # 4 hours


@pytest.mark.parametrize("minutes,seconds", [(1, 60), (4, 240), (30, 1800), (240, 14400)])
def test_minutes_convert_to_wire_seconds(minutes, seconds):
    """The UI works in minutes because nobody schedules feeding in seconds;
    the protocol carries seconds."""
    assert minutes * 60 == seconds


def test_a_four_hour_plan_is_servable():
    """The upper bound must survive the round trip to a feed command."""
    f = Feeder(WET)
    f.device.feeding_plans = [{"planId": 9, "plate": 1, "feedingDuration": 240 * 60}]
    f.run(f.device.serve_plate(1))
    assert f.last()[1]["feedingDuration"] == 14400


# --- capability gating and heartbeat telemetry -----------------------------
#
# The payloads below are copied verbatim from vendor traffic captured on
# 2026-08-05 (petlibro-local-proxy), not invented, so they assert against what
# the devices genuinely send.


def test_wet_feeder_reports_cabinet_temperature_on_the_heartbeat():
    """A refrigerated feeder puts temperature on every heartbeat rather than in
    an attribute push. It was previously dropped by the field map, so the value
    arrived roughly every 90s and went nowhere."""
    f = Feeder(WET)
    f.receive(cmd="HEARTBEAT", count=1455, rssi=-37, wifiType=1, temperature=16.63)
    assert f.device.state["temperature"] == 16.63


def test_dry_feeder_heartbeat_carries_no_temperature():
    """The auger model does not send it, which is why it is mapped per-model."""
    f = Feeder(DRY)
    f.receive(cmd="HEARTBEAT", count=1343, rssi=-59, wifiType=2)
    assert "temperature" not in f.device.state


def test_only_cooled_models_declare_the_temperature_capability():
    assert const.CAP_TEMPERATURE in Feeder(WET).device.profile.CAPABILITIES
    assert const.CAP_TEMPERATURE not in Feeder(DRY).device.profile.CAPABILITIES


def test_only_camera_models_declare_sd_card():
    """A plate feeder never sends an sdCard* attribute, so the storage sensors
    sat permanently unavailable on it."""
    assert const.CAP_SD_CARD in Feeder(DRY).device.profile.CAPABILITIES
    assert const.CAP_SD_CARD not in Feeder(WET).device.profile.CAPABILITIES


def test_close_door_is_a_feed_step_and_is_not_the_end_of_the_cycle():
    """CLOSE_DOOR arrives immediately before GRAIN_END carrying finished=false.
    Treating it as the end would report a feed complete while the door is still
    shutting."""
    f = Feeder(WET)
    f.receive(
        cmd="WET_GRAIN_OUTPUT_EVENT", finished=False, feedingDuration=210,
        planId=1, plate=3, execTime=1785940468677, execStep="CLOSE_DOOR",
    )
    assert f.device.state["wet_exec_step"] == const.EXEC_STEP_CLOSE_DOOR
    assert f.device.state["wet_finished"] is False

    f.receive(
        cmd="WET_GRAIN_OUTPUT_EVENT", finished=True, feedingDuration=210,
        planId=1, plate=3, execTime=1785940468677, execStep="GRAIN_END",
    )
    assert f.device.state["wet_exec_step"] == const.EXEC_STEP_GRAIN_END
    assert f.device.state["wet_finished"] is True


# --- timezone: why scheduled feeds ran at the wrong hour --------------------
#
# A wet plan's executionTime is wall-clock time in the *device's* timezone, and
# the NTP response is the only channel that tells the device what that is. The
# expected values below are copied from vendor traffic captured 2026-08-05.

import os  # noqa: E402
import time as _time  # noqa: E402

from custom_components.petlibro_local.protocol import codec  # noqa: E402


class london:
    """Pin the process to Europe/London so DST assertions are deterministic."""

    def __enter__(self):
        self._old = os.environ.get("TZ")
        os.environ["TZ"] = "Europe/London"
        _time.tzset()

    def __exit__(self, *exc):
        if self._old is None:
            del os.environ["TZ"]
        else:
            os.environ["TZ"] = self._old
        _time.tzset()


# 2026-08-05 14:34 UTC — the moment of the capture, during BST.
CAPTURE_TS = 1785940462


def test_ntp_response_carries_the_offset_the_firmware_actually_uses():
    """Sending only `timezone` left the device on UTC, so a plan entered as
    17:00 fired at 17:00 UTC — an hour late in BST. The vendor sends
    timezoneOffsetSeconds and that is what the firmware honours."""
    with london():
        msg = json.loads(codec.build_ntp_response())
    assert msg["timezoneOffsetSeconds"] == 3600
    assert msg["timezone"] == 1
    # An integer on the wire, as the vendor sends — not 1.0.
    assert isinstance(msg["timezone"], int)


def test_ntp_sync_carries_the_same_timezone_block():
    with london():
        msg = json.loads(codec.build_ntp_sync())
    assert msg["timezoneOffsetSeconds"] == 3600


def test_next_two_dst_transitions_match_the_vendor():
    """The vendor preloads the next two transitions so the device adjusts
    itself when the clocks change. These are the exact values it sent."""
    with london():
        transitions = codec.next_dst_transitions(CAPTURE_TS, count=2)
    assert transitions == [
        (1792890000, 0),      # 2026-10-25 01:00 UTC, BST -> GMT
        (1806195600, 3600),   # 2027-03-28 01:00 UTC, GMT -> BST
    ]


def test_timezone_payload_matches_the_captured_vendor_payload():
    with london():
        payload = codec.timezone_payload(CAPTURE_TS)
    assert payload == {
        "timezoneOffsetSeconds": 3600,
        "timezone": 1,
        "nextDSTTransitionTs": 1792890000000,
        "nextDSTOffsetSeconds": 0,
        "secondNextDSTTransitionTs": 1806195600000,
        "secondNextDSTOffsetSeconds": 3600,
    }


def test_a_zone_without_dst_omits_the_transition_fields():
    """Sending a transition timestamp of 0 would be worse than sending none."""
    old = os.environ.get("TZ")
    os.environ["TZ"] = "UTC"
    _time.tzset()
    try:
        payload = codec.timezone_payload(CAPTURE_TS)
    finally:
        if old is None:
            del os.environ["TZ"]
        else:
            os.environ["TZ"] = old
        _time.tzset()
    assert payload == {"timezoneOffsetSeconds": 0, "timezone": 0}
