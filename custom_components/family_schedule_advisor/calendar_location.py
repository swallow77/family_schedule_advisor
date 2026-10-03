"""Update only the location of one unambiguously matched calendar occurrence."""

from __future__ import annotations

from datetime import timedelta

from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util


def _matches(item, event):
    start = item.start
    return (
        hasattr(start, "hour")
        and dt_util.as_local(start) == event.start
        and item.summary.strip() == event.title.strip()
        and (item.location or "").strip() == event.location.strip()
    )


async def async_write_location(hass, event, location):
    """Preserve all other fields; never create a duplicate or edit an entire series."""
    component = hass.data.get("calendar")
    registry = er.async_get(hass)
    sources = [
        source for source in event.sources or (event.source,) if source.startswith("calendar.")
    ]
    # Prefer the primary calendar. Shared calendars are not all rewritten.
    sources.sort(key=lambda source: source != event.source)
    for source in sources:
        entity = component.get_entity(source) if component else None
        registered = registry.async_get(source)
        if entity is None or registered is None:
            continue
        if registered.platform == "google":
            if getattr(entity.entity_description, "read_only", True):
                continue
            entry = hass.config_entries.async_get_entry(registered.config_entry_id)
            if entry is None or not hasattr(entry.runtime_data, "service"):
                continue
            from gcal_sync.api import ListEventsRequest
            from gcal_sync.exceptions import ApiException

            service = entry.runtime_data.service
            request = ListEventsRequest(
                calendar_id=entity.calendar_id,
                start_time=event.start - timedelta(seconds=1),
                end_time=event.start + timedelta(seconds=1),
            )
            try:
                pages = await service.async_list_events(request)
            except ApiException as err:
                raise HomeAssistantError(
                    "구글 캘린더에서 원본 일정을 확인하지 못했습니다."
                ) from err
            matches = []
            try:
                async for page in pages:
                    for item in page.items:
                        if (
                            item.start.value == event.start
                            and (item.summary or "").strip() == event.title.strip()
                            and (item.location or "").strip() == event.location.strip()
                            and item.status != "cancelled"
                        ):
                            matches.append(item)
            except ApiException as err:
                raise HomeAssistantError(
                    "구글 캘린더에서 원본 일정을 확인하지 못했습니다."
                ) from err
            if len(matches) != 1:
                raise HomeAssistantError(
                    "일정이 변경되었거나 같은 일정이 여러 개여서 저장하지 않았습니다."
                )
            # singleEvents defaults to true: use the instance's Google id, not iCalUID.
            try:
                await service.async_patch_event(
                    entity.calendar_id, matches[0].id, {"location": location}
                )
            except ApiException as err:
                raise HomeAssistantError("구글 캘린더에 장소를 저장하지 못했습니다.") from err
            try:
                await entity.coordinator.async_refresh()
            except (HomeAssistantError, ValueError, TimeoutError):
                # The write succeeded even if refreshing the local cache failed.
                pass
            return f"{source}에 장소 저장 완료"
        if not (int(entity.supported_features or 0) & 4):
            continue
        items = await entity.async_get_events(
            hass, event.start - timedelta(seconds=1), event.start + timedelta(seconds=1)
        )
        matches = [item for item in items if _matches(item, event)]
        if len(matches) != 1 or not matches[0].uid:
            raise HomeAssistantError(
                "일정이 변경되었거나 같은 일정이 여러 개여서 저장하지 않았습니다."
            )
        item = matches[0]
        if item.rrule and not item.recurrence_id:
            raise HomeAssistantError("반복 일정의 개별 날짜를 확인하지 못해 저장하지 않았습니다.")
        data = {
            "dtstart": item.start,
            "dtend": item.end,
            "summary": item.summary,
            "description": item.description or "",
            "location": location,
        }
        await entity.async_update_event(item.uid, data, recurrence_id=item.recurrence_id)
        return f"{source}에 장소 저장 완료"
    raise HomeAssistantError("이 캘린더는 장소 수정 권한이 없거나 센서로만 연결되어 있습니다.")
