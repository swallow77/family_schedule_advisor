"""Sensor platform for Family Schedule Advisor."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import SensorEntity, SensorEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import FamilyScheduleAdvisorCoordinator


@dataclass(frozen=True, kw_only=True)
class AdvisorSensorDescription(SensorEntityDescription):
    """Advisor sensor description."""

    value_key: str


SENSORS: tuple[AdvisorSensorDescription, ...] = (
    AdvisorSensorDescription(key="status", translation_key="status", value_key="status", icon="mdi:calendar-clock"),
    AdvisorSensorDescription(key="recognized_event", translation_key="recognized_event", value_key="recognized_event_text", icon="mdi:clipboard-text-clock"),
    AdvisorSensorDescription(key="next_event", translation_key="next_event", value_key="event_title", icon="mdi:calendar-star"),
    AdvisorSensorDescription(key="event_time", translation_key="event_time", value_key="event_time_text", icon="mdi:clock-outline"),
    AdvisorSensorDescription(key="destination", translation_key="destination", value_key="destination", icon="mdi:map-marker"),
    AdvisorSensorDescription(key="transit_duration", translation_key="transit_duration", value_key="transit_duration_text", icon="mdi:train-car"),
    AdvisorSensorDescription(key="route_summary", translation_key="route_summary", value_key="route_summary", icon="mdi:map-marker-path"),
    AdvisorSensorDescription(key="departure_time", translation_key="departure_time", value_key="departure_time_text", icon="mdi:walk"),
    AdvisorSensorDescription(key="notify_time", translation_key="notify_time", value_key="notify_time_text", icon="mdi:bell-ring-outline"),
    AdvisorSensorDescription(key="route_status", translation_key="route_status", value_key="route_status", icon="mdi:map-check"),
    AdvisorSensorDescription(key="outfit_message", translation_key="outfit_message", value_key="outfit_message", icon="mdi:hanger"),
    AdvisorSensorDescription(key="last_action", translation_key="last_action", value_key="last_action", icon="mdi:gesture-tap-button"),
    AdvisorSensorDescription(key="last_notify_result", translation_key="last_notify_result", value_key="last_notify_result", icon="mdi:message-badge-outline"),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up sensors."""
    coordinator: FamilyScheduleAdvisorCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([AdvisorSensor(coordinator, entry, description) for description in SENSORS])


class AdvisorSensor(CoordinatorEntity[FamilyScheduleAdvisorCoordinator], SensorEntity):
    """Advisor sensor."""

    entity_description: AdvisorSensorDescription

    def __init__(
        self,
        coordinator: FamilyScheduleAdvisorCoordinator,
        entry: ConfigEntry,
        description: AdvisorSensorDescription,
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

    @property
    def native_value(self) -> Any:
        """Return native value."""
        data = self.coordinator.data or {}
        if self.entity_description.key == "route_summary":
            steps = data.get("route_steps") or []
            duration = data.get("transit_duration_text") or ""
            if steps:
                return f"{len(steps)}단계" + (f" / {duration}" if duration else "")
            if data.get("route_status") == "OK":
                return duration or "경로 상세 없음"
            return "정보 없음"
        return data.get(self.entity_description.value_key, "")

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Return attributes for key sensors."""
        data = self.coordinator.data or {}
        if self.entity_description.key == "recognized_event":
            return {
                "event_title": data.get("event_title"),
                "event_source": data.get("event_source"),
                "event_location": data.get("event_location"),
                "event_description": data.get("event_description"),
                "event_time": data.get("event_time"),
                "raw_event_state": data.get("raw_event_state"),
                "destination": data.get("destination"),
                "destination_source": data.get("destination_source"),
                "route_summary": data.get("route_summary"),
                "route_steps": data.get("route_steps"),
                "checked_entities": data.get("checked_entities"),
                "candidate_count": data.get("candidate_count"),
                "accepted_candidate_count": data.get("accepted_candidate_count"),
                "candidate_reject_reason": data.get("candidate_reject_reason"),
                "lookahead_hours": data.get("lookahead_hours"),
                "min_event_hour": data.get("min_event_hour"),
                "max_event_hour": data.get("max_event_hour"),
                "message": data.get("message"),
                "last_action": data.get("last_action"),
                "last_action_time": data.get("last_action_time"),
                "last_notify_result": data.get("last_notify_result"),
            }
        if self.entity_description.key == "route_summary":
            return {
                "route_summary": data.get("route_summary"),
                "route_steps": data.get("route_steps"),
                "start_address": data.get("start_address"),
                "end_address": data.get("end_address"),
                "route_status": data.get("route_status"),
                "route_error": data.get("route_error"),
            }
        if self.entity_description.key != "status":
            return None
        return {
            "event_key": data.get("event_key"),
            "recognized_event": data.get("recognized_event_text"),
            "raw_event_state": data.get("raw_event_state"),
            "event_source": data.get("event_source"),
            "event_location": data.get("event_location"),
            "event_description": data.get("event_description"),
            "event_time": data.get("event_time"),
            "destination": data.get("destination"),
            "destination_source": data.get("destination_source"),
            "departure_time": data.get("departure_time"),
            "notify_time": data.get("notify_time"),
            "route_status": data.get("route_status"),
            "route_error": data.get("route_error"),
            "route_summary": data.get("route_summary"),
            "route_steps": data.get("route_steps"),
            "start_address": data.get("start_address"),
            "end_address": data.get("end_address"),
            "last_notify_result": data.get("last_notify_result"),
            "checked_entities": data.get("checked_entities"),
            "candidate_count": data.get("candidate_count"),
            "accepted_candidate_count": data.get("accepted_candidate_count"),
            "candidate_reject_reason": data.get("candidate_reject_reason"),
            "lookahead_hours": data.get("lookahead_hours"),
            "min_event_hour": data.get("min_event_hour"),
            "max_event_hour": data.get("max_event_hour"),
            "last_action": data.get("last_action"),
            "last_action_time": data.get("last_action_time"),
        }
