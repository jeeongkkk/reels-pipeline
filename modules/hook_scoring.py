"""Module 1.5: Hook rewrite (Gemini Flash) + scoring."""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import asdict, dataclass

from modules.reference import ReferenceInput
from modules.text_quality import KOREAN_ONLY_RULE, sanitize_hook
from modules.utils import get_logger, get_settings, load_brand_config

logger = get_logger(__name__)


def _active_weights() -> dict[str, float]:
    try:
        from modules.analytics import load_learned_weights

        return load_learned_weights()
    except Exception:  # noqa: BLE001
        brand = load_brand_config()
        return dict(brand.get("hook_scoring", {}).get("weights", {}))


@dataclass
class HookCandidate:
    angle: str
    hook: str
    score: float
    rationale: str
    source: str = "seed"  # seed | llm

    def to_dict(self) -> dict:
        return asdict(self)


def _clamp(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return max(low, min(high, value))


def _heuristic_score(
    topic: str,
    hook: str,
    reference: ReferenceInput | None,
    weights: dict[str, float],
) -> tuple[float, str]:
    reasons: list[str] = []
    hook_l = hook.lower()
    topic_tokens = [t for t in re.split(r"[\s,/|·]+", topic.lower()) if len(t) >= 2]

    if topic_tokens:
        hits = sum(1 for t in topic_tokens if t in hook_l)
        relevance = hits / len(topic_tokens)
    else:
        relevance = 0.5
    reasons.append(f"관련성 {relevance:.0%}")

    emotion_cues = ["왜", "지금", "필수", "충격", "주의", "실패", "성공", "배", "%", "?", "진짜", "바꾸", "늦"]
    emotion_hits = sum(1 for c in emotion_cues if c in hook)
    emotional = min(1.0, emotion_hits / 3)
    reasons.append(f"감정 {emotional:.0%}")

    brand = load_brand_config()
    max_chars = brand.get("script", {}).get("hook_max_chars", 28)
    length = len(hook)
    if 10 <= length <= max_chars:
        clarity = 1.0
    elif length < 10:
        clarity = 0.4
    elif length <= max_chars + 12:
        clarity = 0.7
    else:
        clarity = 0.35
    reasons.append(f"명확성(len={length})")

    generic = ["알아보자", "소개합니다", "안녕하세요", "오늘", "정리", "총정리", "전략.zip"]
    novelty = 0.3 if any(g in hook for g in generic) else 0.85
    if any(ch.isdigit() for ch in hook):
        novelty = min(1.0, novelty + 0.1)
    reasons.append(f"신선함 {novelty:.0%}")

    risk_cues = ["무조건", "100%", "대박", "보장", "클릭", "무료로 돈"]
    risk = 1.0 if any(r in hook for r in risk_cues) else 0.0
    if risk:
        reasons.append("리스크 키워드")

    ref_bonus = 0.0
    if reference and reference.style_notes:
        note_tokens = [t for t in re.split(r"\s+", reference.style_notes.lower()) if len(t) >= 2]
        if note_tokens:
            overlap = sum(1 for t in note_tokens if t in hook_l) / len(note_tokens)
            ref_bonus = overlap * 5
            if overlap > 0:
                reasons.append(f"레퍼런스 정렬 +{ref_bonus:.1f}")

    score = (
        weights.get("audience_relevance", 0.3) * relevance * 100
        + weights.get("emotional_trigger", 0.25) * emotional * 100
        + weights.get("clarity", 0.2) * clarity * 100
        + weights.get("novelty", 0.15) * novelty * 100
        + weights.get("risk", -0.1) * risk * 100
        + ref_bonus
    )
    return _clamp(score), "; ".join(reasons)


def _extract_json(text: str):
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{[\s\S]*\}|\[[\s\S]*\]", text)
        if not match:
            raise
        return json.loads(match.group(0))


async def rewrite_hooks_with_llm(
    topic: str,
    seed_hooks: list[str],
    reference: ReferenceInput | None = None,
    count: int = 5,
) -> list[str]:
    """Rewrite headline seeds into short-form Instagram hooks via Gemini Flash."""
    settings = get_settings()
    brand = load_brand_config()
    max_chars = brand.get("script", {}).get("hook_max_chars", 28)

    if not settings.gemini_api_key or settings.gemini_api_key.startswith("your_"):
        logger.warning("GEMINI_API_KEY missing – skipping hook rewrite")
        return [sanitize_hook(h, max_chars) for h in seed_hooks[:count] if h.strip()]

    seeds = "\n".join(f"- {h}" for h in seed_hooks[:8] if h.strip())
    ref = reference.summary_for_prompt() if reference else "없음"

    prompt = f"""당신은 인스타 릴스 훅 카피라이터입니다.
뉴스 제목을 숏폼 훅으로 다시 쓰세요. JSON만 출력.

주제: {topic}
시드 헤드라인:
{seeds}
레퍼런스:
{ref}

규칙:
- {count}개 훅 생성
- 각 훅 {max_chars}자 이내
- 구어체, 첫 1초에 멈추게 하는 문장
- 기계적 인사/오늘 알아볼 금지
- {KOREAN_ONLY_RULE}

JSON:
{{"hooks": ["훅1", "훅2", "..."]}}
"""

    try:
        from modules.gemini_llm import complete_json

        data = await complete_json(
            system="Return valid JSON only. Korean hooks only.",
            user=prompt,
            temperature=0.8,
            max_tokens=600,
        )
        hooks = data.get("hooks", data if isinstance(data, list) else [])
        cleaned = []
        for h in hooks:
            h = sanitize_hook(str(h), max_chars)
            if h and h not in cleaned:
                cleaned.append(h)
        logger.info("Gemini rewrote %d hooks", len(cleaned))
        return cleaned[:count] or [sanitize_hook(h, max_chars) for h in seed_hooks[:count]]
    except Exception as exc:  # noqa: BLE001
        logger.warning("Hook LLM rewrite failed (%s) – using seeds", exc)
        return [sanitize_hook(h, max_chars) for h in seed_hooks[:count] if h.strip()]


async def score_hooks(
    topic: str,
    candidates: list[str],
    reference: ReferenceInput | None = None,
    source: str = "seed",
) -> list[HookCandidate]:
    brand = load_brand_config()
    weights = _active_weights()
    min_score = brand.get("hook_scoring", {}).get("min_score_to_proceed", 70)
    max_chars = brand.get("script", {}).get("hook_max_chars", 28)

    results: list[HookCandidate] = []
    for i, hook in enumerate(candidates):
        hook = sanitize_hook(hook, max_chars)
        if not hook:
            continue
        score, rationale = _heuristic_score(topic, hook, reference, weights)
        results.append(
            HookCandidate(
                angle=f"Angle {i + 1}",
                hook=hook,
                score=round(score, 1),
                rationale=rationale,
                source=source,
            )
        )

    results.sort(key=lambda h: h.score, reverse=True)
    top = results[0].score if results else 0
    logger.info("Top hook score: %.1f (min: %d, n=%d)", top, min_score, len(results))
    await asyncio.sleep(0)
    return results


async def score_research_hooks(
    topic: str,
    research_hooks: list[str],
    reference: ReferenceInput | None = None,
    rewrite: bool = True,
) -> list[HookCandidate]:
    """Rewrite seeds with LLM (optional) then score."""
    if rewrite:
        rewritten = await rewrite_hooks_with_llm(topic, research_hooks, reference=reference)
        scored = await score_hooks(topic, rewritten, reference=reference, source="llm")
        if scored:
            return scored
    return await score_hooks(topic, research_hooks, reference=reference, source="seed")
