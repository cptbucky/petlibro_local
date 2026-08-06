"""Switch entities for Petlibro Local."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import CAP_BUTTON_LOCK, CAP_DETECTION, CAP_FEEDING_AUDIO
from .entity import PetlibroEntity
from .coordinator import PetlibroCoordinator


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up Petlibro switches.

    Gated on capability rather than model. Every switch here was previously
    created for every feeder, so a plate feeder - which has no camera, no
    recording and no call-to-eat audio - carried eight switches that could
    never do anything: the attributes backing them are absent from everything
    the device reports, so they showed as unavailable and toggling them sent an
    attribute the firmware ignores.
    """
    coordinator: PetlibroCoordinator = entry.runtime_data
    device = coordinator.device

    # Reported by every model seen so far.
    entities: list[SwitchEntity] = [
        PetlibroAttrSwitch(coordinator, "Light", "light_switch", "lightSwitch", "mdi:led-on"),
        PetlibroAttrSwitch(coordinator, "Sound", "sound_switch", "soundSwitch", "mdi:volume-high"),
    ]

    # Call-to-eat audio played with a dispense. The plate feeder uses its
    # ringer instead and has no enableAudio attribute.
    if device.supports(CAP_FEEDING_AUDIO):
        entities.append(
            PetlibroAttrSwitch(coordinator, "Audio", "enable_audio", "enableAudio", "mdi:microphone")
        )

    # Camera hardware: recording, streaming and the detection features that
    # depend on it.
    if device.supports(CAP_DETECTION):
        entities += [
            PetlibroAttrSwitch(coordinator, "Camera", "camera_switch", "cameraSwitch", "mdi:camera"),
            PetlibroAttrSwitch(coordinator, "Video Recording", "video_record_switch", "videoRecordSwitch", "mdi:record-rec"),
            PetlibroAttrSwitch(coordinator, "Feeding Video", "feeding_video_switch", "feedingVideoSwitch", "mdi:filmstrip"),
            PetlibroAttrSwitch(coordinator, "Cloud Recording", "cloud_video_record_switch", "cloudVideoRecordSwitch", "mdi:cloud-upload"),
            PetlibroAttrSwitch(coordinator, "Motion Detection", "motion_detection_switch", "motionDetectionSwitch", "mdi:motion-sensor"),
            PetlibroAttrSwitch(coordinator, "Sound Detection", "sound_detection_switch", "soundDetectionSwitch", "mdi:ear-hearing"),
        ]

    # Physical controls on the unit itself.
    if device.supports(CAP_BUTTON_LOCK):
        entities += [
            PetlibroAttrSwitch(coordinator, "Auto Button Lock", "auto_change_mode", "autoChangeMode", "mdi:lock"),
            PetlibroAttrSwitch(coordinator, "Button Lock", "disable_hardware_button", "disableHardwareButton", "mdi:lock-outline"),
        ]

    async_add_entities(entities)


class PetlibroAttrSwitch(PetlibroEntity, SwitchEntity):
    """Generic switch that maps to a boolean device attribute."""

    def __init__(
        self,
        coordinator: PetlibroCoordinator,
        name: str,
        state_key: str,
        mqtt_key: str,
        icon: str,
    ) -> None:
        super().__init__(coordinator)
        self._attr_name = name
        self._state_key = state_key
        self._mqtt_key = mqtt_key
        self._attr_icon = icon

    @property
    def unique_id(self) -> str:
        return f"{self._device.serial}_{self._state_key}"

    @property
    def is_on(self) -> bool | None:
        return self.coordinator.data.get(self._state_key)

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._device.set_attributes(**{self._state_key: True})

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._device.set_attributes(**{self._state_key: False})
