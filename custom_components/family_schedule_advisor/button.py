"""Button platform for Family Schedule Advisor."""

from __future__ import annotations

from dataclasses import dataclass

from homeassistant.components.button import ButtonEntity, ButtonEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import FamilyScheduleAdvisorCoordinator


@dataclass(frozen=True, kw_only=True)
class AdvisorButtonDescription(ButtonEntityDescription):
    """Advisor button description."""

    action: str


BUTTONS: tuple[AdvisorButtonDescription, ...] = (
    AdvisorButtonDescription(
        key="recalculate",
        translation_key="recalculate",
        action="recalculate",
        icon="mdi:reload",
    ),
    AdvisorButtonDescription(
        key="test_notify",
        translation_key="test_notify",
        action="test_notify",
        icon="mdi:speaker-message",
    ),
    AdvisorButtonDescription(
        key="mark_prepared",
        translation_key="mark_prepared",
        action="prepared",
        icon="mdi:check-circle",
    ),
    AdvisorButtonDescription(
        key="mark_departed",
        translation_key="mark_departed",
        action="departed",
        icon="mdi:walk",
    ),
    AdvisorButtonDescription(
        key="snooze", translation_key="snooze", action="snooze", icon="mdi:bell-sleep"
    ),
    AdvisorButtonDescription(
        key="skip_event",
        translation_key="skip_event",
        action="skip",
        icon="mdi:calendar-remove",
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up buttons."""
    coordinator: FamilyScheduleAdvisorCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([AdvisorButton(coordinator, entry, description) for description in BUTTONS])


class AdvisorButton(CoordinatorEntity[FamilyScheduleAdvisorCoordinator], ButtonEntity):
    """Advisor button."""

    entity_description: AdvisorButtonDescription

    def __init__(
        self,
        coordinator: FamilyScheduleAdvisorCoordinator,
        entry: ConfigEntry,
        description: AdvisorButtonDescription,
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{entry.entry_id}_{description.key}"
        self._attr_has_entity_name = True
        self._attr_device_info = {
            "identifiers": {(DOMAIN, entry.entry_id)},
            "name": "Family Schedule Advisor",
            "manufacturer": "Custom",
        }

    async def async_press(self) -> None:
        """Handle button press."""
        if self.entity_description.action == "recalculate":
            await self.coordinator.async_manual_recalculate()
        elif self.entity_description.action == "test_notify":
            await self.coordinator.async_generate_and_notify(test=True)
        else:
            await self.coordinator.async_event_action(self.entity_description.action)
