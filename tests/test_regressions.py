from datetime import timedelta
from unittest.mock import AsyncMock

from conftest import NOW

from custom_components.family_schedule_advisor import calendar_parser as parser
from custom_components.family_schedule_advisor import coordinator as module
from custom_components.family_schedule_advisor.google_directions import TransitResult
from custom_components.family_schedule_advisor.planning import is_virtual, resolve_alias


async def test_same_title_and_time_at_distinct_places_are_not_merged(hass):
    async def call(*args, **kwargs):
        source = kwargs["target"]["entity_id"][0]
        return {
            source: {
                "events": [
                    {
                        "start": (NOW + timedelta(hours=2)).isoformat(),
                        "summary": "회의",
                        "location": source,
                    }
                ]
            }
        }

    hass.services.async_call.side_effect = call
    result = await parser.async_get_event_candidates(
        hass, ["calendar.office_a", "calendar.office_b"], 48, 0, 23
    )
    assert len(result) == 2 and result[0].event.key != result[1].event.key


def test_korean_alias_particles_are_supported():
    event = parser.EventInfo("one", "회사에서 미팅", NOW)
    assert resolve_alias(event, {"회사": "office address"}) == "office address"


def test_physical_place_is_not_mistaken_for_virtual_event():
    event = parser.EventInfo("one", "화상병원 방문", NOW, location="서울 병원 주소")
    assert not is_virtual(event)


async def test_prepare_action_keeps_departure_timer(hass, entry, monkeypatch):
    entry.options["departure_reminder"] = True
    event = parser.EventInfo(
        "one", "one", NOW + timedelta(hours=3), location="station", sources=("calendar.family",)
    )
    monkeypatch.setattr(
        module,
        "async_get_event_candidates",
        AsyncMock(return_value=[parser.EventCandidate(event, True)]),
    )
    monkeypatch.setattr(
        module, "async_get_transit_duration", AsyncMock(return_value=TransitResult(1200, "20분"))
    )
    coordinator = module.FamilyScheduleAdvisorCoordinator(hass, entry)
    coordinator.async_set_updated_data(await coordinator._async_calculate())
    await coordinator.async_start()
    assert len(coordinator._timer_unsubs) == 2
    await coordinator.async_event_action("prepared")
    assert list(coordinator._timer_unsubs) == ["default:one:departure"]
