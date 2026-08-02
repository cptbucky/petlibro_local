"""MQTT topic construction for Petlibro devices."""

from __future__ import annotations

from ..const import DEVICE_PRODUCT_ID


class PetlibroTopics:
    """Build MQTT topics for a specific device."""

    def __init__(self, serial: str, product_id: str = DEVICE_PRODUCT_ID) -> None:
        self._serial = serial
        self._product_id = product_id
        self._base = f"dl/{product_id}/{serial}/device"

    @property
    def product_id(self) -> str:
        """Model currently used to build topics."""
        return self._product_id

    def set_product_id(self, product_id: str) -> None:
        """Rebind topics to the model the device actually publishes under.

        Only the MQTT-discovery config path can learn the model up front; the
        sniffer and manual paths fall back to DEVICE_PRODUCT_ID. This lets the
        coordinator correct it from an observed topic.
        """
        if not product_id or product_id == self._product_id:
            return
        self._product_id = product_id
        self._base = f"dl/{product_id}/{self._serial}/device"

    @property
    def subscribe_all(self) -> str:
        """Wildcard topic to receive all messages from the device."""
        return f"{self._base}/+/post"

    @property
    def subscribe_any_product(self) -> str:
        """Receive this device's messages whatever model it reports as.

        Matches on serial alone, which is unique per device. Guards against a
        wrong or defaulted product_id silently yielding zero messages -
        subscribing to a topic nobody publishes to is not an error in MQTT.
        """
        return f"dl/+/{self._serial}/device/+/post"

    # Device → Server (we subscribe to these)
    @property
    def heart_post(self) -> str:
        return f"{self._base}/heart/post"

    @property
    def ntp_post(self) -> str:
        return f"{self._base}/ntp/post"

    @property
    def ota_post(self) -> str:
        return f"{self._base}/ota/post"

    @property
    def config_post(self) -> str:
        return f"{self._base}/config/post"

    @property
    def event_post(self) -> str:
        return f"{self._base}/event/post"

    @property
    def service_post(self) -> str:
        return f"{self._base}/service/post"

    @property
    def system_post(self) -> str:
        return f"{self._base}/system/post"

    # Server → Device (we publish to these)
    @property
    def ntp_sub(self) -> str:
        return f"{self._base}/ntp/sub"

    @property
    def ota_sub(self) -> str:
        return f"{self._base}/ota/sub"

    @property
    def config_sub(self) -> str:
        return f"{self._base}/config/sub"

    @property
    def event_sub(self) -> str:
        return f"{self._base}/event/sub"

    @property
    def service_sub(self) -> str:
        return f"{self._base}/service/sub"

    @property
    def system_sub(self) -> str:
        return f"{self._base}/system/sub"

    @property
    def broadcast_sub(self) -> str:
        return f"{self._base}/broadcast/sub"
