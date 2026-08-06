"""PetlibroDevice — MQTT state management and command layer.

Replaces plaf203's Client+Backend monolith. Subscribes to all device
topics, maintains consolidated state dict, handles heartbeat and NTP,
and fires callbacks on state changes.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any, Callable

from .const import (
    CMD_ATTR_GET_SERVICE,
    CMD_ATTR_PUSH_EVENT,
    CMD_ATTR_SET_SERVICE,
    CMD_BINDING,
    CMD_DEVICE_START_EVENT,
    CMD_ERROR_EVENT,
    CMD_GET_CONFIG,
    CMD_HEARTBEAT,
    CMD_NTP,
    CMD_NTP_SYNC,
    CMD_RESET,
    CODE_OK,
    HEARTBEAT_INTERVAL_SEC,
    HEARTBEAT_WATCHDOG_SEC,
    NTP_DRIFT_THRESHOLD_SEC,
)
from .protocol.codec import (
    build_attr_get,
    build_command,
    build_function_test,
    build_get_some_attrs,
    build_ntp_response,
    build_ntp_sync,
    build_response,
    parse_payload,
    timestamp_now_ms,
    timezone_offset_hours,
)
from .feeders import get_profile
from .protocol.messages import normalize_payload
from .protocol.topics import PetlibroTopics

_LOGGER = logging.getLogger(__name__)

StateCallback = Callable[["PetlibroDevice"], None]


class PetlibroDevice:
    """Manages MQTT communication with a single Petlibro feeder."""

    def __init__(
        self,
        serial: str,
        mqtt_publish: Callable[[str, str], asyncio.coroutines],
        on_state_changed: StateCallback | None = None,
        product_id: str | None = None,
    ) -> None:
        self.serial = serial
        # product_id is optional so config entries created before it was stored
        # keep working; PetlibroTopics applies DEVICE_PRODUCT_ID in that case.
        self.product_id = product_id
        self.topics = (
            PetlibroTopics(serial, product_id) if product_id else PetlibroTopics(serial)
        )
        self._mqtt_publish = mqtt_publish
        self._on_state_changed = on_state_changed

        # Model-specific behaviour is composed, not inherited. The profile
        # supplies the commands this model understands and how it feeds; its
        # handlers extend the shared set rather than overriding it.
        self.profile = get_profile(product_id)
        self._handlers: dict[str, Callable] = {
            **self._SHARED_HANDLERS,
            **self.profile.handlers(),
        }

        # Consolidated device state
        self.state: dict[str, Any] = {}
        self.feeding_plans: list[dict[str, Any]] = []
        self.device_info: dict[str, Any] = {}

        # Online tracking
        self.online = False
        self._last_heartbeat: float = 0
        self._heartbeat_count: int | None = None
        self._heartbeat_task: asyncio.Task | None = None

    @property
    def name(self) -> str:
        return f"Petlibro {self.serial[-6:]}"

    async def start(self) -> None:
        """Start the heartbeat watchdog."""
        self._heartbeat_task = asyncio.ensure_future(self._heartbeat_watchdog())

    async def stop(self) -> None:
        """Stop background tasks."""
        if self._heartbeat_task:
            self._heartbeat_task.cancel()
            try:
                await self._heartbeat_task
            except asyncio.CancelledError:
                pass

    async def handle_message(self, topic: str, payload_raw: str | bytes) -> None:
        """Dispatch an incoming MQTT message from the device."""
        try:
            payload = parse_payload(payload_raw)
        except (json.JSONDecodeError, ValueError):
            _LOGGER.warning("Invalid JSON from %s on %s", self.serial, topic)
            return

        cmd = payload.get("cmd")
        if not cmd:
            _LOGGER.debug("Message without cmd on %s: %s", topic, payload)
            return

        _LOGGER.debug("Device %s cmd=%s", self.serial, cmd)

        handler = self._handlers.get(cmd)
        if handler:
            await handler(self, payload)
        else:
            _LOGGER.debug("Unhandled cmd %s from %s", cmd, self.serial)

    # --- Command methods (server → device) ---

    async def request_full_state(self) -> None:
        """Request full attribute snapshot from device."""
        await self._publish(self.topics.service_sub, build_attr_get())

    def set_product_id(self, product_id: str) -> None:
        """Adopt a model learned after construction.

        Only the MQTT-discovery config path knows the model up front; the
        sniffer and manual paths default it. Rebinding topics alone is not
        enough - the profile decides which commands are understood and which
        entities exist, so it has to be recomposed too.
        """
        if not product_id or product_id == self.product_id:
            return
        self.product_id = product_id
        self.topics.set_product_id(product_id)
        self.profile = get_profile(product_id)
        self._handlers = {**self._SHARED_HANDLERS, **self.profile.handlers()}
        _LOGGER.info(
            "Device %s is a %s; using the %s profile",
            self.serial, product_id, type(self.profile).__name__,
        )

    def supports(self, capability: str) -> bool:
        """Whether this model exposes a capability (see const.CAP_*)."""
        return capability in self.profile.CAPABILITIES

    # Feeding is model-specific: there is no shared verb. Auger feeders
    # dispense a quantity; wet feeders run a plate/door sequence with no
    # quantity at all. Entity platforms pick the right one via supports().
    # These thin wrappers exist because `manual_feed` is a documented service,
    # so a user automation can call it against any device.

    async def manual_feed(self, portions: int = 1) -> None:
        """Dispense a quantity of kibble. Auger feeders only."""
        dispense = getattr(self.profile, "dispense", None)
        if dispense is None:
            if getattr(self.profile, "serve_plate", None) is not None:
                _LOGGER.error(
                    "Device %s is a %s: it feeds by serving a plate, not by "
                    "quantity. Use the Serve Plate buttons on the device page "
                    "instead of manual_feed.",
                    self.serial,
                    self.profile.MODEL_NAME,
                )
            else:
                # No profile for this model, so we do not know how it feeds.
                # Emitting a guess would be worse than refusing: an unsupported
                # command is dropped silently and looks like it worked.
                _LOGGER.error(
                    "Device %s has no feeder profile (%s), so feeding is "
                    "disabled: sending a feed command we cannot verify would "
                    "silently do nothing. Please open an issue with this "
                    "device's product id so a profile can be added.",
                    self.serial,
                    self.profile.MODEL_NAME,
                )
            return
        await dispense(self, portions=portions)

    async def serve_plate(self, plate: int) -> None:
        """Serve a carousel plate. Wet food feeders only."""
        serve = getattr(self.profile, "serve_plate", None)
        if serve is None:
            _LOGGER.error(
                "Device %s is a %s: it has no plates. Use manual_feed to "
                "dispense a quantity instead.",
                self.serial,
                self.profile.MODEL_NAME,
            )
            return
        await serve(self, plate)

    async def ring_bell(self) -> None:
        """Play the feeder's call-to-eat audio."""
        await self._publish(self.topics.service_sub, build_function_test("AUDIO"))

    async def request_attrs(self, attr_keys: list[str]) -> None:
        """Request specific attributes instead of a full snapshot."""
        await self._publish(self.topics.service_sub, build_get_some_attrs(attr_keys))

    async def set_attributes(self, **attrs: Any) -> None:
        """Set device attributes (sparse). Use camelCase MQTT keys."""
        from .protocol.messages import denormalize_attrs
        mqtt_attrs = denormalize_attrs(**attrs)
        from .protocol.codec import build_attr_set
        await self._publish(self.topics.service_sub, build_attr_set(**mqtt_attrs))

    async def reboot(self) -> None:
        """Reboot the device."""
        from .protocol.codec import build_device_reboot
        await self._publish(self.topics.system_sub, build_device_reboot())

    async def factory_restore(self) -> None:
        """Factory restore the device."""
        from .protocol.codec import build_restore
        await self._publish(self.topics.system_sub, build_restore())

    async def set_feeding_plans(self, plans: list[dict]) -> None:
        """Set feeding plans on the device.

        The plan command is model-specific: an auger feeder uses
        FEEDING_PLAN_SERVICE, a plate feeder WET_GRAIN_FEEDING_PLAN_SERVICE.
        Sending the wrong one is silently dropped by the firmware, so the
        plans would never reach the device.
        """
        await self._publish(self.topics.service_sub, self.profile.build_plans(plans))

    # --- Internal message handlers ---

    async def _handle_heartbeat(self, payload: dict) -> None:
        """Process heartbeat — update online status and check for reboot."""
        self._last_heartbeat = time.monotonic()
        was_online = self.online
        self.online = True

        count = payload.get("count")
        rssi = payload.get("rssi")

        # Detect device reboot (counter resets)
        if self._heartbeat_count is not None and count is not None:
            if count < self._heartbeat_count:
                _LOGGER.info("Device %s rebooted (count reset)", self.serial)
                await self._on_device_online()

        self._heartbeat_count = count

        # Update state with heartbeat data
        state_update = normalize_payload(payload, self.profile.FIELDS)
        if state_update:
            self.state.update(state_update)

        if not was_online:
            await self._on_device_online()

        self._notify_state_changed()

    async def _handle_ntp(self, payload: dict) -> None:
        """Respond to NTP time check from device."""
        device_ts = payload.get("ts", 0)
        server_ts = timestamp_now_ms()
        drift_sec = abs(server_ts - device_ts) / 1000

        calibrate = drift_sec > NTP_DRIFT_THRESHOLD_SEC
        if calibrate:
            _LOGGER.info(
                "Device %s clock drift %.1fs, recalibrating", self.serial, drift_sec
            )

        await self._publish(self.topics.ntp_sub, build_ntp_response(calibrate))

    async def _handle_ntp_sync(self, payload: dict) -> None:
        """Handle NTP_SYNC response from device after calibration."""
        device_ts = payload.get("ts", 0)
        server_ts = timestamp_now_ms()
        drift_sec = abs(server_ts - device_ts) / 1000

        if drift_sec > NTP_DRIFT_THRESHOLD_SEC:
            _LOGGER.warning(
                "Device %s still drifted %.1fs after NTP sync", self.serial, drift_sec
            )

    async def _handle_device_start(self, payload: dict) -> None:
        """Handle DEVICE_START_EVENT.

        Despite the name this is **not** a boot event. Measured over 19 hours
        of vendor traffic (2026-08-06): it arrives every 30 minutes on a TCP
        session that never dropped, while the heartbeat counter ran from 1285
        to 2069 without a single reset. A device that had restarted would have
        reset that counter.

        So it is a periodic re-announce, and re-requesting the full attribute
        set on every one of them is 48 needless round trips a day. The refresh
        now happens only when the device was not already online - a genuine
        (re)connection - or when the heartbeat counter shows it really did
        restart, which `_handle_heartbeat` detects.
        """
        msg_id = payload.get("msgId")
        self.device_info = normalize_payload(payload, self.profile.FIELDS)
        was_online = self.online
        self.online = True
        self._last_heartbeat = time.monotonic()

        # Acknowledge device start
        await self._publish(
            self.topics.event_sub,
            build_response(CMD_DEVICE_START_EVENT, msg_id),
        )

        if not was_online:
            await self._on_device_online()
        self._notify_state_changed()

    async def _handle_attr_push(self, payload: dict) -> None:
        """Sparse attribute update from device."""
        msg_id = payload.get("msgId")

        # Acknowledge
        await self._publish(
            self.topics.event_sub,
            build_response(CMD_ATTR_PUSH_EVENT, msg_id),
        )

        # Update state
        state_update = normalize_payload(payload, self.profile.FIELDS)
        self.state.update(state_update)
        self._notify_state_changed()

    async def _handle_attr_get_response(self, payload: dict) -> None:
        """Full attribute snapshot from device (response to our request)."""
        state_update = normalize_payload(payload, self.profile.FIELDS)
        self.state.update(state_update)
        self._notify_state_changed()

    async def _handle_attr_set_response(self, payload: dict) -> None:
        """Device acknowledged our attribute change."""
        code = payload.get("code", -1)
        if code != CODE_OK:
            _LOGGER.warning(
                "Device %s ATTR_SET_SERVICE failed: code=%s", self.serial, code
            )

    async def _handle_error_event(self, payload: dict) -> None:
        """Device reported an error."""
        msg_id = payload.get("msgId")
        error_code = payload.get("errorCode", "unknown")
        _LOGGER.warning("Device %s error: %s", self.serial, error_code)

        # Acknowledge
        await self._publish(
            self.topics.event_sub,
            build_response(CMD_ERROR_EVENT, msg_id),
        )

        state_update = normalize_payload(payload, self.profile.FIELDS)
        self.state.update(state_update)
        self._notify_state_changed()

    async def _handle_get_config(self, payload: dict) -> None:
        """Device requesting config.

        Deliberately does not reply. config/sub is the server-to-device
        *request* channel, so echoing cmd=GET_CONFIG back on it is
        indistinguishable from a fresh request: the device answers, we echo
        again, and the two sit in a loop. Measured at ~0.8 messages/sec in each
        direction until the integration was stopped.

        The vendor cloud never sends GET_CONFIG to the device at all - it
        pushes DEVICE_CONFIG_SYNC, unprompted - so there is nothing for a local
        broker to usefully answer here.
        """
        _LOGGER.debug(
            "Device %s requested config; nothing to send locally", self.serial
        )

    async def _handle_binding(self, payload: dict) -> None:
        """Device binding request — acknowledge."""
        msg_id = payload.get("msgId")
        await self._publish(
            self.topics.system_sub,
            build_response(CMD_BINDING, msg_id),
        )

    async def _handle_reset(self, payload: dict) -> None:
        """Device is being factory reset."""
        msg_id = payload.get("msgId")
        _LOGGER.info("Device %s factory reset", self.serial)
        await self._publish(
            self.topics.system_sub,
            build_response(CMD_RESET, msg_id),
        )

    # --- Handler dispatch table ---

    # Commands every model implements. Model-specific commands come from the
    # composed feeder profile and are merged into self._handlers at init.
    _SHARED_HANDLERS: dict[str, Callable] = {
        CMD_HEARTBEAT: _handle_heartbeat,
        CMD_NTP: _handle_ntp,
        CMD_NTP_SYNC: _handle_ntp_sync,
        CMD_DEVICE_START_EVENT: _handle_device_start,
        CMD_ATTR_PUSH_EVENT: _handle_attr_push,
        CMD_ATTR_GET_SERVICE: _handle_attr_get_response,
        CMD_ATTR_SET_SERVICE: _handle_attr_set_response,
        CMD_ERROR_EVENT: _handle_error_event,
        CMD_GET_CONFIG: _handle_get_config,
        CMD_BINDING: _handle_binding,
        CMD_RESET: _handle_reset,
    }

    # --- Internal helpers ---

    async def _on_device_online(self) -> None:
        """Called when device first comes online or reboots."""
        _LOGGER.info("Device %s online, requesting state", self.serial)
        # Send NTP sync first
        await self._publish(self.topics.ntp_sub, build_ntp_sync())
        # Then request full state
        await asyncio.sleep(0.5)
        await self.request_full_state()

    async def _heartbeat_watchdog(self) -> None:
        """Periodically check if device is still sending heartbeats."""
        while True:
            await asyncio.sleep(HEARTBEAT_INTERVAL_SEC)
            if self._last_heartbeat > 0:
                elapsed = time.monotonic() - self._last_heartbeat
                if elapsed > HEARTBEAT_WATCHDOG_SEC and self.online:
                    _LOGGER.warning(
                        "Device %s heartbeat timeout (%.0fs)", self.serial, elapsed
                    )
                    self.online = False
                    self._notify_state_changed()

    async def _publish(self, topic: str, payload: str) -> None:
        """Publish an MQTT message."""
        await self._mqtt_publish(topic, payload)

    def _notify_state_changed(self) -> None:
        """Notify coordinator of state change."""
        if self._on_state_changed:
            self._on_state_changed(self)
