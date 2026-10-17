from unittest.mock import AsyncMock

from custom_components.family_schedule_advisor import config_flow
from custom_components.family_schedule_advisor.ollama_client import (
    async_list_models,
    sanitize_outfit_tts,
)
from custom_components.family_schedule_advisor.planning import weather_advice


async def test_ai_model_option_preserves_other_settings(hass, entry, monkeypatch):
    monkeypatch.setattr(config_flow, "_model_names", AsyncMock(return_value=["model:a", "model:b"]))
    entry.options = {"prepare_minutes": 30, "telegram_reminder_entity": "notify.telegram"}
    flow = config_flow.FamilyScheduleAdvisorOptionsFlow()
    flow.config_entry = entry
    flow.hass = hass
    shown = await flow.async_step_ai()
    schema = next(iter(shown["data_schema"].schema.values()))
    assert "model:b" in schema.config["options"] and schema.config["custom_value"] is False
    saved = await flow.async_step_ai({"ollama_model": "model:b"})
    assert saved["data"]["prepare_minutes"] == 30
    assert saved["data"]["telegram_reminder_entity"] == "notify.telegram"


async def test_model_listing_uses_tags_without_loading_models():
    class Response:
        status = 200

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def json(self, **kwargs):
            return {"models": [{"name": "model:b"}, {"model": "model:a"}, {"name": "model:b"}]}

    class Session:
        def get(self, url, **kwargs):
            assert url == "http://ollama/api/tags"
            return Response()

    assert await async_list_models(Session(), "http://ollama/") == ["model:a", "model:b"]


def test_preserved_clothing_advice_does_not_speak_settings():
    advice = sanitize_outfit_tts(
        "준비 시간은 30분입니다. 도착 여유는 10분입니다. 가벼운 겉옷을 챙기세요."
    )
    assert "준비 시간" not in advice and "도착 여유" not in advice
    assert "겉옷" in advice


def test_preserved_weather_advice_uses_weather_and_rain():
    text = weather_advice({"temp": "22", "feels_like": "25", "rain": "70", "sky": "rainy"})
    assert "25" in text and "70" in text and "우산" in text
