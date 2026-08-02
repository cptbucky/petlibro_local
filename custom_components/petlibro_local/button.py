"""Button entities for Petlibro Local."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import CAP_AUDIO_TEST, CAP_DISPENSE_PLATE, CAP_DISPENSE_PORTIONS
from .entity import PetlibroEntity
from .coordinator import PetlibroCoordinator


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up Petlibro buttons."""
    coordinator: PetlibroCoordinator = entry.runtime_data
    device = coordinator.device

    entities: list[ButtonEntity] = [
        PetlibroRebootButton(coordinator),
        PetlibroFactoryResetButton(coordinator),
    ]

    # Gated on capability rather than model, so an untested feeder gets the
    # entities its protocol actually supports.
    if device.supports(CAP_DISPENSE_PORTIONS):
        entities.append(PetlibroDispenseButton(coordinator))

    if device.supports(CAP_DISPENSE_PLATE):
        # One button per physical plate. A single "feed" button would have to
        # guess which plate, and the carousel only has PLATE_COUNT of them.
        entities += [
            PetlibroServePlateButton(coordinator, plate)
            for plate in range(1, device.profile.PLATE_COUNT + 1)
        ]

    if device.supports(CAP_AUDIO_TEST):
        entities.append(PetlibroRingBellButton(coordinator))

    async_add_entities(entities)


class PetlibroDispenseButton(PetlibroEntity, ButtonEntity):
    """Dispense a single portion. Auger feeders only."""

    _attr_name = "Dispense Food"
    _attr_icon = "mdi:food"

    @property
    def unique_id(self) -> str:
        return f"{self._device.serial}_dispense"

    async def async_press(self) -> None:
        await self._device.manual_feed(portions=1)


class PetlibroServePlateButton(PetlibroEntity, ButtonEntity):
    """Serve one plate of the carousel.

    Pressing this starts a sequence the device runs itself: pause
    refrigeration, rotate to the plate, open the door. Progress arrives as
    execStep events, so the Feeding Step sensor tracks it.
    """

    _attr_icon = "mdi:silverware-fork-knife"

    def __init__(self, coordinator: PetlibroCoordinator, plate: int) -> None:
        super().__init__(coordinator)
        self._plate = plate
        self._attr_name = f"Serve Plate {plate}"

    @property
    def unique_id(self) -> str:
        return f"{self._device.serial}_serve_plate_{self._plate}"

    async def async_press(self) -> None:
        await self._device.serve_plate(self._plate)


class PetlibroRingBellButton(PetlibroEntity, ButtonEntity):
    """Play the feeder's call-to-eat audio."""

    _attr_name = "Ring Bell"
    _attr_icon = "mdi:bell-ring"

    @property
    def unique_id(self) -> str:
        return f"{self._device.serial}_ring_bell"

    async def async_press(self) -> None:
        await self._device.ring_bell()


class PetlibroRebootButton(PetlibroEntity, ButtonEntity):
    _attr_name = "Reboot"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_icon = "mdi:restart"

    @property
    def unique_id(self) -> str:
        return f"{self._device.serial}_reboot"

    async def async_press(self) -> None:
        await self._device.reboot()


class PetlibroFactoryResetButton(PetlibroEntity, ButtonEntity):
    _attr_name = "Factory Reset"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_icon = "mdi:factory"

    @property
    def unique_id(self) -> str:
        return f"{self._device.serial}_factory_reset"

    async def async_press(self) -> None:
        await self._device.factory_restore()
