"""Small HA boundary doubles; all scheduling/provider code is the real integration.

The separate Home Assistant smoke job validates imports and selectors against
the actual Home Assistant package. These tests do not contact live services.
"""

from __future__ import annotations

import asyncio
import sys
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock
from zoneinfo import ZoneInfo

import pytest
import voluptuous as vol

ROOT = Path(__file__).resolve().parents[1]
ZONE = ZoneInfo("Asia/Seoul")
NOW = datetime(2026, 10, 3, 7, tzinfo=ZONE)


def module(name, **attributes):
    value = ModuleType(name)
    value.__dict__.update(attributes)
    sys.modules[name] = value
    if "." in name and name.rsplit(".", 1)[0] in sys.modules:
        setattr(sys.modules[name.rsplit(".", 1)[0]], name.rsplit(".", 1)[1], value)
    return value


class HomeAssistantError(Exception):
    pass


class ServiceValidationError(HomeAssistantError):
    pass


class UpdateFailed(HomeAssistantError):
    pass


class Coordinator:
    @classmethod
    def __class_getitem__(cls, item):
        return cls

    def __init__(self, hass, logger, **kwargs):
        self.hass = hass
        self.data = None
        self.last_update_success = True
        self.update_interval = kwargs.get("update_interval")
        self.listeners = []

    def async_set_updated_data(self, value):
        self.data = value
        for listener in list(self.listeners):
            listener()

    def async_add_listener(self, listener):
        self.listeners.append(listener)
        return lambda: self.listeners.remove(listener)

    async def async_request_refresh(self):
        try:
            self.async_set_updated_data(await self._async_update_data())
            self.last_update_success = True
        except UpdateFailed:
            self.last_update_success = False

    async_config_entry_first_refresh = async_request_refresh


class Store:
    contents = {}

    def __init__(self, hass, version, key):
        self.key = key

    async def async_load(self):
        return deepcopy(self.contents.get(self.key))

    async def async_save(self, data):
        self.contents[self.key] = deepcopy(data)


class Flow:
    def __init_subclass__(cls, **kwargs):
        pass

    def async_show_form(self, **kwargs):
        return {"type": "form", **kwargs}

    def async_show_menu(self, **kwargs):
        return {"type": "menu", **kwargs}

    def async_create_entry(self, **kwargs):
        return {"type": "create_entry", **kwargs}

    async def async_set_unique_id(self, value):
        self.unique_id = value

    def _abort_if_unique_id_configured(self):
        pass


class Selector:
    def __init__(self, config=None):
        self.config = config or {}

    def __call__(self, value):
        return value


class NumberSelector(Selector):
    def __call__(self, value):
        try:
            value = float(value)
        except (ValueError, TypeError) as err:
            raise vol.Invalid("number") from err
        if not self.config.get("min", value) <= value <= self.config.get("max", value):
            raise vol.Invalid("range")
        return value


class NumberSelectorMode(StrEnum):
    BOX = "box"
    SLIDER = "slider"


class EntitySelector(Selector):
    def __call__(self, value):
        values = value if self.config.get("multiple") else [value]
        for item in values:
            if not isinstance(item, str) or "." not in item:
                raise vol.Invalid("entity")
        return value


class SelectSelector(Selector):
    def __call__(self, value):
        options = [
            item["value"] if isinstance(item, dict) else item for item in self.config["options"]
        ]
        if value not in options:
            raise vol.Invalid("choice")
        return value


@dataclass(frozen=True, kw_only=True)
class Description:
    key: str
    translation_key: str = ""
    icon: str = ""
    device_class: str | None = None


class Entity:
    @classmethod
    def __class_getitem__(cls, item):
        return cls

    def __init__(self, coordinator):
        self.coordinator = coordinator


class States:
    def __init__(self):
        self.values = {}

    def get(self, entity_id):
        return self.values.get(entity_id)

    def set(self, entity_id, state, **attributes):
        self.values[entity_id] = SimpleNamespace(state=state, attributes=attributes)


class Hass:
    def __init__(self):
        self.states = States()
        self.data = {}
        self.timers = []
        self.bus_listeners = {}
        self.bus = SimpleNamespace(async_listen=self.listen)
        self.services = SimpleNamespace(
            async_call=AsyncMock(return_value={}), has_service=lambda *args: True
        )

    def listen(self, name, listener):
        self.bus_listeners[name] = listener
        return lambda: self.bus_listeners.pop(name, None)

    def async_create_task(self, coroutine):
        return asyncio.create_task(coroutine)


def track(hass, callback, when):
    timer = SimpleNamespace(callback=callback, when=when, cancelled=False)
    hass.timers.append(timer)
    return lambda: setattr(timer, "cancelled", True)


module("homeassistant")
module(
    "homeassistant.core",
    HomeAssistant=Hass,
    ServiceCall=SimpleNamespace,
    callback=lambda fn: fn,
    CALLBACK_TYPE=object,
)
module(
    "homeassistant.config_entries", ConfigEntry=SimpleNamespace, ConfigFlow=Flow, OptionsFlow=Flow
)
module("homeassistant.const", Platform=SimpleNamespace(SENSOR="sensor", BUTTON="button"))
module(
    "homeassistant.exceptions",
    HomeAssistantError=HomeAssistantError,
    ServiceValidationError=ServiceValidationError,
)
module("homeassistant.helpers")
module("homeassistant.helpers.typing", ConfigType=dict)
module("homeassistant.helpers.aiohttp_client", async_get_clientsession=lambda hass: object())
module(
    "homeassistant.helpers.event",
    async_track_point_in_time=track,
    async_track_state_change_event=lambda *args: lambda: None,
)
module("homeassistant.helpers.storage", Store=Store)
module(
    "homeassistant.helpers.update_coordinator",
    DataUpdateCoordinator=Coordinator,
    UpdateFailed=UpdateFailed,
    CoordinatorEntity=Entity,
)
module("homeassistant.helpers.entity_platform", AddEntitiesCallback=object)
module(
    "homeassistant.helpers.selector",
    NumberSelector=NumberSelector,
    NumberSelectorConfig=dict,
    NumberSelectorMode=NumberSelectorMode,
    EntitySelector=EntitySelector,
    EntitySelectorConfig=dict,
    SelectSelector=SelectSelector,
    SelectSelectorConfig=dict,
    TextSelector=Selector,
    TextSelectorConfig=dict,
    TextSelectorType=SimpleNamespace(PASSWORD="password"),
)
module("homeassistant.components")
module(
    "homeassistant.components.sensor",
    SensorEntity=type("SensorEntity", (), {}),
    SensorEntityDescription=Description,
    SensorDeviceClass=SimpleNamespace(TIMESTAMP="timestamp"),
)
module(
    "homeassistant.components.button",
    ButtonEntity=type("ButtonEntity", (), {}),
    ButtonEntityDescription=Description,
)
module("homeassistant.util")


def parse_datetime(value):
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, TypeError, AttributeError):
        return None


dt = module(
    "homeassistant.util.dt",
    DEFAULT_TIME_ZONE=ZONE,
    now=lambda: NOW,
    as_local=lambda value: value.astimezone(ZONE),
    parse_datetime=parse_datetime,
)
package = module("custom_components")
package.__path__ = [str(ROOT / "custom_components")]
package = module("custom_components.family_schedule_advisor")
package.__path__ = [str(ROOT / "custom_components/family_schedule_advisor")]


@pytest.fixture
def hass():
    Store.contents.clear()
    return Hass()


@pytest.fixture
def entry():
    return SimpleNamespace(
        entry_id="test_entry",
        data={
            "calendar_entities": ["calendar.family"],
            "origin_address": "home",
            "google_api_key": "dummy",
            "ollama_url": "",
            "enable_outfit_ai": False,
            "notify_script": "script.universal_notify",
        },
        options={},
    )
