"""Binary sensor entities for Petlibro Local."""

from __future__ import annotations

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import CAP_DISPENSE_PORTIONS, CAP_PET_PRESENCE, CAP_PLATE
from .entity import PetlibroEntity
from .coordinator import PetlibroCoordinator


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up Petlibro binary sensors."""
    coordinator: PetlibroCoordinator = entry.runtime_data
    device = coordinator.device

    entities: list[BinarySensorEntity] = [PetlibroOnlineSensor(coordinator)]

    # Hopper level and outlet are auger concepts; a plate feeder has neither.
    if device.supports(CAP_DISPENSE_PORTIONS):
        entities += [
            PetlibroFoodLevelSensor(coordinator),
            PetlibroGrainOutletSensor(coordinator),
        ]

    if device.supports(CAP_PET_PRESENCE):
        entities.append(PetlibroPetPresenceSensor(coordinator))

    if device.supports(CAP_PLATE):
        entities.append(PetlibroPlateJammedSensor(coordinator))

    async_add_entities(entities)


class PetlibroPetPresenceSensor(PetlibroEntity, BinarySensorEntity):
    """Whether a pet is at the bowl, per the feeder's infrared sensor."""

    _attr_name = "Pet Present"
    _attr_device_class = BinarySensorDeviceClass.PRESENCE
    _attr_icon = "mdi:cat"

    @property
    def unique_id(self) -> str:
        return f"{self._device.serial}_pet_present"

    @property
    def is_on(self) -> bool | None:
        return self._device.state.get("pet_present")


class PetlibroPlateJammedSensor(PetlibroEntity, BinarySensorEntity):
    """Plate failed to reach its home position.

    The firmware declines to actuate while this is true, so without it a jam
    presents only as a feed that silently never happens.
    """

    _attr_name = "Plate Jammed"
    _attr_device_class = BinarySensorDeviceClass.PROBLEM
    _attr_icon = "mdi:alert-circle"

    @property
    def unique_id(self) -> str:
        return f"{self._device.serial}_plate_jammed"

    @property
    def is_on(self) -> bool | None:
        zero_state = self._device.state.get("zero_state")
        if zero_state is None:
            return None
        return zero_state == "TIMEOUT"


class PetlibroOnlineSensor(PetlibroEntity, BinarySensorEntity):
    _attr_name = "Online"
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def unique_id(self) -> str:
        return f"{self._device.serial}_online"

    @property
    def is_on(self) -> bool:
        return self._device.online

    @property
    def available(self) -> bool:
        return True  # Always available — shows connectivity state


class PetlibroFoodLevelSensor(PetlibroEntity, BinarySensorEntity):
    _attr_name = "Food Level OK"
    _attr_device_class = BinarySensorDeviceClass.PROBLEM
    _attr_icon = "mdi:food-drumstick"

    @property
    def unique_id(self) -> str:
        return f"{self._device.serial}_food_level"

    @property
    def is_on(self) -> bool | None:
        """Problem sensor: ON when food is low."""
        surplus = self.coordinator.data.get("surplus_grain")
        if surplus is None:
            return None
        return not surplus  # surplus_grain=True means food OK, invert for problem sensor


class PetlibroGrainOutletSensor(PetlibroEntity, BinarySensorEntity):
    _attr_name = "Grain Outlet Blocked"
    _attr_device_class = BinarySensorDeviceClass.PROBLEM

    @property
    def unique_id(self) -> str:
        return f"{self._device.serial}_grain_outlet"

    @property
    def is_on(self) -> bool | None:
        """Problem sensor: ON when outlet is blocked."""
        state = self.coordinator.data.get("grain_outlet_state")
        if state is None:
            return None
        return not state  # grain_outlet_state=True means not blocked
