"""Google Directions API helper."""
from __future__ import annotations

from dataclasses import dataclass
import logging
from typing import Any

import aiohttp

_LOGGER = logging.getLogger(__name__)

DIRECTIONS_URL = "https://maps.googleapis.com/maps/api/directions/json"


@dataclass(slots=True)
class TransitResult:
    """Transit route result."""

    duration_seconds: int
    duration_text: str
    start_address: str = ""
    end_address: str = ""
    status: str = "OK"
    error_message: str = ""


async def async_get_transit_duration(
    session: aiohttp.ClientSession,
    api_key: str,
    origin: str,
    destination: str,
    arrival_time,
) -> TransitResult | None:
    """Fetch public transit duration from Google Directions API."""
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
    return TransitResult(
        duration_seconds=seconds,
        duration_text=text,
        start_address=str(leg.get("start_address") or ""),
        end_address=str(leg.get("end_address") or ""),
        status="OK",
    )
