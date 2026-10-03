from datetime import timedelta
from unittest.mock import AsyncMock

import aiohttp
import pytest
from conftest import NOW

from custom_components.family_schedule_advisor import ollama_client
from custom_components.family_schedule_advisor.google_directions import async_get_transit_duration
from custom_components.family_schedule_advisor.google_routes import async_get_route
from custom_components.family_schedule_advisor.ollama_client import (
    async_extract_destination,
    sanitize_tts,
)


class Response:
    def __init__(self, data, status=200):
        self.data = data
        self.status = status

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass

    async def json(self, **kwargs):
        return self.data


class Session:
    def __init__(self, data, status=200):
        self.response = Response(data, status)
        self.kwargs = None

    def get(self, url, **kwargs):
        self.kwargs = kwargs
        return self.response

    post = get


async def test_directions_retains_actual_departure_and_arrival():
    departure = NOW + timedelta(hours=2, minutes=10)
    arrival = departure + timedelta(minutes=30)
    session = Session(
        {
            "status": "OK",
            "routes": [
                {
                    "legs": [
                        {
                            "duration": {"value": 1800, "text": "30분"},
                            "departure_time": {"value": departure.timestamp()},
                            "arrival_time": {"value": arrival.timestamp()},
                            "steps": [],
                        }
                    ]
                }
            ],
        }
    )
    result = await async_get_transit_duration(
        session, "dummy", "home", "station", NOW + timedelta(hours=3)
    )
    assert result.departure_time == departure and result.arrival_time == arrival
    assert session.kwargs["params"]["mode"] == "transit"


@pytest.mark.parametrize("mode", ["walking", "driving"])
async def test_non_transit_directions_omit_invalid_arrival_parameter(mode):
    session = Session({"status": "ZERO_RESULTS"})
    await async_get_transit_duration(session, "dummy", "home", "station", NOW, mode=mode)
    assert "arrival_time" not in session.kwargs["params"]
    assert session.kwargs["params"]["mode"] == mode


async def test_connection_error_does_not_leak_key(caplog):
    class Broken:
        def get(self, *args, **kwargs):
            raise aiohttp.ClientError("url?key=SECRET_API_KEY")

    result = await async_get_transit_duration(Broken(), "SECRET_API_KEY", "home", "station", NOW)
    assert result.status == "ERROR"
    assert "SECRET_API_KEY" not in result.error_message
    assert "SECRET_API_KEY" not in caplog.text


async def test_routes_provider_subtracts_initial_walk_from_transit_departure():
    transit_departure = NOW + timedelta(hours=2)
    session = Session(
        {
            "routes": [
                {
                    "duration": "2400s",
                    "legs": [
                        {
                            "steps": [
                                {"travelMode": "WALK", "staticDuration": "300s"},
                                {
                                    "travelMode": "TRANSIT",
                                    "staticDuration": "1800s",
                                    "transitDetails": {
                                        "stopDetails": {
                                            "departureTime": transit_departure.isoformat(),
                                            "arrivalTime": (
                                                transit_departure + timedelta(minutes=30)
                                            ).isoformat(),
                                            "departureStop": {"name": "A"},
                                            "arrivalStop": {"name": "B"},
                                        },
                                        "transitLine": {"nameShort": "1호선"},
                                    },
                                },
                                {"travelMode": "WALK", "staticDuration": "300s"},
                            ]
                        }
                    ],
                }
            ]
        }
    )
    result = await async_get_route(session, "dummy", "home", "station", NOW + timedelta(hours=3))
    assert result.departure_time == transit_departure - timedelta(minutes=5)
    assert result.arrival_time == transit_departure + timedelta(minutes=35)
    assert "arrivalTime" in session.kwargs["json"]
    assert "1호선" in result.route_summary


async def test_routes_provider_surfaces_http_failure():
    result = await async_get_route(
        Session({"error": "not enabled"}, 403), "dummy", "home", "station", NOW
    )
    assert result.status == "ERROR" and result.duration_seconds == 0


def test_tts_removes_thinking_and_markdown():
    assert (
        sanitize_tts("<think>private reasoning</think> **안내문**\n다음 문장") == "안내문 다음 문장"
    )
    assert sanitize_tts("<think>unfinished reasoning") == ""


async def test_none_destination_remains_empty_and_preserves_address_period(monkeypatch):
    generate = AsyncMock(return_value="NONE")
    monkeypatch.setattr(ollama_client, "async_generate_text", generate)
    assert await async_extract_destination(None, "url", "model", "온라인", "") == ""
    assert generate.call_args.kwargs["timeout"] == 8
    generate.return_value = "St. Mary's Hospital"
    assert (
        await async_extract_destination(None, "url", "model", "병원", "") == "St. Mary's Hospital"
    )
