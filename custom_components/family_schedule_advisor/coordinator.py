"""Coordinator for Family Schedule Advisor."""
from __future__ import annotations

import asyncio
from datetime import timedelta
import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.event import async_track_point_in_time, async_track_state_change_event
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .calendar_parser import EventInfo, async_get_event_candidates, format_compact_event, split_entities
from .const import (
    CONF_ARRIVAL_MARGIN_MINUTES,
    CONF_CALENDAR_ENTITIES,
    CONF_ENABLE_AI_DESTINATION,
    CONF_GOOGLE_API_KEY,
    CONF_LOOKAHEAD_HOURS,
    CONF_MAX_EVENT_HOUR,
    CONF_MIN_EVENT_HOUR,
    CONF_NOTIFY_SCRIPT,
    CONF_OLLAMA_MODEL,
    CONF_OLLAMA_URL,
    CONF_ORIGIN_ADDRESS,
    CONF_PREPARE_MINUTES,
    CONF_TTS_PITCH,
    CONF_TTS_SERVICE,
    CONF_TTS_SPEED,
    CONF_TTS_TARGET,
    CONF_WEATHER_APPARENT,
    CONF_WEATHER_DUST,
    CONF_WEATHER_FEELS_LIKE,
    CONF_WEATHER_HUMIDITY,
    CONF_WEATHER_RAIN,
    CONF_WEATHER_SKY,
    CONF_WEATHER_TEMP,
    CONF_WEATHER_UV,
    CONF_WEATHER_WIND,
    DEFAULT_ARRIVAL_MARGIN_MINUTES,
    DEFAULT_CALENDAR_ENTITIES,
    DEFAULT_LOOKAHEAD_HOURS,
    DEFAULT_MAX_EVENT_HOUR,
    DEFAULT_MIN_EVENT_HOUR,
    DEFAULT_NOTIFY_SCRIPT,
    DEFAULT_OLLAMA_MODEL,
    DEFAULT_OLLAMA_URL,
    DEFAULT_PREPARE_MINUTES,
    DEFAULT_TTS_PITCH,
    DEFAULT_TTS_SERVICE,
    DEFAULT_TTS_SPEED,
    DEFAULT_TTS_TARGET,
    DOMAIN,
    STORAGE_KEY,
    STORAGE_VERSION,
)
from .google_directions import async_get_transit_duration
from .notify import async_send_universal_notify
from .ollama_client import async_extract_destination, async_generate_text, build_outfit_prompt, sanitize_tts

_LOGGER = logging.getLogger(__name__)


class FamilyScheduleAdvisorCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Main coordinator."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=None,
        )
        self.entry = entry
        self.session = async_get_clientsession(hass)
        self._state_unsubs: list[CALLBACK_TYPE] = []
        self._notify_unsub: CALLBACK_TYPE | None = None
        self._coordinator_unsub: CALLBACK_TYPE | None = None
        self._store = Store(hass, STORAGE_VERSION, STORAGE_KEY)
        self._last_notified: list[str] = []
        self._last_action = ""
        self._last_action_time = ""
        self._last_notify_result = ""

    @property
    def config(self) -> dict[str, Any]:
        """Merged data and options."""
        config = dict(self.entry.data)
        config.update(self.entry.options)
        return config

    def _mark_action(self, action: str, result: str | None = None) -> None:
        """Store last manual action/debug state."""
        self._last_action = action
        self._last_action_time = dt_util.now().isoformat()
        if result is not None:
            self._last_notify_result = result

        if self.data is not None:
            new_data = dict(self.data)
            new_data["last_action"] = self._last_action
            new_data["last_action_time"] = self._last_action_time
            new_data["last_notify_result"] = self._last_notify_result
            self.async_set_updated_data(new_data)

    def _debug_fields(self) -> dict[str, str]:
        """Return last action fields for sensors."""
        return {
            "last_action": self._last_action,
            "last_action_time": self._last_action_time,
            "last_notify_result": self._last_notify_result,
        }

    @staticmethod
    def _candidate_sort_key(candidate) -> tuple:
        """Sort events and prefer rich calendar events over simple sensor events."""
        event = candidate.event
        return (
            event.start,
            0 if candidate.accepted else 1,
            0 if event.location and str(event.location).strip() else 1,
            0 if str(event.source).startswith("calendar.") else 1,
            0 if event.description and str(event.description).strip() else 1,
            str(event.source),
        )

    async def async_manual_recalculate(self) -> None:
        """Recalculate from button/service and expose visible feedback."""
        self._mark_action("일정 다시 계산 시작", "")
        await self.async_request_refresh()
        self._mark_action("일정 다시 계산 완료", self._last_notify_result)

    async def async_start(self) -> None:
        """Start listeners and scheduling."""
        stored = await self._store.async_load()
        self._last_notified = list((stored or {}).get("last_notified", []))[-50:]

        entities = split_entities(self.config.get(CONF_CALENDAR_ENTITIES, DEFAULT_CALENDAR_ENTITIES))

        @callback
        def _state_changed(event) -> None:
            self.hass.async_create_task(self.async_request_refresh())

        if entities:
            self._state_unsubs.append(async_track_state_change_event(self.hass, entities, _state_changed))

        self._coordinator_unsub = self.async_add_listener(self._schedule_from_current_data)
        self._schedule_from_current_data()

    async def async_shutdown(self) -> None:
        """Shutdown listeners."""
        for unsub in self._state_unsubs:
            unsub()
        self._state_unsubs.clear()
        if self._notify_unsub:
            self._notify_unsub()
            self._notify_unsub = None
        if self._coordinator_unsub:
            self._coordinator_unsub()
            self._coordinator_unsub = None

    async def _async_update_data(self) -> dict[str, Any]:
        """Fetch and calculate next event state."""
        try:
            return await self._async_calculate()
        except Exception as err:  # noqa: BLE001
            raise UpdateFailed(str(err)) from err

    async def _async_calculate(self) -> dict[str, Any]:
        cfg = self.config
        entities = split_entities(cfg.get(CONF_CALENDAR_ENTITIES, DEFAULT_CALENDAR_ENTITIES))
        lookahead_hours = int(cfg.get(CONF_LOOKAHEAD_HOURS, DEFAULT_LOOKAHEAD_HOURS))
        min_hour = int(cfg.get(CONF_MIN_EVENT_HOUR, DEFAULT_MIN_EVENT_HOUR))
        max_hour = int(cfg.get(CONF_MAX_EVENT_HOUR, DEFAULT_MAX_EVENT_HOUR))
        candidates = await async_get_event_candidates(
            self.hass,
            entities,
            lookahead_hours,
            min_hour,
            max_hour,
        )
        candidates = sorted(candidates, key=self._candidate_sort_key)
        accepted = sorted(
            [candidate for candidate in candidates if candidate.accepted],
            key=self._candidate_sort_key,
        )
        first_candidate = candidates[0] if candidates else None

        base_debug = {
            **self._debug_fields(),
            "checked_entities": ", ".join(entities),
            "lookahead_hours": lookahead_hours,
            "min_event_hour": min_hour,
            "max_event_hour": max_hour,
            "candidate_count": len(candidates),
            "accepted_candidate_count": len(accepted),
            "candidate_reject_reason": first_candidate.reject_reason if first_candidate else "",
        }

        if not accepted:
            if first_candidate is not None:
                event = first_candidate.event
                fallback_destination = event.location or event.title
                return {
                    **base_debug,
                    "status": "필터됨",
                    "event_title": event.title,
                    "event_key": "",
                    "recognized_event_text": format_compact_event(event),
                    "raw_event_state": event.raw_text,
                    "event_source": event.source,
                    "event_location": event.location,
                    "event_description": event.description,
                    "event_time": event.start.isoformat(),
                    "event_time_text": self._format_korean_time(event.start),
                    "destination": fallback_destination,
                    "destination_source": "calendar_location" if event.location else "event_title",
                    "route_status": "SKIPPED",
                    "message": f"일정은 인식했지만 알림 대상에서 제외되었습니다. 사유: {first_candidate.reject_reason}",
                }
            return {
                **base_debug,
                "status": "대기 중",
                "event_title": "",
                "event_key": "",
                "recognized_event_text": "인식된 일정 없음",
                "raw_event_state": "",
                "event_location": "",
                "event_description": "",
                "destination_source": "",
                "route_status": "",
                "message": "예정된 일정이 없습니다.",
            }

        selected_candidate = accepted[0]
        event = selected_candidate.event
        prepare_minutes = int(cfg.get(CONF_PREPARE_MINUTES, DEFAULT_PREPARE_MINUTES))
        arrival_margin = int(cfg.get(CONF_ARRIVAL_MARGIN_MINUTES, DEFAULT_ARRIVAL_MARGIN_MINUTES))
        arrival_target = event.start - timedelta(minutes=arrival_margin)

        destination, destination_source = await self._async_resolve_destination(event)
        route_status = "SKIPPED"
        route_error = ""
        transit_seconds = 0
        transit_text = ""
        start_address = ""
        end_address = ""

        if destination:
            result = await async_get_transit_duration(
                self.session,
                str(cfg.get(CONF_GOOGLE_API_KEY, "")).strip(),
                str(cfg.get(CONF_ORIGIN_ADDRESS, "")).strip(),
                destination,
                arrival_target,
            )
            if result:
                route_status = result.status
                route_error = result.error_message
                transit_seconds = int(result.duration_seconds or 0)
                transit_text = result.duration_text
                start_address = result.start_address
                end_address = result.end_address

        if transit_seconds > 0:
            departure_time = arrival_target - timedelta(seconds=transit_seconds)
        else:
            departure_time = event.start - timedelta(minutes=60)
            transit_text = transit_text or "정보 없음"

        notify_time = departure_time - timedelta(minutes=prepare_minutes)

        return {
            **base_debug,
            "status": "준비 완료" if destination else "장소 없음",
            "candidate_reject_reason": "",
            "event_key": event.key,
            "event_title": event.title,
            "recognized_event_text": format_compact_event(event),
            "raw_event_state": event.raw_text,
            "event_source": event.source,
            "event_location": event.location,
            "event_description": event.description,
            "event_time": event.start.isoformat(),
            "event_time_text": self._format_korean_time(event.start),
            "destination": destination,
            "destination_source": destination_source,
            "transit_duration_seconds": transit_seconds,
            "transit_duration_text": transit_text,
            "route_status": route_status,
            "route_error": route_error,
            "start_address": start_address,
            "end_address": end_address,
            "departure_time": departure_time.isoformat(),
            "departure_time_text": self._format_korean_time(departure_time),
            "notify_time": notify_time.isoformat(),
            "notify_time_text": self._format_korean_time(notify_time),
            "outfit_message": (self.data or {}).get("outfit_message", ""),
            "message": "다음 일정 계산 완료",
        }

    async def _async_resolve_destination(self, event: EventInfo) -> tuple[str, str]:
        """Resolve destination from event location, title, description, or AI."""
        cfg = self.config
        if event.location and event.location.strip():
            return event.location.strip(), "calendar_location"

        title = event.title.strip()
        description = event.description.strip()
        if not title:
            return "", "none"

        if cfg.get(CONF_ENABLE_AI_DESTINATION, True):
            extracted = await async_extract_destination(
                self.session,
                str(cfg.get(CONF_OLLAMA_URL, DEFAULT_OLLAMA_URL)),
                str(cfg.get(CONF_OLLAMA_MODEL, DEFAULT_OLLAMA_MODEL)),
                title,
                description,
            )
            if extracted:
                return extracted, "ai_extracted"

        return title, "event_title"

    @callback
    def _schedule_from_current_data(self) -> None:
        """Schedule notification from current data."""
        if self._notify_unsub:
            self._notify_unsub()
            self._notify_unsub = None

        data = self.data or {}
        notify_time_raw = data.get("notify_time")
        event_key = data.get("event_key")
        if not notify_time_raw or not event_key:
            return
        if event_key in self._last_notified:
            return

        notify_time = dt_util.parse_datetime(str(notify_time_raw))
        if notify_time is None:
            return
        notify_time = dt_util.as_local(notify_time)
        now = dt_util.now()
        if notify_time <= now:
            _LOGGER.info("Notification time already passed for %s", data.get("event_title"))
            return

        @callback
        def _fire(_now) -> None:
            self.hass.async_create_task(self.async_generate_and_notify(test=False))

        self._notify_unsub = async_track_point_in_time(self.hass, _fire, notify_time)
        _LOGGER.info("Scheduled departure advisor notification at %s", notify_time)

    async def async_generate_and_notify(self, *, test: bool = False) -> None:
        """Generate AI message and send notification."""
        self._mark_action("테스트 알림 생성 중" if test else "자동 알림 생성 중", "")

        if not self.data:
            await self.async_request_refresh()

        data = dict(self.data or {})
        event_key = data.get("event_key")

        # 테스트 알림은 필터링된 일정도 확인 가능해야 한다.
        # 기존에는 event_key가 없으면 조용히 종료되어 버튼이 아무 반응 없는 것처럼 보였다.
        if not event_key and test and data.get("event_title"):
            event_key = f"test:{data.get('event_source','')}:{data.get('event_time','')}:{data.get('event_title','')}"
            data["event_key"] = event_key
            data.setdefault("transit_duration_text", data.get("transit_duration_text") or "정보 없음")
            data.setdefault("departure_time_text", data.get("departure_time_text") or "정보 없음")
            data.setdefault("notify_time_text", data.get("notify_time_text") or "정보 없음")

        if not event_key:
            self._mark_action("테스트 알림 실패" if test else "자동 알림 실패", "인식된 일정이 없어 알림을 보낼 수 없습니다")
            return

        if not test and event_key in self._last_notified:
            self._mark_action("자동 알림 건너뜀", "이미 발송한 일정입니다")
            return

        weather = self._read_weather()
        prompt = build_outfit_prompt(data, weather)
        cfg = self.config
        text = ""
        for idx in range(3):
            text = await async_generate_text(
                self.session,
                str(cfg.get(CONF_OLLAMA_URL, DEFAULT_OLLAMA_URL)),
                str(cfg.get(CONF_OLLAMA_MODEL, DEFAULT_OLLAMA_MODEL)),
                prompt,
                timeout=120,
            )
            text = sanitize_tts(text)
            if len(text) > 10:
                break
            if idx < 2:
                await asyncio.sleep(2)

        if not text:
            text = self._fallback_message(data)

        try:
            await async_send_universal_notify(
                self.hass,
                notify_script=str(cfg.get(CONF_NOTIFY_SCRIPT, DEFAULT_NOTIFY_SCRIPT)),
                message=text,
                tts_target=str(cfg.get(CONF_TTS_TARGET, DEFAULT_TTS_TARGET)),
                tts_service=str(cfg.get(CONF_TTS_SERVICE, DEFAULT_TTS_SERVICE)),
                speed=float(cfg.get(CONF_TTS_SPEED, DEFAULT_TTS_SPEED)),
                pitch=float(cfg.get(CONF_TTS_PITCH, DEFAULT_TTS_PITCH)),
            )
        except Exception as err:  # noqa: BLE001
            _LOGGER.warning("Family Schedule Advisor notification failed: %s", err)
            new_data = dict(self.data or {})
            new_data["outfit_message"] = text
            new_data["last_action"] = self._last_action
            new_data["last_action_time"] = self._last_action_time
            new_data["last_notify_result"] = f"알림 실패: {err}"
            self._last_notify_result = new_data["last_notify_result"]
            self.async_set_updated_data(new_data)
            return

        new_data = dict(self.data or {})
        new_data["outfit_message"] = text
        new_data["last_action"] = self._last_action
        new_data["last_action_time"] = self._last_action_time
        new_data["last_notify_result"] = "테스트 발송 요청 완료" if test else "발송 요청 완료"
        self._last_notify_result = new_data["last_notify_result"]
        self.async_set_updated_data(new_data)

        if not test:
            self._last_notified.append(str(event_key))
            self._last_notified = self._last_notified[-50:]
            await self._store.async_save({"last_notified": self._last_notified})

    def _read_weather(self) -> dict[str, str]:
        cfg = self.config
        return {
            "rain": self._read_entity(cfg.get(CONF_WEATHER_RAIN), "%"),
            "feels_like": self._read_entity(cfg.get(CONF_WEATHER_FEELS_LIKE), "도"),
            "temp": self._read_entity(cfg.get(CONF_WEATHER_TEMP), "도"),
            "humidity": self._read_entity(cfg.get(CONF_WEATHER_HUMIDITY), "%"),
            "wind": self._read_entity(cfg.get(CONF_WEATHER_WIND), ""),
            "sky": self._read_entity(cfg.get(CONF_WEATHER_SKY), ""),
            "dust": self._read_entity(cfg.get(CONF_WEATHER_DUST), ""),
            "uv": self._read_entity(cfg.get(CONF_WEATHER_UV), ""),
            "apparent": self._read_entity(cfg.get(CONF_WEATHER_APPARENT), "도"),
        }

    def _read_entity(self, entity_id: str | None, fallback_unit: str = "") -> str:
        if not entity_id:
            return "정보 없음"
        state = self.hass.states.get(str(entity_id).strip())
        if state is None or state.state in ("unknown", "unavailable", "none", ""):
            return "정보 없음"
        unit = state.attributes.get("unit_of_measurement") or fallback_unit
        value = str(state.state)
        if unit and not value.endswith(str(unit)):
            return f"{value}{unit}"
        return value

    @staticmethod
    def _format_korean_time(value) -> str:
        return f"{value.month}월 {value.day}일 {value.hour}시 {value.minute:02d}분"

    @staticmethod
    def _format_compact_event(value, title: str) -> str:
        return f"{value.month:02d}월{value.day:02d}일 {value.hour:02d}:{value.minute:02d} {title}".strip()

    @staticmethod
    def _fallback_message(data: dict[str, Any]) -> str:
        title = data.get("event_title") or "일정"
        event_time = data.get("event_time_text") or "예정된 시간"
        departure = data.get("departure_time_text") or "출발 전"
        duration = data.get("transit_duration_text") or "정보 없음"
        destination = data.get("destination") or "목적지"
        return (
            f"오늘 {event_time}에 {title} 일정이 있습니다. "
            f"목적지는 {destination}입니다. "
            f"대중교통 예상 소요시간은 {duration}입니다. "
            f"여유 있게 준비하려면 {departure}쯤 출발을 생각해 주세요. "
            "날씨를 확인해서 편한 옷차림으로 준비해 주세요."
        )
