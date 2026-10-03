"""Notification helper."""

from __future__ import annotations

import logging

from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)


def _parse_script(script_entity: str) -> tuple[str, str]:
    """Parse script entity to service domain/name."""
    value = (script_entity or "script.universal_notify").strip()
    if "." not in value:
        return "script", value
    domain, service = value.split(".", 1)
    return domain, service


async def async_send_universal_notify(
    hass: HomeAssistant,
    *,
    notify_script: str,
    message: str,
    tts_target: str,
    tts_service: str,
    speed: float,
    pitch: float,
    tts_enabled: bool = True,
) -> None:
    """Call the configured notify script with the configured TTS options."""
    domain, service = _parse_script(notify_script)
    options: dict[str, float] = {}
    if speed:
        options["speed"] = float(speed)
    options["pitch"] = float(pitch)

    data = {
        "message": message,
        "tts": tts_enabled,
        "tts_target": tts_target,
        "tts_service": tts_service,
        "tts_options": options,
    }
    await hass.services.async_call(domain, service, data, blocking=True)


async def async_send_mobile_notify(
    hass: HomeAssistant,
    service_name: str,
    message: str,
    *,
    actions: list[dict],
    tag: str,
    route_link: str = "",
) -> None:
    domain, service = service_name.split(".", 1)
    data = {"tag": tag, "actions": actions}
    if route_link:
        data["url"] = route_link
        data["clickAction"] = route_link
    await hass.services.async_call(
        domain,
        service,
        {"title": "가족 일정 출발 도우미", "message": message, "data": data},
        blocking=True,
    )
