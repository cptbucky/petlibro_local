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
    """The upper bound must survive the round trip to a feed command. Four
    hours is 240 on the wire, because the wire carries minutes."""
    f = Feeder(WET)
    f.device.feeding_plans = [
        {"planId": 9, "plate": 1, "feedingDuration": const.WET_FEEDING_MAX_MINUTES},
    ]
    f.run(f.device.serve_plate(1))
    assert f.last()[1]["feedingDuration"] == 240


def test_a_stale_seconds_duration_is_clamped_to_four_hours():
    """A plan written before the units fix holds minutes*60. Sent verbatim,
    14400 would ask a refrigerated feeder to stand open for ten days."""
    f = Feeder(WET)
    f.device.feeding_plans = [{"planId": 9, "plate": 1, "feedingDuration": 240 * 60}]
    f.run(f.device.serve_plate(1))
    assert f.last()[1]["feedingDuration"] == const.WET_FEEDING_MAX_MINUTES


def test_a_zero_duration_plan_falls_back_to_the_default():
    f = Feeder(WET)
    f.device.feeding_plans = [{"planId": 9, "plate": 1, "feedingDuration": 0}]
    f.run(f.device.serve_plate(1))
    assert f.last()[1]["feedingDuration"] == const.DEFAULT_WET_FEEDING_DURATION
    assert const.DEFAULT_WET_FEEDING_DURATION <= const.WET_FEEDING_MAX_MINUTES


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


# --- timezone and duration, measured from a real feed ----------------------
#
# Captured 2026-08-06. Plan 44456688 read executionTime "21:00",
# feedingDuration 120. The device began the cycle at 21:00:01Z and held the
# door open from 21:00:06Z to 23:00:12Z. Both facts below come from that trace.

import os  # noqa: E402
import time as _time  # noqa: E402

from custom_components.petlibro_local.protocol import codec  # noqa: E402


class london:
    """Pin the process to Europe/London so the assertions are deterministic."""

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


def test_ntp_reply_does_not_claim_a_timezone_offset():
    """0.2.3 sent timezoneOffsetSeconds and the DST transition block, copying
    the vendor. That was wrong twice over: plan times are UTC so scheduling
    does not depend on the device's offset, and this process runs in a
    container whose clock is UTC - so we would have told the feeder it was on
    UTC while the vendor had correctly told it otherwise."""
    with london():
        msg = json.loads(codec.build_ntp_response())
    for field in (
        "timezoneOffsetSeconds",
        "nextDSTOffsetSeconds",
        "nextDSTTransitionTs",
        "secondNextDSTOffsetSeconds",
        "secondNextDSTTransitionTs",
    ):
        assert field not in msg, field


def test_feed_now_duration_is_minutes_not_seconds():
    """The wire carries minutes. Converting the user's minutes to seconds held
    the door open 60x too long - a 4-minute feed became 4 hours on a
    refrigerated feeder."""
    f = Feeder(WET)
    f.device.feeding_plans = [
        {"planId": 7, "plate": 1, "feedingDuration": 120},
    ]
    f.run(f.device.serve_plate(1))
    _, msg = f.last()
    assert msg["feedingDuration"] == 120


def test_plan_duration_reaches_the_wire_unmultiplied():
    f = Feeder(WET)
    f.run(f.device.set_feeding_plans([
        {"planId": 1, "plate": 1, "executionTime": "21:00", "feedingDuration": 120},
    ]))
    _, msg = f.last()
    assert msg["plans"][0]["feedingDuration"] == 120


def test_duration_bounds_are_minutes_on_the_wire():
    """WET_FEEDING_MAX_MINUTES is 240. Under the old x60 that reached the
    device as 14400, which it would have read as 14400 minutes - ten days."""
    assert const.WET_FEEDING_MAX_MINUTES == 240
    f = Feeder(WET)
    f.device.feeding_plans = [
        {"planId": 9, "plate": 1, "feedingDuration": const.WET_FEEDING_MAX_MINUTES},
    ]
    f.run(f.device.serve_plate(1))
    assert f.last()[1]["feedingDuration"] == 240


# --- plan times are UTC on the wire ----------------------------------------
#
# The integration converts local->UTC at its edges. The profile layer must not
# convert again, so a time handed to it reaches the wire untouched.

def test_plan_time_reaches_the_wire_unchanged():
    """No conversion between what the user picked and what is sent. A local
    offset applied here would shift every scheduled feed by that offset."""
    f = Feeder(WET)
    f.run(f.device.set_feeding_plans([
        {"planId": 1, "plate": 1, "executionTime": "17:00", "feedingDuration": 240},
    ]))
    _, msg = f.last()
    assert msg["plans"][0]["executionTime"] == "17:00"


def test_dry_plan_time_also_reaches_the_wire_unchanged():
    """Both models get the same NTP timezone block, so both read plan times as
    local. The rule is not wet-feeder-specific."""
    f = Feeder(DRY)
    f.run(f.device.set_feeding_plans([
        {"planId": 1, "executionTime": "07:30", "grainNum": 2},
    ]))
    _, msg = f.last()
    assert msg["plans"][0]["executionTime"] == "07:30"


def test_the_next_day_is_computed_in_utc_like_the_stored_time():
    """executionTime is UTC, so the date that pairs with it must be chosen on
    the same clock. Mixing a UTC plan time with a local clock picks the wrong
    day either side of midnight."""
    utc_now = datetime.datetime(2026, 8, 3, 23, 45, tzinfo=datetime.timezone.utc)
    assert next_execution_day("00:30", [1, 2, 3, 4, 5, 6, 7], utc_now) == "2026-08-04"


# --- capability gating covers every platform -------------------------------
#
# Measured 2026-08-06: 15 entities existed on the wet feeder whose backing
# attribute the device never sends - 8 switches, 6 selects and a detection
# event. They showed as unavailable and toggling them wrote an attribute the
# firmware ignores.


def test_wet_feeder_declares_no_camera_or_auger_only_capabilities():
    """The gates the platforms use. If any of these flip, dead entities come
    back on the plate feeder."""
    caps = Feeder(WET).device.profile.CAPABILITIES
    for cap in (
        const.CAP_DETECTION,       # camera, recording, motion/sound detection
        const.CAP_FEEDING_AUDIO,   # enableAudio; the wet feeder rings instead
        const.CAP_BUTTON_LOCK,     # autoChangeMode, disableHardwareButton
        const.CAP_SD_CARD,
        const.CAP_DISPENSE_PORTIONS,
    ):
        assert cap not in caps, cap


def test_dry_feeder_declares_them_all():
    caps = Feeder(DRY).device.profile.CAPABILITIES
    for cap in (
        const.CAP_DETECTION,
        const.CAP_FEEDING_AUDIO,
        const.CAP_BUTTON_LOCK,
        const.CAP_SD_CARD,
        const.CAP_DISPENSE_PORTIONS,
    ):
        assert cap in caps, cap


def test_expected_portions_reads_the_key_the_wire_actually_sends():
    """The wire sends expectGrainNum, without the "ed". The field map had only
    expectedGrainNum, so Expected Feed Portions never populated."""
    f = Feeder(DRY)
    f.receive(
        cmd="GRAIN_OUTPUT_EVENT", finished=True, planId=5717753, retried=0,
        type=1, actualGrainNum=2, expectGrainNum=2, execStep="GRAIN_END",
    )
    assert f.device.state["expected_grain_num"] == 2
    assert f.device.state["actual_grain_num"] == 2


def test_the_longer_spelling_still_maps():
    """Kept as an alias: the misspelling sat in the map long enough that some
    firmware may yet use it."""
    f = Feeder(DRY)
    f.receive(cmd="GRAIN_OUTPUT_EVENT", finished=True, expectedGrainNum=3)
    assert f.device.state["expected_grain_num"] == 3


def test_newly_mapped_diagnostics_reach_state():
    f = Feeder(DRY)
    f.receive(cmd="ATTR_PUSH_EVENT", bowlMode="SINGLE_BOWL", disableHardwareButton=True)
    assert f.device.state["bowl_mode"] == "SINGLE_BOWL"
    assert f.device.state["disable_hardware_button"] is True


def test_restart_reason_is_captured_from_the_start_event():
    f = Feeder(DRY)
    f.receive(
        cmd="DEVICE_START_EVENT", success=True, pid="PLAF203",
        softwareVersion="1.2.3", restartReason="other",
    )
    assert f.device.device_info["restart_reason"] == "other"


# --- liveness: cadence measured from the devices themselves ----------------


def test_offline_watchdog_clears_the_slowest_observed_heartbeat():
    """Measured 2026-08-06: PLAF203 beats every 72s, PLAF109 every 90s (p95
    91s). The watchdog was 81s, which sits *below* the PLAF109's cadence - so
    that feeder was marked offline on every cycle and every entity on it went
    unavailable roughly every 90 seconds."""
    slowest_observed = 91
    assert const.HEARTBEAT_WATCHDOG_SEC > slowest_observed
    # Three consecutive misses before declaring it gone.
    assert const.HEARTBEAT_WATCHDOG_SEC >= slowest_observed * 3


def test_a_periodic_device_start_does_not_re_request_state():
    """DEVICE_START_EVENT is not a boot event. Measured over 19 hours: it
    arrives every 30 minutes on a TCP session that never dropped, while the
    heartbeat counter ran 1285 -> 2069 with no reset. Treating each one as a
    restart cost a full attribute request 48 times a day."""
    f = Feeder(DRY)
    f.receive(cmd="HEARTBEAT", count=1285, rssi=-50, wifiType=1)
    assert f.device.online
    f.sent.clear()

    f.receive(cmd="DEVICE_START_EVENT", success=True, pid=DRY, softwareVersion="1.0")
    # It is still acknowledged - the device expects that - but nothing else.
    assert f.commands == ["DEVICE_START_EVENT"]
    assert "ATTR_GET_SERVICE" not in f.commands
    assert "NTP_SYNC" not in f.commands


def test_a_device_start_while_offline_still_refreshes():
    """A genuine (re)connection must still pull full state."""
    f = Feeder(DRY)
    assert not f.device.online
    f.receive(cmd="DEVICE_START_EVENT", success=True, pid=DRY, softwareVersion="1.0")
    assert "ATTR_GET_SERVICE" in f.commands


def test_a_heartbeat_counter_reset_still_counts_as_a_restart():
    """The counter resetting is the one reliable restart signal, so it must
    keep triggering a refresh even though DEVICE_START_EVENT no longer does."""
    f = Feeder(DRY)
    f.receive(cmd="HEARTBEAT", count=900, rssi=-50, wifiType=1)
    f.sent.clear()
    f.receive(cmd="HEARTBEAT", count=3, rssi=-50, wifiType=1)
    assert "ATTR_GET_SERVICE" in f.commands


# --- motor current, from the device's own diagnostic log -------------------
#
# Payloads copied verbatim from DEVICE_LOG_REPORT_EVENT captured 2026-08-06.


def test_motor_currents_are_extracted_from_the_log_report():
    """The device reports door and plate motor current in the same units as
    the doorStuckCurrent / plateStuckCurrent thresholds it publishes. The
    handler used to discard the whole payload."""
    f = Feeder(WET)
    f.receive(cmd="DEVICE_LOG_REPORT_EVENT", logs=[
        {"type": "sensor", "content": "io:19 state:0", "time": 1785942001000},
        {"type": "adc", "content": "door_adc=81", "time": 1785942003000},
        {"type": "adc", "content": "door_adc=71", "time": 1785942004000},
        {"type": "adc", "content": "plate_adc=523", "time": 1785942005000},
    ])
    # Latest reading of each wins, by the log's own timestamp.
    assert f.device.state["door_motor_current"] == 71
    assert f.device.state["plate_motor_current"] == 523
    assert f.device.state["door_motor_current_time"] == 1785942004000


def test_out_of_order_log_entries_keep_the_newest_reading():
    f = Feeder(WET)
    f.receive(cmd="DEVICE_LOG_REPORT_EVENT", logs=[
        {"type": "adc", "content": "door_adc=90", "time": 2000},
        {"type": "adc", "content": "door_adc=40", "time": 1000},
    ])
    assert f.device.state["door_motor_current"] == 90


def test_an_empty_log_report_changes_nothing():
    """37 of 44 reports carried no entries at all."""
    f = Feeder(WET)
    f.receive(cmd="DEVICE_LOG_REPORT_EVENT", logs=[])
    assert "door_motor_current" not in f.device.state


def test_unparseable_log_entries_are_skipped_not_fatal():
    f = Feeder(WET)
    f.receive(cmd="DEVICE_LOG_REPORT_EVENT", logs=[
        {"type": "adc", "content": "door_adc=notanumber", "time": 1},
        {"type": "adc", "content": "malformed", "time": 2},
        {"type": "net", "content": "discon=8", "time": 3},
        {"type": "sensor", "content": "Microtime=203", "time": 4},
        {"type": "adc", "content": "door_adc=55", "time": 5},
    ])
    assert f.device.state["door_motor_current"] == 55


def test_the_stall_threshold_is_published_but_not_alarmed_on():
    """A healthy plate rotation drew 523 against a threshold of 400 - starting
    torque exceeds running torque. Deriving a fault from that comparison would
    fire on every normal feed."""
    f = Feeder(WET)
    f.receive(cmd="ATTR_PUSH_EVENT", plateStuckCurrent=400, doorStuckCurrent=400)
    f.receive(cmd="DEVICE_LOG_REPORT_EVENT",
              logs=[{"type": "adc", "content": "plate_adc=523", "time": 1}])
    assert f.device.state["plate_stuck_current"] == 400
    assert f.device.state["plate_motor_current"] == 523
    # No derived fault state exists, deliberately.
    assert "plate_jammed" not in f.device.state
