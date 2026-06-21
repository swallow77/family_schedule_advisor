"""Constants for Family Schedule Advisor."""
from __future__ import annotations

from homeassistant.const import Platform

DOMAIN = "family_schedule_advisor"
NAME = "Family Schedule Advisor"
VERSION = "0.2.9"

PLATFORMS: list[Platform] = [Platform.SENSOR, Platform.BUTTON]

CONF_CALENDAR_ENTITIES = "calendar_entities"
CONF_ORIGIN_ADDRESS = "origin_address"
CONF_GOOGLE_API_KEY = "google_api_key"
CONF_OLLAMA_URL = "ollama_url"
CONF_OLLAMA_MODEL = "ollama_model"
CONF_NOTIFY_SCRIPT = "notify_script"
CONF_TTS_TARGET = "tts_target"
CONF_TTS_SERVICE = "tts_service"
CONF_TTS_SPEED = "tts_speed"
CONF_TTS_PITCH = "tts_pitch"
CONF_PREPARE_MINUTES = "prepare_minutes"
CONF_ARRIVAL_MARGIN_MINUTES = "arrival_margin_minutes"
CONF_LOOKAHEAD_HOURS = "lookahead_hours"
CONF_MIN_EVENT_HOUR = "min_event_hour"
CONF_MAX_EVENT_HOUR = "max_event_hour"
CONF_ENABLE_AI_DESTINATION = "enable_ai_destination"

CONF_WEATHER_RAIN = "weather_rain"
CONF_WEATHER_FEELS_LIKE = "weather_feels_like"
CONF_WEATHER_TEMP = "weather_temp"
CONF_WEATHER_HUMIDITY = "weather_humidity"
CONF_WEATHER_WIND = "weather_wind"
CONF_WEATHER_SKY = "weather_sky"
CONF_WEATHER_DUST = "weather_dust"
CONF_WEATHER_UV = "weather_uv"
CONF_WEATHER_APPARENT = "weather_apparent"

DEFAULT_CALENDAR_ENTITIES = "sensor.calendar_gamil,sensor.calendar_family"
DEFAULT_OLLAMA_URL = "http://192.168.0.17:11434"
DEFAULT_OLLAMA_MODEL = "qwen3:14b"
DEFAULT_NOTIFY_SCRIPT = "script.universal_notify"
DEFAULT_TTS_TARGET = "media_player.jeonce2"
DEFAULT_TTS_SERVICE = "tts.google_cloud_say"
DEFAULT_TTS_SPEED = 0.9
DEFAULT_TTS_PITCH = -1.5
DEFAULT_PREPARE_MINUTES = 15
DEFAULT_ARRIVAL_MARGIN_MINUTES = 10
DEFAULT_LOOKAHEAD_HOURS = 48
DEFAULT_MIN_EVENT_HOUR = 0
DEFAULT_MAX_EVENT_HOUR = 23

STORAGE_VERSION = 1
STORAGE_KEY = f"{DOMAIN}.storage"
