"""Native configuration forms with explicit number boxes and profile editors."""

from __future__ import annotations

from typing import Any
from uuid import uuid4

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.helpers import selector

from . import const as c
from .calendar_parser import split_entities

WEATHER_KEYS = [
    c.CONF_WEATHER_RAIN,
    c.CONF_WEATHER_FEELS_LIKE,
    c.CONF_WEATHER_TEMP,
    c.CONF_WEATHER_HUMIDITY,
    c.CONF_WEATHER_WIND,
    c.CONF_WEATHER_SKY,
    c.CONF_WEATHER_DUST,
    c.CONF_WEATHER_UV,
    c.CONF_WEATHER_APPARENT,
]
INTEGER_KEYS = {
    c.CONF_PREPARE_MINUTES,
    c.CONF_ARRIVAL_MARGIN_MINUTES,
    c.CONF_LOOKAHEAD_HOURS,
    c.CONF_MIN_EVENT_HOUR,
    c.CONF_MAX_EVENT_HOUR,
    c.CONF_FALLBACK_TRAVEL_MINUTES,
    c.CONF_POLL_MINUTES,
    c.CONF_QUIET_START,
    c.CONF_QUIET_END,
    c.CONF_SNOOZE_MINUTES,
}


def _number(minimum: float, maximum: float, step: float = 1):
    return selector.NumberSelector(
        selector.NumberSelectorConfig(
            min=minimum,
            max=maximum,
            step=step,
            mode=selector.NumberSelectorMode.BOX,
        )
    )


def _entities(domains: list[str], multiple: bool = False):
    return selector.EntitySelector(selector.EntitySelectorConfig(domain=domains, multiple=multiple))


def _choice(values: list[str], translation_key: str):
    return selector.SelectSelector(
        selector.SelectSelectorConfig(options=values, translation_key=translation_key)
    )


def _optional_entity(key: str, defaults: dict):
    value = defaults.get(key)
    return vol.Optional(key, default=value) if value else vol.Optional(key)


def _normalize_user_input(values: dict[str, Any]) -> dict[str, Any]:
    result = dict(values)
    if c.CONF_CALENDAR_ENTITIES in result:
        result[c.CONF_CALENDAR_ENTITIES] = list(
            dict.fromkeys(split_entities(result[c.CONF_CALENDAR_ENTITIES]))
        )
    for key in INTEGER_KEYS & result.keys():
        if float(result[key]) != int(float(result[key])):
            raise vol.Invalid("whole_number")
        result[key] = int(float(result[key]))
    for key, value in result.items():
        if isinstance(value, str):
            result[key] = value.strip()
    return result


def _validate(values: dict) -> dict[str, str]:
    errors = {}
    if c.CONF_CALENDAR_ENTITIES in values and not values[c.CONF_CALENDAR_ENTITIES]:
        errors[c.CONF_CALENDAR_ENTITIES] = "required"
    if c.CONF_ORIGIN_ADDRESS in values and not values[c.CONF_ORIGIN_ADDRESS]:
        errors[c.CONF_ORIGIN_ADDRESS] = "required"
    mobile = values.get(c.CONF_MOBILE_NOTIFY_SERVICE, "")
    if mobile and (not mobile.startswith("notify.") or len(mobile.split(".")) != 2):
        errors[c.CONF_MOBILE_NOTIFY_SERVICE] = "notify_service"
    url = values.get(c.CONF_OLLAMA_URL, "")
    if url and not url.startswith(("http://", "https://")):
        errors[c.CONF_OLLAMA_URL] = "invalid_url"
    return errors


def _schema(defaults: dict) -> vol.Schema:
    return vol.Schema(
        {
            vol.Required(
                c.CONF_CALENDAR_ENTITIES,
                default=split_entities(defaults.get(c.CONF_CALENDAR_ENTITIES, [])),
            ): _entities(["calendar", "sensor"], True),
            vol.Required(
                c.CONF_ORIGIN_ADDRESS, default=defaults.get(c.CONF_ORIGIN_ADDRESS, "")
            ): str,
            vol.Optional(
                c.CONF_GOOGLE_API_KEY, default=defaults.get(c.CONF_GOOGLE_API_KEY, "")
            ): selector.TextSelector(
                selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD)
            ),
            vol.Required(
                c.CONF_ROUTE_PROVIDER,
                default=defaults.get(c.CONF_ROUTE_PROVIDER, c.DEFAULT_ROUTE_PROVIDER),
            ): _choice(["directions", "routes"], "route_provider"),
            vol.Required(
                c.CONF_TRAVEL_MODE,
                default=defaults.get(c.CONF_TRAVEL_MODE, c.DEFAULT_TRAVEL_MODE),
            ): _choice(["transit", "driving", "walking"], "travel_mode"),
            vol.Required(
                c.CONF_PREPARE_MINUTES,
                default=defaults.get(c.CONF_PREPARE_MINUTES, c.DEFAULT_PREPARE_MINUTES),
            ): _number(0, 180),
            vol.Required(
                c.CONF_ARRIVAL_MARGIN_MINUTES,
                default=defaults.get(
                    c.CONF_ARRIVAL_MARGIN_MINUTES, c.DEFAULT_ARRIVAL_MARGIN_MINUTES
                ),
            ): _number(0, 180),
            vol.Required(
                c.CONF_FALLBACK_TRAVEL_MINUTES,
                default=defaults.get(c.CONF_FALLBACK_TRAVEL_MINUTES, 0),
            ): _number(0, 480),
            vol.Required(
                c.CONF_LOOKAHEAD_HOURS,
                default=defaults.get(c.CONF_LOOKAHEAD_HOURS, c.DEFAULT_LOOKAHEAD_HOURS),
            ): _number(1, 168),
            vol.Required(
                c.CONF_POLL_MINUTES,
                default=defaults.get(c.CONF_POLL_MINUTES, c.DEFAULT_POLL_MINUTES),
            ): _number(1, 60),
            vol.Required(
                c.CONF_MIN_EVENT_HOUR,
                default=defaults.get(c.CONF_MIN_EVENT_HOUR, c.DEFAULT_MIN_EVENT_HOUR),
            ): _number(0, 23),
            vol.Required(
                c.CONF_MAX_EVENT_HOUR,
                default=defaults.get(c.CONF_MAX_EVENT_HOUR, c.DEFAULT_MAX_EVENT_HOUR),
            ): _number(0, 23),
            vol.Required(
                c.CONF_ENABLE_AI_DESTINATION,
                default=defaults.get(c.CONF_ENABLE_AI_DESTINATION, True),
            ): bool,
            vol.Required(
                c.CONF_ENABLE_OUTFIT_AI,
                default=defaults.get(c.CONF_ENABLE_OUTFIT_AI, True),
            ): bool,
            vol.Optional(c.CONF_OLLAMA_URL, default=defaults.get(c.CONF_OLLAMA_URL, "")): str,
            vol.Optional(
                c.CONF_OLLAMA_MODEL,
                default=defaults.get(c.CONF_OLLAMA_MODEL, c.DEFAULT_OLLAMA_MODEL),
            ): str,
        }
    )


def _notification_schema(defaults: dict) -> vol.Schema:
    return vol.Schema(
        {
            vol.Optional(
                c.CONF_NOTIFY_SCRIPT,
                default=defaults.get(c.CONF_NOTIFY_SCRIPT, c.DEFAULT_NOTIFY_SCRIPT),
            ): str,
            vol.Optional(
                c.CONF_MOBILE_NOTIFY_SERVICE,
                default=defaults.get(c.CONF_MOBILE_NOTIFY_SERVICE, ""),
            ): str,
            vol.Optional(
                c.CONF_TTS_TARGET,
                default=defaults.get(c.CONF_TTS_TARGET, c.DEFAULT_TTS_TARGET),
            ): str,
            vol.Optional(
                c.CONF_TTS_SERVICE,
                default=defaults.get(c.CONF_TTS_SERVICE, c.DEFAULT_TTS_SERVICE),
            ): str,
            vol.Required(
                c.CONF_TTS_SPEED,
                default=defaults.get(c.CONF_TTS_SPEED, c.DEFAULT_TTS_SPEED),
            ): _number(0.25, 4, 0.05),
            vol.Required(
                c.CONF_TTS_PITCH,
                default=defaults.get(c.CONF_TTS_PITCH, c.DEFAULT_TTS_PITCH),
            ): _number(-20, 20, 0.5),
            vol.Required(
                c.CONF_DEPARTURE_REMINDER,
                default=defaults.get(c.CONF_DEPARTURE_REMINDER, False),
            ): bool,
            vol.Required(
                c.CONF_SNOOZE_MINUTES,
                default=defaults.get(c.CONF_SNOOZE_MINUTES, c.DEFAULT_SNOOZE_MINUTES),
            ): _number(1, 60),
            vol.Required(
                c.CONF_QUIET_ENABLED, default=defaults.get(c.CONF_QUIET_ENABLED, False)
            ): bool,
            vol.Required(c.CONF_QUIET_START, default=defaults.get(c.CONF_QUIET_START, 22)): _number(
                0, 23
            ),
            vol.Required(c.CONF_QUIET_END, default=defaults.get(c.CONF_QUIET_END, 7)): _number(
                0, 23
            ),
        }
    )


class FamilyScheduleAdvisorConfigFlow(config_entries.ConfigFlow, domain=c.DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input=None):
        errors = {}
        if user_input is not None:
            try:
                user_input = _normalize_user_input(user_input)
                errors = _validate(user_input)
            except (ValueError, vol.Invalid):
                errors = {"base": "whole_number"}
            if not errors:
                await self.async_set_unique_id(c.DOMAIN)
                self._abort_if_unique_id_configured()
                return self.async_create_entry(title=c.NAME, data=user_input)
        return self.async_show_form(
            step_id="user", data_schema=_schema(user_input or {}), errors=errors
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        return FamilyScheduleAdvisorOptionsFlow()


class FamilyScheduleAdvisorOptionsFlow(config_entries.OptionsFlow):
    @property
    def defaults(self):
        return {**self.config_entry.data, **self.config_entry.options}

    def _save(self, changes):
        return self.async_create_entry(title="", data={**self.config_entry.options, **changes})

    async def async_step_init(self, user_input=None):
        return self.async_show_menu(
            step_id="init",
            menu_options=["general", "places", "profiles", "notifications", "weather"],
        )

    async def _form(self, step, schema, user_input):
        errors = {}
        if user_input is not None:
            try:
                values = _normalize_user_input(user_input)
                errors = _validate(values)
            except (ValueError, vol.Invalid):
                errors = {"base": "whole_number"}
            if not errors:
                return self._save(values)
        return self.async_show_form(step_id=step, data_schema=schema, errors=errors)

    async def async_step_general(self, user_input=None):
        return await self._form(
            "general", _schema({**self.defaults, **(user_input or {})}), user_input
        )

    async def async_step_notifications(self, user_input=None):
        return await self._form(
            "notifications",
            _notification_schema({**self.defaults, **(user_input or {})}),
            user_input,
        )

    async def async_step_weather(self, user_input=None):
        defaults = self.defaults
        schema = {_optional_entity(c.CONF_WEATHER_ENTITY, defaults): _entities(["weather"])}
        schema.update(
            {
                _optional_entity(key, defaults): _entities(["sensor", "input_number", "input_text"])
                for key in WEATHER_KEYS
            }
        )
        if user_input is not None:
            return self._save(
                {key: user_input.get(key, "") for key in [c.CONF_WEATHER_ENTITY, *WEATHER_KEYS]}
            )
        return self.async_show_form(step_id="weather", data_schema=vol.Schema(schema))

    async def async_step_places(self, user_input=None):
        aliases = self.defaults.get(c.CONF_PLACE_ALIASES, {})
        if not aliases:
            self._editing_alias = ""
            return await self.async_step_place_edit()
        if user_input is not None:
            self._editing_alias = "" if user_input["alias"] == "__new__" else user_input["alias"]
            return await self.async_step_place_edit()
        choices = [
            {"value": "__new__", "label": "새 장소 추가"},
            *[
                {"value": alias, "label": f"{alias} → {address}"}
                for alias, address in aliases.items()
            ],
        ]
        return self.async_show_form(
            step_id="places",
            data_schema=vol.Schema(
                {
                    vol.Required("alias"): selector.SelectSelector(
                        selector.SelectSelectorConfig(options=choices)
                    )
                }
            ),
        )

    async def async_step_place_edit(self, user_input=None):
        aliases = dict(self.defaults.get(c.CONF_PLACE_ALIASES, {}))
        old = getattr(self, "_editing_alias", "")
        errors = {}
        if user_input is not None:
            alias = user_input["alias"].strip()
            address = user_input["address"].strip()
            deleting = user_input.get("delete", False)
            if not deleting and (not alias or not address):
                errors["base"] = "required"
            elif alias != old and alias in aliases and not deleting:
                errors["alias"] = "duplicate_alias"
            else:
                aliases.pop(old, None)
                if not deleting:
                    aliases[alias] = address
                return self._save({c.CONF_PLACE_ALIASES: aliases})
        return self.async_show_form(
            step_id="place_edit",
            data_schema=vol.Schema(
                {
                    vol.Required("alias", default=(user_input or {}).get("alias", old)): str,
                    vol.Required(
                        "address",
                        default=(user_input or {}).get("address", aliases.get(old, "")),
                    ): str,
                    vol.Required("delete", default=False): bool,
                }
            ),
            errors=errors,
        )

    async def async_step_profiles(self, user_input=None):
        profiles = self.defaults.get(c.CONF_FAMILY_PROFILES, {})
        if not profiles:
            self._profile_id = uuid4().hex[:12]
            return await self.async_step_profile_edit()
        if user_input is not None:
            self._profile_id = (
                uuid4().hex[:12] if user_input["profile"] == "__new__" else user_input["profile"]
            )
            return await self.async_step_profile_edit()
        choices = [
            {"value": "__new__", "label": "새 가족 추가"},
            *[{"value": key, "label": value["name"]} for key, value in profiles.items()],
        ]
        return self.async_show_form(
            step_id="profiles",
            data_schema=vol.Schema(
                {
                    vol.Required("profile"): selector.SelectSelector(
                        selector.SelectSelectorConfig(options=choices)
                    )
                }
            ),
        )

    async def async_step_profile_edit(self, user_input=None):
        profiles = dict(self.defaults.get(c.CONF_FAMILY_PROFILES, {}))
        profile_id = self._profile_id
        defaults = {
            **self.defaults,
            **profiles.get(profile_id, {}),
            **(user_input or {}),
        }
        errors = {}
        if user_input is not None:
            if user_input.get("delete"):
                profiles.pop(profile_id, None)
                return self._save({c.CONF_FAMILY_PROFILES: profiles})
            try:
                values = _normalize_user_input(user_input)
                errors = _validate(values)
                if not values["name"]:
                    errors["name"] = "required"
            except (ValueError, vol.Invalid):
                errors = {"base": "whole_number"}
            if not errors:
                values.pop("delete", None)
                values["person_entity"] = values.get("person_entity", "")
                profiles[profile_id] = values
                return self._save({c.CONF_FAMILY_PROFILES: profiles})
        return self.async_show_form(
            step_id="profile_edit",
            data_schema=vol.Schema(
                {
                    vol.Required("name", default=defaults.get("name", "")): str,
                    vol.Required(
                        c.CONF_CALENDAR_ENTITIES,
                        default=split_entities(defaults.get(c.CONF_CALENDAR_ENTITIES, [])),
                    ): _entities(["calendar", "sensor"], True),
                    vol.Required(
                        c.CONF_ORIGIN_ADDRESS,
                        default=defaults.get(c.CONF_ORIGIN_ADDRESS, ""),
                    ): str,
                    vol.Required(
                        c.CONF_PREPARE_MINUTES,
                        default=defaults.get(c.CONF_PREPARE_MINUTES, c.DEFAULT_PREPARE_MINUTES),
                    ): _number(0, 180),
                    vol.Required(
                        c.CONF_ARRIVAL_MARGIN_MINUTES,
                        default=defaults.get(
                            c.CONF_ARRIVAL_MARGIN_MINUTES,
                            c.DEFAULT_ARRIVAL_MARGIN_MINUTES,
                        ),
                    ): _number(0, 180),
                    vol.Required(
                        c.CONF_FALLBACK_TRAVEL_MINUTES,
                        default=defaults.get(c.CONF_FALLBACK_TRAVEL_MINUTES, 0),
                    ): _number(0, 480),
                    vol.Required(
                        c.CONF_TRAVEL_MODE,
                        default=defaults.get(c.CONF_TRAVEL_MODE, c.DEFAULT_TRAVEL_MODE),
                    ): _choice(["transit", "driving", "walking"], "travel_mode"),
                    vol.Optional(
                        c.CONF_MOBILE_NOTIFY_SERVICE,
                        default=defaults.get(c.CONF_MOBILE_NOTIFY_SERVICE, ""),
                    ): str,
                    vol.Optional(
                        c.CONF_TTS_TARGET,
                        default=defaults.get(c.CONF_TTS_TARGET, c.DEFAULT_TTS_TARGET),
                    ): str,
                    _optional_entity("person_entity", defaults): _entities(
                        ["person", "device_tracker"]
                    ),
                    vol.Required("delete", default=False): bool,
                }
            ),
            errors=errors,
        )
