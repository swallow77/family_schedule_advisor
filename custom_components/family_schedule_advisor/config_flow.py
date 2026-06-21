"""Config flow for Family Schedule Advisor."""
from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import callback

from .const import (
    CONF_ARRIVAL_MARGIN_MINUTES,
    CONF_CALENDAR_ENTITIES,
    CONF_ENABLE_AI_DESTINATION,
    CONF_GOOGLE_API_KEY,
    CONF_LOOKAHEAD_HOURS,
    CONF_MAX_EVENT_HOUR,
    CONF_MIN_EVENT_HOUR,
    CONF_NOTIFY_SCRIPT,
    CONF_OLLAMA_MODEL,
    CONF_OLLAMA_URL,
    CONF_ORIGIN_ADDRESS,
    CONF_PREPARE_MINUTES,
    CONF_TTS_PITCH,
    CONF_TTS_SERVICE,
    CONF_TTS_SPEED,
    CONF_TTS_TARGET,
    CONF_WEATHER_APPARENT,
    CONF_WEATHER_DUST,
    CONF_WEATHER_FEELS_LIKE,
    CONF_WEATHER_HUMIDITY,
    CONF_WEATHER_RAIN,
    CONF_WEATHER_SKY,
    CONF_WEATHER_TEMP,
    CONF_WEATHER_UV,
    CONF_WEATHER_WIND,
    DEFAULT_ARRIVAL_MARGIN_MINUTES,
    DEFAULT_CALENDAR_ENTITIES,
    DEFAULT_LOOKAHEAD_HOURS,
    DEFAULT_MAX_EVENT_HOUR,
    DEFAULT_MIN_EVENT_HOUR,
    DEFAULT_NOTIFY_SCRIPT,
    DEFAULT_OLLAMA_MODEL,
    DEFAULT_OLLAMA_URL,
    DEFAULT_PREPARE_MINUTES,
    DEFAULT_TTS_PITCH,
    DEFAULT_TTS_SERVICE,
    DEFAULT_TTS_SPEED,
    DEFAULT_TTS_TARGET,
    DOMAIN,
)


WEATHER_DEFAULTS = {
    CONF_WEATHER_RAIN: "sensor.gangsuhwagryul",
    CONF_WEATHER_FEELS_LIKE: "sensor.cegamondo",
    CONF_WEATHER_TEMP: "sensor.hyeonjaeondo",
    CONF_WEATHER_HUMIDITY: "sensor.hyeonjaeseubdo",
    CONF_WEATHER_WIND: "sensor.hyeonjaepungsog",
    CONF_WEATHER_SKY: "sensor.hyeonjaenalssi",
    CONF_WEATHER_DUST: "sensor.comisemeonjideunggeub",
    CONF_WEATHER_UV: "sensor.jaoeseondeunggeub",
    CONF_WEATHER_APPARENT: "sensor.dw1_realtime_apparent_temperature",
}


def _schema(defaults: dict[str, Any]) -> vol.Schema:
    """Return common schema."""
    return vol.Schema(
        {
            vol.Required(CONF_CALENDAR_ENTITIES, default=defaults.get(CONF_CALENDAR_ENTITIES, DEFAULT_CALENDAR_ENTITIES)): str,
            vol.Required(CONF_ORIGIN_ADDRESS, default=defaults.get(CONF_ORIGIN_ADDRESS, "")): str,
            vol.Required(CONF_GOOGLE_API_KEY, default=defaults.get(CONF_GOOGLE_API_KEY, "")): str,
            vol.Required(CONF_OLLAMA_URL, default=defaults.get(CONF_OLLAMA_URL, DEFAULT_OLLAMA_URL)): str,
            vol.Required(CONF_OLLAMA_MODEL, default=defaults.get(CONF_OLLAMA_MODEL, DEFAULT_OLLAMA_MODEL)): str,
            vol.Required(CONF_PREPARE_MINUTES, default=defaults.get(CONF_PREPARE_MINUTES, DEFAULT_PREPARE_MINUTES)): vol.All(vol.Coerce(int), vol.Range(min=0, max=180)),
            vol.Required(CONF_ARRIVAL_MARGIN_MINUTES, default=defaults.get(CONF_ARRIVAL_MARGIN_MINUTES, DEFAULT_ARRIVAL_MARGIN_MINUTES)): vol.All(vol.Coerce(int), vol.Range(min=0, max=180)),
            vol.Required(CONF_LOOKAHEAD_HOURS, default=defaults.get(CONF_LOOKAHEAD_HOURS, DEFAULT_LOOKAHEAD_HOURS)): vol.All(vol.Coerce(int), vol.Range(min=1, max=168)),
            vol.Required(CONF_MIN_EVENT_HOUR, default=defaults.get(CONF_MIN_EVENT_HOUR, DEFAULT_MIN_EVENT_HOUR)): vol.All(vol.Coerce(int), vol.Range(min=0, max=23)),
            vol.Required(CONF_MAX_EVENT_HOUR, default=defaults.get(CONF_MAX_EVENT_HOUR, DEFAULT_MAX_EVENT_HOUR)): vol.All(vol.Coerce(int), vol.Range(min=0, max=23)),
            vol.Required(CONF_ENABLE_AI_DESTINATION, default=defaults.get(CONF_ENABLE_AI_DESTINATION, True)): bool,
            vol.Required(CONF_NOTIFY_SCRIPT, default=defaults.get(CONF_NOTIFY_SCRIPT, DEFAULT_NOTIFY_SCRIPT)): str,
            vol.Required(CONF_TTS_TARGET, default=defaults.get(CONF_TTS_TARGET, DEFAULT_TTS_TARGET)): str,
            vol.Required(CONF_TTS_SERVICE, default=defaults.get(CONF_TTS_SERVICE, DEFAULT_TTS_SERVICE)): str,
            vol.Required(CONF_TTS_SPEED, default=defaults.get(CONF_TTS_SPEED, DEFAULT_TTS_SPEED)): vol.Coerce(float),
            vol.Required(CONF_TTS_PITCH, default=defaults.get(CONF_TTS_PITCH, DEFAULT_TTS_PITCH)): vol.Coerce(float),
            vol.Optional(CONF_WEATHER_RAIN, default=defaults.get(CONF_WEATHER_RAIN, WEATHER_DEFAULTS[CONF_WEATHER_RAIN])): str,
            vol.Optional(CONF_WEATHER_FEELS_LIKE, default=defaults.get(CONF_WEATHER_FEELS_LIKE, WEATHER_DEFAULTS[CONF_WEATHER_FEELS_LIKE])): str,
            vol.Optional(CONF_WEATHER_TEMP, default=defaults.get(CONF_WEATHER_TEMP, WEATHER_DEFAULTS[CONF_WEATHER_TEMP])): str,
            vol.Optional(CONF_WEATHER_HUMIDITY, default=defaults.get(CONF_WEATHER_HUMIDITY, WEATHER_DEFAULTS[CONF_WEATHER_HUMIDITY])): str,
            vol.Optional(CONF_WEATHER_WIND, default=defaults.get(CONF_WEATHER_WIND, WEATHER_DEFAULTS[CONF_WEATHER_WIND])): str,
            vol.Optional(CONF_WEATHER_SKY, default=defaults.get(CONF_WEATHER_SKY, WEATHER_DEFAULTS[CONF_WEATHER_SKY])): str,
            vol.Optional(CONF_WEATHER_DUST, default=defaults.get(CONF_WEATHER_DUST, WEATHER_DEFAULTS[CONF_WEATHER_DUST])): str,
            vol.Optional(CONF_WEATHER_UV, default=defaults.get(CONF_WEATHER_UV, WEATHER_DEFAULTS[CONF_WEATHER_UV])): str,
            vol.Optional(CONF_WEATHER_APPARENT, default=defaults.get(CONF_WEATHER_APPARENT, WEATHER_DEFAULTS[CONF_WEATHER_APPARENT])): str,
        }
    )


class FamilyScheduleAdvisorConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow."""

    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None):
        """Handle the initial step."""
        errors: dict[str, str] = {}
        if user_input is not None:
            await self.async_set_unique_id(DOMAIN)
            self._abort_if_unique_id_configured()
            if not user_input[CONF_CALENDAR_ENTITIES].strip():
                errors[CONF_CALENDAR_ENTITIES] = "required"
            if not user_input[CONF_ORIGIN_ADDRESS].strip():
                errors[CONF_ORIGIN_ADDRESS] = "required"
            if not errors:
                return self.async_create_entry(title="Family Schedule Advisor", data=user_input)

        return self.async_show_form(step_id="user", data_schema=_schema({}), errors=errors)

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: config_entries.ConfigEntry):
        """Create the options flow."""
        return FamilyScheduleAdvisorOptionsFlow(config_entry)


class FamilyScheduleAdvisorOptionsFlow(config_entries.OptionsFlow):
    """Options flow."""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        self.config_entry = config_entry

    async def async_step_init(self, user_input: dict[str, Any] | None = None):
        """Manage options."""
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)
        defaults = dict(self.config_entry.data)
        defaults.update(self.config_entry.options)
        return self.async_show_form(step_id="init", data_schema=_schema(defaults))
