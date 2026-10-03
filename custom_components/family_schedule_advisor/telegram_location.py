"""Persisted, bot-scoped location conversations for upcoming appointments."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import replace
from datetime import timedelta

from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.util import dt as dt_util

from . import const as c
from .calendar_location import async_write_location
from .calendar_parser import _event_key
from .planning import is_virtual

_LOGGER = logging.getLogger(__name__)


def resolve_target(hass, config):
    """Both selected entities must belong to the same existing Telegram bot."""
    registry = er.async_get(hass)
    notify = registry.async_get(config.get(c.CONF_TELEGRAM_NOTIFY_ENTITY, ""))
    event = registry.async_get(config.get(c.CONF_TELEGRAM_EVENT_ENTITY, ""))
    if (
        not notify
        or not event
        or notify.platform != "telegram_bot"
        or event.platform != "telegram_bot"
        or notify.config_entry_id != event.config_entry_id
        or notify.disabled_by
        or event.disabled_by
    ):
        raise ValueError("같은 텔레그램 봇의 알림 및 업데이트 이벤트를 선택해 주세요.")
    entry = hass.config_entries.async_get_entry(notify.config_entry_id)
    subentry = entry.subentries.get(notify.config_subentry_id) if entry else None
    chat_id = int(subentry.data["chat_id"]) if subentry else 0
    if chat_id <= 0:
        raise ValueError("장소 확인은 개인 채팅만 지원합니다. 봇의 개인 채팅 알림을 선택해 주세요.")
    return notify.config_entry_id, chat_id


class TelegramLocationConversation:
    def __init__(self, coordinator):
        self.coordinator = coordinator
        self.pending = {}
        self.overrides = {}
        self.error = ""
        self._lock = asyncio.Lock()
        self._seen = set()
        self._bot_id = None
        self._chat_id = None

    def restore(self, stored):
        self.pending = dict(stored.get("pending", {}))
        self.overrides = dict(stored.get("overrides", {}))

    def snapshot(self):
        now = dt_util.now()
        for records in (self.pending, self.overrides):
            for key, record in list(records.items()):
                expires = dt_util.parse_datetime(record.get("expires", ""))
                if expires is None or dt_util.as_local(expires) <= now:
                    records.pop(key, None)
        return {"pending": dict(self.pending), "overrides": dict(self.overrides)}

    def apply_override(self, event):
        for key, record in self.overrides.items():
            if event.key in record["keys"]:
                return replace(event, key=key, location=record["location"])
        return event

    def start(self):
        cfg = self.coordinator.config
        if not cfg.get(c.CONF_TELEGRAM_LOCATION_ENABLED, False):
            return []
        try:
            self._bot_id, self._chat_id = resolve_target(self.coordinator.hass, cfg)
        except (ValueError, KeyError, TypeError) as err:
            self.error = str(err)
            return []

        def state_changed(event):
            state = event.data.get("new_state")
            if state and state.state not in ("unknown", "unavailable"):
                self.coordinator._create_task(self.async_receive(dict(state.attributes)))

        async def legacy_event(event):
            await self.async_receive(event.data)

        return [
            async_track_state_change_event(
                self.coordinator.hass, [cfg[c.CONF_TELEGRAM_EVENT_ENTITY]], state_changed
            ),
            self.coordinator.hass.bus.async_listen("telegram_text", legacy_event),
        ]

    async def _send(self, message, reply_to=None):
        data = {
            "entity_id": [self.coordinator.config[c.CONF_TELEGRAM_NOTIFY_ENTITY]],
            "message": message,
            "parse_mode": "plain_text",
        }
        if reply_to is not None:
            data["reply_to_message_id"] = int(reply_to)
        async with asyncio.timeout(20):
            response = await self.coordinator.hass.services.async_call(
                "telegram_bot", "send_message", data, blocking=True, return_response=True
            )
        ids = [
            int(chat["message_id"])
            for chat in (response or {}).get("chats", [])
            if int(chat.get("chat_id", 0)) == self._chat_id
        ]
        if not ids:
            raise ValueError("텔레그램 메시지 번호를 받지 못했습니다.")
        return ids

    async def async_prompt_missing(self):
        if self._bot_id is None or self.coordinator._stopped:
            return
        async with self._lock:
            self.snapshot()
            changed = False
            now = dt_util.now()
            limit = now + timedelta(
                hours=int(self.coordinator.config.get(c.CONF_TELEGRAM_REQUEST_HOURS, 24))
            )
            for plan in self.coordinator._plans.values():
                if (
                    is_virtual(plan.event)
                    or not now < plan.event.start <= limit
                    or (plan.destination and plan.route_status not in ("ZERO_RESULTS", "NOT_FOUND"))
                    or f"{plan.key}:all" in self.coordinator._done
                ):
                    continue
                record = self.pending.get(plan.event.key)
                if record and (record.get("state") != "sending" or record.get("attempts", 0) >= 3):
                    continue
                if record:
                    retry = dt_util.parse_datetime(record.get("retry", ""))
                    if retry and retry > now:
                        continue
                if (
                    len(
                        [
                            r
                            for r in self.pending.values()
                            if r.get("state") in ("address", "confirm")
                        ]
                    )
                    >= 5
                ):
                    break
                record = record or {
                    "expires": plan.event.start.isoformat(),
                    "message_ids": [],
                    "attempts": 0,
                }
                self.pending[plan.event.key] = record
                record["attempts"] += 1
                changed = True
                record["state"] = "sending"
                record["retry"] = (now + timedelta(minutes=5)).isoformat()
                await self.coordinator._save_state()
                try:
                    text = f"{plan.event.start:%m월 %d일 %H:%M} · {plan.event.title}\n장소를 확인해 주세요."
                    if plan.destination:
                        text += f"\n현재 장소: {plan.destination}\n이 장소의 이동 경로를 찾지 못했습니다."
                    text += "\n이 메시지에 답장으로 장소 이름 또는 정확한 주소를 보내 주세요.\n예: 서울 강남구 테헤란로 152\n취소하려면 '취소'라고 답장해 주세요."
                    record["message_ids"] = await self._send(text)
                    record["state"] = "address"
                    self.error = ""
                except (
                    HomeAssistantError,
                    TimeoutError,
                    ValueError,
                    TypeError,
                    KeyError,
                    OSError,
                ) as err:
                    self.error = (
                        "텔레그램 장소 확인 요청을 보내지 못했습니다. 봇 연결을 확인해 주세요."
                    )
                    _LOGGER.warning("Location request failed: %s", type(err).__name__)
                await self.coordinator._save_state()

            if changed:
                self.coordinator._publish()

    async def async_receive(self, data):
        if self._bot_id is None or data.get("event_type", "telegram_text") != "telegram_text":
            return
        bot = data.get("bot") or {}
        # Only the selected bot and the selected private chat's user can respond.
        if bot.get("config_entry_id") != self._bot_id:
            return
        if str(data.get("chat_id")) != str(self._chat_id) or str(data.get("user_id")) != str(
            self._chat_id
        ):
            return
        if data.get("id") is None:
            return
        message_id = int(data["id"])
        async with self._lock:
            if message_id in self._seen:
                return
            self._seen.add(message_id)
            if len(self._seen) > 100:
                self._seen.remove(min(self._seen))
            self.snapshot()
            reply_to = data.get("reply_to_message_id")
            active = [
                (key, record)
                for key, record in self.pending.items()
                if record.get("state") in ("address", "confirm")
            ]
            matching = [
                (key, record)
                for key, record in active
                if reply_to is not None and int(reply_to) in record["message_ids"]
            ]
            if reply_to is None and len(active) == 1:
                matching = active
            if len(matching) != 1:
                # Unrelated chat and replies to other conversations stay untouched.
                return
            key, record = matching[0]
            plans = [
                plan
                for plan in self.coordinator._plans.values()
                if plan.event.key == key and plan.event.start > dt_util.now()
            ]
            if not plans:
                record["state"] = "closed"
                await self.coordinator._save_state()
                return
            text = str(data.get("text", "")).strip()
            try:
                await self._handle_text(key, record, plans, text, message_id)
                self.error = ""
            except (
                HomeAssistantError,
                TimeoutError,
                ValueError,
                TypeError,
                KeyError,
                OSError,
            ) as err:
                self.error = "텔레그램 답변 처리 중 오류가 발생했습니다. 장소 확인 메시지에 다시 답장해 주세요."
                _LOGGER.warning("Location reply failed: %s", type(err).__name__)
            await self.coordinator._save_state()
            self.coordinator._publish()

    async def _handle_text(self, key, record, plans, text, message_id):
        if text in ("취소", "cancel", "/cancel"):
            record["state"] = "closed"
            await self.coordinator._save_state()
            await self._send("이 일정의 장소 확인을 취소했습니다.", message_id)
            return
        if record["state"] == "confirm" and text in ("확인", "네", "예"):
            location = record["location"]
            original = plans[0].event
            record["state"] = "closed"
            self.overrides[key] = {
                "location": location,
                "keys": [key, _event_key("", original.start, original.title, location)],
                "expires": original.start.isoformat(),
            }
            # Save before external writes, so a repeated confirmation cannot send twice.
            await self.coordinator._save_state()
            calendar_result = "출발 계산에 장소를 적용했습니다."
            if self.coordinator.config.get(c.CONF_TELEGRAM_WRITE_CALENDAR, False):
                try:
                    async with asyncio.timeout(30):
                        calendar_result = await async_write_location(
                            self.coordinator.hass, original, location
                        )
                except (
                    HomeAssistantError,
                    TimeoutError,
                    ValueError,
                    TypeError,
                    KeyError,
                    OSError,
                ) as err:
                    _LOGGER.warning("Calendar location write failed: %s", type(err).__name__)
                    calendar_result = "출발 계산에는 적용했습니다. 캘린더 저장은 실패했습니다. 수정 권한과 일정 변경 여부를 확인해 주세요."
            self.coordinator._route_cache.clear()
            await self.coordinator.async_request_refresh()
            self.coordinator._mark_action("텔레그램 장소 적용", calendar_result)
            await self._send(f"장소: {location}\n{calendar_result}", message_id)
            return
        if text in ("다시 입력", "아니오", "아니요"):
            record["state"] = "address"
            record["message_ids"].extend(
                await self._send("정확한 장소 이름 또는 주소를 다시 보내 주세요.", message_id)
            )
            return
        if not 2 <= len(text) <= 400 or text in ("확인", "네", "예") or text.startswith("/"):
            record["message_ids"].extend(
                await self._send("2~400자의 장소 이름 또는 주소를 입력해 주세요.", message_id)
            )
            return
        location = text.removeprefix("장소:").strip()
        if len(location) < 2:
            record["message_ids"].extend(
                await self._send("장소 이름 또는 주소를 입력해 주세요.", message_id)
            )
            return
        preview = await asyncio.gather(
            *(
                self.coordinator._async_build_plan(
                    plan.event, plan.profile, destination_override=location
                )
                for plan in plans
            )
        )
        lines = [
            f"{plans[0].event.start:%m월 %d일 %H:%M} · {plans[0].event.title}",
            f"입력한 장소: {location}",
        ]
        for plan in preview:
            details = plan.as_data()
            lines.append(
                f"{plan.person_name}: 이동 {details['transit_duration_text']}, 출발 {details['departure_time_text']}"
            )
            if plan.route_status != "OK":
                lines.append(
                    "경로를 확인하지 못했습니다. 주소를 더 정확히 입력하거나, 장소만 저장할 수 있습니다."
                )
        action = (
            "캘린더에 장소를 저장하고 출발 계산에 적용"
            if self.coordinator.config.get(c.CONF_TELEGRAM_WRITE_CALENDAR, False)
            else "출발 계산에 적용"
        )
        lines.append(
            f"이 장소가 맞으면 '확인'이라고 답장해 주세요. {action}합니다.\n다르면 새 주소를 보내 주세요. 취소는 '취소'입니다."
        )
        record["location"] = location
        record["state"] = "confirm"
        record["message_ids"].extend(await self._send("\n".join(lines), message_id))
