"""Anthropic Claude client – shared JSON/text completion with rate-limit retries."""

from __future__ import annotations

import asyncio
import json
import re
from typing import Any

from modules.utils import get_logger, get_settings

logger = get_logger(__name__)

DEFAULT_MODEL = "claude-3-5-sonnet-20241022"


class AnthropicRateLimitError(RuntimeError):
    """Raised when Anthropic rate limits persist after retries."""


class AnthropicNotConfiguredError(RuntimeError):
    """Raised when ANTHROPIC_API_KEY is missing."""


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
    key = (settings.anthropic_api_key or "").strip()
    if not key or key.startswith("your_"):
        raise AnthropicNotConfiguredError("ANTHROPIC_API_KEY not configured")
    from anthropic import AsyncAnthropic

    return AsyncAnthropic(api_key=key), (settings.anthropic_model or DEFAULT_MODEL).strip()


async def complete_json(
    *,
    system: str,
    user: str,
    temperature: float = 0.35,
    max_tokens: int = 4096,
    max_retries: int = 4,
) -> dict[str, Any]:
    """Call Claude and parse a JSON object from the response."""
    client, model = _client()
    delay = 2.0
    last_exc: Exception | None = None

    for attempt in range(max_retries):
        try:
            response = await client.messages.create(
                model=model,
                max_tokens=max_tokens,
                temperature=temperature,
                system=system,
                messages=[{"role": "user", "content": user}],
            )
            parts = []
            for block in response.content:
                if getattr(block, "type", "") == "text":
                    parts.append(getattr(block, "text", "") or "")
            text = "\n".join(parts).strip()
            if not text:
                raise ValueError("empty Claude response")
            return _extract_json(text)
        except AnthropicNotConfiguredError:
            raise
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            retryable = _is_rate_limit(exc)
            if not retryable or attempt >= max_retries - 1:
                if retryable:
                    raise AnthropicRateLimitError(str(exc)) from exc
                raise
            logger.warning(
                "Anthropic rate limit (attempt %d/%d) – retry in %.1fs",
                attempt + 1,
                max_retries,
                delay,
            )
            await asyncio.sleep(delay)
            delay = min(delay * 2, 60.0)

    raise AnthropicRateLimitError(str(last_exc or "unknown rate limit"))


async def complete_text(
    *,
    system: str,
    user: str,
    temperature: float = 0.7,
    max_tokens: int = 1200,
    max_retries: int = 3,
) -> str:
    """Call Claude and return plain text."""
    client, model = _client()
    delay = 2.0

    for attempt in range(max_retries):
        try:
            response = await client.messages.create(
                model=model,
                max_tokens=max_tokens,
                temperature=temperature,
                system=system,
                messages=[{"role": "user", "content": user}],
            )
            parts = []
            for block in response.content:
                if getattr(block, "type", "") == "text":
                    parts.append(getattr(block, "text", "") or "")
            text = "\n".join(parts).strip()
            if text:
                return text
            raise ValueError("empty Claude response")
        except AnthropicNotConfiguredError:
            raise
        except Exception as exc:  # noqa: BLE001
            if not _is_rate_limit(exc) or attempt >= max_retries - 1:
                if _is_rate_limit(exc):
                    raise AnthropicRateLimitError(str(exc)) from exc
                raise
            await asyncio.sleep(delay)
            delay = min(delay * 2, 60.0)

    raise AnthropicRateLimitError("Anthropic rate limit exceeded")


def _is_rate_limit(exc: Exception) -> bool:
    name = type(exc).__name__.lower()
    if "ratelimit" in name or "rate_limit" in name:
        return True
    status = getattr(exc, "status_code", None)
    if status == 429:
        return True
    msg = str(exc).lower()
    return "rate limit" in msg or "429" in msg or "overloaded" in msg
