"""The Petlibro Local integration."""

from __future__ import annotations

import datetime
import logging
import os
import time

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers import device_registry as dr

from .const import (
    CAP_DISPENSE_PLATE,
    CONF_FEEDING_PLANS,
    DOMAIN,
    PLATFORMS,
    WET_FEEDING_MAX_MINUTES,
    WET_FEEDING_MIN_MINUTES,
)
from .coordinator import PetlibroCoordinator
from .protocol.codec import timestamp_now_ms

_LOGGER = logging.getLogger(__name__)

PetlibroConfigEntry = ConfigEntry


def _shift_utc_plan_to_local(time_str: str, offset_minutes: int) -> str:
    """Reinterpret a stored UTC plan time as the equivalent local wall clock."""
    try:
        h, m = map(int, time_str.split(":"))
    except (ValueError, AttributeError):
        return time_str
    total = (h * 60 + m + offset_minutes) % (24 * 60)
    return f"{total // 60:02}:{total % 60:02}"


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Migrate stored plan times from UTC to device-local wall clock.

    Until v2 the integration converted plan times local->UTC on the way out and
    back on the way in, which was self-consistent only because the device was
    left on UTC - the NTP reply never carried timezoneOffsetSeconds. Now that it
    does, the device reads executionTime in our timezone, so a stored UTC time
    would fire an hour early in BST.

    The shift uses the offset in force at migration time. A plan is a recurring
    wall-clock instruction rather than an instant, so there is no single correct
    offset for one written months ago under a different DST state; the current
    one is right for the overwhelmingly common case of migrating during the
    season the plan was created in.
    """
    if entry.version >= 2:
        return True

    plans = entry.options.get(CONF_FEEDING_PLANS, [])
    if plans:
        offset = datetime.datetime.now().astimezone().utcoffset()
        offset_minutes = int(offset.total_seconds() // 60) if offset else 0
        migrated = []
        for plan in plans:
            p = dict(plan)
            if p.get("executionTime"):
                p["executionTime"] = _shift_utc_plan_to_local(
                    p["executionTime"], offset_minutes
                )
            migrated.append(p)
        hass.config_entries.async_update_entry(
            entry,
            options={**entry.options, CONF_FEEDING_PLANS: migrated},
            version=2,
        )
        _LOGGER.info(
            "Migrated %d feeding plan(s) from UTC to local (offset %+d minutes)",
            len(migrated),
            offset_minutes,
        )
    else:
        hass.config_entries.async_update_entry(entry, version=2)

    return True


async def async_setup_entry(hass: HomeAssistant, entry: PetlibroConfigEntry) -> bool:
    """Set up Petlibro Local from a config entry."""
    coordinator = PetlibroCoordinator(hass, entry)
    await coordinator.async_setup()

    entry.runtime_data = coordinator

    # Restore persisted feeding plans
    stored_plans = entry.options.get(CONF_FEEDING_PLANS, [])
    if stored_plans:
        coordinator.device.feeding_plans = list(stored_plans)
        _LOGGER.info(
            "Restored %d feeding plan(s) for %s",
            len(stored_plans),
            coordinator.device.serial,
        )

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    entry.async_on_unload(coordinator.async_shutdown)

    # Register services (once per integration, not per entry)
    if not hass.services.has_service(DOMAIN, "manual_feed"):
        _register_services(hass)

    # Register static path + auto-register Lovelace resource for the card (once)
    frontend_key = f"{DOMAIN}_frontend_registered"
    if frontend_key not in hass.data:
        hass.data[frontend_key] = True
        card_js = os.path.join(os.path.dirname(__file__), "www", "petlibro-feeding-card.js")
        card_url = f"/{DOMAIN}/petlibro-feeding-card.js"

        # Serve the JS file
        try:
            from homeassistant.components.http import StaticPathConfig
            await hass.http.async_register_static_paths([
                StaticPathConfig(card_url, card_js, False)
            ])
        except (ImportError, AttributeError):
            hass.http.register_static_path(card_url, card_js, cache_headers=False)

        # Auto-register as a Lovelace resource so the card is available
        # in the card picker without manual resource setup
        await _ensure_card_resource(hass, card_url)

    return True


async def async_unload_entry(hass: HomeAssistant, entry: PetlibroConfigEntry) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


def _get_coordinator(hass: HomeAssistant, call: ServiceCall) -> PetlibroCoordinator:
    """Get coordinator for the target device in a service call."""
    device_ids = call.data.get("device_id", [])
    if not device_ids:
        # Fall back to first petlibro config entry
        for entry in hass.config_entries.async_entries(DOMAIN):
            if hasattr(entry, "runtime_data") and entry.runtime_data:
                return entry.runtime_data
        raise ValueError("No Petlibro device found")

    dev_reg = dr.async_get(hass)
    device_id = device_ids[0] if isinstance(device_ids, list) else device_ids
    device = dev_reg.async_get(device_id)
    if not device:
        raise ValueError(f"Device {device_id} not found")

    for entry_id in device.config_entries:
        entry = hass.config_entries.async_get_entry(entry_id)
        if entry and entry.domain == DOMAIN:
            return entry.runtime_data

    raise ValueError(f"No Petlibro entry for device {device_id}")


def _register_services(hass: HomeAssistant) -> None:
    """Register custom services."""

    async def handle_manual_feed(call: ServiceCall) -> None:
        coordinator = _get_coordinator(hass, call)
        portions = call.data.get("portions", 1)
        await coordinator.device.manual_feed(portions=portions)

    async def handle_set_feeding_plan(call: ServiceCall) -> None:
        coordinator = _get_coordinator(hass, call)
        plan_id = call.data["plan_id"]
        time_val = call.data["time"]
        portions = call.data.get("portions", 1)
        days = call.data.get("days", [])
        enable_audio = call.data.get("enable_audio", True)

        # Convert time to UTC HH:MM
        if isinstance(time_val, str):
            h, m = map(int, time_val.split(":"))
        else:
            h, m = time_val.hour, time_val.minute

        # executionTime is wall-clock time in the *device's* timezone, and the
        # NTP reply sets that timezone to ours. So the time the user picked
        # goes on the wire unchanged - converting it to UTC here would shift
        # every feed by the local offset.
        execution_time = f"{h:02}:{m:02}"

        # Build repeat_day array
        if days:
            repeat_day = [int(d) for d in days]
        else:
            repeat_day = [1, 2, 3, 4, 5, 6, 7]
        repeat_day.extend([0] * (7 - len(repeat_day)))

        plan = {
            "planId": plan_id,
            "executionTime": execution_time,
            "repeatDay": repeat_day,
            "enableAudio": enable_audio,
            "audioTimes": 3,
            "syncTime": timestamp_now_ms(),
        }

        # The amount a plan carries is model-specific: an auger dispenses a
        # quantity, a plate feeder rotates to a plate and holds the door open.
        # Gated on capability so an untested model keeps the auger default.
        if coordinator.device.supports(CAP_DISPENSE_PLATE):
            plate = int(call.data.get("plate", 1))
            plate_count = coordinator.device.profile.PLATE_COUNT
            if not 1 <= plate <= plate_count:
                _LOGGER.error(
                    "Plate %s is out of range (1-%s); plan not set", plate, plate_count
                )
                return
            plan["plate"] = plate
            # Presented in minutes; the protocol carries seconds.
            minutes = int(call.data.get("duration", 4))
            minutes = max(WET_FEEDING_MIN_MINUTES, min(WET_FEEDING_MAX_MINUTES, minutes))
            plan["feedingDuration"] = minutes * 60
            # The device stores an absolute date, so a plan needs a next
            # occurrence. Whether the firmware advances this itself is
            # unconfirmed - see docs/plaf109-protocol.md.
            plan["executionDay"] = datetime.date.today().isoformat()
        else:
            plan["grainNum"] = portions

        # Store locally
        existing = [p for p in coordinator.device.feeding_plans if p.get("planId") != plan_id]
        existing.append(plan)
        existing.sort(key=lambda p: p.get("planId", 0))
        coordinator.device.feeding_plans = existing

        # Persist to config entry
        await _persist_feeding_plans(hass, coordinator, existing)

        # Send to device
        await coordinator.device.set_feeding_plans(existing)

        # Notify sensor of plan change
        coordinator.device.state["_feeding_plans_version"] = timestamp_now_ms()
        coordinator.device._notify_state_changed()

    async def handle_clear_feeding_plans(call: ServiceCall) -> None:
        coordinator = _get_coordinator(hass, call)
        coordinator.device.feeding_plans = []
        await coordinator.device.set_feeding_plans([])

        # Persist empty
        await _persist_feeding_plans(hass, coordinator, [])

        # Notify sensor of plan change
        coordinator.device.state["_feeding_plans_version"] = timestamp_now_ms()
        coordinator.device._notify_state_changed()

    async def handle_remove_feeding_plan(call: ServiceCall) -> None:
        coordinator = _get_coordinator(hass, call)
        plan_id = call.data["plan_id"]
        existing = [p for p in coordinator.device.feeding_plans if p.get("planId") != plan_id]
        coordinator.device.feeding_plans = existing

        # Persist
        await _persist_feeding_plans(hass, coordinator, existing)

        # Send to device
        await coordinator.device.set_feeding_plans(existing)

        # Notify sensor
        coordinator.device.state["_feeding_plans_version"] = timestamp_now_ms()
        coordinator.device._notify_state_changed()

    hass.services.async_register(DOMAIN, "manual_feed", handle_manual_feed)
    hass.services.async_register(DOMAIN, "set_feeding_plan", handle_set_feeding_plan)
    hass.services.async_register(DOMAIN, "clear_feeding_plans", handle_clear_feeding_plans)
    hass.services.async_register(DOMAIN, "remove_feeding_plan", handle_remove_feeding_plan)


async def _ensure_card_resource(hass: HomeAssistant, url: str) -> None:
    """Auto-register the feeding card as a Lovelace dashboard resource.

    Uses HA's lovelace ResourceStorageCollection so the card appears
    in the card picker immediately — no manual resource setup needed.
    """
    try:
        lovelace_data = hass.data.get("lovelace")
        if lovelace_data is None:
            return

        # Support both dict and object access across HA versions
        resources = getattr(lovelace_data, "resources", None)
        if resources is None and isinstance(lovelace_data, dict):
            resources = lovelace_data.get("resources")

        if resources is None:
            return

        # Already registered?
        for item in resources.async_items():
            if item.get("url") == url:
                return

        await resources.async_create_item({"res_type": "module", "url": url})
        _LOGGER.info("Auto-registered Petlibro feeding card as Lovelace resource")
    except Exception:
        _LOGGER.debug(
            "Could not auto-register card resource — "
            "add manually: Settings > Dashboards > Resources > %s",
            url,
        )


async def _persist_feeding_plans(
    hass: HomeAssistant,
    coordinator: PetlibroCoordinator,
    plans: list[dict],
) -> None:
    """Persist feeding plans to config entry options."""
    entry = coordinator.entry
    new_options = dict(entry.options)
    new_options[CONF_FEEDING_PLANS] = plans
    hass.config_entries.async_update_entry(entry, options=new_options)
