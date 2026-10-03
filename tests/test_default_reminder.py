import asyncio
from datetime import timedelta
from unittest.mock import AsyncMock

import pytest
from conftest import NOW

from custom_components.family_schedule_advisor import coordinator as module
from custom_components.family_schedule_advisor import telegram_location as telegram
from custom_components.family_schedule_advisor.calendar_parser import EventCandidate, EventInfo
from custom_components.family_schedule_advisor.google_directions import TransitResult


@pytest.fixture
async def instance(hass, entry, monkeypatch):
    entry.options.update(
        telegram_location_enabled=True,
        telegram_notify_entity="notify.telegram",
        telegram_event_entity="event.telegram",
        telegram_write_calendar=True,
    )
    monkeypatch.setattr(telegram, "resolve_target", lambda *args: ("bot1", 123))
    event = EventInfo(
        "missing",
        "병원 진료",
        NOW + timedelta(hours=3),
        source="calendar.family",
        sources=("calendar.family",),
    )
    monkeypatch.setattr(
        module, "async_get_event_candidates", AsyncMock(return_value=[EventCandidate(event, True)])
    )
    monkeypatch.setattr(
        module, "async_get_transit_duration", AsyncMock(return_value=TransitResult(1200, "20분"))
    )
    hass.services.async_call.return_value = {"chats": [{"chat_id": 123, "message_id": 10}]}
    instance = module.FamilyScheduleAdvisorCoordinator(hass, entry)
    instance.async_set_updated_data(await instance._async_calculate())
    await instance.async_start()
    await instance.telegram.async_prompt_missing()
    yield instance
    await instance.async_shutdown()


def reply(text, id=100):
    return {
        "bot": {"config_entry_id": "bot1"},
        "event_type": "telegram_text",
        "chat_id": 123,
        "user_id": 123,
        "id": id,
        "reply_to_message_id": 10,
        "text": text,
    }


@pytest.mark.parametrize("cancel", [False, True])
async def test_silent_or_cancelled_conversation_still_executes_hour_before(
    instance, monkeypatch, cancel
):
    send = AsyncMock()
    monkeypatch.setattr(module, "async_send_universal_notify", send)
    if cancel:
        await instance.telegram.async_receive(reply("취소"))
    await instance.async_request_refresh()
    active = [timer for timer in instance.hass.timers if not timer.cancelled]
    assert len(active) == 1 and active[0].when == NOW + timedelta(hours=2)
    monkeypatch.setattr(module.dt_util, "now", lambda: active[0].when)
    active[0].callback(active[0].when)
    await asyncio.gather(*list(instance._tasks))
    send.assert_awaited_once()
    assert "병원 진료" in send.call_args.kwargs["message"]
    assert "default:missing:prepare:script" in instance._sent


async def test_confirmation_before_deadline_reschedules_calculated_reminder(instance, monkeypatch):
    monkeypatch.setattr(telegram, "async_write_location", AsyncMock(return_value="저장 완료"))
    await instance.telegram.async_receive(reply("서울역"))
    await instance.telegram.async_receive(reply("확인", 101))
    active = [timer for timer in instance.hass.timers if not timer.cancelled]
    assert len(active) == 1
    assert active[0].when == NOW + timedelta(hours=3) - timedelta(minutes=45)
    assert instance.data["route_status"] == "OK"


async def test_late_confirmation_does_not_repeat_completed_default_reminder(instance, monkeypatch):
    send = AsyncMock()
    monkeypatch.setattr(module, "async_send_universal_notify", send)
    monkeypatch.setattr(telegram, "async_write_location", AsyncMock(return_value="저장 완료"))
    monkeypatch.setattr(module.dt_util, "now", lambda: NOW + timedelta(hours=2))
    await instance._async_notify("default:missing", "prepare")
    await instance.telegram.async_receive(reply("서울역"))
    await instance.telegram.async_receive(reply("확인", 101))
    assert instance.data["destination"] == "서울역"
    assert not [timer for timer in instance.hass.timers if not timer.cancelled]
    await instance._async_notify("default:missing", "prepare")
    send.assert_awaited_once()


async def test_missed_hour_deadline_catches_up_before_appointment(instance, monkeypatch):
    monkeypatch.setattr(module.dt_util, "now", lambda: NOW + timedelta(hours=2, minutes=20))
    await instance.async_request_refresh()
    active = [timer for timer in instance.hass.timers if not timer.cancelled]
    assert len(active) == 1
    assert active[0].when == NOW + timedelta(hours=2, minutes=20, seconds=1)
