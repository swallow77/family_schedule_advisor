from datetime import timedelta

import pytest
from conftest import NOW, HomeAssistantError

from custom_components.family_schedule_advisor import calendar_parser as parser
from custom_components.family_schedule_advisor.google_directions import TransitResult
from custom_components.family_schedule_advisor.planning import (
    Plan,
    fallback_message,
    find_conflicts,
    forecast_window,
    is_quiet,
    resolve_alias,
)


def event(title="서울역", hours=3, **kwargs):
    start = NOW + timedelta(hours=hours)
    return parser.EventInfo(
        parser._event_key("calendar.family", start, title),
        title,
        start,
        source="calendar.family",
        sources=("calendar.family",),
        **kwargs,
    )


async def test_calendar_and_sensor_are_both_collected(hass):
    async def call(*args, **kwargs):
        return {
            "calendar.family": {
                "events": [
                    {"start": (NOW + timedelta(hours=3)).isoformat(), "summary": "calendar10"}
                ]
            }
        }

    hass.services.async_call.side_effect = call
    hass.states.set("sensor.legacy", "10월 03일 09:00 sensor09")
    result = await parser.async_get_event_candidates(
        hass, ["calendar.family", "sensor.legacy"], 48, 0, 23
    )
    assert [item.event.title for item in result] == ["sensor09", "calendar10"]


async def test_duplicates_keep_richest_location_and_all_sources(hass):
    hass.services.async_call.return_value = {
        "calendar.family": {
            "events": [
                {
                    "start": (NOW + timedelta(hours=3)).isoformat(),
                    "summary": "서울역",
                    "location": "서울역 주소",
                }
            ]
        }
    }
    hass.states.set("sensor.legacy", "10월 03일 10:00 서울역")
    result = await parser.async_get_event_candidates(
        hass, ["calendar.family", "sensor.legacy"], 48, 0, 23
    )
    assert len(result) == 1
    assert result[0].event.location == "서울역 주소"
    assert set(result[0].event.sources) == {"calendar.family", "sensor.legacy"}
    assert len(result[0].event.legacy_keys) == 2


async def test_partial_calendar_outage_does_not_hide_working_source(hass):
    async def call(*args, **kwargs):
        source = kwargs["target"]["entity_id"][0]
        if source == "calendar.broken":
            raise HomeAssistantError("offline")
        return {
            source: {
                "events": [{"start": (NOW + timedelta(hours=2)).isoformat(), "summary": "working"}]
            }
        }

    hass.services.async_call.side_effect = call
    errors = []
    result = await parser.async_get_event_candidates(
        hass, ["calendar.broken", "calendar.family"], 48, 0, 23, errors
    )
    assert len(result) == 1 and errors == ["calendar.broken"]


async def test_total_calendar_outage_is_not_an_empty_calendar(hass):
    hass.services.async_call.side_effect = HomeAssistantError("offline")
    with pytest.raises(HomeAssistantError):
        await parser.async_get_event_candidates(hass, ["calendar.family"], 48, 0, 23)


@pytest.mark.parametrize(
    "item",
    [
        {"start": "2026-10-03", "summary": "종일"},
        {"start": (NOW + timedelta(hours=2)).isoformat(), "status": "cancelled"},
        {"start": "invalid"},
    ],
)
def test_all_day_cancelled_and_invalid_events_are_excluded(item):
    assert parser._parse_calendar_event("calendar.family", item) is None


def test_naive_datetime_uses_ha_timezone():
    value = parser._parse_datetime(NOW.replace(tzinfo=None))
    assert value == NOW


def test_keys_do_not_collide_on_long_titles_or_different_sources():
    prefix = "아" * 300
    assert parser._event_key("calendar.a", NOW, prefix + "a") != parser._event_key(
        "calendar.a", NOW, prefix + "b"
    )
    assert parser._event_key("calendar.a", NOW, "same") == parser._event_key(
        "sensor.b", NOW, "same"
    )


@pytest.mark.parametrize("hour,accepted", [(23, True), (2, True), (10, False)])
def test_overnight_event_hour_filter(hour, accepted):
    item = event()
    item.start = (NOW + timedelta(days=1)).replace(hour=hour)
    candidate = parser._validate_event(item, NOW, NOW + timedelta(days=2), 22, 6)
    assert candidate.accepted is accepted


def test_route_uses_actual_departure_instead_of_target_minus_duration():
    item = event()
    actual = item.start - timedelta(minutes=50)
    route = TransitResult(
        1800, "30분", departure_time=actual, arrival_time=actual + timedelta(minutes=30)
    )
    plan = Plan(item, {"id": "default"}, "station", "calendar_location", route)
    plan.calculate_times(15, 10, 0)
    assert plan.departure_time == actual
    assert plan.notify_time == actual - timedelta(minutes=15)


def test_failed_route_never_invents_departure_time():
    plan = Plan(event(), {}, "station", "location", TransitResult(0, "", status="ZERO_RESULTS"))
    plan.calculate_times(15, 10, 0)
    assert plan.departure_time is None and plan.status == "경로 확인 필요"
    assert plan.notify_time == plan.event.start - timedelta(minutes=60)
    assert "직접 확인" in fallback_message(plan)


@pytest.mark.parametrize(
    "destination,status",
    [("", None), ("station", None), ("station", "ZERO_RESULTS"), ("station", "ERROR")],
)
def test_unresolved_reminder_is_exactly_one_hour_before_start(destination, status):
    route = TransitResult(0, "", status=status) if status else None
    plan = Plan(event(), {}, destination, "location", route)
    plan.calculate_times(45, 20, 0)
    assert plan.notify_time == plan.event.start - timedelta(hours=1)
    assert plan.departure_time is None


def test_manual_fallback_is_explicit_and_includes_margin():
    plan = Plan(event(), {"fallback_travel_minutes": 90}, "station", "location")
    plan.calculate_times(15, 10, 90)
    assert plan.departure_time == plan.event.start - timedelta(minutes=100)
    assert plan.notify_time == plan.event.start - timedelta(minutes=115)
    assert plan.estimated and "직접 설정" in plan.as_data()["transit_duration_text"]


def test_virtual_event_has_no_travel_even_with_manual_fallback():
    plan = Plan(event("온라인 회의"), {}, "", "virtual")
    plan.calculate_times(15, 10, 60)
    assert plan.departure_time is None and plan.route_status == "VIRTUAL"
    assert plan.notify_time == plan.event.start - timedelta(minutes=15)
    assert "온라인" in fallback_message(plan)


def test_alias_matches_specific_place_but_not_part_of_another_word():
    assert resolve_alias(event("학교 회의"), {"학교": "school address"}) == "school address"
    assert resolve_alias(event("대학교 회의"), {"학교": "school address"}) == ""


@pytest.mark.parametrize("hour,expected", [(23, True), (6, True), (7, False), (12, False)])
def test_quiet_hours_cross_midnight(hour, expected):
    assert is_quiet(NOW.replace(hour=hour), True, 22, 7) is expected


def test_conflicts_only_apply_to_same_person_and_include_travel():
    a = Plan(event("first", 3, end=NOW + timedelta(hours=4)), {"id": "a"}, "station", "location")
    b = Plan(event("second", 5), {"id": "a"}, "station", "location")
    b.departure_time = NOW + timedelta(hours=3, minutes=45)
    assert len(find_conflicts([a, b])) == 1
    b.profile = {"id": "b"}
    assert find_conflicts([a, b]) == []


def test_forecast_window_includes_current_hour_but_not_stale_forecast():
    forecasts = [
        {"datetime": (NOW + timedelta(minutes=offset)).isoformat(), "temperature": value}
        for offset, value in [(-120, 0), (-30, 10), (30, 12), (90, 30)]
    ]
    result = forecast_window(forecasts, NOW, NOW + timedelta(hours=1))
    assert [item["temperature"] for item in result] == [10, 12]


def test_forecast_weather_is_used_in_fallback():
    plan = Plan(event(), {}, "station", "location")
    plan.weather = {"temp": "30도", "rain": "0%", "forecast_temp": "5도", "forecast_rain": "80%"}
    message = fallback_message(plan)
    assert "따뜻한 겉옷" in message and "우산" in message
