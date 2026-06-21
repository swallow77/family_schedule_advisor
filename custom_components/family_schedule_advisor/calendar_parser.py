"""Calendar and sensor parsing helpers."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
import logging
import re
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.util import dt as dt_util

_LOGGER = logging.getLogger(__name__)

# Supports examples:
# 06월22일 02:01 내과
# 6월 22일 02:01 내과
# 06/22 02:01 내과
# 06-22 02:01 내과
# 06.22 02:01 내과
# 2026-06-22 02:01 내과
SENSOR_RE = re.compile(
    r"(?: (?P<year>\d{4})\s*(?:년|[./-])\s*)?"
    r"(?P<month>\d{1,2})\s*(?:월|[./-])\s*(?P<day>\d{1,2})\s*(?:일)?\s+"
    r"(?P<hour>\d{1,2}):(?P<minute>\d{2})\s*(?P<title>.*)",
    re.UNICODE | re.VERBOSE,
)


@dataclass(slots=True)
class EventInfo:
    """Normalized event information."""

    key: str
    title: str
    start: Any
    end: Any | None = None
    location: str = ""
    description: str = ""
    source: str = ""
    raw_text: str = ""


@dataclass(slots=True)
class EventCandidate:
    """Parsed event candidate with validation result."""

    event: EventInfo
    accepted: bool
    reject_reason: str = ""


def split_entities(value: str | list[str]) -> list[str]:
    """Split comma separated entity list."""
    if isinstance(value, list):
        return [v.strip() for v in value if v and v.strip()]
    return [v.strip() for v in str(value).split(",") if v.strip()]


def format_compact_event(event: EventInfo) -> str:
    """Format event as compact Korean text for a state value."""
    return f"{event.start.month:02d}월{event.start.day:02d}일 {event.start.hour:02d}:{event.start.minute:02d} {event.title}".strip()


def _parse_datetime(value: Any):
    if not value:
        return None
    if hasattr(value, "tzinfo"):
        return dt_util.as_local(value)
    parsed = dt_util.parse_datetime(str(value))
    if parsed is None:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt_util.DEFAULT_TIME_ZONE)
    return dt_util.as_local(parsed)


def _event_key(source: str, start, title: str) -> str:
    return f"{source}|{start.isoformat()}|{title}"[:240]


def _parse_sensor_entity(hass: HomeAssistant, entity_id: str) -> EventInfo | None:
    state_obj = hass.states.get(entity_id)
    if state_obj is None:
        _LOGGER.debug("Calendar sensor %s does not exist", entity_id)
        return None
    raw = state_obj.state
    if raw in (None, "unknown", "unavailable", "none", ""):
        _LOGGER.debug("Calendar sensor %s has empty state: %s", entity_id, raw)
        return None

    attrs = state_obj.attributes
    title = str(attrs.get("message") or attrs.get("summary") or attrs.get("title") or "").strip()
    location = str(attrs.get("location") or "").strip()
    description = str(attrs.get("description") or "").strip()
    start = _parse_datetime(attrs.get("start_time") or attrs.get("start") or attrs.get("date_time"))
    end = _parse_datetime(attrs.get("end_time") or attrs.get("end"))

    if start is None:
        match = SENSOR_RE.search(str(raw))
        if match is None:
            _LOGGER.debug("Calendar sensor %s did not match supported date format: %s", entity_id, raw)
            return None
        now = dt_util.now()
        year_raw = match.group("year")
        year = int(year_raw) if year_raw else now.year
        month = int(match.group("month"))
        day = int(match.group("day"))
        hour = int(match.group("hour"))
        minute = int(match.group("minute"))
        parsed_title = match.group("title").strip()
        title = title or parsed_title
        try:
            start = now.replace(year=year, month=month, day=day, hour=hour, minute=minute, second=0, microsecond=0)
        except ValueError as err:
            _LOGGER.debug("Calendar sensor %s has invalid date %s: %s", entity_id, raw, err)
            return None
        if not year_raw and start < now - timedelta(days=1):
            try:
                start = start.replace(year=now.year + 1)
            except ValueError:
                return None

    title = title or str(raw).strip()
    return EventInfo(
        key=_event_key(entity_id, start, title),
        title=title,
        start=start,
        end=end,
        location=location,
        description=description,
        source=entity_id,
        raw_text=str(raw).strip(),
    )


def _parse_calendar_event(entity_id: str, item: dict[str, Any]) -> EventInfo | None:
    start = item.get("start")
    if isinstance(start, dict):
        raw_start = start.get("dateTime") or start.get("date")
    else:
        raw_start = start
    if raw_start and len(str(raw_start)) <= 10:
        return None
    start_dt = _parse_datetime(raw_start)
    if start_dt is None:
        return None

    end = item.get("end")
    if isinstance(end, dict):
        raw_end = end.get("dateTime") or end.get("date")
    else:
        raw_end = end

    title = str(item.get("summary") or item.get("title") or item.get("message") or "일정").strip()
    location = str(item.get("location") or "").strip()
    description = str(item.get("description") or "").strip()
    return EventInfo(
        key=_event_key(entity_id, start_dt, title),
        title=title,
        start=start_dt,
        end=_parse_datetime(raw_end),
        location=location,
        description=description,
        source=entity_id,
        raw_text=str(title).strip(),
    )


def _validate_event(event: EventInfo, now, until, min_hour: int, max_hour: int) -> EventCandidate:
    if event.start <= now:
        return EventCandidate(event, False, "지난 일정")
    if event.start > until:
        return EventCandidate(event, False, "조회 범위 초과")
    if not (min_hour <= int(event.start.hour) <= max_hour):
        return EventCandidate(event, False, f"허용 시간대 제외 {min_hour}시부터 {max_hour}시")
    return EventCandidate(event, True, "")


async def async_get_event_candidates(
    hass: HomeAssistant,
    entity_ids: list[str],
    lookahead_hours: int,
    min_hour: int,
    max_hour: int,
) -> list[EventCandidate]:
    """Get parsed event candidates from calendar and sensor entities."""
    now = dt_util.now()
    until = now + timedelta(hours=lookahead_hours)
    parsed_events: list[EventInfo] = []

    sensor_entities = [e for e in entity_ids if not e.startswith("calendar.")]
    calendar_entities = [e for e in entity_ids if e.startswith("calendar.")]

    for entity_id in sensor_entities:
        event = _parse_sensor_entity(hass, entity_id)
        if event is not None:
            parsed_events.append(event)

    if calendar_entities:
        try:
            response = await hass.services.async_call(
                "calendar",
                "get_events",
                {
                    "entity_id": calendar_entities,
                    "start_date_time": now.isoformat(),
                    "end_date_time": until.isoformat(),
                },
                blocking=True,
                return_response=True,
            )
            for entity_id, payload in (response or {}).items():
                for item in payload.get("events", []):
                    event = _parse_calendar_event(entity_id, item)
                    if event is not None:
                        parsed_events.append(event)
        except (HomeAssistantError, ValueError, TypeError) as err:
            _LOGGER.warning("Could not read calendar events: %s", err)

    candidates = [_validate_event(event, now, until, min_hour, max_hour) for event in parsed_events]
    candidates.sort(key=lambda item: item.event.start)
    return candidates


async def async_get_events(
    hass: HomeAssistant,
    entity_ids: list[str],
    lookahead_hours: int,
    min_hour: int,
    max_hour: int,
) -> list[EventInfo]:
    """Get accepted upcoming events from calendar and sensor entities."""
    candidates = await async_get_event_candidates(hass, entity_ids, lookahead_hours, min_hour, max_hour)
    return [candidate.event for candidate in candidates if candidate.accepted]
