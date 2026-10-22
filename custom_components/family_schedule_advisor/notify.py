"""Notification helper."""

from __future__ import annotations

import logging

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError

_LOGGER = logging.getLogger(__name__)


def telegram_reminder_text(message: str, details: dict, stage: str, test: bool = False) -> str:
    """Use the exact notified plan, including its own route, not a selected sensor."""
    title = "가족 일정 출발 안내" if stage == "departure" else "가족 일정 준비 안내"
    if test:
        title = "[테스트] " + title
    text = "\n".join(
        [
            title,
            message[:1900],
            "",
            f"일정: {str(details.get('event_title') or '')[:250]}",
            f"약속: {details.get('event_time_text') or '정보 없음'}",
            f"목적지: {str(details.get('destination') or '정보 없음')[:300]}",
            f"이동시간: {details.get('transit_duration_text') or '정보 없음'}",
            f"출발: {details.get('departure_time_text') or '정보 없음'}",
            f"준비 알림: {details.get('notify_time_text') or '정보 없음'}",
            "",
            "이동 경로:",
            str(details.get("route_summary") or "상세 경로 정보 없음")[:1100],
            str(details.get("route_link") or "")[:400],
        ]
    )
    # Telegram's limit counts UTF-16 units, including emoji surrogate pairs.
    return text.encode("utf-16-le")[:8000].decode("utf-16-le", errors="ignore")


async def async_send_telegram_notify(hass: HomeAssistant, entity_id: str, message: str) -> None:
    """Only record success after Telegram returns a sent message ID."""
    result = await hass.services.async_call(
        "telegram_bot",
        "send_message",
        {"entity_id": [entity_id], "message": message, "parse_mode": "plain_text"},
        blocking=True,
        return_response=True,
    )
    chats = (result or {}).get("chats", [])
    if not any(
        chat.get("message_id") is not None and chat.get("entity_id", entity_id) == entity_id
        for chat in chats
    ):
        raise HomeAssistantError("Telegram did not acknowledge the reminder")


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
    if domain == "script" and service == "family_schedule_advisor_noop_notify":
        raise HomeAssistantError(
            "알림을 보내지 않는 빈 스크립트입니다. 실제 알림 스크립트를 선택하세요."
        )
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
    # HA consumes `stop` with error=true inside top-level scripts. Capture
    # an explicit playback result rather than treating that abort as delivery.
    result = await hass.services.async_call(
        domain,
        service,
        data,
        blocking=True,
        **({"return_response": True} if domain == "script" else {}),
    )
    if isinstance(result, dict) and "tts_status" in result:
        if result["tts_status"] == "failed":
            raise HomeAssistantError(
                "음성 재생 실패: " + str(result.get("reason") or "playback_not_confirmed")
            )
        if result["tts_status"] not in ("played", "suppressed"):
            raise HomeAssistantError("알 수 없는 음성 재생 결과입니다")


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
