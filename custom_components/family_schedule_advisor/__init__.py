"""Family Schedule Advisor integration."""
from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers.typing import ConfigType

from .const import DOMAIN, PLATFORMS
from .coordinator import FamilyScheduleAdvisorCoordinator

_LOGGER = logging.getLogger(__name__)


def _get_coordinator(hass: HomeAssistant, entry_id: str | None = None) -> FamilyScheduleAdvisorCoordinator | None:
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

    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up from a config entry."""
    coordinator = FamilyScheduleAdvisorCoordinator(hass, entry)
    await coordinator.async_config_entry_first_refresh()
    await coordinator.async_start()

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    return True


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload integration when options change."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    coordinator: FamilyScheduleAdvisorCoordinator | None = hass.data.get(DOMAIN, {}).pop(entry.entry_id, None)
    if coordinator is not None:
        await coordinator.async_shutdown()
    if not hass.data.get(DOMAIN):
        hass.data.pop(DOMAIN, None)
    return unload_ok
