"""Module 2: High-End Script / Caption Generation via Groq API.

Supports two production modes:
  - voice_tts: spoken narration script + visual markers
  - music_caption: on-screen caption cards only (no narration)
"""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import asdict, dataclass, field
from typing import Any

from modules.modes import ProductionMode, parse_mode
from modules.reference import ReferenceInput
from modules.text_quality import (
    KOREAN_ONLY_RULE,
    SPOKEN_STYLE_RULE,
    enforce_korean_copy,
    sanitize_hook,
    sanitize_script_fields,
)
from modules.utils import get_logger, get_settings, load_brand_config

logger = get_logger(__name__)


@dataclass
class ScriptResult:
    mode: str
    hook: str
    body: str
    cta: str
    visual_markers: list[str] = field(default_factory=list)
    full_text: str = ""  # TTS-ready text (voice mode) or empty
    caption_lines: list[str] = field(default_factory=list)  # music_caption cards
    caption_durations: list[float] = field(default_factory=list)  # seconds per line
    source: str = "stub"  # groq | fallback

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _brand_tone_block(tone_override: str) -> str:
    brand = load_brand_config()
    tone = brand.get("tone", {})
    formality = tone_override if tone_override != "brand default" else "casual-spoken"
    do = "\n".join(f"- {x}" for x in tone.get("do", []))
    dont = "\n".join(f"- {x}" for x in tone.get("dont", []))
    return (
        f"Persona: {tone.get('persona', '')}\n"
        f"Style: {tone.get('style', '')} — Instagram creator spoken Korean\n"
        f"Formality: {formality} (해요체/해체 only)\n"
        f"DO:\n{do}\n"
        f"DONT:\n{dont}\n"
        f"- NEVER use textbook endings like 입니다/합니다/예상됩니다\n"
    )


def _extract_json(text: str) -> dict[str, Any]:
    text = text.strip()
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


def _parse_visual_markers(body: str, markers: list[str] | None) -> list[str]:
    if markers:
        return markers
    return re.findall(r"\[\d+\.\s*Visual:[^\]]+\]", body)


def _tts_text_from_parts(hook: str, body: str, cta: str) -> str:
    # Strip visual markers for speech
    spoken_body = re.sub(r"\[\d+\.\s*Visual:[^\]]+\]", "", body)
    spoken_body = re.sub(r"\s+", " ", spoken_body).strip()
    parts = [p for p in [hook, spoken_body, cta] if p]
    return " ".join(parts)


def _estimate_caption_durations(lines: list[str], total_sec: float = 20.0) -> list[float]:
    if not lines:
        return []
    weights = [max(len(line), 8) for line in lines]
    total_w = sum(weights)
    durations = [round(total_sec * w / total_w, 2) for w in weights]
    # Ensure minimum readable time
    durations = [max(d, 1.5) for d in durations]
    return durations


def _fallback_script(
    topic: str,
    selected_hook: str,
    mode: ProductionMode,
    research_facts: list[str],
) -> ScriptResult:
    brand = load_brand_config()
    cta = brand.get("script", {}).get(
        "cta_template",
        "댓글에 '비밀' 남겨주시면 자료 보내드릴게요.",
    )
    hook = selected_hook or f"{topic}, 지금 왜 중요할까요"
    fact = research_facts[0] if research_facts else f"{topic} 관련해서 요즘 말이 많아요."

    if mode == ProductionMode.MUSIC_CAPTION:
        lines = [
            hook,
            fact[:40] + ("…" if len(fact) > 40 else ""),
            "핵심은 실행 속도예요",
            cta,
        ]
        markers = [
            "[1. Visual: business meeting closeup]",
            "[2. Visual: laptop typing marketing]",
            "[3. Visual: team collaboration office]",
            "[4. Visual: office hallway walk]",
            "[5. Visual: whiteboard brainstorm]",
            "[6. Visual: coffee shop laptop work]",
        ]
        return ScriptResult(
            mode=mode.value,
            hook=hook,
            body="\n".join(lines),
            cta=cta,
            visual_markers=markers,
            full_text="",
            caption_lines=lines,
            caption_durations=_estimate_caption_durations(lines),
            source="fallback",
        )

    body = (
        f"{fact} "
        f"[1. Visual: business meeting closeup] "
        f"그래서 팀이 지금 당장 바꿔야 할 건 콘텐츠 속도예요. "
        f"[2. Visual: laptop typing marketing] "
        f"짧게, 명확하게, 매주 쌓는 쪽이 이겨요. "
        f"[3. Visual: team collaboration office] "
        f"[4. Visual: whiteboard brainstorm] "
        f"[5. Visual: office hallway walk] "
        f"[6. Visual: coffee shop laptop work]"
    )
    full = _tts_text_from_parts(hook, body, cta)
    return ScriptResult(
        mode=mode.value,
        hook=hook,
        body=body,
        cta=cta,
        visual_markers=_parse_visual_markers(body, None),
        full_text=full,
        caption_lines=[],
        caption_durations=[],
        source="fallback",
    )


def _build_voice_prompt(
    topic: str,
    selected_hook: str,
    facts: list[str],
    reference: ReferenceInput | None,
    tone_override: str,
) -> str:
    brand = load_brand_config()
    script_cfg = brand.get("script", {})
    facts_block = "\n".join(f"- {f}" for f in facts[:8]) or "- (팩트 없음)"
    ref_block = reference.summary_for_prompt() if reference else "No reference"
    return f"""당신은 B2B 숏폼 릴스 대본 작가이자, 인스타 크리에이터 톤의 목소리 연출가입니다.
아래 JSON만 출력하세요. 다른 설명 금지.

{_brand_tone_block(tone_override)}

규칙:
- 기계적 인사 금지
- {SPOKEN_STYLE_RULE}
- 구어체, 20~40초 분량 (약 {script_cfg.get('max_words', 120)}단어 이하)
- 훅은 {script_cfg.get('hook_max_chars', 28)}자 이내, 말로 터지는 한 줄
- body 중간에 영어 검색용 비주얼 마커를 5~8개 넣으세요. 형식: [1. Visual: office handshake closeup]
- Visual 키워드는 반드시 영어. 빈 스마트폰 화면/화이트 스크린 금지.
  좋은 예: business meeting, laptop typing marketing, team brainstorm whiteboard, office hallway walk, coffee shop laptop work, handshake closeup, marketing analytics laptop
- CTA는 행동 유도 한 문장 (해요체). 예: "댓글에 '비밀' 남겨주시면 자료 보내드릴게요"
- 출력 전 문법·띄어쓰기 최종 검수
- {KOREAN_ONLY_RULE}

주제: {topic}
선택된 훅(개선 가능): {selected_hook}
팩트:
{facts_block}
레퍼런스:
{ref_block}

JSON 스키마:
{{
  "hook": "string",
  "body": "string (visual markers included)",
  "cta": "string",
  "visual_markers": ["[1. Visual: ...]", "..."]
}}
"""


def _build_caption_prompt(
    topic: str,
    selected_hook: str,
    facts: list[str],
    reference: ReferenceInput | None,
    tone_override: str,
) -> str:
    facts_block = "\n".join(f"- {f}" for f in facts[:8]) or "- (팩트 없음)"
    ref_block = reference.summary_for_prompt() if reference else "No reference"
    return f"""당신은 음악+자막형 인스타 릴스 카피라이터입니다.
보이스오버 없이, 화면에 뜨는 짧은 자막 카드만 만듭니다.
JSON만 출력하세요.

{_brand_tone_block(tone_override)}

규칙:
- caption_lines 4~7개
- 각 줄 8~22자 권장, 한 줄에 한 메시지
- 첫 줄은 훅
- 마지막 줄은 CTA
- {SPOKEN_STYLE_RULE}
- visual_markers는 반드시 영어 검색 키워드만 사용 (예: office meeting, laptop typing, team collaboration). 빈 화면/화이트스크린 금지.
- visual_markers 5~8개 (빠른 컷용)
- 나레이션 문장/인사 금지
- {KOREAN_ONLY_RULE}

주제: {topic}
선택된 훅: {selected_hook}
팩트:
{facts_block}
레퍼런스:
{ref_block}

JSON 스키마:
{{
  "hook": "string",
  "caption_lines": ["string", "..."],
  "cta": "string",
  "visual_markers": ["[1. Visual: ...]", "..."]
}}
"""


async def _call_claude(prompt: str, *, system: str, temperature: float = 0.7) -> dict[str, Any]:
    from modules.anthropic_llm import AnthropicNotConfiguredError, complete_json

    try:
        return await complete_json(system=system, user=prompt, temperature=temperature, max_tokens=1200)
    except AnthropicNotConfiguredError as exc:
        raise RuntimeError("ANTHROPIC_API_KEY not configured") from exc


async def generate_script(
    topic: str,
    research_facts: list[str],
    selected_hook: str = "",
    mode: str | ProductionMode = ProductionMode.VOICE_TTS,
    reference: ReferenceInput | None = None,
    tone_override: str = "brand default",
) -> ScriptResult:
    """Generate script or caption cards for the chosen production mode."""
    prod_mode = parse_mode(mode)
    logger.info("generate_script mode=%s topic=%r", prod_mode.value, topic)

    try:
        if prod_mode == ProductionMode.MUSIC_CAPTION:
            prompt = _build_caption_prompt(topic, selected_hook, research_facts, reference, tone_override)
        else:
            prompt = _build_voice_prompt(topic, selected_hook, research_facts, reference, tone_override)

        data = await _call_claude(
            prompt,
            system=(
                "Return valid JSON only. Korean spoken Instagram tone only "
                "(해요체/해체). No textbook 입니다/합니다. No Chinese/Hanja."
            ),
        )
        data = sanitize_script_fields(data)
        max_chars = load_brand_config().get("script", {}).get("hook_max_chars", 28)
        hook = sanitize_hook((data.get("hook") or selected_hook or topic), max_chars)
        cta = enforce_korean_copy(
            (data.get("cta") or load_brand_config().get("script", {}).get("cta_template", "")).strip()
        )
        markers = data.get("visual_markers") or []

        if prod_mode == ProductionMode.MUSIC_CAPTION:
            lines = [enforce_korean_copy(str(x)) for x in data.get("caption_lines", []) if str(x).strip()]
            if not lines:
                lines = [hook, cta]
            body = "\n".join(lines)
            return ScriptResult(
                mode=prod_mode.value,
                hook=hook,
                body=body,
                cta=cta,
                visual_markers=markers or _parse_visual_markers(body, None),
                full_text="",
                caption_lines=lines,
                caption_durations=_estimate_caption_durations(lines),
                source="claude",
            )

        body = (data.get("body") or "").strip()
        # Re-sanitize body while keeping visual markers
        sanitized = sanitize_script_fields({"body": body, "hook": hook, "cta": cta})
        body = sanitized.get("body", body)
        full = enforce_korean_copy(_tts_text_from_parts(hook, body, cta))
        return ScriptResult(
            mode=prod_mode.value,
            hook=hook,
            body=body,
            cta=cta,
            visual_markers=_parse_visual_markers(body, markers),
            full_text=full,
            caption_lines=[],
            caption_durations=[],
            source="claude",
        )

    except Exception as exc:  # noqa: BLE001
        logger.warning("Claude script failed (%s) – using fallback", exc)
        await asyncio.sleep(0)
        return _fallback_script(topic, selected_hook, prod_mode, research_facts)
