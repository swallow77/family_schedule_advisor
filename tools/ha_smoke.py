"""Validate imports and UI selectors using actual Home Assistant, without a server."""

import asyncio
import sys
from datetime import timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from types import MappingProxyType
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from homeassistant.config_entries import ConfigEntry  # noqa: E402
from homeassistant.core import HomeAssistant  # noqa: E402
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
            options={},
            source="user",
            unique_id="smoke",
            discovery_keys=MappingProxyType({}),
            subentries_data=None,
        )
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
        instance.async_set_updated_data(data)
        await instance.async_shutdown()
        await instance.session.close()


asyncio.run(runtime_check())
print(
    "Real Home Assistant imports, configuration validation, number boxes, entity descriptions and coordinator initialization/calculation passed."
)
