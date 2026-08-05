"""Sensor entities for Petlibro Local."""

from __future__ import annotations

import datetime

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    PERCENTAGE,
    SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
    UnitOfTemperature,
    EntityCategory,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    CAP_DISPENSE_PLATE,
    CAP_DISPENSE_PORTIONS,
    CAP_PLATE,
    CAP_SD_CARD,
    CAP_TEMPERATURE,
)
from .entity import PetlibroEntity
from .coordinator import PetlibroCoordinator

DAY_NAMES = {
    1: "Mon", 2: "Tue", 3: "Wed", 4: "Thu",
    5: "Fri", 6: "Sat", 7: "Sun",
}


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up Petlibro sensors."""
    coordinator: PetlibroCoordinator = entry.runtime_data
    device = coordinator.device

    entities: list[SensorEntity] = [
        PetlibroBatterySensor(coordinator),
        PetlibroWifiRssiSensor(coordinator),
        PetlibroVolumeSensor(coordinator),
        PetlibroErrorCodeSensor(coordinator),
        PetlibroFirmwareVersionSensor(coordinator),
        PetlibroPowerModeSensor(coordinator),
        PetlibroPowerTypeSensor(coordinator),
        PetlibroFeedingScheduleSensor(coordinator),
    ]

    # Onboard storage exists only on camera models. A plate feeder never sends
    # an sdCard* attribute, so these sat permanently unavailable on it.
    if device.supports(CAP_SD_CARD):
        entities += [
            PetlibroSdCardCapacitySensor(coordinator),
            PetlibroSdCardUsedSensor(coordinator),
        ]

    # Cooled models report cabinet temperature on the heartbeat.
    if device.supports(CAP_TEMPERATURE):
        entities.append(PetlibroTemperatureSensor(coordinator))

    # Auger-only: a wet feeder reports no grain attributes at all.
    if device.supports(CAP_DISPENSE_PORTIONS):
        entities += [
            PetlibroMotorStateSensor(coordinator),
            PetlibroGrainOutputTypeSensor(coordinator),
            PetlibroActualGrainSensor(coordinator),
            PetlibroExpectedGrainSensor(coordinator),
            PetlibroGrainExecStepSensor(coordinator),
        ]

    # Plate feeders: position, feeding phase, and the homing diagnostic.
    if device.supports(CAP_PLATE):
        entities += [
            PetlibroPlatePositionSensor(coordinator),
            PetlibroFeedingStepSensor(coordinator),
            PetlibroPlateHomingSensor(coordinator),
        ]

    async_add_entities(entities)


class PetlibroPlatePositionSensor(PetlibroEntity, SensorEntity):
    """Which plate of the carousel is currently in position."""

    _attr_name = "Plate Position"
    _attr_icon = "mdi:rotate-right"

    @property
    def unique_id(self) -> str:
        return f"{self._device.serial}_plate_position"

    @property
    def native_value(self):
        return self._device.state.get("plate_position")


class PetlibroFeedingStepSensor(PetlibroEntity, SensorEntity):
    """Current phase of a feed cycle.

    A wet feed is not instantaneous: the device pauses refrigeration, rotates
    the plate, opens the door, then shuts it again - reporting GRAIN_THAW,
    GRAIN_START, OPEN_DOOR, CLOSE_DOOR then GRAIN_END as it goes.

    Only GRAIN_END carries finished=true. CLOSE_DOOR arrives immediately
    before it with finished=false, so treating any step other than GRAIN_END
    as "still feeding" is correct.
    """

    _attr_name = "Feeding Step"
    _attr_icon = "mdi:progress-clock"

    @property
    def unique_id(self) -> str:
        return f"{self._device.serial}_feeding_step"

    @property
    def native_value(self):
        return self._device.state.get("wet_exec_step")


class PetlibroPlateHomingSensor(PetlibroEntity, SensorEntity):
    """Plate homing result (zeroState).

    Worth surfacing: the firmware silently declines to actuate unless this
    reads SUCCESS, so a jammed or misseated plate otherwise presents only as a
    feed that never happens and an app that times out with no explanation.
    """

    _attr_name = "Plate Homing"
    _attr_icon = "mdi:target"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def unique_id(self) -> str:
        return f"{self._device.serial}_plate_homing"

    @property
    def native_value(self):
        return self._device.state.get("zero_state")


class PetlibroBatterySensor(PetlibroEntity, SensorEntity):
    _attr_name = "Battery"
    _attr_device_class = SensorDeviceClass.BATTERY
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = PERCENTAGE

    @property
    def unique_id(self) -> str:
        return f"{self._device.serial}_battery"

    @property
    def native_value(self):
        return self.coordinator.data.get("battery_percent")


class PetlibroWifiRssiSensor(PetlibroEntity, SensorEntity):
    _attr_name = "WiFi Signal"
    _attr_device_class = SensorDeviceClass.SIGNAL_STRENGTH
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = SIGNAL_STRENGTH_DECIBELS_MILLIWATT
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def unique_id(self) -> str:
        return f"{self._device.serial}_wifi_rssi"

    @property
    def native_value(self):
        return self.coordinator.data.get("wifi_rssi")


class PetlibroTemperatureSensor(PetlibroEntity, SensorEntity):
    """Cabinet temperature of a refrigerated feeder.

    Arrives on the heartbeat rather than in an attribute push, so it updates
    roughly every 90 seconds without needing to be polled.
    """

    _attr_name = "Temperature"
    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS

    @property
    def unique_id(self) -> str:
        return f"{self._device.serial}_temperature"

    @property
    def native_value(self):
        return self.coordinator.data.get("temperature")


class PetlibroMotorStateSensor(PetlibroEntity, SensorEntity):
    _attr_name = "Motor State"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def unique_id(self) -> str:
        return f"{self._device.serial}_motor_state"

    @property
    def native_value(self):
        return self.coordinator.data.get("motor_state")


class PetlibroVolumeSensor(PetlibroEntity, SensorEntity):
    _attr_name = "Volume"
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def unique_id(self) -> str:
        return f"{self._device.serial}_volume"

    @property
    def native_value(self):
        return self.coordinator.data.get("volume")


class PetlibroGrainOutputTypeSensor(PetlibroEntity, SensorEntity):
    _attr_name = "Last Feed Type"

    @property
    def unique_id(self) -> str:
        return f"{self._device.serial}_grain_output_type"

    @property
    def native_value(self):
        val = self.coordinator.data.get("grain_output_type")
        if val is None:
            return None
        type_map = {0: "idle", 1: "scheduled", 2: "manual_app", 3: "manual_button"}
        return type_map.get(val, str(val))


class PetlibroActualGrainSensor(PetlibroEntity, SensorEntity):
    _attr_name = "Last Feed Portions"
    _attr_state_class = SensorStateClass.MEASUREMENT

    @property
    def unique_id(self) -> str:
        return f"{self._device.serial}_actual_grain"

    @property
    def native_value(self):
        return self.coordinator.data.get("actual_grain_num")


class PetlibroExpectedGrainSensor(PetlibroEntity, SensorEntity):
    _attr_name = "Expected Feed Portions"
    _attr_state_class = SensorStateClass.MEASUREMENT

    @property
    def unique_id(self) -> str:
        return f"{self._device.serial}_expected_grain"

    @property
    def native_value(self):
        return self.coordinator.data.get("expected_grain_num")


class PetlibroGrainExecStepSensor(PetlibroEntity, SensorEntity):
    _attr_name = "Feeding Status"

    @property
    def unique_id(self) -> str:
        return f"{self._device.serial}_grain_exec_step"

    @property
    def native_value(self):
        val = self.coordinator.data.get("grain_exec_step")
        if val is None:
            return None
        step_map = {"GRAIN_START": "dispensing", "GRAIN_END": "done", "GRAIN_BLOCKING": "blocked"}
        return step_map.get(val, str(val))


class PetlibroErrorCodeSensor(PetlibroEntity, SensorEntity):
    _attr_name = "Error Code"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def unique_id(self) -> str:
        return f"{self._device.serial}_error_code"

    @property
    def native_value(self):
        return self.coordinator.data.get("error_code")


class PetlibroFirmwareVersionSensor(PetlibroEntity, SensorEntity):
    _attr_name = "Firmware Version"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def unique_id(self) -> str:
        return f"{self._device.serial}_firmware"

    @property
    def native_value(self):
        return self._device.device_info.get("software_version")


class PetlibroPowerModeSensor(PetlibroEntity, SensorEntity):
    _attr_name = "Power Mode"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def unique_id(self) -> str:
        return f"{self._device.serial}_power_mode"

    @property
    def native_value(self):
        val = self.coordinator.data.get("power_mode")
        if val is None:
            return None
        return {1: "USB", 2: "Battery"}.get(val, str(val))


class PetlibroPowerTypeSensor(PetlibroEntity, SensorEntity):
    _attr_name = "Power Type"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def unique_id(self) -> str:
        return f"{self._device.serial}_power_type"

    @property
    def native_value(self):
        val = self.coordinator.data.get("power_type")
        if val is None:
            return None
        return {0: "Invalid", 1: "USB Only", 2: "Battery Only", 3: "USB + Battery"}.get(val, str(val))


class PetlibroSdCardCapacitySensor(PetlibroEntity, SensorEntity):
    _attr_name = "SD Card Total"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def unique_id(self) -> str:
        return f"{self._device.serial}_sd_total"

    @property
    def native_value(self):
        return self.coordinator.data.get("sd_card_total_capacity")


class PetlibroSdCardUsedSensor(PetlibroEntity, SensorEntity):
    _attr_name = "SD Card Used"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def unique_id(self) -> str:
        return f"{self._device.serial}_sd_used"

    @property
    def native_value(self):
        return self.coordinator.data.get("sd_card_used_capacity")


def _utc_to_local(utc_time_str: str) -> str:
    """Convert UTC HH:MM string to local timezone HH:MM AM/PM."""
    try:
        h, m = map(int, utc_time_str.split(":"))
        utc_dt = datetime.datetime.combine(
            datetime.date.today(),
            datetime.time(h, m),
            tzinfo=datetime.timezone.utc,
        )
        local_dt = utc_dt.astimezone()
        return local_dt.strftime("%-I:%M %p")
    except (ValueError, AttributeError):
        return utc_time_str


def _utc_to_local_24h(utc_time_str: str) -> str:
    """Convert UTC HH:MM to local HH:MM in 24-hour format (for form inputs)."""
    try:
        h, m = map(int, utc_time_str.split(":"))
        utc_dt = datetime.datetime.combine(
            datetime.date.today(),
            datetime.time(h, m),
            tzinfo=datetime.timezone.utc,
        )
        local_dt = utc_dt.astimezone()
        return f"{local_dt.hour:02}:{local_dt.minute:02}"
    except (ValueError, AttributeError):
        return utc_time_str


def _format_days(repeat_day: list[int]) -> str:
    """Format repeatDay array to human-readable string."""
    active_days = [d for d in repeat_day if d > 0]
    if not active_days or set(active_days) == {1, 2, 3, 4, 5, 6, 7}:
        return "Every day"
    if set(active_days) == {1, 2, 3, 4, 5}:
        return "Weekdays"
    if set(active_days) == {6, 7}:
        return "Weekends"
    return ", ".join(DAY_NAMES.get(d, str(d)) for d in sorted(active_days))


def _next_feed_time(plans: list[dict]) -> str | None:
    """Calculate the next scheduled feed time from plans."""
    if not plans:
        return None

    now = datetime.datetime.now().astimezone()
    today_weekday = now.isoweekday()  # 1=Mon, 7=Sun

    candidates = []
    for plan in plans:
        exec_time = plan.get("executionTime", "")
        repeat_day = plan.get("repeatDay", [])
        active_days = [d for d in repeat_day if d > 0]
        if not active_days:
            active_days = [1, 2, 3, 4, 5, 6, 7]

        try:
            h, m = map(int, exec_time.split(":"))
        except (ValueError, AttributeError):
            continue

        # Check each day for next 7 days
        for offset in range(8):
            check_day = ((today_weekday - 1 + offset) % 7) + 1
            if check_day in active_days:
                feed_dt = datetime.datetime.combine(
                    now.date() + datetime.timedelta(days=offset),
                    datetime.time(h, m),
                    tzinfo=datetime.timezone.utc,
                ).astimezone()

                if feed_dt > now:
                    candidates.append(feed_dt)
                    break

    if candidates:
        next_dt = min(candidates)
        return next_dt.strftime("%-I:%M %p")

    return None


class PetlibroFeedingScheduleSensor(PetlibroEntity, SensorEntity):
    """Sensor showing next scheduled feed time with plan details as attributes."""

    _attr_name = "Feeding Schedule"
    _attr_icon = "mdi:clock-outline"

    @property
    def unique_id(self) -> str:
        return f"{self._device.serial}_feeding_schedule"

    @property
    def native_value(self) -> str:
        plans = self._device.feeding_plans
        if not plans:
            return "No schedules"
        next_time = _next_feed_time(plans)
        return f"Next: {next_time}" if next_time else "No schedules"

    @property
    def extra_state_attributes(self) -> dict:
        plans = self._device.feeding_plans
        attrs: dict = {"plan_count": len(plans)}

        # Describe the feeder so the Lovelace card can render the right control
        # without knowing model numbers: a plate feeder schedules "which plate,
        # open for how long", an auger feeder schedules "how many portions".
        plate_feeder = self._device.supports(CAP_DISPENSE_PLATE)
        attrs["feeder_type"] = "plate" if plate_feeder else "auger"
        attrs["supports_plates"] = plate_feeder
        if plate_feeder:
            attrs["plate_count"] = getattr(self._device.profile, "PLATE_COUNT", 0)


        for i, plan in enumerate(plans):
            slot = plan.get("planId", i + 1)
            local_time = _utc_to_local(plan.get("executionTime", ""))
            days = _format_days(plan.get("repeatDay", []))
            audio = "Yes" if plan.get("enableAudio", True) else "No"

            attrs[f"plan_{slot}_time"] = local_time
            attrs[f"plan_{slot}_days"] = days
            attrs[f"plan_{slot}_audio"] = audio
            # Only publish the amount the model actually has. Emitting a
            # portions figure for a plate feeder reads as real data.
            if plate_feeder:
                attrs[f"plan_{slot}_plate"] = plan.get("plate")
                attrs[f"plan_{slot}_duration"] = plan.get("feedingDuration")
            else:
                attrs[f"plan_{slot}_portions"] = plan.get("grainNum", 1)

        # Structured plan data for the Lovelace card
        attrs["plans"] = [
            {
                "slot": plan.get("planId", i + 1),
                "time_utc": plan.get("executionTime", ""),
                "time_local": _utc_to_local_24h(plan.get("executionTime", "")),
                "time_display": _utc_to_local(plan.get("executionTime", "")),
                "days": [d for d in plan.get("repeatDay", []) if d > 0],
                "audio": plan.get("enableAudio", True),
                # Only the keys this model actually has. The card
                # null-coalesces, so an absent key is safe and a null one just
                # renders as "null".
                **(
                    {
                        "plate": plan.get("plate"),
                        "duration": plan.get("feedingDuration"),
                    }
                    if plate_feeder
                    else {"portions": plan.get("grainNum", 1)}
                ),
            }
            for i, plan in enumerate(plans)
        ]

        return attrs

    @callback
    def _handle_coordinator_update(self) -> None:
        """Update when coordinator data changes (including plan changes)."""
        self.async_write_ha_state()
