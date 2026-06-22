"""Google Directions API helper."""
from __future__ import annotations

from dataclasses import dataclass, field
import html
import logging
import re
from typing import Any

import aiohttp

_LOGGER = logging.getLogger(__name__)

DIRECTIONS_URL = "https://maps.googleapis.com/maps/api/directions/json"
_TAG_RE = re.compile(r"<[^>]+>")


@dataclass(slots=True)
class TransitResult:
    """Transit route result."""

    duration_seconds: int
    duration_text: str
    start_address: str = ""
    end_address: str = ""
    route_summary: str = ""
    route_steps: list[str] = field(default_factory=list)
    status: str = "OK"
    error_message: str = ""


def _clean_text(value: Any) -> str:
    """Clean Google HTML-ish instruction text."""
    if value is None:
        return ""
    text = html.unescape(str(value))
    text = text.replace("<div style=\"font-size:0.9em\">", " ")
    text = text.replace("</div>", " ")
    text = _TAG_RE.sub("", text)
    return " ".join(text.split()).strip()


def _duration_text(step: dict[str, Any]) -> str:
    duration = step.get("duration") or {}
    return str(duration.get("text") or "").strip()


def _distance_text(step: dict[str, Any]) -> str:
    distance = step.get("distance") or {}
    return str(distance.get("text") or "").strip()


def _format_route_step(index: int, step: dict[str, Any]) -> str:
    """Format one Directions API step for Telegram/text display."""
    mode = str(step.get("travel_mode") or "").upper()
    duration = _duration_text(step)
    distance = _distance_text(step)

    if mode == "TRANSIT":
        details = step.get("transit_details") or {}
        line = details.get("line") or {}
        vehicle = line.get("vehicle") or {}
        line_name = str(line.get("short_name") or line.get("name") or "대중교통").strip()
        vehicle_name = str(vehicle.get("name") or vehicle.get("type") or "대중교통").strip()
        departure_stop = (details.get("departure_stop") or {}).get("name") or "출발 정류장"
        arrival_stop = (details.get("arrival_stop") or {}).get("name") or "도착 정류장"
        headsign = str(details.get("headsign") or "").strip()
        stops = details.get("num_stops")

        parts = [f"{index}. {vehicle_name} {line_name}: {departure_stop} 승차 → {arrival_stop} 하차"]
        extra: list[str] = []
        if headsign:
            extra.append(f"방면 {headsign}")
        if stops:
            extra.append(f"{stops}정거장")
        if duration:
            extra.append(f"약 {duration}")
        if extra:
            parts.append(f"({', '.join(extra)})")
        return " ".join(parts)

    instruction = _clean_text(step.get("html_instructions"))
    if not instruction:
        instruction = "도보 이동" if mode == "WALKING" else "이동"
    extras = []
    if duration:
        extras.append(f"약 {duration}")
    if distance:
        extras.append(distance)
    suffix = f" ({', '.join(extras)})" if extras else ""
    return f"{index}. {instruction}{suffix}"


def _build_route_steps(leg: dict[str, Any]) -> list[str]:
    steps = leg.get("steps") or []
    route_steps: list[str] = []
    for idx, step in enumerate(steps, start=1):
        text = _format_route_step(idx, step)
        if text:
            route_steps.append(text)
    return route_steps


async def async_get_transit_duration(
    session: aiohttp.ClientSession,
    api_key: str,
    origin: str,
    destination: str,
    arrival_time,
) -> TransitResult | None:
    """Fetch public transit duration and route steps from Google Directions API."""
    if not api_key or not origin or not destination:
        return None

    params: dict[str, Any] = {
        "origin": origin,
        "destination": destination,
        "mode": "transit",
        "arrival_time": int(arrival_time.timestamp()),
        "language": "ko",
        "region": "kr",
        "key": api_key,
    }

    try:
        async with session.get(DIRECTIONS_URL, params=params, timeout=aiohttp.ClientTimeout(total=25)) as resp:
            data = await resp.json(content_type=None)
    except (aiohttp.ClientError, TimeoutError, ValueError) as err:
        _LOGGER.warning("Google Directions request failed: %s", err)
        return TransitResult(0, "", status="ERROR", error_message=str(err))

    status = data.get("status", "UNKNOWN")
    if status != "OK":
        message = data.get("error_message", status)
        _LOGGER.warning("Google Directions status=%s message=%s", status, message)
        return TransitResult(0, "", status=status, error_message=message)

    routes = data.get("routes") or []
    if not routes:
        return TransitResult(0, "", status="ZERO_RESULTS", error_message="No routes")

    legs = routes[0].get("legs") or []
    if not legs:
        return TransitResult(0, "", status="ZERO_RESULTS", error_message="No legs")

    leg = legs[0]
    duration = leg.get("duration") or {}
    seconds = int(duration.get("value") or 0)
    text = str(duration.get("text") or "")
    route_steps = _build_route_steps(leg)
    return TransitResult(
        duration_seconds=seconds,
        duration_text=text,
        start_address=str(leg.get("start_address") or ""),
        end_address=str(leg.get("end_address") or ""),
        route_summary="\n".join(route_steps),
        route_steps=route_steps,
        status="OK",
    )
