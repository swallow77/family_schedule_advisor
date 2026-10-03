import json
from types import SimpleNamespace

import pytest
import voluptuous as vol
from conftest import ROOT, NumberSelector

from custom_components.family_schedule_advisor import config_flow as flow
from custom_components.family_schedule_advisor.sensor import SENSORS, AdvisorSensor


@pytest.mark.parametrize("schema", [flow._schema({}), flow._notification_schema({})])
def test_every_number_is_a_box(schema):
    numbers = [value for value in schema.schema.values() if isinstance(value, NumberSelector)]
    assert numbers
    assert all(value.config["mode"] == "box" for value in numbers)


def test_general_schema_rejects_out_of_range_time():
    with pytest.raises(vol.Invalid):
        flow._schema({})(
            {
                "calendar_entities": ["calendar.family"],
                "origin_address": "home",
                "prepare_minutes": 181,
            }
        )


def test_integer_time_normalization():
    values = flow._normalize_user_input(
        {"prepare_minutes": 15.0, "tts_speed": 0.9, "calendar_entities": "calendar.a,sensor.b"}
    )
    assert values == {
        "prepare_minutes": 15,
        "tts_speed": 0.9,
        "calendar_entities": ["calendar.a", "sensor.b"],
    }
    with pytest.raises(vol.Invalid):
        flow._normalize_user_input({"prepare_minutes": 15.5})


async def test_options_updates_preserve_other_sections(entry):
    entry.options = {
        "place_aliases": {"회사": "office"},
        "family_profiles": {"a": {"name": "member"}},
        "prepare_minutes": 20,
    }
    options = flow.FamilyScheduleAdvisorOptionsFlow()
    options.config_entry = entry
    result = await options.async_step_general(
        {
            "calendar_entities": ["calendar.family"],
            "origin_address": "home",
            "prepare_minutes": 30.0,
        }
    )
    assert result["data"]["prepare_minutes"] == 30
    assert result["data"]["place_aliases"] == entry.options["place_aliases"]
    assert result["data"]["family_profiles"] == entry.options["family_profiles"]


async def test_options_validate_required_fields(entry):
    options = flow.FamilyScheduleAdvisorOptionsFlow()
    options.config_entry = entry
    result = await options.async_step_general({"calendar_entities": [], "origin_address": ""})
    assert result["type"] == "form"
    assert set(result["errors"]) == {"calendar_entities", "origin_address"}


async def test_profile_numbers_are_boxes_and_profile_is_saved(entry):
    options = flow.FamilyScheduleAdvisorOptionsFlow()
    options.config_entry = entry
    result = await options.async_step_profiles()
    assert result["step_id"] == "profile_edit"
    assert all(
        value.config["mode"] == "box"
        for value in result["data_schema"].schema.values()
        if isinstance(value, NumberSelector)
    )
    result = await options.async_step_profile_edit(
        {
            "name": "member",
            "calendar_entities": ["calendar.child"],
            "origin_address": "home",
            "prepare_minutes": 30.0,
        }
    )
    profile = next(iter(result["data"]["family_profiles"].values()))
    assert profile["prepare_minutes"] == 30 and profile["name"] == "member"


async def test_place_editor_detects_duplicate_alias_and_can_delete(entry):
    entry.options = {"place_aliases": {"회사": "office", "학교": "school"}}
    options = flow.FamilyScheduleAdvisorOptionsFlow()
    options.config_entry = entry
    options._editing_alias = "학교"
    result = await options.async_step_place_edit(
        {"alias": "회사", "address": "other", "delete": False}
    )
    assert result["errors"]["alias"] == "duplicate_alias"
    result = await options.async_step_place_edit(
        {"alias": "학교", "address": "school", "delete": True}
    )
    assert result["data"]["place_aliases"] == {"회사": "office"}


async def test_weather_clear_does_not_restore_old_value(entry):
    entry.data["weather_temp"] = "sensor.old_temperature"
    options = flow.FamilyScheduleAdvisorOptionsFlow()
    options.config_entry = entry
    result = await options.async_step_weather({})
    assert result["data"]["weather_temp"] == ""


def test_outfit_sensor_truncates_state_and_retains_full_text(entry):
    message = "가" * 400
    description = next(item for item in SENSORS if item.key == "outfit_message")
    sensor = AdvisorSensor(SimpleNamespace(data={"outfit_message": message}), entry, description)
    assert len(sensor.native_value) == 255
    assert sensor.extra_state_attributes["full_text"] == message


def test_timestamp_sensor_is_a_datetime(entry):
    description = next(item for item in SENSORS if item.key == "departure_timestamp")
    sensor = AdvisorSensor(
        SimpleNamespace(data={"departure_time": "2026-10-03T09:10:00+09:00"}), entry, description
    )
    assert sensor.native_value.hour == 9 and sensor.native_value.minute == 10


def test_manifest_and_translations_are_consistent():
    root = ROOT / "custom_components/family_schedule_advisor"
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    from custom_components.family_schedule_advisor.const import VERSION

    assert manifest["version"] == VERSION == "0.4.0"
    translations = [
        json.loads((root / "translations" / f"{lang}.json").read_text(encoding="utf-8"))
        for lang in ("en", "ko")
    ]
    for data in translations:
        assert {item.translation_key for item in SENSORS} <= data["entity"]["sensor"].keys()
        assert "whole_number" in data["options"]["error"]
    assert translations[0] == json.loads((root / "strings.json").read_text(encoding="utf-8"))
