# Family Schedule Advisor

Home Assistant custom integration for family schedule based departure, transit, and outfit recommendations.

## Features

- Select multiple `calendar.*` or legacy `sensor.calendar_*` entities from the setup screen.
- Supports multiple Google Calendar accounts/calendars at the same time.
- Shows the raw recognized event in `sensor.family_schedule_advisor_recognized_event`.
- Extracts destination from event title/location, with optional Ollama based destination extraction.
- Calculates public transit duration with Google Directions API.
- Calculates recommended preparation and departure times.
- Generates a Korean TTS friendly outfit recommendation with Ollama.
- Sends the message through `script.universal_notify`.
- Provides buttons for recalculation and test notification.
- Shows debug/status sensors for last action and last notification result.

## Installation

Copy this folder into Home Assistant:

```text
/config/custom_components/family_schedule_advisor
```

Restart Home Assistant, then add the integration from:

```text
Settings → Devices & services → Add integration → Family Schedule Advisor
```

## Calendar selection

From version `0.2.5`, the calendar setting is no longer a plain text field. It is an entity selector, so you can choose multiple Gmail/Google Calendar entities directly from the UI.

Recommended selection examples:

```text
calendar.family
calendar.sdh7707_gmail_com
sensor.calendar_gamil
sensor.calendar_family
```

Legacy comma text values from older versions are still accepted internally.

## Important

Do not hard-code Google API keys in files. Enter the key from the integration configuration screen and restrict the key in Google Cloud.

## Main entities

```text
sensor.family_schedule_advisor_status
sensor.family_schedule_advisor_next_event
sensor.family_schedule_advisor_recognized_event
sensor.family_schedule_advisor_event_time
sensor.family_schedule_advisor_destination
sensor.family_schedule_advisor_transit_duration
sensor.family_schedule_advisor_departure_time
sensor.family_schedule_advisor_notify_time
sensor.family_schedule_advisor_route_status
sensor.family_schedule_advisor_outfit_message
sensor.family_schedule_advisor_last_action
sensor.family_schedule_advisor_last_notify_result
button.family_schedule_advisor_recalculate
button.family_schedule_advisor_test_notify
```

## Notes

If an event is visible in `sensor.family_schedule_advisor_recognized_event` but the status is `필터됨`, check the configured allowed event hours. For example, a `02:00` event requires the minimum allowed event hour to be `0`.
