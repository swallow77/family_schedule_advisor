from datetime import timedelta
from unittest.mock import AsyncMock

import pytest
from conftest import NOW, HomeAssistantError, Store

from custom_components.family_schedule_advisor import config_flow
from custom_components.family_schedule_advisor import coordinator as module
from custom_components.family_schedule_advisor.calendar_parser import EventCandidate, EventInfo
from custom_components.family_schedule_advisor.google_directions import TransitResult
from custom_components.family_schedule_advisor.notify import (
    async_send_telegram_notify,
    async_send_universal_notify,
    telegram_reminder_text,
)
from custom_components.family_schedule_advisor.planning import Plan


@pytest.fixture
async def coordinator(hass, entry, monkeypatch):
    entry.options.update(telegram_reminder_entity="notify.telegram", departure_reminder=True)
    event = EventInfo(
        "one",
        "첫 번째 약속",
        NOW + timedelta(hours=3),
        location="서울역",
        source="calendar.family",
        sources=("calendar.family",),
    )
    monkeypatch.setattr(
        module, "async_get_event_candidates", AsyncMock(return_value=[EventCandidate(event, True)])
    )
    monkeypatch.setattr(
        module,
        "async_get_transit_duration",
        AsyncMock(return_value=TransitResult(1200, "20분", route_steps=["1번 버스"])),
    )
    hass.services.async_call.return_value = {
        "chats": [{"message_id": 12, "entity_id": "notify.telegram"}]
    }
    instance = module.FamilyScheduleAdvisorCoordinator(hass, entry)
    instance.async_set_updated_data(await instance._async_calculate())
    await instance.async_start()
    yield instance
    await instance.async_shutdown()


@pytest.mark.parametrize("stage", ["prepare", "departure"])
async def test_each_stage_dispatches_both_real_channels_once(coordinator, stage):
    await coordinator._async_notify("default:one", stage)
    calls = coordinator.hass.services.async_call.call_args_list
    assert [call.args[0] for call in calls] == ["telegram_bot", "script"]
    assert calls[0].args[2]["parse_mode"] == "plain_text"
    text = calls[0].args[2]["message"]
    assert ("출발 안내" if stage == "departure" else "준비 안내") in text
    assert "1번 버스" in text and "서울역" in text
    assert f"default:one:{stage}" in coordinator._done
    assert (
        coordinator.data["notification_channel_results"]["telegram"]["status"]
        == "telegram_accepted"
    )
    assert (
        coordinator.data["notification_channel_results"]["script"]["status"] == "service_completed"
    )
    await coordinator._async_notify("default:one", stage)
    assert len(coordinator.hass.services.async_call.call_args_list) == 2


@pytest.mark.parametrize("failed_channel", ["script", "telegram_bot"])
async def test_only_failed_channel_is_retried(coordinator, failed_channel):
    attempts = {"script": 0, "telegram_bot": 0}

    async def call(domain, service, *args, **kwargs):
        attempts[domain] += 1
        if domain == failed_channel and attempts[domain] == 1:
            raise HomeAssistantError("temporary failure")
        return {"chats": [{"message_id": 12, "entity_id": "notify.telegram"}]}

    coordinator.hass.services.async_call.side_effect = call
    await coordinator._async_notify("default:one", "departure")
    assert "default:one:departure" not in coordinator._done
    await coordinator._async_notify("default:one", "departure")
    assert attempts[failed_channel] == 2
    assert attempts["script" if failed_channel == "telegram_bot" else "telegram_bot"] == 1
    assert "default:one:departure" in coordinator._done


async def test_no_telegram_ack_is_failure_while_voice_still_runs(coordinator):
    coordinator.hass.services.async_call.return_value = {}
    await coordinator._async_notify("default:one", "prepare")
    assert "default:one:prepare:telegram" not in coordinator._sent
    assert "default:one:prepare:script" in coordinator._sent
    assert coordinator.data["notification_channel_results"]["telegram"]["status"] == "failed"


async def test_wrong_target_ack_is_not_accepted(hass):
    hass.services.async_call.return_value = {
        "chats": [{"message_id": 12, "entity_id": "notify.other"}]
    }
    with pytest.raises(HomeAssistantError):
        await async_send_telegram_notify(hass, "notify.telegram", "message")


async def test_known_noop_script_cannot_be_recorded_as_success(hass):
    with pytest.raises(HomeAssistantError):
        await async_send_universal_notify(
            hass,
            notify_script="script.family_schedule_advisor_noop_notify",
            message="message",
            tts_target="media_player.speaker",
            tts_service="tts.speak",
            speed=0.9,
            pitch=-1.5,
        )
    hass.services.async_call.assert_not_awaited()
    assert config_flow._validate(
        {"notify_script": "script.family_schedule_advisor_noop_notify"}
    ) == {"notify_script": "noop_script"}


async def test_quiet_hours_keep_telegram_and_mute_voice(coordinator):
    coordinator.entry.options.update(quiet_enabled=True, quiet_start=0, quiet_end=0)
    await coordinator.async_request_refresh()
    await coordinator._async_notify("default:one", "departure")
    calls = coordinator.hass.services.async_call.call_args_list
    assert calls[0].args[0] == "telegram_bot"
    assert calls[1].args[2]["tts"] is False


async def test_notified_event_supplies_route_even_if_another_event_is_selected(coordinator):
    selected = coordinator._plans["default:one"]
    event = EventInfo("two", "두 번째 약속", NOW + timedelta(hours=4), location="병원")
    plan = Plan(
        event,
        selected.profile,
        "병원",
        "calendar_location",
        TransitResult(600, "10분", route_steps=["2번 버스"]),
    )
    plan.calculate_times(30, 10, 0)
    coordinator._plans[plan.key] = plan
    await coordinator._async_notify(plan.key, "prepare")
    text = coordinator.hass.services.async_call.call_args_list[0].args[2]["message"]
    assert "두 번째 약속" in text and "2번 버스" in text
    assert "첫 번째 약속" not in text and "1번 버스" not in text


def test_long_unicode_message_is_bounded_for_telegram():
    text = telegram_reminder_text(
        "😀" * 4000, {"event_title": "[약속]_!", "route_summary": "경로" * 2000}, "prepare"
    )
    assert len(text.encode("utf-16-le")) <= 8000
    assert text.startswith("가족 일정 준비 안내")


async def test_adding_channel_does_not_replay_successful_stage(coordinator):
    coordinator.entry.options["telegram_reminder_entity"] = ""
    await coordinator.async_request_refresh()
    await coordinator._async_notify("default:one", "prepare")
    coordinator.entry.options["telegram_reminder_entity"] = "notify.telegram"
    await coordinator.async_request_refresh()
    await coordinator._async_notify("default:one", "prepare")
    assert coordinator.hass.services.async_call.await_count == 1


async def test_old_completed_receipts_are_migrated_without_replay(coordinator):
    await coordinator.async_shutdown()
    Store.contents[coordinator._store.key] = {
        "sent": {
            "default:one:prepare:script": NOW.isoformat(),
            "default:one:departure:script": NOW.isoformat(),
        }
    }
    restored = module.FamilyScheduleAdvisorCoordinator(coordinator.hass, coordinator.entry)
    restored.async_set_updated_data(await restored._async_calculate())
    await restored.async_start()
    assert restored._stage_done(restored._plans["default:one"], "prepare")
    assert restored._stage_done(restored._plans["default:one"], "departure")
    assert not restored._timer_unsubs
    await restored.async_shutdown()


async def test_partial_old_delivery_is_not_migrated_as_complete(coordinator):
    coordinator.entry.options["mobile_notify_service"] = "notify.mobile_app_phone"
    coordinator._sent = {"default:one:prepare:script": NOW.isoformat()}
    coordinator._migrate_completed_stages()
    assert "default:one:prepare" not in coordinator._done


async def test_voice_timeout_allows_long_guidance(coordinator, monkeypatch):
    original = module.asyncio.wait_for
    timeouts = []

    async def wait(coro, timeout):
        timeouts.append(timeout)
        return await original(coro, timeout=timeout)

    monkeypatch.setattr(module.asyncio, "wait_for", wait)
    await coordinator._async_notify("default:one", "prepare")
    assert 600 in timeouts


async def test_notification_option_can_be_cleared_without_losing_others(entry):
    entry.options = {"telegram_reminder_entity": "notify.telegram", "prepare_minutes": 30}
    options = config_flow.FamilyScheduleAdvisorOptionsFlow()
    options.config_entry = entry
    result = await options.async_step_notifications({"notify_script": "script.universal_notify"})
    assert result["data"]["telegram_reminder_entity"] == ""
    assert result["data"]["prepare_minutes"] == 30
