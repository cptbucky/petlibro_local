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
    f.run(f.device.dispense(portions=3))
    topic, msg = f.last()
    assert msg["cmd"] == "MANUAL_FEEDING_SERVICE"
    assert msg["grainNum"] == 3
    assert topic.endswith("/device/service/sub")


def test_wet_feeder_dispenses_by_referencing_a_plan():
    f = Feeder(WET)
    f.device.feeding_plans = [{"planId": 44122386, "feedingDuration": 240, "plate": 1}]
    f.run(f.device.dispense())
    _, msg = f.last()
    assert msg["cmd"] == "WET_FOOD_FEED_NOW_SERVICE"
    assert msg["planId"] == 44122386
    assert msg["feedingDuration"] == 240
    # A wet feeder has no notion of quantity.
    assert "grainNum" not in msg


def test_wet_feeder_never_sends_the_auger_command():
    """The firmware drops MANUAL_FEEDING_SERVICE silently, so sending it looks
    identical to success while nothing happens."""
    f = Feeder(WET)
    f.device.feeding_plans = [{"planId": 1, "feedingDuration": 60}]
    f.run(f.device.dispense())
    assert "MANUAL_FEEDING_SERVICE" not in f.commands


def test_wet_feeder_refuses_to_feed_without_a_plan():
    """Feeding borrows the plate from a plan, so with no plan there is nothing
    coherent to send - better to refuse than emit an invalid command."""
    f = Feeder(WET)
    f.run(f.device.dispense())
    assert f.sent == []


def test_wet_feeder_uses_explicit_plan_id_when_given():
    f = Feeder(WET)
    f.device.feeding_plans = [{"planId": 111, "feedingDuration": 60}]
    f.run(f.device.dispense(plan_id=222))
    _, msg = f.last()
    assert msg["planId"] == 222


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
def test_unknown_model_falls_back_to_auger_behaviour(pid):
    """Preserves the integration's historical default rather than leaving an
    untested feeder with no behaviour at all."""
    f = Feeder(pid)
    f.run(f.device.dispense(portions=1))
    assert f.last()[1]["cmd"] == "MANUAL_FEEDING_SERVICE"


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
