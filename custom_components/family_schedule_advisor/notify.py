"""Notification helper."""
from __future__ import annotations

import asyncio
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


async def _wait_for_media_state(
    hass: HomeAssistant,
    entity_id: str,
    state: str,
    timeout: float,
) -> bool:
    """Wait until a media entity reaches a state."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        if hass.states.is_state(entity_id, state):
            return True
        await asyncio.sleep(0.5)
    return False


async def _wait_for_media_idle(
    hass: HomeAssistant,
    entity_id: str,
    timeout: float,
) -> bool:
    """Wait until a media entity is not playing for two seconds."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    idle_since: float | None = None
    while loop.time() < deadline:
        if hass.states.is_state(entity_id, "playing"):
            idle_since = None
        else:
            if idle_since is None:
                idle_since = loop.time()
            elif loop.time() - idle_since >= 2:
                return True
        await asyncio.sleep(0.5)
    return False


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
    """Call the configured notify script and keep the task alive during media play."""
    domain, service = _parse_script(notify_script)
    data = {
        "message": message,
        "tts_target": tts_target,
        "tts_service": tts_service,
        "tts_options": {
            "speed": speed,
            "pitch": pitch,
        },
    }
    await hass.services.async_call(domain, service, data, blocking=False)
    if tts_target:
        started = await _wait_for_media_state(hass, tts_target, "playing", 30)
        if started:
            await _wait_for_media_idle(hass, tts_target, 300)
            await asyncio.sleep(5)
