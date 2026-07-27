"""Google Gemini client – hooks / lightweight JSON tasks."""

from __future__ import annotations

import asyncio
import json
import re
from typing import Any

import httpx

from modules.utils import get_logger, get_settings

logger = get_logger(__name__)

# 2.0-flash free tier often returns 429 on new AQ keys; lite models work.
DEFAULT_MODEL = "gemini-3.1-flash-lite"
GEMINI_API = "https://generativelanguage.googleapis.com/v1beta/models"


class GeminiNotConfiguredError(RuntimeError):
    """Raised when GEMINI_API_KEY is missing."""


class GeminiRateLimitError(RuntimeError):
    """Raised when Gemini rate limits persist after retries."""


def _extract_json(text: str) -> dict[str, Any]:
    text = (text or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{[\s\S]*\}", text)
        if not match:
            raise
        return json.loads(match.group(0))


def _api_key() -> str:
    key = (get_settings().gemini_api_key or "").strip()
    if not key or key.startswith("your_"):
        raise GeminiNotConfiguredError("GEMINI_API_KEY not configured")
    return key


def _is_rate_limit(exc: Exception) -> bool:
    status = getattr(exc, "status_code", None)
    if status in {429, 503}:
        return True
    msg = str(exc).lower()
    return "rate limit" in msg or "429" in msg or "resource exhausted" in msg


async def complete_json(
    *,
    system: str,
    user: str,
    temperature: float = 0.8,
    max_tokens: int = 1024,
    max_retries: int = 3,
) -> dict[str, Any]:
    """Call Gemini Flash and parse a JSON object from the response."""
    settings = get_settings()
    key = _api_key()
    model = (settings.gemini_model or DEFAULT_MODEL).strip()
    url = f"{GEMINI_API}/{model}:generateContent"
    payload = {
        "systemInstruction": {"parts": [{"text": system}]},
        "contents": [{"role": "user", "parts": [{"text": user}]}],
        "generationConfig": {
            "temperature": temperature,
            "maxOutputTokens": max_tokens,
            "responseMimeType": "application/json",
        },
    }
    delay = 2.0
    last_exc: Exception | None = None

    for attempt in range(max_retries):
        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                # Prefer header auth so keys are not logged in request URLs
                resp = await client.post(
                    url,
                    headers={"x-goog-api-key": key},
                    json=payload,
                )
                if resp.status_code == 429:
                    raise GeminiRateLimitError(resp.text)
                resp.raise_for_status()
                data = resp.json()

            candidates = data.get("candidates") or []
            parts = (candidates[0].get("content") or {}).get("parts") or [] if candidates else []
            text = "\n".join(str(p.get("text") or "") for p in parts).strip()
            if not text:
                raise ValueError("empty Gemini response")
            return _extract_json(text)
        except GeminiNotConfiguredError:
            raise
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            if not _is_rate_limit(exc) or attempt >= max_retries - 1:
                if _is_rate_limit(exc):
                    raise GeminiRateLimitError(str(exc)) from exc
                raise
            logger.warning(
                "Gemini rate limit (attempt %d/%d) – retry in %.1fs",
                attempt + 1,
                max_retries,
                delay,
            )
            await asyncio.sleep(delay)
            delay = min(delay * 2, 60.0)

    raise GeminiRateLimitError(str(last_exc or "unknown rate limit"))
