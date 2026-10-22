"""Validate imports and UI selectors using actual Home Assistant, without a server."""

import asyncio
import sys
from datetime import timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from types import MappingProxyType
from unittest.mock import AsyncMock, patch

import aiohttp

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from homeassistant.config_entries import ConfigEntry  # noqa: E402
from homeassistant.core import HomeAssistant, SupportsResponse  # noqa: E402
from homeassistant.exceptions import HomeAssistantError  # noqa: E402
from homeassistant.helpers import config_validation as cv  # noqa: E402
from homeassistant.helpers.script import Script  # noqa: E402
from homeassistant.helpers.selector import NumberSelector  # noqa: E402
from homeassistant.util import dt as dt_util  # noqa: E402

from custom_components.family_schedule_advisor import (  # noqa: E402
    button,
    config_flow,
    coordinator,
    sensor,
)
from custom_components.family_schedule_advisor.calendar_parser import (  # noqa: E402
    EventCandidate,
    EventInfo,
)
from custom_components.family_schedule_advisor.google_directions import TransitResult  # noqa: E402
from custom_components.family_schedule_advisor.notify import (  # noqa: E402
    async_send_universal_notify,
)


def check_boxes(schema):
    count = 0
    for value in schema.schema.values():
        if isinstance(value, NumberSelector):
            assert value.config["mode"] == "box"
            count += 1
    assert count > 0


check_boxes(config_flow._schema({}))
check_boxes(config_flow._notification_schema({}))
values = config_flow._schema({})(
    {"calendar_entities": ["calendar.family"], "origin_address": "home"}
)
assert config_flow._normalize_user_input(values)["prepare_minutes"] == 15
assert len(sensor.SENSORS) >= 17 and len(button.BUTTONS) >= 6
assert coordinator.FamilyScheduleAdvisorCoordinator is not None


async def runtime_check():
    with TemporaryDirectory() as directory:
        hass = HomeAssistant(directory)
        # Execute HA's real stop/response machinery behind the service boundary.
        # No external service, speaker or notification is contacted.
        for status in ("failed", "played", "suppressed"):
            script = Script(
                hass,
                cv.SCRIPT_SCHEMA(
                    [
                        {"variables": {"result": {"tts_status": status}}},
                        {"stop": "result", "response_variable": "result"},
                    ]
                ),
                "playback_ack",
                "script",
            )

            async def handler(call, instance=script):
                result = await instance.async_run(dict(call.data))
                return result.service_response

            hass.services.async_register(
                "script", "playback_ack", handler, supports_response=SupportsResponse.OPTIONAL
            )
            failed = False
            try:
                await async_send_universal_notify(
                    hass,
                    notify_script="script.playback_ack",
                    message="isolated test",
                    tts_target="media_player.test",
                    tts_service="tts.speak",
                    speed=1,
                    pitch=0,
                )
            except HomeAssistantError:
                failed = True
            assert failed == (status == "failed")
            await script.async_unload()
        entry = ConfigEntry(
            version=1,
            minor_version=1,
            domain="family_schedule_advisor",
            title="Smoke",
            data={
                "calendar_entities": ["calendar.family"],
                "origin_address": "home",
                "ollama_url": "",
                "enable_outfit_ai": False,
            },
            options={"prepare_minutes": 20, "arrival_margin_minutes": 10},
            source="user",
            unique_id="smoke",
            discovery_keys=MappingProxyType({}),
            subentries_data=None,
        )
        # The HTTP boundary is isolated; initializing HA's network/zeroconf
        # stack is outside an import/configuration/coordinator smoke check.
        with patch.object(
            coordinator, "async_get_clientsession", return_value=aiohttp.ClientSession()
        ):
            instance = coordinator.FamilyScheduleAdvisorCoordinator(hass, entry)
        event = EventInfo(
            "smoke",
            "온라인 회의",
            dt_util.now() + timedelta(hours=2),
            source="calendar.family",
            sources=("calendar.family",),
        )
        with patch.object(
            coordinator,
            "async_get_event_candidates",
            AsyncMock(return_value=[EventCandidate(event, True)]),
        ):
            data = await instance._async_calculate()
        assert data["route_status"] == "VIRTUAL" and data["departure_time"] is None
        assert data["reminder_basis"] == "virtual_preparation"
        event.title = "Appointment"
        event.location = "Station"
        with (
            patch.object(
                coordinator,
                "async_get_event_candidates",
                AsyncMock(return_value=[EventCandidate(event, True)]),
            ),
            patch.object(
                coordinator,
                "async_get_transit_duration",
                AsyncMock(return_value=TransitResult(1800, "30 min")),
            ),
        ):
            data = await instance._async_calculate()
        assert dt_util.parse_datetime(data["notify_time"]) == event.start - timedelta(minutes=60)
        assert data["prepare_minutes"] == 20 and data["reminder_basis"] == "route_departure"
        assert data["preparation_calculation"]
        instance.async_set_updated_data(data)
        await instance.async_shutdown()
        await instance.session.close()


asyncio.run(runtime_check())
print(
    "Real Home Assistant imports, configuration validation, number boxes, entity descriptions and coordinator initialization/calculation passed."
)
