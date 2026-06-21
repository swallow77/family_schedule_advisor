"""Ollama API helper."""
from __future__ import annotations

import logging
import re

import aiohttp

_LOGGER = logging.getLogger(__name__)


def _base_url(url: str) -> str:
    return url.rstrip("/")


async def async_generate_text(
    session: aiohttp.ClientSession,
    ollama_url: str,
    model: str,
    prompt: str,
    *,
    timeout: int = 120,
) -> str:
    """Generate text from Ollama."""
    if not ollama_url or not model:
        return ""

    url = f"{_base_url(ollama_url)}/api/generate"
    payload = {
        "model": model,
        "prompt": prompt,
        "stream": False,
        "options": {
            "temperature": 0.4,
            "top_p": 0.9,
        },
    }
    try:
        async with session.post(url, json=payload, timeout=aiohttp.ClientTimeout(total=timeout)) as resp:
            data = await resp.json(content_type=None)
            if resp.status >= 400:
                _LOGGER.warning("Ollama returned HTTP %s: %s", resp.status, data)
                return ""
            return str(data.get("response") or "").strip()
    except (aiohttp.ClientError, TimeoutError, ValueError) as err:
        _LOGGER.warning("Ollama generation failed: %s", err)
        return ""


def sanitize_tts(text: str) -> str:
    """Sanitize generated text for TTS."""
    text = text.replace("*", "")
    text = text.replace("#", "")
    text = text.replace("`", "")
    text = text.replace("\n", " ")
    text = re.sub(r"\s+", " ", text)
    return text.strip()


async def async_extract_destination(
    session: aiohttp.ClientSession,
    ollama_url: str,
    model: str,
    title: str,
    description: str,
) -> str:
    """Extract destination text from event title/description."""
    prompt = f"""
너는 일정 제목에서 실제 목적지만 추출하는 도우미다.
아래 일정에서 Google 지도 검색에 넣을 만한 목적지 한 개만 한국어로 출력해라.
목적지가 없으면 NONE 만 출력해라.
설명하지 마라.

제목: {title}
설명: {description}
""".strip()
    response = await async_generate_text(session, ollama_url, model, prompt, timeout=60)
    response = sanitize_tts(response)
    first_line = response.split(".")[0].strip()
    if not first_line or first_line.upper() == "NONE":
        return ""
    if len(first_line) > 80:
        return ""
    return first_line


def build_outfit_prompt(data: dict, weather: dict[str, str]) -> str:
    """Build final TTS prompt."""
    return f"""
너는 가족 외출 준비를 도와주는 한국어 음성 안내 도우미다.
아래 정보를 바탕으로 외출 옷차림과 준비물을 자연스럽게 안내해라.

일정 정보
제목: {data.get('event_title') or '일정'}
일정 시간: {data.get('event_time_text') or '정보 없음'}
목적지: {data.get('destination') or '정보 없음'}
대중교통 예상 소요시간: {data.get('transit_duration_text') or '정보 없음'}
추천 출발 시간: {data.get('departure_time_text') or '정보 없음'}
추천 준비 시작 시간: {data.get('notify_time_text') or '정보 없음'}

날씨 정보
강수 확률: {weather.get('rain')}
체감 온도: {weather.get('feels_like')}
현재 온도: {weather.get('temp')}
현재 습도: {weather.get('humidity')}
현재 풍속: {weather.get('wind')}
하늘 상태: {weather.get('sky')}
초미세먼지 등급: {weather.get('dust')}
자외선 등급: {weather.get('uv')}
실시간 체감온도: {weather.get('apparent')}

응답 규칙
처음에 약속 시간과 내용을 말해라.
대중교통 소요시간과 추천 출발 시간을 자연스럽게 말해라.
옷차림을 장소와 날씨에 맞게 구체적으로 추천해라.
우산, 선크림, 마스크, 보조배터리 같은 준비물은 필요한 경우에만 말해라.
별표 문자는 절대 쓰지 마라.
마크다운을 쓰지 마라.
영문 단위를 쓰지 마라.
온도는 도로 말해라.
TTS용이므로 너무 길지 않게 5문장 안팎으로 말해라.
""".strip()
