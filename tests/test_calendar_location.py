from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from conftest import NOW, HomeAssistantError

from custom_components.family_schedule_advisor.calendar_location import async_write_location
from custom_components.family_schedule_advisor.calendar_parser import EventInfo


def appointment():
    return EventInfo(
        "event",
        "진료",
        NOW + timedelta(hours=3),
        end=NOW + timedelta(hours=4),
        location="",
        source="calendar.family",
        sources=("calendar.family",),
    )


def setup_calendar(hass, platform="local_calendar", features=7):
    event = appointment()
    item = SimpleNamespace(
        start=event.start,
        end=event.end,
        summary=event.title,
        description="비공개 메모",
        location=event.location,
        uid="uid-1",
        recurrence_id=None,
        rrule=None,
    )
    entity = SimpleNamespace(
        supported_features=features,
        async_get_events=AsyncMock(return_value=[item]),
        async_update_event=AsyncMock(),
        entity_description=SimpleNamespace(read_only=False),
        calendar_id="calendar-id",
        coordinator=SimpleNamespace(async_refresh=AsyncMock()),
    )
    registered = SimpleNamespace(platform=platform, config_entry_id="google-entry")
    hass.registry = SimpleNamespace(async_get=lambda entity_id: registered)
    hass.data["calendar"] = SimpleNamespace(get_entity=lambda entity_id: entity)
    return event, item, entity


async def test_native_calendar_preserves_title_times_notes_and_occurrence(hass):
    event, item, entity = setup_calendar(hass)
    item.recurrence_id = "20261003T100000"
    result = await async_write_location(hass, event, "서울역")
    args, kwargs = entity.async_update_event.call_args
    assert args[0] == "uid-1"
    assert args[1] == {
        "dtstart": event.start,
        "dtend": event.end,
        "summary": "진료",
        "description": "비공개 메모",
        "location": "서울역",
    }
    assert kwargs == {"recurrence_id": "20261003T100000"}
    assert "저장 완료" in result


@pytest.mark.parametrize("case", ["duplicate", "changed", "unsupported", "series"])
async def test_ambiguous_changed_read_only_or_series_calendar_is_not_written(hass, case):
    event, item, entity = setup_calendar(hass)
    if case == "duplicate":
        entity.async_get_events.return_value.append(item)
    elif case == "changed":
        item.location = "이미 변경된 장소"
    elif case == "unsupported":
        entity.supported_features = 3
    else:
        item.rrule = "FREQ=DAILY"
    with pytest.raises(HomeAssistantError):
        await async_write_location(hass, event, "서울역")
    entity.async_update_event.assert_not_awaited()


async def setup_google(hass, items):
    event, item, entity = setup_calendar(hass, "google", 3)
    service = SimpleNamespace(async_patch_event=AsyncMock())

    async def pages():
        yield SimpleNamespace(items=items)

    service.async_list_events = AsyncMock(return_value=pages())
    entry = SimpleNamespace(runtime_data=SimpleNamespace(service=service))
    hass.config_entries = SimpleNamespace(async_get_entry=lambda key: entry)
    return event, entity, service


def google_item(id="instance-id", location=""):
    event = appointment()
    return SimpleNamespace(
        start=SimpleNamespace(value=event.start),
        summary=event.title,
        location=location,
        status="confirmed",
        id=id,
    )


async def test_google_patches_only_location_using_instance_id(hass):
    event, entity, service = await setup_google(hass, [google_item()])
    await async_write_location(hass, event, "서울역")
    service.async_patch_event.assert_awaited_once_with(
        "calendar-id", "instance-id", {"location": "서울역"}
    )
    request = service.async_list_events.call_args.args[0]
    assert request.start_time < event.start < request.end_time
    assert request.to_request().single_events.value == "true"
    entity.async_update_event.assert_not_awaited()


@pytest.mark.parametrize(
    "items", [[], [google_item(), google_item("other")], [google_item(location="변경됨")]]
)
async def test_google_refuses_changed_or_ambiguous_match(hass, items):
    event, entity, service = await setup_google(hass, items)
    with pytest.raises(HomeAssistantError):
        await async_write_location(hass, event, "서울역")
    service.async_patch_event.assert_not_awaited()


async def test_google_read_only_and_refresh_failure(hass):
    event, entity, service = await setup_google(hass, [google_item()])
    entity.entity_description.read_only = True
    with pytest.raises(HomeAssistantError):
        await async_write_location(hass, event, "서울역")
    service.async_patch_event.assert_not_awaited()
    entity.entity_description.read_only = False
    entity.coordinator.async_refresh.side_effect = HomeAssistantError("offline")
    assert "저장 완료" in await async_write_location(hass, event, "서울역")
