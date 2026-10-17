# Changelog

## 0.5.3

- Add a native Telegram reminder channel for both preparation and departure stages, with per-plan route details and server acknowledgement before recording success.
- Retain separate channel receipts and retry only failed channels; expose channel/target/status diagnostics and distinguish script completion from Telegram acceptance.
- Reject the known no-op notification script so a deliberately empty script cannot be reported as successful delivery.
- Allow up to 10 minutes for long queued voice announcements instead of timing out after 45 seconds.
- Preserve completed stages when adding channels and migrate fully completed old receipts without replaying them. Partial deliveries remain retryable; snooze still resets the chosen stage.
- Preserve local AI model selection, expanded weather/destination clothing advice, and spoken time formatting.

## 0.5.2

- Explain preparation timing in setup, general options and family profile options with a concrete appointment/travel/margin/preparation example.
- Expose effective preparation and arrival margins, reminder basis, and a readable preparation calculation in a new sensor and existing preparation/status sensor attributes.
- Include destination, travel duration, configured preparation time and departure time in preparation messages. Preserve the user's richer departure announcement from commit 1fd436a.
- Verify option changes reschedule preparation timers, family-specific preparation takes precedence, and actual transit departure times are respected.
- Keep the 60-minute default for unresolved travel and unanswered Telegram requests.

## 0.5.1

- Send the normal appointment reminder exactly 60 minutes before start when a destination/route is unresolved and no manual departure estimate is configured.
- Keep this reminder independent of unanswered or cancelled Telegram location conversations. Preparation and arrival margins do not shorten the default hour.
- Preserve calculated reminders after location confirmation, completed notification receipts, restart catch-up, virtual events and explicitly configured manual travel estimates.

## 0.5.0

- Restore visible names for every options menu item in Korean and English.
- Optional Telegram location questions with a route preview and explicit confirmation.
- Match replies to the exact question, selected bot and private chat; preserve pending conversations across restart.
- Apply confirmed locations to every matching family profile and preserve reminder receipts when calendar location changes.
- Optionally save only the location of a freshly matched Google Calendar occurrence using its existing authenticated integration. Native editable calendars are also supported; ambiguous/changed/read-only events are never overwritten.
- Report calendar write failures while retaining the confirmed location for departure calculation.
- Add regression tests for conversation routing, retries, expiry, cancellation, duplicate replies, calendar preservation and recurring Google instance IDs.

## 0.4.0

- Explicit number boxes for every time/duration option, including family profiles.
- Independent reminders for all upcoming events; catch up after downtime before an event starts.
- Merge native calendars and legacy sensors, deduplicate shared appointments, and retain every source.
- Use the actual transit departure returned by the route provider. Remove the arbitrary 60-minute fallback.
- Wait for notification service completion; retry failed channels without repeating successful channels.
- Pre-generate optional outfit advice so AI availability cannot delay a due notification.
- Place aliases; family profiles with calendar, origin, preparation, mode, mobile target and presence.
- Mobile actions and native buttons/services: prepared, departed, snooze, skip this event.
- Optional departure reminder, quiet hours, hourly forecasts, today schedule and conflict sensors.
- Transit/driving/walking and optional modern Google Routes API provider.
- Safe long-text sensor states, full-text attributes and timestamp sensors for automations.
- Existing entry data/options, original entity unique IDs and legacy notification history are preserved.
- Automated regression tests, lint, Home Assistant import/UI checks and hassfest validation.

Updating Python integration files requires a Home Assistant restart. Recreating the integration is unnecessary.
