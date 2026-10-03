import asyncio
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from conftest import NOW, ServiceValidationError, Store

from custom_components.family_schedule_advisor import coordinator as module
from custom_components.family_schedule_advisor.calendar_parser import EventCandidate, EventInfo
from custom_components.family_schedule_advisor.google_directions import TransitResult


def event(key, hours, source="calendar.family", **kwargs):
    return EventInfo(
        key,
        key,
        NOW + timedelta(hours=hours),
        source=source,
        sources=(source,),
        legacy_keys=(f"legacy:{key}",),
        location="station",
        **kwargs,
    )


@pytest.fixture
def coordinator(hass, entry):
    return module.FamilyScheduleAdvisorCoordinator(hass, entry)


async def load(coordinator, monkeypatch, events, route=None):
    monkeypatch.setattr(
        module,
        "async_get_event_candidates",
        AsyncMock(return_value=[EventCandidate(item, True) for item in events]),
    )
    monkeypatch.setattr(
        module,
        "async_get_transit_duration",
        AsyncMock(return_value=route or TransitResult(1200, "20分")),
    )
    coordinator.async_set_updated_data(await coordinator._async_calculate())
    await coordinator.async_start()


async def test_every_event_has_its_own_timer_and_long_trip_is_first(coordinator, monkeypatch):
    a, b = event("nearby", 3), event("far-away", 4)
    b.location = "far-away"

    async def route(*args, **kwargs):
        return TransitResult(7200 if args[3] == "far-away" else 1200, "duration")

    monkeypatch.setattr(
        module,
        "async_get_event_candidates",
        AsyncMock(return_value=[EventCandidate(a, True), EventCandidate(b, True)]),
    )
    monkeypatch.setattr(module, "async_get_transit_duration", route)
    coordinator.async_set_updated_data(await coordinator._async_calculate())
    await coordinator.async_start()
    assert len(coordinator._timer_unsubs) == 2
    assert coordinator.data["event_title"] == "far-away"
    assert coordinator.data["notify_time"].endswith("08:35:00+09:00")


async def test_restart_after_due_time_schedules_immediate_catch_up(coordinator, monkeypatch, hass):
    await load(coordinator, monkeypatch, [event("late", 0.5)])
    timers = [timer for timer in hass.timers if not timer.cancelled]
    assert len(timers) == 1 and timers[0].when == NOW + timedelta(seconds=1)


async def test_missed_prepare_and_departure_send_only_departure(coordinator, monkeypatch, entry):
    entry.options["departure_reminder"] = True
    await load(coordinator, monkeypatch, [event("late", 0.4)])
    assert list(coordinator._timer_unsubs) == ["default:late:departure"]


async def test_notified_first_event_does_not_block_later_timer(coordinator, monkeypatch):
    await load(coordinator, monkeypatch, [event("first", 3), event("second", 4)])
    await coordinator._async_notify("default:first", "prepare")
    assert list(coordinator._timer_unsubs) == ["default:second:prepare"]
    assert coordinator.data["event_title"] == "second"


async def test_success_requires_blocking_script_completion(coordinator, monkeypatch, hass):
    await load(coordinator, monkeypatch, [event("one", 3)])
    await coordinator._async_notify("default:one", "prepare")
    assert hass.services.async_call.call_args.kwargs["blocking"] is True
    assert "default:one:prepare:script" in coordinator._sent


async def test_failed_script_is_not_recorded_and_gets_retry(coordinator, monkeypatch, hass):
    await load(coordinator, monkeypatch, [event("one", 3)])
    hass.services.async_call.side_effect = RuntimeError("script failed")
    await coordinator._async_notify("default:one", "prepare")
    assert not coordinator._sent
    assert coordinator._retry_counts["default:one:prepare"] == 1
    assert "실패" in coordinator.data["last_notify_result"]
    assert len(coordinator._timer_unsubs) == 1


async def test_successful_channel_is_not_repeated_when_another_fails(
    coordinator, monkeypatch, hass, entry
):
    entry.options["mobile_notify_service"] = "notify.mobile_app_phone"
    await load(coordinator, monkeypatch, [event("one", 3)])

    async def call(domain, service, *args, **kwargs):
        if domain == "script":
            raise RuntimeError("script failed")

    hass.services.async_call.side_effect = call
    await coordinator._async_notify("default:one", "prepare")
    await coordinator._async_notify("default:one", "prepare")
    mobile_calls = [
        call for call in hass.services.async_call.call_args_list if call.args[0] == "notify"
    ]
    assert len(mobile_calls) == 1
    assert "default:one:prepare:mobile" in coordinator._sent
    assert "default:one:prepare:script" not in coordinator._sent


async def test_automatic_notification_never_waits_for_ai(coordinator, monkeypatch):
    await load(coordinator, monkeypatch, [event("one", 3)])
    generate = AsyncMock(side_effect=AssertionError("AI must not run on the notification path"))
    monkeypatch.setattr(module, "async_generate_text", generate)
    await coordinator._async_notify("default:one", "prepare")
    generate.assert_not_awaited()


async def test_concurrent_notify_attempts_do_not_duplicate(coordinator, monkeypatch, hass):
    await load(coordinator, monkeypatch, [event("one", 3)])

    async def delayed(*args, **kwargs):
        await asyncio.sleep(0.01)

    hass.services.async_call.side_effect = delayed
    await asyncio.gather(
        coordinator._async_notify("default:one", "prepare"),
        coordinator._async_notify("default:one", "prepare"),
    )
    assert hass.services.async_call.await_count == 1


async def test_test_notify_does_not_consume_real_notification(coordinator, monkeypatch):
    await load(coordinator, monkeypatch, [event("one", 3)])
    await coordinator._async_notify("default:one", "prepare", test=True)
    assert not coordinator._sent and "default:one:prepare" in coordinator._timer_unsubs


async def test_skip_cancels_timers_and_persists(coordinator, monkeypatch):
    await load(coordinator, monkeypatch, [event("one", 3)])
    await coordinator.async_event_action("skip")
    assert not coordinator._timer_unsubs
    assert "default:one:all" in Store.contents[coordinator._store.key]["done"]


async def test_snooze_after_success_creates_one_new_timer(coordinator, monkeypatch, hass):
    await load(coordinator, monkeypatch, [event("one", 3)])
    await coordinator._async_notify("default:one", "prepare")
    await coordinator.async_event_action("snooze", event_key="default:one", minutes=10)
    assert not coordinator._sent
    timers = [timer for timer in hass.timers if not timer.cancelled]
    assert len(timers) == 1 and timers[0].when == NOW + timedelta(minutes=10)


async def test_snooze_rejects_after_event_and_done_event(coordinator, monkeypatch):
    await load(coordinator, monkeypatch, [event("soon", 0.1)])
    with pytest.raises(ServiceValidationError):
        await coordinator.async_event_action("snooze", minutes=10)
    await coordinator.async_event_action("departed")
    with pytest.raises(ServiceValidationError):
        await coordinator.async_event_action("snooze", minutes=1)


async def test_legacy_history_survives_upgrade(coordinator, monkeypatch):
    Store.contents["family_schedule_advisor.storage"] = {"last_notified": ["legacy:one"]}
    await load(coordinator, monkeypatch, [event("one", 3)])
    assert not coordinator._timer_unsubs


async def test_snooze_survives_restart(coordinator, monkeypatch, entry, hass):
    await load(coordinator, monkeypatch, [event("one", 3)])
    await coordinator.async_event_action("snooze", minutes=10)
    await coordinator.async_shutdown()
    restarted = module.FamilyScheduleAdvisorCoordinator(hass, entry)
    restarted.async_set_updated_data(await restarted._async_calculate())
    await restarted.async_start()
    timers = [timer for timer in hass.timers if not timer.cancelled]
    assert len(timers) == 1 and timers[0].when == NOW + timedelta(minutes=10)


async def test_profiles_match_all_sources_and_query_extra_calendar(coordinator, monkeypatch, entry):
    entry.options["family_profiles"] = {
        "child": {"name": "child", "calendar_entities": ["calendar.child"], "prepare_minutes": 30}
    }
    shared = event("shared", 3)
    shared.sources = ("calendar.family", "calendar.child")
    await load(coordinator, monkeypatch, [shared])
    assert list(coordinator._plans) == ["child:shared"]
    assert "calendar.child" in coordinator._calendar_entities()
    assert coordinator.data["notify_time"].endswith("09:00:00+09:00")


async def test_none_destination_does_not_fall_back_to_title(coordinator, monkeypatch):
    item = event("not-a-place", 3)
    item.location = ""
    await load(coordinator, monkeypatch, [item])
    assert coordinator.data["destination"] == ""
    assert coordinator.data["departure_time"] is None
    module.async_get_transit_duration.assert_not_awaited()


async def test_saved_alias_prevents_ai_destination_request(coordinator, monkeypatch, entry):
    entry.options["place_aliases"] = {"회사": "office address"}
    item = event("회사 회의", 3)
    item.location = ""
    extract = AsyncMock(side_effect=AssertionError("should use alias"))
    monkeypatch.setattr(module, "async_extract_destination", extract)
    await load(coordinator, monkeypatch, [item])
    assert coordinator.data["destination"] == "office address"
    extract.assert_not_awaited()


async def test_quiet_hours_keep_text_and_disable_speech(coordinator, monkeypatch, entry, hass):
    entry.options.update(quiet_enabled=True, quiet_start=0, quiet_end=8)
    await load(coordinator, monkeypatch, [event("one", 3)])
    await coordinator._async_notify("default:one", "prepare")
    assert hass.services.async_call.call_args.args[2]["tts"] is False


async def test_presence_away_disables_speech(coordinator, monkeypatch, entry, hass):
    entry.options["family_profiles"] = {
        "member": {
            "name": "member",
            "calendar_entities": ["calendar.family"],
            "person_entity": "person.member",
        }
    }
    hass.states.set("person.member", "not_home")
    await load(coordinator, monkeypatch, [event("one", 3)])
    await coordinator._async_notify("member:one", "prepare")
    assert hass.services.async_call.call_args.args[2]["tts"] is False


async def test_route_is_cached_on_periodic_refresh(coordinator, monkeypatch):
    await load(coordinator, monkeypatch, [event("one", 3)])
    await coordinator.async_request_refresh()
    assert module.async_get_transit_duration.await_count == 1


async def test_removed_event_cannot_be_notified_by_stale_timer(coordinator, monkeypatch, hass):
    await load(coordinator, monkeypatch, [event("one", 3)])
    coordinator._plans.clear()
    await coordinator._async_notify("default:one", "prepare")
    hass.services.async_call.assert_not_awaited()


async def test_mobile_actions_have_entry_and_event_scope(coordinator, monkeypatch, entry):
    entry.options["mobile_notify_service"] = "notify.mobile_app_phone"
    await load(coordinator, monkeypatch, [event("one", 3)])
    action = coordinator._notification_actions(coordinator._plans["default:one"], "prepare")[1][
        "action"
    ]
    coordinator._mobile_action(SimpleNamespace(data={"action": action}))
    await asyncio.gather(*list(coordinator._tasks))
    assert "default:one:all" in coordinator._done


async def test_shutdown_removes_all_listeners_and_timers(coordinator, monkeypatch, hass):
    await load(coordinator, monkeypatch, [event("one", 3)])
    await coordinator.async_shutdown()
    assert not coordinator._timer_unsubs and not coordinator._unsubs
    assert not hass.bus_listeners and all(timer.cancelled for timer in hass.timers)
