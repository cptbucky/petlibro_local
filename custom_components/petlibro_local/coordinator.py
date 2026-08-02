"""PetlibroCoordinator — push-based coordinator using MQTT."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .const import (
    CONF_PRODUCT_ID,
    CONF_SERIAL,
    DOMAIN,
)
from .device import PetlibroDevice

_LOGGER = logging.getLogger(__name__)


class PetlibroCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Coordinate Petlibro device state via MQTT.

    Uses push-based updates — no polling. State updates arrive via MQTT
    messages and are pushed to entities via async_set_updated_data().
    """

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=f"petlibro_{entry.data[CONF_SERIAL][-6:]}",
            # No update_interval — we're push-based
        )
        self._entry = entry
        self._serial = entry.data[CONF_SERIAL]

        # .get() rather than [] — entries created before CONF_PRODUCT_ID existed
        # have no such key, and would otherwise raise KeyError on startup.
        self.device = PetlibroDevice(
            serial=self._serial,
            mqtt_publish=self._mqtt_publish,
            on_state_changed=self._on_device_state_changed,
            product_id=entry.data.get(CONF_PRODUCT_ID),
        )

        self._mqtt_client: Any = None
        self._unsubscribe: list = []

    @property
    def entry(self) -> ConfigEntry:
        """Return the config entry."""
        return self._entry

    async def async_setup(self) -> None:
        """Set up MQTT connection and subscribe to device topics."""
        from homeassistant.components import mqtt

        # Subscribe by serial, wildcarding the model segment. The model is only
        # known up front via the MQTT-discovery config path; the sniffer and
        # manual paths default it, and a wrong model means zero messages with
        # no error. The real one is learned in _on_mqtt_message below.
        topic = self.device.topics.subscribe_any_product
        _LOGGER.info("Subscribing to %s", topic)

        unsub = await mqtt.async_subscribe(
            self.hass,
            topic,
            self._on_mqtt_message,
            qos=0,
        )
        self._unsubscribe.append(unsub)

        # Start device heartbeat watchdog
        await self.device.start()

        # Set initial data
        self.async_set_updated_data(self.device.state)

    @callback
    def _on_mqtt_message(self, msg) -> None:
        """Handle incoming MQTT message from HA's MQTT component."""
        # dl/{product_id}/{serial}/device/{kind}/post
        parts = msg.topic.split("/")
        if len(parts) >= 3 and parts[1] and parts[1] != "+":
            self._learn_product_id(parts[1].upper())
        self.hass.async_create_task(
            self.device.handle_message(msg.topic, msg.payload)
        )

    @callback
    def _learn_product_id(self, product_id: str) -> None:
        """Adopt the model the device actually publishes under.

        Subscribing wildcards the model, but commands we publish must use the
        exact one, so correct the topic builder as soon as we observe it. Also
        persisted so the stored entry stops being wrong.
        """
        if product_id == self.device.topics.product_id:
            return
        _LOGGER.info(
            "Device %s reports model %s (was %s) - correcting topics",
            self._serial,
            product_id,
            self.device.topics.product_id,
        )
        # Recomposes the profile too, not just the topics.
        self.device.set_product_id(product_id)
        if self._entry.data.get(CONF_PRODUCT_ID) != product_id:
            self.hass.config_entries.async_update_entry(
                self._entry,
                data={**self._entry.data, CONF_PRODUCT_ID: product_id},
            )

    async def _mqtt_publish(self, topic: str, payload: str) -> None:
        """Publish an MQTT message via HA's MQTT component."""
        from homeassistant.components import mqtt

        await mqtt.async_publish(
            self.hass,
            topic,
            payload,
            qos=0,
            retain=False,
        )

    @callback
    def _on_device_state_changed(self, device: PetlibroDevice) -> None:
        """Called by PetlibroDevice when state changes."""
        self.async_set_updated_data(dict(device.state))

    async def _async_update_data(self) -> dict[str, Any]:
        """Not used — we're push-based. Return current state."""
        return dict(self.device.state)

    async def async_shutdown(self) -> None:
        """Clean up on unload."""
        await self.device.stop()
        for unsub in self._unsubscribe:
            unsub()
        self._unsubscribe.clear()
