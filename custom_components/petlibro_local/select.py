"""Select entities for Petlibro Local."""

from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import CAP_AUDIO_TEST, CAP_DETECTION
from .entity import PetlibroEntity
from .coordinator import PetlibroCoordinator


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up Petlibro select entities.

    Every select here configures camera hardware - night vision, resolution,
    recording mode and the detection tuning that depends on the lens. A plate
    feeder has none of it and reports none of the backing attributes, so these
    were six permanently unavailable entities on that model.
    """
    coordinator: PetlibroCoordinator = entry.runtime_data
    device = coordinator.device
    entities: list[SelectEntity] = []

    # The wet feeder's call-to-eat ringer. NORMAL and SMART are the values
    # observed from the vendor app; SMART is the default this device shipped
    # with.
    if device.supports(CAP_AUDIO_TEST):
        entities.append(PetlibroRingerModeSelect(coordinator))

    if not device.supports(CAP_DETECTION):
        async_add_entities(entities)
        return

    entities += [
        PetlibroNightVisionSelect(coordinator),
        PetlibroResolutionSelect(coordinator),
        PetlibroVideoRecordModeSelect(coordinator),
        PetlibroMotionDetectionRangeSelect(coordinator),
        PetlibroMotionDetectionSensitivitySelect(coordinator),
        PetlibroSoundDetectionSensitivitySelect(coordinator),
    ]
    async_add_entities(entities)


class PetlibroRingerModeSelect(PetlibroEntity, SelectEntity):
    """How the feeder rings to call the animal at feeding time.

    Values observed from the vendor app on a PLAF109: NORMAL and SMART. SMART
    is what the device shipped with. Any other value the firmware may accept is
    unknown, so an unrecognised one is surfaced verbatim rather than hidden.
    """

    _attr_name = "Ringer Mode"
    _attr_icon = "mdi:bell-ring"
    _attr_options = ["Normal", "Smart"]

    _OPTION_MAP = {"NORMAL": "Normal", "SMART": "Smart"}
    _REVERSE_MAP = {"Normal": "NORMAL", "Smart": "SMART"}

    @property
    def unique_id(self) -> str:
        return f"{self._device.serial}_ringer_mode"

    @property
    def current_option(self) -> str | None:
        val = self.coordinator.data.get("ringer_mode")
        if val is None:
            return None
        return self._OPTION_MAP.get(str(val), str(val))

    async def async_select_option(self, option: str) -> None:
        await self._device.set_attributes(ringer_mode=self._REVERSE_MAP[option])


class PetlibroNightVisionSelect(PetlibroEntity, SelectEntity):
    _attr_name = "Night Vision"
    _attr_icon = "mdi:weather-night"
    _attr_options = ["Automatic", "On", "Off"]

    _OPTION_MAP = {"AUTOMATIC": "Automatic", "OPEN": "On", "CLOSE": "Off"}
    _REVERSE_MAP = {"Automatic": "AUTOMATIC", "On": "OPEN", "Off": "CLOSE"}

    @property
    def unique_id(self) -> str:
        return f"{self._device.serial}_night_vision"

    @property
    def current_option(self) -> str | None:
        val = self.coordinator.data.get("night_vision")
        if val is None:
            return None
        if isinstance(val, int):
            int_map = {0: "Automatic", 1: "On", 2: "Off"}
            return int_map.get(val)
        return self._OPTION_MAP.get(str(val))

    async def async_select_option(self, option: str) -> None:
        mqtt_val = self._REVERSE_MAP[option]
        await self._device.set_attributes(night_vision=mqtt_val)


class PetlibroResolutionSelect(PetlibroEntity, SelectEntity):
    _attr_name = "Resolution"
    _attr_icon = "mdi:video"
    _attr_options = ["720p", "1080p"]

    @property
    def unique_id(self) -> str:
        return f"{self._device.serial}_resolution"

    @property
    def current_option(self) -> str | None:
        val = self.coordinator.data.get("resolution")
        if val is None:
            return None
        if isinstance(val, int):
            return {0: "720p", 1: "1080p"}.get(val)
        return {"P720": "720p", "P1080": "1080p"}.get(str(val))

    async def async_select_option(self, option: str) -> None:
        mqtt_val = {"720p": "P720", "1080p": "P1080"}[option]
        await self._device.set_attributes(resolution=mqtt_val)


class PetlibroVideoRecordModeSelect(PetlibroEntity, SelectEntity):
    _attr_name = "Video Record Mode"
    _attr_icon = "mdi:record-rec"
    _attr_options = ["Continuous", "Motion Detection"]

    @property
    def unique_id(self) -> str:
        return f"{self._device.serial}_video_record_mode"

    @property
    def current_option(self) -> str | None:
        val = self.coordinator.data.get("video_record_mode")
        if val is None:
            return None
        if isinstance(val, int):
            return {0: "Continuous", 1: "Motion Detection"}.get(val)
        return {"CONTINUOUS": "Continuous", "MOTION_DETECTION": "Motion Detection"}.get(str(val))

    async def async_select_option(self, option: str) -> None:
        mqtt_val = {"Continuous": "CONTINUOUS", "Motion Detection": "MOTION_DETECTION"}[option]
        await self._device.set_attributes(video_record_mode=mqtt_val)


class PetlibroMotionDetectionRangeSelect(PetlibroEntity, SelectEntity):
    _attr_name = "Motion Detection Range"
    _attr_icon = "mdi:motion-sensor"
    _attr_options = ["Small", "Medium", "Large"]

    @property
    def unique_id(self) -> str:
        return f"{self._device.serial}_motion_detection_range"

    @property
    def current_option(self) -> str | None:
        val = self.coordinator.data.get("motion_detection_range")
        if val is None:
            return None
        if isinstance(val, int):
            return {0: "Small", 1: "Medium", 2: "Large"}.get(val)
        return {"SMALL": "Small", "MEDIUM": "Medium", "LARGE": "Large"}.get(str(val))

    async def async_select_option(self, option: str) -> None:
        mqtt_val = {"Small": "SMALL", "Medium": "MEDIUM", "Large": "LARGE"}[option]
        await self._device.set_attributes(motion_detection_range=mqtt_val)


class PetlibroMotionDetectionSensitivitySelect(PetlibroEntity, SelectEntity):
    _attr_name = "Motion Detection Sensitivity"
    _attr_icon = "mdi:motion-sensor"
    _attr_options = ["Low", "Medium", "High"]

    @property
    def unique_id(self) -> str:
        return f"{self._device.serial}_motion_detection_sensitivity"

    @property
    def current_option(self) -> str | None:
        val = self.coordinator.data.get("motion_detection_sensitivity")
        if val is None:
            return None
        if isinstance(val, int):
            return {0: "Low", 1: "Medium", 2: "High"}.get(val)
        return {"LOW": "Low", "MEDIUM": "Medium", "HIGH": "High"}.get(str(val))

    async def async_select_option(self, option: str) -> None:
        mqtt_val = {"Low": "LOW", "Medium": "MEDIUM", "High": "HIGH"}[option]
        await self._device.set_attributes(motion_detection_sensitivity=mqtt_val)


class PetlibroSoundDetectionSensitivitySelect(PetlibroEntity, SelectEntity):
    _attr_name = "Sound Detection Sensitivity"
    _attr_icon = "mdi:ear-hearing"
    _attr_options = ["Low", "Medium", "High"]

    @property
    def unique_id(self) -> str:
        return f"{self._device.serial}_sound_detection_sensitivity"

    @property
    def current_option(self) -> str | None:
        val = self.coordinator.data.get("sound_detection_sensitivity")
        if val is None:
            return None
        if isinstance(val, int):
            return {0: "Low", 1: "Medium", 2: "High"}.get(val)
        return {"LOW": "Low", "MEDIUM": "Medium", "HIGH": "High"}.get(str(val))

    async def async_select_option(self, option: str) -> None:
        mqtt_val = {"Low": "LOW", "Medium": "MEDIUM", "High": "HIGH"}[option]
        await self._device.set_attributes(sound_detection_sensitivity=mqtt_val)
