"""Generate 5 hooky Instagram-reels card-news ideas from a keyword + target."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from modules.utils import get_logger, load_brand_config

logger = get_logger(__name__)

REEL_TARGETS = ("B2B", "B2G", "IR")

_TARGET_HINTS = {
    "B2B": "B2B 실무자·스타트업 대표. 지원사업, 영업, 실행 팁.",
    "B2G": "공공·조달·정책 실무. 공고, 입찰, 지원금 타이밍.",
    "IR": "투자·IR·성장 스토리. 숫자, 포지셔닝, 설득 한 줄.",
}


@dataclass
class ReelIdea:
    title: str
    hook: str
    angle: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _fallback_ideas(keyword: str, target: str) -> list[ReelIdea]:
    kw = (keyword or "이 주제").strip() or "이 주제"
    t = target if target in _TARGET_HINTS else "B2B"
    templates = [
        (f"{kw}, 지금 안 보면 늦는 이유", f"{kw} 타이밍을 놓치면 비용이 됩니다", "긴급성"),
        (f"{t}가 {kw}에서 제일 많이 놓치는 것", f"{kw}에서 실무가 빠지는 한 가지", "실수 지적"),
        (f"{kw} 체크리스트 3줄", f"{kw}, 이 3개만 확인하세요", "실행 체크"),
        (f"{kw} 숫자로 한 장 정리", f"{kw}를 숫자 한 줄로 끊습니다", "데이터 훅"),
        (f"{kw} vs 하던 방식", f"{kw}, 예전 방식으로는 안 됩니다", "대비"),
    ]
    return [ReelIdea(title=a, hook=b, angle=c) for a, b, c in templates]


async def generate_reel_ideas(
    keyword: str,
    target: str = "B2B",
    *,
    count: int = 5,
) -> list[ReelIdea]:
    """Return ``count`` hooky script candidates. Gemini → OpenAI → local fallback."""
    keyword = (keyword or "").strip()
    target = (target or "B2B").strip().upper()
    if target not in _TARGET_HINTS:
        target = "B2B"
    if not keyword:
        return _fallback_ideas("핵심 키워드", target)[:count]

    brand = load_brand_config()
    brand_name = (
        (brand.get("card_news") or {}).get("brand_name")
        or (brand.get("brand") or {}).get("name")
        or "WITHCHOYOOL"
    )
    hint = _TARGET_HINTS[target]
    system = (
        "You are a Korean Instagram card-news copywriter. "
        "Return JSON only. No markdown."
    )
    user = f"""브랜드: {brand_name}
핵심 키워드: {keyword}
타겟: {target} ({hint})

인스타 릴스(9:16 카드뉴스)용 후킹 아이디어 {count}개를 만들어라.
조건:
- 한국어만
- 제목 18자 내외, 훅 28자 내외
- 클릭을 유도하는 구체적 한 줄 (막연한 '총정리' 금지)
- 진단/보장/광고 과장 금지
- 뉴스 스크랩이 아니라 인사이트·꿀팁 각도

JSON:
{{"ideas":[{{"title":"...","hook":"...","angle":"긴급성|실수|체크|숫자|대비"}}]}}
"""

    data: dict[str, Any] | None = None
    try:
        from modules.gemini_llm import complete_json as gemini_json

        data = await gemini_json(system=system, user=user, temperature=0.9, max_tokens=1200)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Gemini reel ideas failed (%s) – trying OpenAI", exc)
        try:
            from modules.openai_llm import complete_json as openai_json

            data = await openai_json(system=system, user=user, temperature=0.85, max_tokens=1200)
        except Exception as exc2:  # noqa: BLE001
            logger.warning("OpenAI reel ideas failed (%s) – fallback templates", exc2)
            return _fallback_ideas(keyword, target)[:count]

    rows = (data or {}).get("ideas") if isinstance(data, dict) else None
    if not isinstance(rows, list):
        return _fallback_ideas(keyword, target)[:count]

    out: list[ReelIdea] = []
    seen: set[str] = set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        title = str(row.get("title") or "").strip()
        hook = str(row.get("hook") or title).strip()
        angle = str(row.get("angle") or "").strip() or "인사이트"
        if len(title) < 6:
            continue
        key = title.replace(" ", "")[:24]
        if key in seen:
            continue
        seen.add(key)
        out.append(ReelIdea(title=title, hook=hook, angle=angle))
        if len(out) >= count:
            break
    if len(out) < count:
        for extra in _fallback_ideas(keyword, target):
            key = extra.title.replace(" ", "")[:24]
            if key in seen:
                continue
            out.append(extra)
            if len(out) >= count:
                break
    logger.info("Reel ideas %d for keyword=%r target=%s", len(out), keyword, target)
    return out[:count]
