from datetime import timedelta
from unittest.mock import AsyncMock

import pytest
from conftest import NOW

from custom_components.family_schedule_advisor import coordinator as module
from custom_components.family_schedule_advisor.calendar_parser import EventCandidate, EventInfo
from custom_components.family_schedule_advisor.config_flow import FamilyScheduleAdvisorOptionsFlow
from custom_components.family_schedule_advisor.google_directions import TransitResult
from custom_components.family_schedule_advisor.planning import Plan, fallback_message
from custom_components.family_schedule_advisor.sensor import SENSORS, AdvisorSensor


def event():
    return EventInfo(
        "meeting",
        "병원 진료",
        NOW + timedelta(hours=3),
        location="서울역",
        source="calendar.family",
        sources=("calendar.family",),
    )


@pytest.mark.parametrize("prepare,margin,travel", [(20, 10, 30), (45, 15, 70), (0, 0, 20)])
def test_known_route_uses_all_three_time_inputs(prepare, margin, travel):
    plan = Plan(
        event(), {}, "서울역", "calendar_location", TransitResult(travel * 60, f"{travel}분")
    )
    plan.calculate_times(prepare, margin, 0)
    assert plan.notify_time == plan.event.start - timedelta(minutes=travel + margin + prepare)
    data = plan.as_data()
    assert data["prepare_minutes"] == prepare and data["arrival_margin_minutes"] == margin
    assert data["reminder_basis"] == "route_departure"
    assert f"준비 {prepare}분" in data["preparation_calculation"]
    message = fallback_message(plan)
    assert "목적지는 서울역" in message and f"이동시간은 {travel}분" in message
    assert "설정한 준비시간" not in message


def test_transit_uses_actual_departure_and_explains_it():
    item = event()
    departure = item.start - timedelta(minutes=80)
    plan = Plan(
        item,
        {},
        "서울역",
        "calendar_location",
        TransitResult(1800, "30분", departure_time=departure),
    )
    plan.calculate_times(20, 10, 0)
    assert plan.notify_time == item.start - timedelta(minutes=100)
    assert "경로 출발" in plan.as_data()["preparation_calculation"]
    assert "준비 20분" in plan.as_data()["preparation_calculation"]


@pytest.mark.parametrize("prepare", [0, 15, 90])
def test_unknown_travel_keeps_one_hour_and_labels_the_reason(prepare):
    plan = Plan(event(), {}, "", "none")
    plan.calculate_times(prepare, 25, 0)
    data = plan.as_data()
    assert plan.notify_time == plan.event.start - timedelta(hours=1)
    assert data["prepare_minutes"] == prepare
    assert data["reminder_basis"] == "unresolved_60"
    assert "이동정보 미확인" in data["preparation_calculation"]
    assert "기본 60분" in data["preparation_calculation"]
    assert "기본 준비 알림" in fallback_message(plan)


def test_user_departure_message_is_preserved():
    plan = Plan(event(), {}, "서울역", "calendar_location", TransitResult(1800, "30분"))
    plan.calculate_times(20, 10, 0)
    text = fallback_message(plan, "departure")
    assert "목적지는 서울역" in text and "출발 예정 시각은" in text
    assert "예상 이동시간은 30분" in text and "지금 출발해 주세요" in text


async def test_options_change_reschedules_preparation_timer_using_cached_route(
    hass, entry, monkeypatch
):
    calendar = AsyncMock(return_value=[EventCandidate(event(), True)])
    route = AsyncMock(return_value=TransitResult(1800, "30분"))
    monkeypatch.setattr(module, "async_get_event_candidates", calendar)
    monkeypatch.setattr(module, "async_get_transit_duration", route)
    instance = module.FamilyScheduleAdvisorCoordinator(hass, entry)
    instance.async_set_updated_data(await instance._async_calculate())
    await instance.async_start()
    previous = [timer for timer in hass.timers if not timer.cancelled]
    assert previous[0].when == event().start - timedelta(minutes=55)
    options = FamilyScheduleAdvisorOptionsFlow()
    options.config_entry = entry
    result = await options.async_step_general(
        {
            "calendar_entities": ["calendar.family"],
            "origin_address": "home",
            "prepare_minutes": 35,
            "arrival_margin_minutes": 10,
        }
    )
    entry.options = result["data"]
    await instance.async_request_refresh()
    current = [timer for timer in hass.timers if not timer.cancelled]
    assert len(current) == 1 and current[0].when == event().start - timedelta(minutes=75)
    assert previous[0].cancelled
    assert route.await_count == 1
    assert instance.data["prepare_minutes"] == 35
    await instance.async_shutdown()


async def test_family_preparation_overrides_general_setting(hass, entry, monkeypatch):
    entry.options = {
        "prepare_minutes": 50,
        "family_profiles": {
            "child": {
                "name": "아이",
                "calendar_entities": ["calendar.family"],
                "prepare_minutes": 20,
            },
            "parent": {
                "name": "부모",
                "calendar_entities": ["calendar.family"],
                "prepare_minutes": 40,
            },
        },
    }
    monkeypatch.setattr(
        module,
        "async_get_event_candidates",
        AsyncMock(return_value=[EventCandidate(event(), True)]),
    )
    monkeypatch.setattr(
        module, "async_get_transit_duration", AsyncMock(return_value=TransitResult(1800, "30분"))
    )
    instance = module.FamilyScheduleAdvisorCoordinator(hass, entry)
    data = await instance._async_calculate()
    members = {item["person_name"]: item for item in data["upcoming_events"]}
    assert members["아이"]["prepare_minutes"] == 20
    assert members["부모"]["prepare_minutes"] == 40
    assert members["아이"]["notify_time"] == (event().start - timedelta(minutes=60)).isoformat()
    assert members["부모"]["notify_time"] == (event().start - timedelta(minutes=80)).isoformat()


@pytest.mark.parametrize(
    "key",
    ["status", "notify_time", "notify_timestamp", "recognized_event", "preparation_calculation"],
)
def test_sensor_attributes_explain_preparation_calculation(key, entry):
    from types import SimpleNamespace

    plan = Plan(event(), {}, "서울역", "calendar_location", TransitResult(1800, "30분"))
    plan.calculate_times(20, 10, 0)
    sensor = AdvisorSensor(
        SimpleNamespace(data=plan.as_data()),
        entry,
        next(description for description in SENSORS if description.key == key),
    )
    attributes = sensor.extra_state_attributes
    assert attributes["prepare_minutes"] == 20
    assert attributes["reminder_basis"] == "route_departure"
    assert "준비 20분" in attributes["preparation_calculation"]
