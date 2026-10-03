from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from conftest import NOW, HomeAssistantError, Store

from custom_components.family_schedule_advisor import coordinator as module
from custom_components.family_schedule_advisor import telegram_location as telegram
from custom_components.family_schedule_advisor.calendar_parser import (
    EventCandidate,
    EventInfo,
    _event_key,
)
from custom_components.family_schedule_advisor.config_flow import FamilyScheduleAdvisorOptionsFlow
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


def reply(text, id=100, reply_to=10, **kwargs):
    return {
        "bot": {"config_entry_id": "bot1"},
        "event_type": "telegram_text",
        "chat_id": 123,
        "user_id": 123,
        "id": id,
        "reply_to_message_id": reply_to,
        "text": text,
        **kwargs,
    }


async def test_prompt_once_and_explicit_confirmation_updates_calendar(instance, monkeypatch):
    requests = instance.coordinator.hass if hasattr(instance, "coordinator") else instance.hass
    calls = requests.services.async_call.call_count
    await instance.telegram.async_prompt_missing()
    assert requests.services.async_call.call_count == calls
    write = AsyncMock(return_value="calendar.family에 장소 저장 완료")
    monkeypatch.setattr(telegram, "async_write_location", write)
    await instance.telegram.async_receive(reply("서울 강남구 테헤란로 152"))
    write.assert_not_awaited()
    assert instance.telegram.pending["missing"]["state"] == "confirm"
    message = requests.services.async_call.call_args.args[2]["message"]
    assert "20분" in message and "출발" in message and "확인" in message
    await instance.telegram.async_receive(reply("확인", id=101))
    assert write.await_count == 1
    assert write.call_args.args[1].location == ""
    assert instance.data["destination"] == "서울 강남구 테헤란로 152"
    assert instance.data["destination_source"] == "telegram_reply"
    assert instance.data["event_key"] == "default:missing"
    await instance.telegram.async_receive(reply("확인", id=102))
    assert write.await_count == 1


@pytest.mark.parametrize(
    "kwargs",
    [
        {"bot": {"config_entry_id": "bot2"}},
        {"chat_id": 456},
        {"user_id": 456},
        {"event_type": "telegram_sent"},
        {"reply_to_message_id": 99},
    ],
)
async def test_wrong_bot_chat_sender_and_unrelated_replies_are_ignored(instance, kwargs):
    await instance.telegram.async_receive(reply("주소", **kwargs))
    assert instance.telegram.pending["missing"]["state"] == "address"
    assert instance.telegram.overrides == {}


async def test_failure_keeps_override_and_existing_notification_receipt(instance, monkeypatch):
    write = AsyncMock(side_effect=HomeAssistantError("denied"))
    monkeypatch.setattr(telegram, "async_write_location", write)
    instance._sent["default:missing:prepare:script"] = NOW.isoformat()
    await instance.telegram.async_receive(reply("서울역"))
    await instance.telegram.async_receive(reply("확인", id=101))
    assert instance.data["destination"] == "서울역"
    assert "저장은 실패" in instance.data["last_notify_result"]
    assert instance._stage_done(instance._plans["default:missing"], "prepare")
    assert not [timer for timer in instance.hass.timers if not timer.cancelled]


async def test_new_calendar_location_keeps_same_identity_after_restart(instance, monkeypatch):
    monkeypatch.setattr(telegram, "async_write_location", AsyncMock(return_value="저장 완료"))
    await instance.telegram.async_receive(reply("서울역"))
    await instance.telegram.async_receive(reply("확인", id=101))
    event = instance._plans["default:missing"].event
    event.key = _event_key("", event.start, event.title, "서울역")
    monkeypatch.setattr(
        module, "async_get_event_candidates", AsyncMock(return_value=[EventCandidate(event, True)])
    )
    restored = module.FamilyScheduleAdvisorCoordinator(instance.hass, instance.entry)
    restored.telegram.restore(Store.contents[instance._store.key]["telegram"])
    data = await restored._async_calculate()
    assert data["event_key"] == "default:missing" and data["destination"] == "서울역"


async def test_cancel_duplicate_input_and_plain_text(instance):
    await instance.telegram.async_receive(reply("서울_[역]"))
    calls = instance.hass.services.async_call.call_count
    await instance.telegram.async_receive(reply("서울_[역]"))
    assert instance.hass.services.async_call.call_count == calls
    assert instance.hass.services.async_call.call_args.args[2]["parse_mode"] == "plain_text"
    await instance.telegram.async_receive(reply("취소", id=101))
    await instance.telegram.async_prompt_missing()
    assert instance.telegram.pending["missing"]["state"] == "closed"
    assert not instance.telegram.overrides


async def test_multiple_appointments_require_a_reply_to_the_right_question(instance):
    instance.telegram.pending["other"] = {
        "state": "address",
        "expires": (NOW + timedelta(hours=4)).isoformat(),
        "message_ids": [20],
    }
    await instance.telegram.async_receive(reply("서울역", reply_to=None))
    assert instance.telegram.pending["missing"]["state"] == "address"
    await instance.telegram.async_receive(reply("서울역", id=101))
    assert instance.telegram.pending["missing"]["state"] == "confirm"
    assert instance.telegram.pending["other"]["state"] == "address"


async def test_each_missing_event_gets_its_own_question_and_a_working_event_is_skipped(instance):
    from dataclasses import replace

    plan = instance._plans["default:missing"]
    second = replace(plan, event=replace(plan.event, key="second", title="두 번째 약속"))
    healthy = replace(
        plan, event=replace(plan.event, key="healthy"), destination="서울역", route_status="OK"
    )
    instance._plans[second.key] = second
    instance._plans[healthy.key] = healthy
    instance.telegram.pending.clear()
    instance.hass.services.async_call.side_effect = [
        {"chats": [{"chat_id": 123, "message_id": 10}]},
        {"chats": [{"chat_id": 123, "message_id": 20}]},
    ]
    await instance.telegram.async_prompt_missing()
    assert instance.telegram.pending["missing"]["message_ids"] == [10]
    assert instance.telegram.pending["second"]["message_ids"] == [20]
    assert "healthy" not in instance.telegram.pending
    assert instance.data["pending_location_requests"] == 2


async def test_no_route_does_not_invent_departure_and_can_retry_address(instance, monkeypatch):
    monkeypatch.setattr(
        module,
        "async_get_transit_duration",
        AsyncMock(return_value=TransitResult(0, "", status="ZERO_RESULTS")),
    )
    await instance.telegram.async_receive(reply("알 수 없는 장소"))
    text = instance.hass.services.async_call.call_args.args[2]["message"]
    assert "정보 없음" in text and "경로를 확인하지 못" in text
    await instance.telegram.async_receive(reply("다시 입력", id=101))
    assert instance.telegram.pending["missing"]["state"] == "address"


async def test_expired_or_removed_appointment_cannot_be_written(instance, monkeypatch):
    write = AsyncMock()
    monkeypatch.setattr(telegram, "async_write_location", write)
    await instance.telegram.async_receive(reply("서울역"))
    instance._plans.clear()
    await instance.telegram.async_receive(reply("확인", id=101))
    write.assert_not_awaited()


async def test_prompt_send_failure_retries_only_after_backoff(instance):
    instance.telegram.pending.clear()
    instance.hass.services.async_call.side_effect = HomeAssistantError("offline")
    await instance.telegram.async_prompt_missing()
    calls = instance.hass.services.async_call.call_count
    await instance.telegram.async_prompt_missing()
    assert instance.hass.services.async_call.call_count == calls
    assert instance.telegram.error


async def test_telegram_options_preserve_all_other_settings(instance):
    options = FamilyScheduleAdvisorOptionsFlow()
    options.config_entry = instance.entry
    options.hass = instance.hass
    result = await options.async_step_telegram(
        {
            "telegram_location_enabled": True,
            "telegram_notify_entity": "notify.telegram",
            "telegram_event_entity": "event.telegram",
            "telegram_write_calendar": False,
            "telegram_request_hours": 24,
        }
    )
    assert result["data"]["telegram_write_calendar"] is False
    assert result["data"]["telegram_notify_entity"] == "notify.telegram"


def test_target_requires_same_bot_and_private_chat(hass):
    records = {
        "notify.telegram": SimpleNamespace(
            platform="telegram_bot",
            config_entry_id="bot1",
            config_subentry_id="chat1",
            disabled_by=None,
        ),
        "event.telegram": SimpleNamespace(
            platform="telegram_bot", config_entry_id="bot1", disabled_by=None
        ),
    }
    hass.registry = SimpleNamespace(async_get=records.get)
    entry = SimpleNamespace(subentries={"chat1": SimpleNamespace(data={"chat_id": 123})})
    hass.config_entries = SimpleNamespace(async_get_entry=lambda key: entry)
    config = {
        "telegram_notify_entity": "notify.telegram",
        "telegram_event_entity": "event.telegram",
    }
    assert telegram.resolve_target(hass, config) == ("bot1", 123)
    records["event.telegram"].config_entry_id = "bot2"
    with pytest.raises(ValueError):
        telegram.resolve_target(hass, config)
    records["event.telegram"].config_entry_id = "bot1"
    entry.subentries["chat1"].data["chat_id"] = -123
    with pytest.raises(ValueError):
        telegram.resolve_target(hass, config)
