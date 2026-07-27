"""Category-scoped automatic topic selection.

User picks a large category → RSS (+ optional Tavily) scans recent news
inside that bucket → scored topic candidates → best topic returned.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any

from modules.research import (
    _dedupe_articles,
    _is_low_quality_article,
    fetch_rss_feeds,
    google_news_search_url,
)
from modules.utils import get_logger, get_settings

logger = get_logger(__name__)

CURRENT_YEAR = datetime.now().year


@dataclass(frozen=True)
class TopicCategory:
    id: str
    label: str
    description: str
    search_queries: tuple[str, ...]
    must_match: tuple[str, ...]  # Korean/English keywords that keep article on-topic
    topic_prefix: str  # used when shaping final topic string


CATEGORIES: tuple[TopicCategory, ...] = (
    TopicCategory(
        id="gov_support",
        label="정부지원 · 정책",
        description="지원사업·공고·예산·중소벤처·창업지원",
        search_queries=(
            f"{CURRENT_YEAR} 정부지원사업",
            f"{CURRENT_YEAR} 중소벤처기업부 지원사업",
            "창업지원사업 공고",
            "소상공인 지원금",
            "R&D 지원사업 선정",
            "산업부 지원사업",
        ),
        must_match=("지원", "공고", "선정", "보조금", "바우처", "창업", "중소", "예산", "산업부", "중기부"),
        topic_prefix="최근 정부지원사업 트렌드",
    ),
    TopicCategory(
        id="startup",
        label="스타트업 · 창업",
        description="스타트업·투자·IR·유니콘·엑셀러레이터",
        search_queries=(
            f"{CURRENT_YEAR} 스타트업 투자",
            "스타트업 시리즈 투자",
            "창업 생태계 트렌드",
            "벤처투자 동향",
            "스타트업 IR",
            "엑셀러레이터 프로그램",
        ),
        must_match=("스타트업", "창업", "투자", "벤처", "시리즈", "IR", "유니콘", "엑셀"),
        topic_prefix="최근 스타트업·창업 트렌드",
    ),
    TopicCategory(
        id="b2b_marketing",
        label="B2B · 마케팅",
        description="B2B 마케팅·숏폼·리드·콘텐츠",
        search_queries=(
            "B2B 마케팅 트렌드",
            "숏폼 마케팅 성과",
            "인스타그램 릴스 B2B",
            "콘텐츠 마케팅 리드",
            "B2B 리드 생성",
        ),
        must_match=("마케팅", "B2B", "숏폼", "릴스", "콘텐츠", "리드", "광고", "브랜드"),
        topic_prefix="최근 B2B 마케팅 트렌드",
    ),
    TopicCategory(
        id="ai_tech",
        label="AI · 테크",
        description="AI·반도체·디지털전환·보안",
        search_queries=(
            f"{CURRENT_YEAR} AI 산업 동향",
            "인공지능 기업 도입",
            "반도체 산업 뉴스",
            "디지털 전환 AX",
            "사이버보안 기업",
        ),
        must_match=("AI", "인공지능", "반도체", "테크", "디지털", "AX", "보안", "클라우드", "챗GPT"),
        topic_prefix="최근 AI·테크 트렌드",
    ),
    TopicCategory(
        id="economy",
        label="경제 · 투자",
        description="금리·증시·환율·펀드·IPO",
        search_queries=(
            f"{CURRENT_YEAR} 경제 전망",
            "금리 인하 전망",
            "코스피 증시",
            "IPO 상장",
            "환율 달러",
            "벤처펀드 투자",
        ),
        must_match=("경제", "금리", "증시", "코스피", "환율", "IPO", "펀드", "투자", "주식"),
        topic_prefix="최근 경제·투자 동향",
    ),
    TopicCategory(
        id="global",
        label="글로벌 · 해외비즈",
        description="수출·해외진출·무역·글로벌 규제",
        search_queries=(
            "한국 기업 해외진출",
            "수출 지원 정책",
            "글로벌 규제 ESG",
            "무역 관세",
            "K뷰티 수출",
            "해외 시장 진출",
        ),
        must_match=("수출", "해외", "글로벌", "무역", "관세", "진출", "ESG", "규제"),
        topic_prefix="최근 글로벌·해외비즈 트렌드",
    ),
)


@dataclass
class TopicCandidate:
    topic: str
    score: float
    source_title: str
    source_url: str = ""
    rationale: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class TopicPickResult:
    category_id: str
    category_label: str
    selected_topic: str
    candidates: list[TopicCandidate] = field(default_factory=list)
    article_count: int = 0
    query_used: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "category_id": self.category_id,
            "category_label": self.category_label,
            "selected_topic": self.selected_topic,
            "candidates": [c.to_dict() for c in self.candidates],
            "article_count": self.article_count,
            "query_used": self.query_used,
        }


def list_categories() -> list[TopicCategory]:
    return list(CATEGORIES)


def get_category(category_id: str) -> TopicCategory | None:
    for c in CATEGORIES:
        if c.id == category_id or c.label == category_id:
            return c
    return None


def _clean_headline(title: str) -> str:
    t = re.sub(r"\s+", " ", (title or "").strip())
    t = re.sub(r"\s*[-–—|]\s*[^-–—|]{0,40}$", "", t)
    t = re.sub(r"^(속보|단독|종합)\s*", "", t)
    return t.strip(" .…")


def _is_date_only_headline(title: str) -> bool:
    """Reject calendar/date dump titles like '2026년 06월 26일 금요일'."""
    t = _clean_headline(title)
    if re.fullmatch(
        r"\d{4}\s*년\s*\d{1,2}\s*월\s*\d{1,2}\s*일(?:\s*[월화수목금토일]요일)?",
        t,
    ):
        return True
    if re.fullmatch(r"\d{4}[./-]\d{1,2}[./-]\d{1,2}", t):
        return True
    return False


def _shape_topic(category: TopicCategory, headline: str) -> str:
    """Turn a news headline into a Human-Directed production topic."""
    clean = _clean_headline(headline)
    if not clean:
        return category.topic_prefix

    if len(clean) <= 42:
        return clean

    for sep in ("…", "·", ",", ":"):
        if sep in clean:
            left = clean.split(sep, 1)[0].strip()
            if 10 <= len(left) <= 42:
                return left

    return clean[:40].rstrip() + "…"


def _parse_published_age_days(article: dict[str, Any]) -> float | None:
    """Return age in days from published field, or None if unknown."""
    from email.utils import parsedate_to_datetime

    raw = str(article.get("published") or "").strip()
    if not raw:
        return None
    try:
        dt = parsedate_to_datetime(raw)
        if dt.tzinfo is not None:
            dt = dt.replace(tzinfo=None)
        return max(0.0, (datetime.now() - dt).total_seconds() / 86400.0)
    except Exception:  # noqa: BLE001
        pass
    # Korean / ISO fallbacks
    m = re.search(r"(20\d{2})[./년\s-]+(\d{1,2})[./월\s-]+(\d{1,2})", raw)
    if m:
        try:
            dt = datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)))
            return max(0.0, (datetime.now() - dt).total_seconds() / 86400.0)
        except ValueError:
            return None
    return None


def _score_article(article: dict[str, Any], category: TopicCategory) -> float:
    title = str(article.get("title") or "")
    summary = str(article.get("summary") or "")
    blob = f"{title} {summary}"
    low = blob.lower()

    if _is_date_only_headline(title):
        return -1.0
    if _is_low_quality_article(article):
        return -1.0
    if re.search(r"허위|과장광고|송치|사기|피의자|야동|주소콘", blob):
        return -1.0

    hits = sum(1 for k in category.must_match if k.lower() in low or k in blob)
    if hits == 0:
        return 0.0

    score = 1.0 + hits * 1.2

    # Freshness by year mention
    years = {int(y) for y in re.findall(r"(20\d{2})", blob)}
    if CURRENT_YEAR in years:
        score += 2.0
    elif CURRENT_YEAR - 1 in years:
        score += 0.4
    if years and max(years) < CURRENT_YEAR - 1:
        score -= 2.0

    # Hard recency: prefer last N days (TAVILY_DAYS, default 7), drop older
    age = _parse_published_age_days(article)
    settings = get_settings()
    window = max(1, int(settings.tavily_days or 7))
    if age is not None:
        if age > window:
            return -1.0  # outside recent window
        if age <= 2:
            score += 2.0
        elif age <= window:
            score += 1.2
        else:
            score += 0.3
    else:
        # Unknown publish date – mild penalty vs dated fresh articles
        score -= 0.3

    # Concrete signals that make good card-news topics
    if re.search(r"\d+(?:\.\d+)?(?:억|조|만|%|원)", blob):
        score += 1.5
    if re.search(r"공고|선정|모집|발표|확대|도입|규제|지원", blob):
        score += 0.8

    # Prefer headlines long enough to be specific
    if 12 <= len(_clean_headline(title)) <= 55:
        score += 0.5

    return score


def _category_feed_urls(category: TopicCategory) -> list[str]:
    settings = get_settings()
    days = max(1, int(settings.tavily_days or 7))
    feeds = [google_news_search_url(q, days=days) for q in category.search_queries]
    # Topical KR news + category keyword (also within freshness window)
    feeds.append(
        google_news_search_url(category.label.split("·")[0].strip(), days=days)
    )
    seen: set[str] = set()
    out: list[str] = []
    for u in feeds:
        if u not in seen:
            seen.add(u)
            out.append(u)
    return out


async def _tavily_boost(category: TopicCategory, limit: int = 6) -> list[dict[str, Any]]:
    """Optional Tavily enrichment for category topic picking."""
    settings = get_settings()
    key = (settings.tavily_api_key or "").strip()
    if not key or key.startswith("your_"):
        return []

    days = max(1, int(settings.tavily_days or 7))
    try:
        import httpx

        query = " OR ".join(category.search_queries[:3])
        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.post(
                "https://api.tavily.com/search",
                json={
                    "api_key": key,
                    "query": query,
                    "search_depth": "basic",
                    "include_answer": False,
                    "max_results": limit,
                    "topic": "news",
                    "days": days,
                },
            )
            if resp.status_code >= 400:
                logger.warning("Tavily topic boost failed (%s)", resp.status_code)
                return []
            results = (resp.json() or {}).get("results") or []
    except Exception as exc:  # noqa: BLE001
        logger.warning("Tavily topic boost exception (%s)", exc)
        return []

    articles: list[dict[str, Any]] = []
    for r in results:
        title = str(r.get("title") or "").strip()
        if not title:
            continue
        articles.append(
            {
                "title": title,
                "summary": str(r.get("content") or "")[:280],
                "link": str(r.get("url") or ""),
                "published": "",
                "feed": "tavily",
            }
        )
    return articles


async def pick_topic_for_category(
    category_id: str,
    *,
    top_n: int = 5,
) -> TopicPickResult:
    """Scan category news and pick the best production topic."""
    category = get_category(category_id)
    if category is None:
        raise ValueError(f"unknown category: {category_id}")

    feeds = _category_feed_urls(category)
    logger.info("Topic pick – category=%s feeds=%d", category.id, len(feeds))

    articles = await fetch_rss_feeds(feeds, limit=120)
    tavily_extra = await _tavily_boost(category, limit=6)
    articles.extend(tavily_extra)
    articles = _dedupe_articles(articles)

    scored: list[tuple[float, dict[str, Any]]] = []
    for a in articles:
        s = _score_article(a, category)
        if s >= 1.5:
            scored.append((s, a))
    scored.sort(key=lambda x: x[0], reverse=True)

    candidates: list[TopicCandidate] = []
    seen_topics: set[str] = set()
    for score, art in scored:
        topic = _shape_topic(category, str(art.get("title") or ""))
        key = re.sub(r"\s+", "", topic.lower())[:36]
        if key in seen_topics or len(topic) < 8:
            continue
        seen_topics.add(key)
        candidates.append(
            TopicCandidate(
                topic=topic,
                score=round(score, 2),
                source_title=_clean_headline(str(art.get("title") or "")),
                source_url=str(art.get("link") or ""),
                rationale=f"카테고리 '{category.label}' 관련 뉴스 점수 {score:.1f}",
            )
        )
        if len(candidates) >= top_n:
            break

    if not candidates:
        # Soft fallback – still produce a usable Human-Directed topic
        fallback = f"{category.topic_prefix} {CURRENT_YEAR}"
        candidates = [
            TopicCandidate(
                topic=fallback,
                score=0.0,
                source_title="(카테고리 기본 주제)",
                rationale="관련 뉴스가 부족해 카테고리 기본 주제로 대체",
            )
        ]

    selected = candidates[0]
    result = TopicPickResult(
        category_id=category.id,
        category_label=category.label,
        selected_topic=selected.topic,
        candidates=candidates,
        article_count=len(articles),
        query_used=" | ".join(category.search_queries[:4]),
    )
    logger.info(
        "Topic picked – category=%s topic=%r candidates=%d articles=%d",
        category.id,
        selected.topic,
        len(candidates),
        len(articles),
    )
    return result
