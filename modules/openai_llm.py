"""OpenAI client – card planner / structured JSON tasks."""

from __future__ import annotations

import asyncio
import json
import re
from typing import Any

from modules.utils import get_logger, get_settings

logger = get_logger(__name__)

DEFAULT_MODEL = "gpt-4o-mini"


class OpenAINotConfiguredError(RuntimeError):
    """Raised when OPENAI_API_KEY is missing."""


class OpenAIRateLimitError(RuntimeError):
    """Raised when OpenAI rate limits persist after retries."""


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


def _client():
    settings = get_settings()
    key = (settings.openai_api_key or "").strip()
    if not key or key.startswith("your_"):
        raise OpenAINotConfiguredError("OPENAI_API_KEY not configured")
    from openai import AsyncOpenAI

    return AsyncOpenAI(api_key=key), (settings.openai_planner_model or DEFAULT_MODEL).strip()


def _is_rate_limit(exc: Exception) -> bool:
    name = type(exc).__name__.lower()
    if "ratelimit" in name or "rate_limit" in name:
        return True
    status = getattr(exc, "status_code", None)
    if status == 429:
        return True
    msg = str(exc).lower()
    return "rate limit" in msg or "429" in msg or "insufficient_quota" in msg


async def complete_json(
    *,
    system: str,
    user: str,
    temperature: float = 0.35,
    max_tokens: int = 4096,
    max_retries: int = 4,
) -> dict[str, Any]:
    """Call OpenAI and parse a JSON object from the response."""
    client, model = _client()
    delay = 2.0
    last_exc: Exception | None = None

    for attempt in range(max_retries):
        try:
            response = await client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                response_format={"type": "json_object"},
                temperature=temperature,
                max_tokens=max_tokens,
            )
            text = (response.choices[0].message.content or "").strip()
            if not text:
                raise ValueError("empty OpenAI response")
            return _extract_json(text)
        except OpenAINotConfiguredError:
            raise
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            if not _is_rate_limit(exc) or attempt >= max_retries - 1:
                if _is_rate_limit(exc):
                    raise OpenAIRateLimitError(str(exc)) from exc
                raise
            logger.warning(
                "OpenAI rate limit (attempt %d/%d) – retry in %.1fs",
                attempt + 1,
                max_retries,
                delay,
            )
            await asyncio.sleep(delay)
            delay = min(delay * 2, 60.0)

    raise OpenAIRateLimitError(str(last_exc or "unknown rate limit"))
