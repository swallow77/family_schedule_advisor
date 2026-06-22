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
) -> None:
    """Call the configured notify script with the configured TTS options."""
    domain, service = _parse_script(notify_script)
    options: dict[str, float] = {}
    if speed:
        options["speed"] = float(speed)
    if pitch:
        options["pitch"] = float(pitch)

    data = {
        "message": message,
        "tts": True,
        "tts_target": tts_target,
        "tts_service": tts_service,
        "tts_options": options,
    }
    await hass.services.async_call(domain, service, data, blocking=False)
