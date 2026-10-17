"""Deterministic scheduling, location, and weather helpers."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any
from urllib.parse import urlencode

from .calendar_parser import EventInfo
from .const import DEFAULT_UNRESOLVED_REMINDER_MINUTES

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
    prepare_minutes: int = 0
    arrival_margin_minutes: int = 0
    reminder_basis: str = "unresolved_60"

    @property
    def key(self) -> str:
        return f"{self.profile.get('id', 'default')}:{self.event.key}"

    @property
    def person_name(self) -> str:
        return str(self.profile.get("name") or "가족")

    def calculate_times(self, prepare: int, margin: int, fallback: int) -> None:
        self.prepare_minutes = prepare
        self.arrival_margin_minutes = margin
        target = self.event.start - timedelta(minutes=margin)
        if is_virtual(self.event):
            self.status = "온라인 일정"
            self.route_status = "VIRTUAL"
            self.notify_time = self.event.start - timedelta(minutes=prepare)
            self.reminder_basis = "virtual_preparation"
            return
        if self.route is not None:
            self.route_status = self.route.status
            self.route_error = self.route.error_message
        if self.route is not None and self.route.status == "OK" and self.route.duration_seconds > 0:
            self.departure_time = self.route.departure_time or target - timedelta(
                seconds=self.route.duration_seconds
            )
            self.status = "준비 완료"
            self.reminder_basis = "route_departure"
        elif fallback > 0:
            self.departure_time = target - timedelta(minutes=fallback)
            self.status = "수동 이동시간 사용"
            self.estimated = True
            self.reminder_basis = "manual_travel"
        else:
            self.status = "경로 확인 필요" if self.destination else "장소 확인 필요"
            self.reminder_basis = "unresolved_60"
        # A location conversation never delays the appointment reminder. When
        # departure is unknown, the deadline is exactly one hour before start;
        # preparation and arrival margins are not subtracted a second time.
        self.notify_time = (
            self.departure_time - timedelta(minutes=prepare)
            if self.departure_time is not None
            else self.event.start - timedelta(minutes=DEFAULT_UNRESOLVED_REMINDER_MINUTES)
        )

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
        if self.reminder_basis == "virtual_preparation":
            calculation = f"온라인 일정: 약속 {display(self.event.start)} − 준비 {self.prepare_minutes}분 → 준비 알림 {display(self.notify_time)}"
        elif self.reminder_basis == "unresolved_60":
            calculation = f"이동정보 미확인: 약속 {display(self.event.start)} − 기본 {DEFAULT_UNRESOLVED_REMINDER_MINUTES}분 → 준비 알림 {display(self.notify_time)}"
        elif (
            self.route is not None and self.route.departure_time is not None and not self.estimated
        ):
            calculation = f"도착 여유 {self.arrival_margin_minutes}분을 반영한 경로 출발 {display(self.departure_time)} − 준비 {self.prepare_minutes}분 → 준비 알림 {display(self.notify_time)}"
        else:
            calculation = f"약속 {display(self.event.start)} − 이동 {duration} − 도착 여유 {self.arrival_margin_minutes}분 − 준비 {self.prepare_minutes}분 → 준비 알림 {display(self.notify_time)}"
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
            "prepare_minutes": self.prepare_minutes,
            "arrival_margin_minutes": self.arrival_margin_minutes,
            "reminder_basis": self.reminder_basis,
            "preparation_calculation": calculation,
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


def spoken_time(value: datetime) -> str:
    """Speak the day and local time without repeating the month."""
    period = "오전" if value.hour < 12 else "오후"
    minute = f" {value.minute}분" if value.minute else ""
    return f"{value.day}일 {period} {value.hour % 12 or 12}시{minute}"


def weather_advice(weather: dict[str, str]) -> str:
    """Keep useful clothing guidance available even when AI is not ready."""

    def numeric(key):
        match = re.search(r"-?\d+(?:\.\d+)?", str(weather.get(key, "")))
        return float(match[0]) if match else None

    def first_number(*keys):
        return next((value for key in keys if (value := numeric(key)) is not None), None)

    advice = []
    temp = first_number("forecast_temp", "apparent", "feels_like", "temp")
    current = numeric("temp")
    feels = first_number("apparent", "feels_like")
    sky = str(weather.get("forecast_condition") or weather.get("sky") or "")
    conditions = {
        "sunny": "맑음",
        "clear-night": "맑음",
        "cloudy": "흐림",
        "partlycloudy": "구름 많음",
        "rainy": "비",
        "pouring": "강한 비",
        "lightning-rainy": "뇌우",
        "snowy": "눈",
        "snowy-rainy": "비 또는 눈",
        "fog": "안개",
        "windy": "바람이 강함",
    }
    if sky not in {"", "정보 없음", "unknown", "unavailable", "none"}:
        advice.append(f"하늘 상태는 {conditions.get(sky, sky)}입니다.")
    if current is not None:
        text = f"현재 기온은 {current:g}도"
        if feels is not None:
            text += f", 체감온도는 {feels:g}도"
        advice.append(text + "입니다.")
    if numeric("forecast_temp") is not None:
        advice.append(f"외출 시간대에는 약 {numeric('forecast_temp'):g}도로 예상됩니다.")
    if temp is not None:
        if temp < 5:
            clothing = "추위에 대비해 긴소매 안에 보온 내의를 겹쳐 입고 두꺼운 외투와 긴바지를 입으세요. 목도리와 따뜻한 양말도 도움이 됩니다."
        elif temp < 12:
            clothing = "쌀쌀한 기온이므로 긴소매와 니트 위에 따뜻한 겉옷을 입고 긴바지와 발을 덮는 신발로 준비하세요."
        elif temp < 20:
            clothing = "서늘하게 느낄 수 있으니 긴소매에 가벼운 겉옷이나 가디건을 겹쳐 입고 긴바지를 입으세요. 실내에서는 겉옷을 벗어 조절하면 좋겠습니다."
        elif temp < 26:
            clothing = "얇은 긴소매나 반소매에 가벼운 긴바지를 입고, 냉방이 있는 실내에서 입을 얇은 가디건을 챙기세요."
        else:
            clothing = "더운 기온이므로 통풍이 잘되는 반소매와 가벼운 옷차림으로 준비하고, 오래 이동한다면 마실 물을 챙기세요."
        advice.append(clothing)
    else:
        advice.append("기온 정보가 없어 옷 두께를 정하기 어렵습니다. 외출 전 날씨를 확인해 주세요.")
    rain = first_number("forecast_rain", "rain")
    if rain is not None:
        label = "외출 시간대" if numeric("forecast_rain") is not None else "현재 제공된"
        advice.append(f"{label} 강수확률은 {rain:g}퍼센트입니다.")
    rainy = sky in {"rainy", "pouring", "lightning-rainy", "snowy-rainy"} or "비" in sky
    snowy = sky in {"snowy", "snowy-rainy"} or "눈" in sky
    if (rain is not None and rain >= 40) or rainy or snowy:
        advice.append(
            "우산을 챙기고 젖거나 미끄러운 길에 대비해 밑창이 잘 미끄러지지 않는 신발을 신으세요."
        )
    wind = numeric("wind")
    if wind is not None:
        wind_text = str(weather.get("wind", "")).lower()
        if "km/h" in wind_text or "kmh" in wind_text:
            wind /= 3.6
        elif "mph" in wind_text:
            wind *= 0.44704
        if wind >= 5:
            advice.append("바람이 강한 편이니 바람을 막아주는 겉옷으로 준비하세요.")
    humidity = numeric("humidity")
    if humidity is not None and humidity >= 75 and temp is not None and temp >= 24:
        advice.append("습도가 높아 답답할 수 있으니 땀이 잘 마르는 소재가 좋겠습니다.")
    dust = str(weather.get("dust", ""))
    if "나쁨" in dust or "매우나쁨" in dust:
        advice.append(f"초미세먼지 등급은 {dust}이므로 보건용 마스크를 챙기세요.")
    uv = str(weather.get("uv", ""))
    uv_number = numeric("uv")
    if any(level in uv for level in ("보통", "높음", "위험")) or (
        uv_number is not None and uv_number >= 3
    ):
        advice.append("자외선에 대비해 자외선 차단제를 바르고, 야외에 오래 있다면 모자도 챙기세요.")
    return " ".join(advice)


def destination_advice(plan: Plan) -> str:
    """Suggest relevant items without inventing venue-specific requirements."""
    text = f"{plan.event.title} {plan.event.location} {plan.destination}".casefold()
    explicit = re.search(r"(?:준비물|지참물)\s*[:：]\s*([^\n]{1,160})", plan.event.description)
    if explicit:
        return f"일정에 적힌 준비물은 {explicit[1].strip()}입니다. 빠진 것이 없는지 확인해 주세요."
    if any(word in text for word in ("병원", "의원", "진료", "치과", "검진")):
        return "진료에 사용할 신분증과 예약 내역을 챙기고, 병원에서 따로 안내한 지참물이나 검사 전 주의사항도 확인해 주세요."
    if any(word in text for word in ("등산", "산행", "트레킹")):
        return "산행에는 접지력이 좋은 신발과 마실 물, 가벼운 간식을 챙기고 체온을 조절할 겉옷을 준비하세요."
    if any(word in text for word in ("운동", "헬스", "수영", "체육", "러닝", "축구")):
        return "활동에 맞는 운동복과 물, 땀을 닦을 수건을 챙기고 수영 일정이라면 수영용품도 확인하세요."
    if any(word in text for word in ("학교", "수업", "학원", "도서관", "시험", "대학")):
        return "일정에 사용할 교재나 자료와 필기구를 챙기고, 시험이라면 수험표와 신분증이 필요한지 확인하세요."
    if any(word in text for word in ("회의", "미팅", "출장", "면접", "출근")):
        return "업무나 만남에 필요한 자료와 필기구를 챙기고, 노트북을 가져간다면 충전기도 함께 준비하세요."
    if any(word in text for word in ("공연", "콘서트", "영화", "전시", "뮤지컬")):
        return "예매한 입장권이나 예약 화면을 미리 확인하고, 모바일 입장권을 쓴다면 휴대전화 배터리도 확인해 주세요."
    if any(word in text for word in ("식당", "갈비", "식사", "외식", "카페", "음식점", "레스토랑")):
        return "식사 장소의 예약 내역과 결제 수단을 확인해 주세요. 대중교통으로 이동한다면 교통카드도 챙기세요."
    if plan.profile.get("travel_mode", "transit") == "transit":
        return "이동에 사용할 교통카드와 휴대전화를 챙기고, 일정 설명에 별도로 적힌 지참물이 있는지 확인해 주세요."
    return "휴대전화와 일정에 필요한 자료를 챙기고, 방문 전에 예약 내역이 있는지도 확인해 주세요."


def fallback_message(plan: Plan, stage: str = "prepare", *, include_advice: bool = True) -> str:
    data = plan.as_data()
    person = "" if plan.person_name == "가족" else plan.person_name + "님, "
    message = f"{person}{spoken_time(plan.event.start)}에 {plan.event.title} 일정이 있습니다."
    if is_virtual(plan.event):
        return message + " 온라인 일정입니다. 접속과 준비를 확인해 주세요."
    if stage == "departure":
        destination = plan.destination or plan.event.location.strip() or "목적지 미확인"
        message += f" 목적지는 {destination}입니다."
        if plan.departure_time is not None:
            message += f" 출발 예정 시각은 {spoken_time(plan.departure_time)}입니다."
        else:
            message += " 출발 시각은 확인되지 않았습니다."
        if data["transit_duration_text"] != "정보 없음":
            message += f" 예상 이동시간은 {data['transit_duration_text']}입니다."
        else:
            message += " 예상 이동시간은 확인되지 않았습니다."
        return message + " 지금 출발해 주세요."
    if plan.departure_time:
        if plan.destination:
            message += f" 목적지는 {plan.destination}입니다."
        if data["transit_duration_text"] != "정보 없음":
            message += f" 예상 이동시간은 {data['transit_duration_text']}입니다."
        message += f" {spoken_time(plan.departure_time)}에 출발하도록 준비해 주세요."
        if plan.estimated:
            message += " 이동시간은 직접 설정한 예상값입니다."
    else:
        message += " 이동정보가 확인되지 않아 기본 준비 알림을 드립니다."
        message += " 이동 경로나 장소를 확인하지 못했으니 출발 시간을 직접 확인해 주세요."
    if include_advice:
        message += " " + weather_advice(plan.weather) + " " + destination_advice(plan)
    return message
