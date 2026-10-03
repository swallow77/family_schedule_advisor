"""Optional modern Google Routes API provider."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

import aiohttp

from .google_directions import TransitResult

_LOGGER = logging.getLogger(__name__)
ROUTES_URL = "https://routes.googleapis.com/directions/v2:computeRoutes"


def _seconds(value: str) -> int:
    try:
        return int(float(str(value).removesuffix("s")))
    except (ValueError, TypeError):
        return 0


def _time(value: str | None) -> datetime | None:
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00")) if value else None
        return result if result is None or result.tzinfo else result.replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return None


async def async_get_route(
    session,
    api_key: str,
    origin: str,
    destination: str,
    arrival_time,
    mode: str = "transit",
) -> TransitResult | None:
    if not api_key or not origin or not destination:
        return None
    payload = {
        "origin": {"address": origin},
        "destination": {"address": destination},
        "travelMode": {"transit": "TRANSIT", "driving": "DRIVE", "walking": "WALK"}[mode],
        "languageCode": "ko-KR",
        "units": "METRIC",
    }
    if mode == "transit":
        payload["arrivalTime"] = (
            arrival_time.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
        )
    headers = {
        "X-Goog-Api-Key": api_key,
        "X-Goog-FieldMask": "routes.duration,routes.legs.steps.staticDuration,routes.legs.steps.travelMode,routes.legs.steps.navigationInstruction,routes.legs.steps.transitDetails",
    }
    try:
        async with session.post(
            ROUTES_URL,
            json=payload,
            headers=headers,
            timeout=aiohttp.ClientTimeout(total=15),
        ) as response:
            data = await response.json(content_type=None)
            if response.status >= 400:
                return TransitResult(
                    0,
                    "",
                    status="ERROR",
                    error_message="Routes API 요청 실패: 설정과 API 권한을 확인하세요",
                )
    except (aiohttp.ClientError, TimeoutError, ValueError) as err:
        _LOGGER.warning("Google Routes connection failed (%s)", type(err).__name__)
        return TransitResult(0, "", status="ERROR", error_message="경로 서비스 연결 실패")
    routes = data.get("routes") or []
    if not routes:
        return TransitResult(0, "", status="ZERO_RESULTS", error_message="조회된 경로 없음")
    route = routes[0]
    duration = _seconds(route.get("duration", "0s"))
    steps = [step for leg in route.get("legs", []) for step in leg.get("steps", [])]
    instructions = []
    elapsed_before_transit = 0
    departure = None
    arrival = None
    trailing_walk = 0
    for index, step in enumerate(steps, 1):
        transit = step.get("transitDetails") or {}
        stop = transit.get("stopDetails") or {}
        if transit:
            depart = _time(stop.get("departureTime"))
            if departure is None and depart is not None:
                departure = depart - timedelta(seconds=elapsed_before_transit)
            arrival = _time(stop.get("arrivalTime")) or arrival
            trailing_walk = 0
            line = transit.get("transitLine") or {}
            origin_stop = (stop.get("departureStop") or {}).get("name", "출발 정류장")
            dest_stop = (stop.get("arrivalStop") or {}).get("name", "도착 정류장")
            instructions.append(
                f"{index}. {line.get('nameShort') or line.get('name') or '대중교통'}: {origin_stop} 승차 → {dest_stop} 하차"
            )
        else:
            seconds = _seconds(step.get("staticDuration", "0s"))
            if departure is None:
                elapsed_before_transit += seconds
            elif arrival is not None:
                trailing_walk += seconds
            instruction = (step.get("navigationInstruction") or {}).get(
                "instructions"
            ) or "도보 이동"
            instructions.append(f"{index}. {instruction}")
    if arrival is not None:
        arrival += timedelta(seconds=trailing_walk)
    return TransitResult(
        duration,
        f"약 {(duration + 59) // 60}분",
        route_steps=instructions,
        route_summary="\n".join(instructions),
        departure_time=departure,
        arrival_time=arrival,
    )
