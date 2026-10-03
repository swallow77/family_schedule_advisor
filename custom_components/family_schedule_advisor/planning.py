"""Deterministic scheduling, location, and weather helpers."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any
from urllib.parse import urlencode

from .calendar_parser import EventInfo

_VIRTUAL = re.compile(
    r"(?<![\w])(?:온라인|화상|재택|비대면)(?=\W|$|회의|수업|근무)|(?:\bzoom\b)|(?:\bteams\b)|meet\.google|zoom\.us|teams\.microsoft",
    re.IGNORECASE,
)


def is_virtual(event: EventInfo) -> bool:
    return bool(_VIRTUAL.search(f"{event.title} {event.location}"))


def resolve_alias(event: EventInfo, aliases: dict[str, str]) -> str:
    text = f"{event.title} {event.location}".casefold()
    for alias, address in sorted(aliases.items(), key=lambda item: -len(item[0])):
        if re.search(
            r"(?<![\w])" + re.escape(alias.casefold()) + r"(?=\W|$|에서|으로|에\s|로\s)", text
        ):
            return address.strip()
    return ""


def is_quiet(now: datetime, enabled: bool, start: int, end: int) -> bool:
    if not enabled:
        return False
    if start == end:
        return True
    return start <= now.hour < end if start < end else now.hour >= start or now.hour < end


@dataclass(slots=True)
class Plan:
    """One event for one family profile, independent of the currently shown sensor."""

    event: EventInfo
    profile: dict[str, Any]
    destination: str
    destination_source: str
    route: Any = None
    departure_time: datetime | None = None
    notify_time: datetime | None = None
    status: str = "장소 확인 필요"
    route_status: str = "SKIPPED"
    route_error: str = ""
    estimated: bool = False
    weather: dict[str, str] = field(default_factory=dict)

    @property
    def key(self) -> str:
        return f"{self.profile.get('id', 'default')}:{self.event.key}"

    @property
    def person_name(self) -> str:
        return str(self.profile.get("name") or "가족")

    def calculate_times(self, prepare: int, margin: int, fallback: int) -> None:
        target = self.event.start - timedelta(minutes=margin)
        if is_virtual(self.event):
            self.status = "온라인 일정"
            self.route_status = "VIRTUAL"
            self.notify_time = self.event.start - timedelta(minutes=prepare)
            return
        if self.route is not None:
            self.route_status = self.route.status
            self.route_error = self.route.error_message
        if self.route is not None and self.route.status == "OK" and self.route.duration_seconds > 0:
            self.departure_time = self.route.departure_time or target - timedelta(
                seconds=self.route.duration_seconds
            )
            self.status = "준비 완료"
        elif fallback > 0:
            self.departure_time = target - timedelta(minutes=fallback)
            self.status = "수동 이동시간 사용"
            self.estimated = True
        else:
            self.status = "경로 확인 필요" if self.destination else "장소 확인 필요"
        # Unknown routes get an appointment reminder, never a fabricated departure.
        self.notify_time = (self.departure_time or target) - timedelta(minutes=prepare)

    def as_data(self) -> dict[str, Any]:
        route = self.route

        def display(value):
            return (
                f"{value.month}월 {value.day}일 {value.hour}시 {value.minute:02d}분"
                if value
                else "정보 없음"
            )

        steps = route.route_steps if route else []
        link = (
            "https://www.google.com/maps/dir/?"
            + urlencode(
                {
                    "api": 1,
                    "origin": self.profile.get("origin_address", ""),
                    "destination": self.destination,
                    "travelmode": self.profile.get("travel_mode", "transit"),
                }
            )
            if self.destination
            else ""
        )
        duration = (
            route.duration_text
            if route and route.duration_seconds
            else (
                f"약 {self.profile.get('fallback_travel_minutes', 0)}분 (직접 설정)"
                if self.estimated
                else "정보 없음"
            )
        )
        return {
            "status": self.status,
            "event_key": self.key,
            "event_title": self.event.title,
            "person_name": self.person_name,
            "event_source": self.event.source,
            "event_sources": list(self.event.sources),
            "event_location": self.event.location,
            "event_description": self.event.description,
            "raw_event_state": self.event.raw_text,
            "recognized_event_text": f"{self.event.start:%m월%d일 %H:%M} {self.event.title}",
            "event_time": self.event.start.isoformat(),
            "event_time_text": display(self.event.start),
            "event_end": self.event.end.isoformat() if self.event.end else None,
            "destination": self.destination,
            "destination_source": self.destination_source,
            "departure_time": self.departure_time.isoformat() if self.departure_time else None,
            "departure_time_text": display(self.departure_time),
            "notify_time": self.notify_time.isoformat() if self.notify_time else None,
            "notify_time_text": display(self.notify_time),
            "transit_duration_seconds": route.duration_seconds if route else 0,
            "transit_duration_text": duration,
            "route_status": self.route_status,
            "route_error": self.route_error,
            "route_summary": "\n".join(steps),
            "route_steps": steps,
            "start_address": route.start_address if route else "",
            "end_address": route.end_address if route else "",
            "route_link": link,
            "estimated_travel_time": self.estimated,
            "weather": self.weather,
        }


def find_conflicts(plans: list[Plan]) -> list[dict[str, str]]:
    conflicts = []
    ordered = sorted(plans, key=lambda plan: plan.event.start)
    for index, first in enumerate(ordered):
        if first.event.end is None:
            continue
        for second in ordered[index + 1 :]:
            if first.profile.get("id", "default") != second.profile.get("id", "default"):
                continue
            if (second.departure_time or second.event.start) < first.event.end:
                conflicts.append(
                    {
                        "person_name": first.person_name,
                        "first": first.event.title,
                        "second": second.event.title,
                        "reason": "이전 일정 종료 전에 다음 일정 또는 이동이 시작됩니다",
                    }
                )
    return conflicts


def forecast_window(forecasts: list[dict], start: datetime, end: datetime) -> list[dict]:
    result = []
    previous = None
    for item in forecasts:
        try:
            when = datetime.fromisoformat(str(item["datetime"]).replace("Z", "+00:00"))
            if when.tzinfo is None:
                when = when.replace(tzinfo=start.tzinfo)
        except (KeyError, TypeError, ValueError):
            continue
        if when <= start and (previous is None or when > previous[0]):
            previous = (when, item)
        if start <= when <= end:
            result.append(item)
    if (
        previous is not None
        and start - previous[0] <= timedelta(hours=1)
        and previous[1] not in result
    ):
        result.insert(0, previous[1])
    return result


def fallback_message(plan: Plan, stage: str = "prepare") -> str:
    data = plan.as_data()
    person = "" if plan.person_name == "가족" else plan.person_name + "님, "
    message = f"{person}{data['event_time_text']}에 {plan.event.title} 일정이 있습니다."
    if is_virtual(plan.event):
        return message + " 온라인 일정입니다. 접속과 준비를 확인해 주세요."
    if stage == "departure":
        return message + f" {plan.destination}로 출발할 시간입니다."
    if plan.departure_time:
        message += f" {data['departure_time_text']}에 출발하도록 준비해 주세요."
        if plan.estimated:
            message += " 이동시간은 직접 설정한 예상값입니다."
    else:
        message += " 이동 경로나 장소를 확인하지 못했으니 출발 시간을 직접 확인해 주세요."
    weather = plan.weather

    def numeric(key):
        match = re.search(r"-?\d+(?:\.\d+)?", str(weather.get(key, "")))
        return float(match[0]) if match else None

    temp = numeric("forecast_temp")
    if temp is None:
        temp = numeric("feels_like")
    if temp is None:
        temp = numeric("temp")
    rain = numeric("forecast_rain")
    if rain is None:
        rain = numeric("rain")
    if temp is not None:
        message += (
            " 따뜻한 겉옷을 챙기세요."
            if temp < 12
            else " 가벼운 옷차림으로 준비하세요."
            if temp >= 26
            else " 가벼운 겉옷을 챙기세요."
        )
    if (rain is not None and rain >= 40) or weather.get("forecast_condition") in {
        "rainy",
        "pouring",
        "lightning-rainy",
        "snowy-rainy",
    }:
        message += " 우산을 챙기세요."
    return message
