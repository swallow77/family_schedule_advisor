"""Family Schedule Advisor integration."""

from __future__ import annotations

import logging

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers.typing import ConfigType

from .const import DOMAIN, PLATFORMS
from .coordinator import FamilyScheduleAdvisorCoordinator

_LOGGER = logging.getLogger(__name__)


def _get_coordinator(
    hass: HomeAssistant, entry_id: str | None = None
) -> FamilyScheduleAdvisorCoordinator | None:
    domain_data = hass.data.get(DOMAIN, {})
    if entry_id:
        return domain_data.get(entry_id)
    if domain_data:
        return next(iter(domain_data.values()))
    return None


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Set up services."""

    async def _handle_recalculate(call: ServiceCall) -> None:
        coordinator = _get_coordinator(hass, call.data.get("entry_id"))
        if coordinator is None:
            _LOGGER.warning("No Family Schedule Advisor entry is loaded")
            return
        await coordinator.async_manual_recalculate()

    async def _handle_test_notify(call: ServiceCall) -> None:
        coordinator = _get_coordinator(hass, call.data.get("entry_id"))
        if coordinator is None:
            _LOGGER.warning("No Family Schedule Advisor entry is loaded")
            return
        await coordinator.async_generate_and_notify(test=True)

    if not hass.services.has_service(DOMAIN, "recalculate"):
        hass.services.async_register(DOMAIN, "recalculate", _handle_recalculate)
    if not hass.services.has_service(DOMAIN, "test_notify"):
        hass.services.async_register(DOMAIN, "test_notify", _handle_test_notify)

    action_schema = vol.Schema(
        {
            vol.Optional("entry_id"): str,
            vol.Optional("event_key"): str,
            vol.Optional("stage", default="prepare"): vol.In(["prepare", "departure"]),
            vol.Optional("minutes"): vol.All(vol.Coerce(int), vol.Range(min=1, max=60)),
        }
    )
    for service, command in {
        "mark_prepared": "prepared",
        "mark_departed": "departed",
        "snooze": "snooze",
        "skip_event": "skip",
    }.items():

        async def handle_action(call: ServiceCall, action=command):
            coordinator = _get_coordinator(hass, call.data.get("entry_id"))
            if coordinator is None:
                raise ServiceValidationError("Family Schedule Advisor is not loaded")
            await coordinator.async_event_action(
                action,
                event_key=call.data.get("event_key"),
                stage=call.data.get("stage", "prepare"),
                minutes=call.data.get("minutes"),
            )

        if not hass.services.has_service(DOMAIN, service):
            hass.services.async_register(DOMAIN, service, handle_action, schema=action_schema)

    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up from a config entry."""
    coordinator = FamilyScheduleAdvisorCoordinator(hass, entry)
    await coordinator.async_config_entry_first_refresh()
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator
    try:
        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
        await coordinator.async_start()
    except Exception:
        await coordinator.async_shutdown()
        await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
        hass.data[DOMAIN].pop(entry.entry_id, None)
        raise
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    return True


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload integration when options change."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if not unload_ok:
        return False
    coordinator: FamilyScheduleAdvisorCoordinator | None = hass.data.get(DOMAIN, {}).pop(
        entry.entry_id, None
    )
    if coordinator is not None:
        await coordinator.async_shutdown()
    if not hass.data.get(DOMAIN):
        hass.data.pop(DOMAIN, None)
    return unload_ok
