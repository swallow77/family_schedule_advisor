"""Track and notify every upcoming family appointment independently."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from datetime import timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.event import (
    async_track_point_in_time,
    async_track_state_change_event,
)
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from . import const as c
from .calendar_parser import (
    async_get_event_candidates,
    format_compact_event,
    split_entities,
)
from .google_directions import async_get_transit_duration
from .google_routes import async_get_route
from .notify import async_send_mobile_notify, async_send_universal_notify
from .ollama_client import (
    async_extract_destination,
    async_generate_text,
    build_outfit_prompt,
    sanitize_tts,
)
from .planning import (
    Plan,
    fallback_message,
    find_conflicts,
    forecast_window,
    is_quiet,
    is_virtual,
    resolve_alias,
)
from .telegram_location import TelegramLocationConversation

_LOGGER = logging.getLogger(__name__)


class FamilyScheduleAdvisorCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.entry = entry
        super().__init__(
            hass,
            _LOGGER,
            name=c.DOMAIN,
            config_entry=entry,
            update_interval=timedelta(
                minutes=int(self.config.get(c.CONF_POLL_MINUTES, c.DEFAULT_POLL_MINUTES))
            ),
        )
        self.session = async_get_clientsession(hass)
        self._store = Store(hass, c.STORAGE_VERSION, f"{c.STORAGE_KEY}.{entry.entry_id}")
        self._plans: dict[str, Plan] = {}
        self._timer_unsubs = {}
        self._unsubs = []
        self._tasks: set[asyncio.Task] = set()
        self._sent: dict[str, str] = {}
        self._done: dict[str, str] = {}
        self._snoozed: dict[str, str] = {}
        self._legacy_notified: list[str] = []
        self._inflight: set[str] = set()
        self._retry_at = {}
        self._retry_counts = {}
        self._route_cache = {}
        self._destination_cache = {}
        self._messages = {}
        self._message_jobs = set()
        self._message_sem = asyncio.Semaphore(1)
        self._calculation_sem = asyncio.Semaphore(4)
        self._forecasts = []
        self._forecast_error = ""
        self._base_debug = {}
        self._last_action = ""
        self._last_action_time = ""
        self._last_notify_result = ""
        self._started = False
        self._stopped = False
        self._state_save_lock = asyncio.Lock()
        self._last_message = ""
        self._last_message_event_key = ""
        self.telegram = TelegramLocationConversation(self)

    @property
    def config(self) -> dict[str, Any]:
        return {**self.entry.data, **self.entry.options}

    def _create_task(self, coroutine):
        task = self.hass.async_create_task(coroutine)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    async def async_start(self) -> None:
        stored = await self._store.async_load()
        if stored is None:
            old = await Store(self.hass, c.STORAGE_VERSION, c.STORAGE_KEY).async_load()
            self._legacy_notified = list((old or {}).get("last_notified", []))
        else:
            self._sent = dict(stored.get("sent", {}))
            self._done = dict(stored.get("done", {}))
            self._snoozed = dict(stored.get("snoozed", {}))
            self._legacy_notified = list(stored.get("legacy_notified", []))
            self.telegram.restore(stored.get("telegram", {}))
            if self.telegram.overrides:
                await self.async_request_refresh()
        entities = self._calendar_entities()

        @callback
        def state_changed(event):
            self._create_task(self.async_request_refresh())

        if entities:
            self._unsubs.append(async_track_state_change_event(self.hass, entities, state_changed))
        self._unsubs.append(self.async_add_listener(self._schedule_from_current_data))
        self._unsubs.append(
            self.hass.bus.async_listen("mobile_app_notification_action", self._mobile_action)
        )
        self._unsubs.extend(self.telegram.start())
        self._started = True
        self._publish()

    async def async_shutdown(self) -> None:
        self._stopped = True
        self._started = False
        for unsub in [*self._unsubs, *self._timer_unsubs.values()]:
            unsub()
        self._unsubs.clear()
        self._timer_unsubs.clear()
        tasks = list(self._tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    def _calendar_entities(self):
        entities = split_entities(
            self.config.get(c.CONF_CALENDAR_ENTITIES, c.DEFAULT_CALENDAR_ENTITIES)
        )
        for profile in self.config.get(c.CONF_FAMILY_PROFILES, {}).values():
            entities.extend(split_entities(profile.get(c.CONF_CALENDAR_ENTITIES, [])))
        return list(dict.fromkeys(entities))

    async def async_manual_recalculate(self) -> None:
        self._route_cache.clear()
        self._destination_cache.clear()
        self._retry_counts.clear()
        self._retry_at.clear()
        self._mark_action("일정 다시 계산 시작")
        await self.async_request_refresh()
        self._mark_action("일정 다시 계산 완료" if self.last_update_success else "일정 계산 실패")

    async def _async_update_data(self):
        try:
            return await self._async_calculate()
        except HomeAssistantError as err:
            raise UpdateFailed(str(err)) from err
        except Exception as err:
            _LOGGER.exception("Schedule calculation failed")
            raise UpdateFailed(type(err).__name__) from err

    async def _async_calculate(self):
        cfg = self.config
        failures = []
        entities = self._calendar_entities()
        candidates = await async_get_event_candidates(
            self.hass,
            entities,
            int(cfg.get(c.CONF_LOOKAHEAD_HOURS, c.DEFAULT_LOOKAHEAD_HOURS)),
            int(cfg.get(c.CONF_MIN_EVENT_HOUR, c.DEFAULT_MIN_EVENT_HOUR)),
            int(cfg.get(c.CONF_MAX_EVENT_HOUR, c.DEFAULT_MAX_EVENT_HOUR)),
            errors=failures,
        )
        self.telegram.snapshot()
        accepted = [
            self.telegram.apply_override(item.event) for item in candidates if item.accepted
        ]
        self._base_debug = {
            "checked_entities": ", ".join(entities),
            "calendar_errors": failures,
            "candidate_count": len(candidates),
            "accepted_candidate_count": len(accepted),
            "lookahead_hours": int(cfg.get(c.CONF_LOOKAHEAD_HOURS, c.DEFAULT_LOOKAHEAD_HOURS)),
            "min_event_hour": int(cfg.get(c.CONF_MIN_EVENT_HOUR, c.DEFAULT_MIN_EVENT_HOUR)),
            "max_event_hour": int(cfg.get(c.CONF_MAX_EVENT_HOUR, c.DEFAULT_MAX_EVENT_HOUR)),
            "candidate_reject_reason": candidates[0].reject_reason
            if candidates and not accepted
            else "",
        }
        await self._async_read_forecasts()
        work = []
        profiles = cfg.get(c.CONF_FAMILY_PROFILES, {})
        for event in accepted:
            matching = [
                {**cfg, **profile, "id": key}
                for key, profile in profiles.items()
                if set(event.sources or (event.source,))
                & set(split_entities(profile.get(c.CONF_CALENDAR_ENTITIES, [])))
            ]
            for profile in matching or [{**cfg, "id": "default", "name": "가족"}]:
                work.append((event, profile))
        self._base_debug["plan_limit_exceeded"] = max(0, len(work) - c.MAX_PLANS)
        plans = await asyncio.gather(
            *(self._async_build_plan(event, profile) for event, profile in work[: c.MAX_PLANS])
        )
        new_plans = {plan.key: plan for plan in plans}
        # A partial calendar outage must not cancel known upcoming reminders.
        now = dt_util.now()
        for key, old in self._plans.items():
            if (
                key not in new_plans
                and old.event.start > now
                and set(old.event.sources) & set(failures)
            ):
                new_plans[key] = old
        self._plans = new_plans
        active_event_keys = {plan.event.key for plan in new_plans.values()}
        self._destination_cache = {
            key: value
            for key, value in self._destination_cache.items()
            if key[0] in active_event_keys
        }
        self._route_cache = {
            key: value
            for key, value in self._route_cache.items()
            if value[0] > now - timedelta(minutes=c.ROUTE_CACHE_MINUTES)
        }
        self._messages = {key: value for key, value in self._messages.items() if key in new_plans}
        if not plans and not new_plans and candidates:
            event = candidates[0].event
            self._base_debug.update(
                {
                    "status": "필터됨",
                    "event_title": event.title,
                    "recognized_event_text": format_compact_event(event),
                    "event_time": event.start.isoformat(),
                    "event_source": event.source,
                    "raw_event_state": event.raw_text,
                    "message": candidates[0].reject_reason,
                }
            )
        return self._view()

    async def _async_build_plan(self, event, profile, destination_override=None):
        async with self._calculation_sem:
            destination, source = (
                (destination_override, "telegram_reply")
                if destination_override is not None
                else await self._async_resolve_destination(event)
            )
            plan = Plan(event, profile, destination, source)
            if destination and not is_virtual(event):
                target = event.start - timedelta(
                    minutes=int(
                        profile.get(
                            c.CONF_ARRIVAL_MARGIN_MINUTES,
                            c.DEFAULT_ARRIVAL_MARGIN_MINUTES,
                        )
                    )
                )
                mode = profile.get(c.CONF_TRAVEL_MODE, c.DEFAULT_TRAVEL_MODE)
                cache_key = (
                    profile.get(c.CONF_ROUTE_PROVIDER, c.DEFAULT_ROUTE_PROVIDER),
                    profile.get(c.CONF_ORIGIN_ADDRESS, ""),
                    destination,
                    target.isoformat(),
                    mode,
                )
                cached = self._route_cache.get(cache_key)
                if cached and cached[0] > dt_util.now() - timedelta(minutes=c.ROUTE_CACHE_MINUTES):
                    plan.route = cached[1]
                else:
                    route_fn = (
                        async_get_route if cache_key[0] == "routes" else async_get_transit_duration
                    )
                    plan.route = await route_fn(
                        self.session,
                        str(profile.get(c.CONF_GOOGLE_API_KEY, "")),
                        str(profile.get(c.CONF_ORIGIN_ADDRESS, "")),
                        destination,
                        target,
                        mode=mode,
                    )
                    if plan.route is not None:
                        self._route_cache[cache_key] = (dt_util.now(), plan.route)
            plan.calculate_times(
                int(profile.get(c.CONF_PREPARE_MINUTES, c.DEFAULT_PREPARE_MINUTES)),
                int(profile.get(c.CONF_ARRIVAL_MARGIN_MINUTES, c.DEFAULT_ARRIVAL_MARGIN_MINUTES)),
                int(profile.get(c.CONF_FALLBACK_TRAVEL_MINUTES, 0)),
            )
            if plan.departure_time is not None:
                plan.departure_time = dt_util.as_local(plan.departure_time)
            if plan.notify_time is not None:
                plan.notify_time = dt_util.as_local(plan.notify_time)
            plan.weather = self._weather_for_plan(plan)
            return plan

    async def _async_resolve_destination(self, event):
        cfg = self.config
        if is_virtual(event):
            return "", "virtual"
        override = self.telegram.overrides.get(event.key)
        if override:
            return override["location"], "telegram_reply"
        alias = resolve_alias(event, cfg.get(c.CONF_PLACE_ALIASES, {}))
        if alias:
            return alias, "saved_alias"
        if event.location.strip():
            return event.location.strip(), "calendar_location"
        cache_key = (event.key, event.description)
        cached = self._destination_cache.get(cache_key)
        if cached and cached[0] > dt_util.now() - timedelta(minutes=c.ROUTE_CACHE_MINUTES):
            return cached[1], "ai_extracted" if cached[1] else "none"
        destination = ""
        if cfg.get(c.CONF_ENABLE_AI_DESTINATION, True) and cfg.get(
            c.CONF_OLLAMA_URL, c.DEFAULT_OLLAMA_URL
        ):
            destination = await async_extract_destination(
                self.session,
                str(cfg.get(c.CONF_OLLAMA_URL, c.DEFAULT_OLLAMA_URL)),
                str(cfg.get(c.CONF_OLLAMA_MODEL, c.DEFAULT_OLLAMA_MODEL)),
                event.title,
                event.description,
            )
        self._destination_cache[cache_key] = (dt_util.now(), destination)
        return destination, "ai_extracted" if destination else "none"

    def _channels(self, plan):
        channels = {}
        script = str(plan.profile.get(c.CONF_NOTIFY_SCRIPT, c.DEFAULT_NOTIFY_SCRIPT)).strip()
        mobile = str(plan.profile.get(c.CONF_MOBILE_NOTIFY_SERVICE, "")).strip()
        if script:
            channels["script"] = script
        if mobile:
            channels["mobile"] = mobile
        return channels

    def _stage_done(self, plan, stage):
        if f"{plan.key}:all" in self._done or f"{plan.key}:{stage}" in self._done:
            return True
        if stage == "prepare" and any(
            key in self._legacy_notified for key in plan.event.legacy_keys
        ):
            return True
        channels = self._channels(plan)
        return bool(channels) and all(
            f"{plan.key}:{stage}:{channel}" in self._sent for channel in channels
        )

    def _stages(self, plan):
        stages = [("prepare", plan.notify_time)]
        if (
            plan.profile.get(c.CONF_DEPARTURE_REMINDER, False)
            and plan.departure_time is not None
            and plan.departure_time != plan.notify_time
        ):
            stages.append(("departure", plan.departure_time))
        now = dt_util.now()
        # If both reminders were missed during downtime, send the departure
        # reminder once instead of two back-to-back notifications.
        if len(stages) == 2 and plan.departure_time <= now:
            stages = stages[1:]
        result = []
        for stage, when in stages:
            if when is None or self._stage_done(plan, stage):
                continue
            token = f"{plan.key}:{stage}"
            snoozed = dt_util.parse_datetime(self._snoozed.get(token, ""))
            if snoozed is not None:
                when = dt_util.as_local(snoozed)
            retry = self._retry_at.get(token)
            if retry is not None:
                when = max(when, retry)
            result.append((stage, when))
        return result

    @callback
    def _schedule_from_current_data(self):
        if not self._started or self._stopped:
            return
        if self.telegram._bot_id is not None and not self.telegram._lock.locked():
            self._create_task(self.telegram.async_prompt_missing())
        for unsub in self._timer_unsubs.values():
            unsub()
        self._timer_unsubs.clear()
        now = dt_util.now()
        for plan in self._plans.values():
            if plan.event.start <= now:
                continue
            for stage, when in self._stages(plan):
                token = f"{plan.key}:{stage}"
                if token in self._inflight or self._retry_counts.get(token, 0) >= 3:
                    continue
                fire_at = max(when, now + timedelta(seconds=1))
                if fire_at >= plan.event.start:
                    continue

                @callback
                def fire(_now, key=plan.key, kind=stage, timer_key=token):
                    self._timer_unsubs.pop(timer_key, None)
                    self._create_task(self._async_notify(key, kind))

                self._timer_unsubs[token] = async_track_point_in_time(self.hass, fire, fire_at)
                if stage == "prepare" and when > now and when - now <= timedelta(minutes=15):
                    self._prewarm(plan)

    def _signature(self, plan):
        data = plan.as_data()
        return hashlib.sha256(
            json.dumps(data, sort_keys=True, ensure_ascii=False).encode()
        ).hexdigest()

    def _prewarm(self, plan):
        if not self.config.get(c.CONF_ENABLE_OUTFIT_AI, True) or not self.config.get(
            c.CONF_OLLAMA_URL, c.DEFAULT_OLLAMA_URL
        ):
            return
        signature = self._signature(plan)
        if self._messages.get(plan.key, (None,))[0] == signature or plan.key in self._message_jobs:
            return
        self._message_jobs.add(plan.key)
        self._create_task(self._async_prewarm(plan, signature))

    async def _async_prewarm(self, plan, signature):
        try:
            async with self._message_sem:
                if (
                    self._stopped
                    or plan.key not in self._plans
                    or self._stage_done(plan, "prepare")
                ):
                    return
                text = await async_generate_text(
                    self.session,
                    str(self.config.get(c.CONF_OLLAMA_URL, c.DEFAULT_OLLAMA_URL)),
                    str(self.config.get(c.CONF_OLLAMA_MODEL, c.DEFAULT_OLLAMA_MODEL)),
                    build_outfit_prompt(plan.as_data(), plan.weather),
                    timeout=20,
                )
                text = sanitize_tts(text)
                current = self._plans.get(plan.key)
                if current is not None and self._signature(current) == signature and len(text) > 10:
                    self._messages[plan.key] = (signature, text[:800])
        except Exception:
            _LOGGER.exception("Outfit pre-generation failed; using the deterministic message")
        finally:
            self._message_jobs.discard(plan.key)

    async def async_generate_and_notify(self, *, test=False):
        if not self._plans:
            await self.async_request_refresh()
        key = (self.data or {}).get("event_key")
        if key not in self._plans:
            self._mark_action("테스트 알림 실패", "알림 가능한 예정 일정이 없습니다")
            return
        await self._async_notify(key, "prepare", test=test)

    async def _async_notify(self, key, stage, *, test=False):
        token = f"{key}:{stage}"
        plan = self._plans.get(key)
        if self._stopped or plan is None or token in self._inflight:
            return
        if not test and (plan.event.start <= dt_util.now() or self._stage_done(plan, stage)):
            return
        self._inflight.add(token)
        try:
            self._mark_action("테스트 알림 실행 중" if test else "자동 알림 실행 중")
            # Automatic notifications never wait for an LLM at their due time.
            plan.weather = self._weather_for_plan(plan)
            message = fallback_message(plan, stage)
            if (
                not test
                and stage == "prepare"
                and plan.notify_time
                and dt_util.now() - plan.notify_time > timedelta(minutes=1)
            ):
                message = "준비 알림 시각이 지나 지금 안내합니다. " + message
            cached = self._messages.get(key)
            if stage == "prepare" and cached and cached[0] == self._signature(plan):
                message += " " + cached[1]
            channels = self._channels(plan)
            failures = []
            successes = []
            if not channels:
                failures.append("알림 스크립트 또는 휴대폰 알림 서비스를 설정하세요")
            for channel, service in channels.items():
                receipt = f"{token}:{channel}"
                if not test and receipt in self._sent:
                    continue
                # A refresh/action may have removed or completed this event while
                # another channel was running. Never dispatch stale follow-up work.
                if not test and (
                    key not in self._plans or self._stage_done(self._plans[key], stage)
                ):
                    break
                try:
                    if channel == "script":
                        quiet = is_quiet(
                            dt_util.now(),
                            bool(plan.profile.get(c.CONF_QUIET_ENABLED, False)),
                            int(plan.profile.get(c.CONF_QUIET_START, 22)),
                            int(plan.profile.get(c.CONF_QUIET_END, 7)),
                        )
                        person_entity = plan.profile.get("person_entity")
                        person = self.hass.states.get(person_entity) if person_entity else None
                        tts_enabled = not quiet and (
                            not person_entity or (person is not None and person.state == "home")
                        )
                        await asyncio.wait_for(
                            async_send_universal_notify(
                                self.hass,
                                notify_script=service,
                                message=message,
                                tts_target=str(
                                    plan.profile.get(c.CONF_TTS_TARGET, c.DEFAULT_TTS_TARGET)
                                ),
                                tts_service=str(
                                    plan.profile.get(c.CONF_TTS_SERVICE, c.DEFAULT_TTS_SERVICE)
                                ),
                                speed=float(
                                    plan.profile.get(c.CONF_TTS_SPEED, c.DEFAULT_TTS_SPEED)
                                ),
                                pitch=float(
                                    plan.profile.get(c.CONF_TTS_PITCH, c.DEFAULT_TTS_PITCH)
                                ),
                                tts_enabled=tts_enabled,
                            ),
                            timeout=45,
                        )
                    else:
                        await asyncio.wait_for(
                            async_send_mobile_notify(
                                self.hass,
                                service,
                                message,
                                actions=self._notification_actions(plan, stage, test),
                                tag=f"fsa_{self.entry.entry_id}_{hashlib.sha256(key.encode()).hexdigest()[:12]}",
                                route_link=plan.as_data()["route_link"],
                            ),
                            timeout=15,
                        )
                    successes.append(channel)
                    if not test:
                        self._sent[receipt] = dt_util.now().isoformat()
                        await self._save_state()
                except Exception as err:  # noqa: BLE001 - user scripts can raise arbitrary service errors
                    failures.append(f"{channel}: {type(err).__name__}")
                    _LOGGER.warning(
                        "Family reminder channel %s failed (%s)",
                        channel,
                        type(err).__name__,
                    )
            if failures and not test:
                self._retry_counts[token] = self._retry_counts.get(token, 0) + 1
                self._retry_at[token] = dt_util.now() + timedelta(minutes=1)
            else:
                self._retry_counts.pop(token, None)
                self._retry_at.pop(token, None)
                if not test:
                    self._snoozed.pop(token, None)
                    await self._save_state()
            result = (
                "알림 실행 실패: " + ", ".join(failures)
                if failures
                else ("테스트 알림 실행 완료" if test else "알림 서비스 실행 완료")
            )
            if key in self._plans:
                self._messages.setdefault(key, ("", ""))
                self._last_message_event_key = key
                self._last_message = message
            self._last_notify_result = result
            self._last_action = "테스트 알림 완료" if test else "자동 알림 완료"
        finally:
            self._inflight.discard(token)
            self._publish()

    def _notification_actions(self, plan, stage, test=False):
        if test:
            return []
        token = hashlib.sha256(plan.key.encode()).hexdigest()[:16]
        prefix = f"FSA_{self.entry.entry_id}_{token}_{stage}_"
        return [
            {"action": prefix + command, "title": title}
            for command, title in (
                ("prepared", "준비 완료"),
                ("departed", "출발했어요"),
                (
                    "snooze",
                    f"{self.config.get(c.CONF_SNOOZE_MINUTES, c.DEFAULT_SNOOZE_MINUTES)}분 뒤 다시",
                ),
                ("skip", "이 일정 건너뛰기"),
            )
        ]

    @callback
    def _mobile_action(self, event):
        action = str(event.data.get("action", ""))
        prefix = f"FSA_{self.entry.entry_id}_"
        if not action.startswith(prefix):
            return
        parts = action[len(prefix) :].split("_")
        if (
            len(parts) != 3
            or parts[1] not in {"prepare", "departure"}
            or parts[2] not in {"prepared", "departed", "snooze", "skip"}
        ):
            return
        token, stage, command = parts
        for key in self._plans:
            if hashlib.sha256(key.encode()).hexdigest()[:16] == token:
                self._create_task(self._async_mobile_action(command, key, stage))
                return

    async def _async_mobile_action(self, command, key, stage):
        try:
            await self.async_event_action(command, event_key=key, stage=stage)
        except ServiceValidationError as err:
            self._mark_action("알림 버튼 처리 불가", str(err))

    async def async_event_action(
        self, command: str, *, event_key=None, stage="prepare", minutes=None
    ):
        key = event_key or (self.data or {}).get("event_key")
        plan = self._plans.get(key)
        if plan is None or plan.event.start <= dt_util.now():
            raise ServiceValidationError("처리할 예정 일정이 없습니다")
        if command not in {"prepared", "departed", "snooze", "skip"} or stage not in {
            "prepare",
            "departure",
        }:
            raise ServiceValidationError("지원하지 않는 알림 동작입니다")
        now = dt_util.now()
        if command == "snooze":
            delay = int(
                minutes
                if minutes is not None
                else self.config.get(c.CONF_SNOOZE_MINUTES, c.DEFAULT_SNOOZE_MINUTES)
            )
            if delay < 1 or delay > 60 or now + timedelta(minutes=delay) >= plan.event.start:
                raise ServiceValidationError("다시 알림은 1~60분이며 일정 시작 전이어야 합니다")
            if f"{key}:all" in self._done:
                raise ServiceValidationError("이미 완료하거나 건너뛴 일정입니다")
            token = f"{key}:{stage}"
            self._done.pop(token, None)
            for receipt in [receipt for receipt in self._sent if receipt.startswith(token + ":")]:
                self._sent.pop(receipt)
            self._legacy_notified = [
                value for value in self._legacy_notified if value not in plan.event.legacy_keys
            ]
            self._snoozed[token] = (now + timedelta(minutes=delay)).isoformat()
            self._retry_counts.pop(token, None)
            self._retry_at.pop(token, None)
        else:
            self._done[f"{key}:prepare" if command == "prepared" else f"{key}:all"] = (
                now.isoformat()
            )
            for token in [token for token in self._snoozed if token.startswith(key + ":")]:
                if command != "prepared" or token.endswith(":prepare"):
                    self._snoozed.pop(token, None)
        await self._save_state()
        labels = {
            "prepared": "준비 완료",
            "departed": "출발 완료",
            "skip": "일정 알림 건너뜀",
            "snooze": "다시 알림 예약",
        }
        self._mark_action(labels[command], plan.event.title)

    async def _save_state(self):
        async with self._state_save_lock:
            await self._async_save_snapshot()

    async def _async_save_snapshot(self):
        cutoff = dt_util.now() - timedelta(days=14)
        for values in (self._sent, self._done):
            for key, timestamp in list(values.items()):
                parsed = dt_util.parse_datetime(timestamp)
                if parsed is None or dt_util.as_local(parsed) < cutoff:
                    values.pop(key, None)
        for key, timestamp in list(self._snoozed.items()):
            parsed = dt_util.parse_datetime(timestamp)
            if parsed is None or dt_util.as_local(parsed) < cutoff:
                self._snoozed.pop(key, None)
        await self._store.async_save(
            {
                "sent": dict(self._sent),
                "done": dict(self._done),
                "snoozed": dict(self._snoozed),
                "legacy_notified": self._legacy_notified[-50:],
                "telegram": self.telegram.snapshot(),
            }
        )

    def _mark_action(self, action, result=None):
        self._last_action = action
        self._last_action_time = dt_util.now().isoformat()
        if result is not None:
            self._last_notify_result = result
        self._publish()

    def _publish(self):
        if not self._stopped:
            self.async_set_updated_data(self._view())

    def _view(self):
        plans = sorted(self._plans.values(), key=lambda plan: plan.event.start)
        pending = [(when, plan) for plan in plans for _, when in self._stages(plan)]
        selected = (
            min(pending, key=lambda item: item[0])[1] if pending else (plans[0] if plans else None)
        )
        data = (
            selected.as_data()
            if selected
            else {
                "status": "대기 중",
                "event_key": "",
                "event_title": "",
                "recognized_event_text": "인식된 일정 없음",
                "event_time": None,
                "departure_time": None,
                "notify_time": None,
                "message": "예정된 일정이 없습니다.",
            }
        )
        data.update(self._base_debug)
        data.update(
            {
                "last_action": self._last_action,
                "last_action_time": self._last_action_time,
                "last_notify_result": self._last_notify_result,
                "forecast_error": self._forecast_error,
                "telegram_error": self.telegram.error,
                "pending_location_requests": sum(
                    record.get("state") in ("address", "confirm")
                    for record in self.telegram.pending.values()
                ),
                "upcoming_events": [plan.as_data() for plan in plans],
                "today_events": [
                    plan.as_data()
                    for plan in plans
                    if plan.event.start.date() == dt_util.now().date()
                ],
                "conflicts": find_conflicts(plans),
                "plan_count": len(plans),
                "pending_reminders": len(pending),
            }
        )
        data["outfit_message"] = (
            self._last_message if selected and self._last_message_event_key == selected.key else ""
        )
        return data

    async def _async_read_forecasts(self):
        entity_id = self.config.get(c.CONF_WEATHER_ENTITY)
        self._forecasts = []
        self._forecast_error = ""
        if not entity_id:
            return
        try:
            result = await self.hass.services.async_call(
                "weather",
                "get_forecasts",
                {"type": "hourly"},
                target={"entity_id": entity_id},
                blocking=True,
                return_response=True,
            )
            self._forecasts = ((result or {}).get(entity_id) or {}).get("forecast", [])
        except (HomeAssistantError, TypeError, ValueError):
            self._forecast_error = "시간별 예보를 사용할 수 없어 현재 날씨를 사용합니다"

    def _weather_for_plan(self, plan):
        cfg = self.config
        keys = {
            "rain": c.CONF_WEATHER_RAIN,
            "feels_like": c.CONF_WEATHER_FEELS_LIKE,
            "temp": c.CONF_WEATHER_TEMP,
            "humidity": c.CONF_WEATHER_HUMIDITY,
            "wind": c.CONF_WEATHER_WIND,
            "sky": c.CONF_WEATHER_SKY,
            "dust": c.CONF_WEATHER_DUST,
            "uv": c.CONF_WEATHER_UV,
            "apparent": c.CONF_WEATHER_APPARENT,
        }
        weather = {name: self._read_entity(cfg.get(key)) for name, key in keys.items()}
        state = (
            self.hass.states.get(cfg.get(c.CONF_WEATHER_ENTITY))
            if cfg.get(c.CONF_WEATHER_ENTITY)
            else None
        )
        if state is not None:
            if weather["temp"] == "정보 없음" and state.attributes.get("temperature") is not None:
                weather["temp"] = self._temperature(
                    state.attributes["temperature"],
                    state.attributes.get("temperature_unit", "°C"),
                )
            if weather["sky"] == "정보 없음":
                weather["sky"] = state.state
        start = plan.departure_time or plan.event.start
        end = plan.event.end or plan.event.start + timedelta(hours=1)
        window = forecast_window(self._forecasts, start, end)
        if window:
            temperatures = [
                float(item["temperature"])
                for item in window
                if isinstance(item.get("temperature"), (int, float))
            ]
            rain = [
                float(item["precipitation_probability"])
                for item in window
                if isinstance(item.get("precipitation_probability"), (int, float))
            ]
            if temperatures:
                weather["forecast_temp"] = self._temperature(
                    min(temperatures),
                    state.attributes.get("temperature_unit", "°C") if state else "°C",
                )
            if rain:
                weather["forecast_rain"] = f"{max(rain):g}%"
            conditions = [item.get("condition", "") for item in window]
            rainy = {"rainy", "pouring", "lightning-rainy", "snowy-rainy"}
            weather["forecast_condition"] = next(
                (value for value in conditions if value in rainy), conditions[0]
            )
        return weather

    @staticmethod
    def _temperature(value, unit):
        try:
            temperature = float(value)
            if unit == "°F":
                temperature = (temperature - 32) * 5 / 9
            return f"{temperature:g}도"
        except (ValueError, TypeError):
            return "정보 없음"

    def _read_entity(self, entity_id):
        state = self.hass.states.get(str(entity_id)) if entity_id else None
        if state is None or state.state in {"unknown", "unavailable", "none", ""}:
            return "정보 없음"
        unit = state.attributes.get("unit_of_measurement", "")
        if unit in {"°C", "°F"}:
            return self._temperature(state.state, unit)
        return str(state.state) + str(unit)
