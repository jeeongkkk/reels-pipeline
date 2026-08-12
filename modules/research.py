"""Module 1: Target Research & Angle Extraction.

Human-directed topic + reference → Google News / RSS → ranked articles → hook seeds.
Twikit remains optional and disabled by default.
"""

from __future__ import annotations

import asyncio
import html
import re
from dataclasses import asdict, dataclass, field
from typing import Any
from urllib.parse import quote_plus

import feedparser
import httpx

from datetime import datetime

from modules.reference import ReferenceInput
from modules.utils import get_logger, get_settings

logger = get_logger(__name__)

CURRENT_YEAR = datetime.now().year

DEFAULT_FEEDS = [
    "https://news.google.com/rss?hl=ko&gl=KR&ceid=KR:ko",
]

USER_AGENT = (
    "Mozilla/5.0 (compatible; ReelsPipeline/0.1; +https://localhost) "
    "AppleWebKit/537.36 (KHTML, like Gecko)"
)


@dataclass
class ResearchResult:
    topic: str
    facts: list[str] = field(default_factory=list)
    hooks: list[str] = field(default_factory=list)
    sources: list[dict[str, str]] = field(default_factory=list)
    raw_articles: list[dict[str, Any]] = field(default_factory=list)
    reference_summary: str = ""
    query_used: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _strip_html(text: str) -> str:
    text = html.unescape(text or "")
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _tokenize(topic: str) -> list[str]:
    raw = re.split(r"[\s,/|·]+", topic.lower())
    tokens = [t for t in raw if len(t) >= 2]
    # Expand compound Korean keywords for better headline matching
    blob = topic.replace(" ", "")
    extras = []
    for key in (
        "정부지원",
        "지원사업",
        "창업지원",
        "중소벤처",
        "소상공인",
        "R&D",
        "보조금",
        "공고",
        "태양광",
        "반도체",
        "스타트업",
    ):
        if key.lower() in blob.lower() or key in topic:
            extras.append(key.lower())
    for e in extras:
        if e not in tokens:
            tokens.append(e)
    return tokens


_LOW_QUALITY_SOURCES = re.compile(
    r"브런치|brunch|티스토리|tistory|네이버\s*블로그|blog\.naver|"
    r"instagram\.com|www\.instagram|#협찬|제품제공|광고협찬|체험단",
    re.I,
)


def _is_low_quality_article(article: dict[str, Any]) -> bool:
    blob = (
        f"{article.get('title', '')} {article.get('summary', '')} "
        f"{article.get('link', '')} {article.get('feed', '')}"
    )
    return bool(_LOW_QUALITY_SOURCES.search(blob))


def _topic_news_queries(topic: str) -> list[str]:
    """Multiple Google News queries so broad topics still get concrete hits."""
    topic = topic.strip()
    queries: list[str] = []

    def _add(q: str) -> None:
        q = re.sub(r"\s+", " ", q.strip())
        if len(q) >= 2 and q not in queries:
            queries.append(q)

    _add(topic)
    core = re.sub(
        r"(최근|요즘|올해|202[0-9]년?|트렌드|동향|분석|총정리|정리)\s*",
        "",
        topic,
    ).strip()
    _add(core)

    if re.search(r"정부\s*지원|지원\s*사업|창업\s*지원|보조금|중소|소상공인", topic):
        _add(f"2026 중소벤처기업부 지원사업")
        _add(f"2026 창업지원사업 공고")
        _add("중소벤처기업부 지원사업")
        _add("창업지원사업 공고")
        _add("소상공인 지원금")
        _add("R&D 지원사업 선정")
        _add("정부지원 스타트업")

    if re.search(r"마케팅|B2B|릴스|인스타", topic, re.I):
        _add("B2B 마케팅 트렌드")
        _add("숏폼 마케팅 성과")

    return queries[:6]


def google_news_search_url(topic: str, *, days: int | None = None) -> str:
    """Google News RSS. When ``days`` is set, append ``when:Nd`` for recency."""
    topic = topic.strip()
    if days is not None and int(days) > 0:
        topic = f"{topic} when:{int(days)}d"
    q = quote_plus(topic)
    return f"https://news.google.com/rss/search?q={q}&hl=ko&gl=KR&ceid=KR:ko"


NEWS_FRESHNESS_CAP_DAYS = 7  # never surface ~1-month Google News recrawls


def _freshness_window_days() -> int:
    """News window: settings.tavily_days, hard-capped at 7 days."""
    configured = max(1, int(get_settings().tavily_days or 7))
    if configured > NEWS_FRESHNESS_CAP_DAYS:
        logger.warning(
            "TAVILY_DAYS=%d exceeds news cap %dd – using %dd",
            configured,
            NEWS_FRESHNESS_CAP_DAYS,
            NEWS_FRESHNESS_CAP_DAYS,
        )
    return min(configured, NEWS_FRESHNESS_CAP_DAYS)


def _age_from_datetime(dt: datetime) -> float:
    if dt.tzinfo is not None:
        dt = dt.replace(tzinfo=None)
    return max(0.0, (datetime.now() - dt).total_seconds() / 86400.0)


def _parse_datetime_string(raw: str) -> datetime | None:
    from email.utils import parsedate_to_datetime

    raw = (raw or "").strip()
    if not raw:
        return None
    try:
        dt = parsedate_to_datetime(raw)
        if dt.tzinfo is not None:
            dt = dt.replace(tzinfo=None)
        return dt
    except Exception:  # noqa: BLE001
        pass
    m_iso = re.search(r"(20\d{2})-(\d{1,2})-(\d{1,2})", raw)
    if m_iso:
        try:
            return datetime(int(m_iso.group(1)), int(m_iso.group(2)), int(m_iso.group(3)))
        except ValueError:
            pass
    m = re.search(r"(20\d{2})[./년\s-]+(\d{1,2})[./월\s-]+(\d{1,2})", raw)
    if m:
        try:
            return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            pass
    return None


def _text_age_days(blob: str) -> float | None:
    """Age implied by Korean relative phrases / calendar dates in title·summary.

    Google News RSS pubdate is often a recrawl stamp – text wins when older.
    """
    text = blob or ""
    ages: list[float] = []

    if re.search(r"지난\s*달|지난달|한\s*달여|한\s*달\s*전|한달\s*전", text):
        ages.append(30.0)
    m = re.search(r"(\d+)\s*(?:개월|달)\s*전", text)
    if m:
        ages.append(float(int(m.group(1)) * 30))
    m = re.search(r"(\d+)\s*주\s*전", text)
    if m:
        ages.append(float(int(m.group(1)) * 7))
    m = re.search(r"(\d+)\s*일\s*전", text)
    if m:
        ages.append(float(int(m.group(1))))

    dates: list[datetime] = []
    for m in re.finditer(r"(20\d{2})[./년\s-]+(\d{1,2})[./월\s-]+(\d{1,2})", text):
        try:
            dates.append(datetime(int(m.group(1)), int(m.group(2)), int(m.group(3))))
        except ValueError:
            continue
    # Month-day without year (e.g. 7월 12일) → assume current year, else previous
    now = datetime.now()
    for m in re.finditer(r"(?<!\d)(\d{1,2})\s*월\s*(\d{1,2})\s*일", text):
        try:
            dt = datetime(now.year, int(m.group(1)), int(m.group(2)))
        except ValueError:
            continue
        if dt > now:
            try:
                dt = datetime(now.year - 1, int(m.group(1)), int(m.group(2)))
            except ValueError:
                continue
        dates.append(dt)
    if dates:
        # Oldest explicit date = how stale the story actually is
        ages.append(_age_from_datetime(min(dates)))

    if not ages:
        return None
    return max(ages)


def _parse_published_age_days(article: dict[str, Any]) -> float | None:
    """Conservative age: max(RSS pubdate, title/summary date signals)."""
    ages: list[float] = []
    raw = str(article.get("published") or article.get("published_date") or "").strip()
    dt = _parse_datetime_string(raw)
    if dt is not None:
        ages.append(_age_from_datetime(dt))
    parsed = article.get("published_parsed")
    if parsed:
        try:
            ages.append(_age_from_datetime(datetime(*parsed[:6])))
        except Exception:  # noqa: BLE001
            pass
    blob = f"{article.get('title', '')} {article.get('summary', '')}"
    text_age = _text_age_days(blob)
    if text_age is not None:
        ages.append(text_age)
    if not ages:
        return None
    return max(ages)


def _is_within_freshness_window(article: dict[str, Any], *, days: int | None = None) -> bool:
    """True only when known age <= window. Undated kept only without old-year signal."""
    window = max(1, int(days if days is not None else _freshness_window_days()))
    age = _parse_published_age_days(article)
    if age is not None:
        return age <= window + 0.01
    years = _article_years(article)
    if years:
        newest = max(years)
        if newest < CURRENT_YEAR:
            return False
    return True


def _filter_fresh_articles(
    articles: list[dict[str, Any]], *, days: int | None = None
) -> list[dict[str, Any]]:
    """Hard-drop items older than the freshness window.

    Prefer dated hits; drop undated when a few dated articles remain.
    """
    window = max(1, int(days if days is not None else _freshness_window_days()))
    kept = [a for a in articles if _is_within_freshness_window(a, days=window)]
    dated = [a for a in kept if _parse_published_age_days(a) is not None]
    if len(dated) >= 4:
        return dated
    return kept


def resolve_feed_urls(topic: str, extra_feeds: list[str] | None = None) -> list[str]:
    """Build feed list: several topic Google News queries + configured defaults."""
    settings = get_settings()
    days = _freshness_window_days()
    feeds: list[str] = []

    for q in _topic_news_queries(topic):
        feeds.append(google_news_search_url(q, days=days))

    # Only use explicit env feeds — never mix undated top-news DEFAULT into topic search
    env_feeds = [u.strip() for u in (settings.news_rss_feeds or "").split(",") if u.strip()]
    if env_feeds:
        feeds.extend(env_feeds)
    elif not feeds:
        feeds.extend(DEFAULT_FEEDS)

    if extra_feeds:
        feeds.extend(extra_feeds)

    seen: set[str] = set()
    unique: list[str] = []
    for u in feeds:
        if u not in seen:
            seen.add(u)
            unique.append(u)
    return unique


def _article_years(article: dict[str, Any]) -> set[int]:
    blob = f"{article.get('title', '')} {article.get('summary', '')} {article.get('published', '')}"
    return {int(y) for y in re.findall(r"(20\d{2})", blob)}


def _score_relevance(article: dict[str, Any], tokens: list[str], topic: str = "") -> float:
    if not tokens:
        return 0.5
    blob = f"{article.get('title', '')} {article.get('summary', '')}".lower()
    hits = sum(1 for t in tokens if t in blob)
    score = hits / max(len(tokens), 1)

    # Boost concrete policy / grant language
    boost_terms = (
        "지원사업",
        "선정",
        "공고",
        "모집",
        "보조금",
        "중소벤처",
        "창업",
        "r&d",
        "예산",
        "억원",
    )
    boost = sum(0.08 for t in boost_terms if t in blob)
    score = min(1.0, score + boost)

    if _is_low_quality_article(article):
        score *= 0.35

    blob = f"{article.get('title', '')} {article.get('summary', '')}"
    if re.search(r"허위|과장광고|송치|사기|피의자", blob):
        score *= 0.15

    # Soft penalty if headline shares almost nothing with topic core
    core = re.sub(r"(최근|트렌드|동향|분석)\s*", "", topic).strip()
    if core and len(core) >= 4 and core[:4] not in blob and hits == 0:
        score *= 0.4

    years = _article_years(article)
    if CURRENT_YEAR in years:
        score += 0.35
    elif (CURRENT_YEAR - 1) in years:
        score += 0.15
    elif years and max(years) <= CURRENT_YEAR - 2:
        score *= 0.25

    # Hard recency vs TAVILY_DAYS window (default 7)
    age = _parse_published_age_days(article)
    window = _freshness_window_days()
    if age is not None:
        if age > window:
            return 0.0
        if age <= 2:
            score += 0.25
        elif age <= window:
            score += 0.12
    else:
        score *= 0.75  # undated: prefer dated fresh hits

    return score


def _parse_feed_entries(feed: feedparser.FeedParserDict, source_url: str) -> list[dict[str, Any]]:
    articles: list[dict[str, Any]] = []
    for entry in feed.entries:
        title = _strip_html(getattr(entry, "title", "") or "")
        summary = _strip_html(
            getattr(entry, "summary", "") or getattr(entry, "description", "") or ""
        )
        link = getattr(entry, "link", "") or ""
        published = getattr(entry, "published", "") or getattr(entry, "updated", "") or ""
        published_parsed = getattr(entry, "published_parsed", None) or getattr(
            entry, "updated_parsed", None
        )
        if published_parsed and not published:
            try:
                published = datetime(*published_parsed[:6]).strftime("%a, %d %b %Y %H:%M:%S")
            except Exception:  # noqa: BLE001
                published = ""
        if not title:
            continue
        articles.append(
            {
                "title": title,
                "summary": summary[:400],
                "link": link,
                "published": published,
                "published_parsed": published_parsed,
                "feed": source_url,
            }
        )
    return articles


async def _download_feed(client: httpx.AsyncClient, url: str) -> list[dict[str, Any]]:
    try:
        resp = await client.get(url, timeout=20.0)
        resp.raise_for_status()
        feed = feedparser.parse(resp.text)
        articles = _parse_feed_entries(feed, url)
        logger.info("Fetched %d articles from %s", len(articles), url)
        return articles
    except Exception as exc:  # noqa: BLE001 – network resilience for Phase A
        logger.warning("Failed to fetch feed %s: %s", url, exc)
        return []


def _dedupe_articles(articles: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    unique: list[dict[str, Any]] = []
    for a in articles:
        key = re.sub(r"\s+", "", a.get("title", "").lower())
        if not key or key in seen:
            continue
        seen.add(key)
        unique.append(a)
    return unique


def _build_facts(articles: list[dict[str, Any]], limit: int = 8) -> list[str]:
    facts: list[str] = []
    for a in articles[:limit]:
        title = _clean_headline(a.get("title", ""))
        summary = a.get("summary", "")
        summary = re.sub(r"\.zip\b", "", summary, flags=re.IGNORECASE)
        summary = re.sub(r"\s+", " ", summary).strip()
        if summary and summary != title:
            facts.append(f"{title} — {summary[:160]}")
        elif title:
            facts.append(title)
    return facts


def _clean_headline(title: str) -> str:
    title = title.strip()
    title = re.split(r"\s[-–|]\s", title)[0].strip()
    title = re.sub(r"\.zip\b", "", title, flags=re.IGNORECASE)
    title = re.sub(r"\[.*?\]", "", title)
    title = re.sub(r"[🧠🔥✅❌▶️🎬]+", "", title)
    return re.sub(r"\s+", " ", title).strip(" .…")


def _build_hook_seeds(
    topic: str,
    articles: list[dict[str, Any]],
    reference: ReferenceInput | None,
    limit: int = 5,
) -> list[str]:
    """Heuristic hook candidates from headlines (style notes are NOT hooks)."""
    hooks: list[str] = []
    brand_max = 28

    for a in articles:
        title = _clean_headline(a.get("title", ""))
        if len(title) < 8:
            continue
        if len(title) > brand_max + 10:
            title = title[: brand_max - 1] + "…"
        if title not in hooks:
            hooks.append(title)
        if len(hooks) >= limit:
            break

    if not hooks:
        hooks = [
            f"{topic}, 지금 왜 중요한가",
            f"{topic} 한 가지 팩트",
            f"팀에서 바로 쓸 {topic}",
        ]

    # Reference only nudges wording later; keep a soft preference note in logs
    if reference and reference.style_notes:
        logger.info("Reference style notes present (applied in scoring/prompt, not as hook)")

    return hooks[:limit]


async def fetch_rss_feeds(feed_urls: list[str], limit: int = 80) -> list[dict[str, Any]]:
    """Download and parse multiple RSS feeds concurrently."""
    if not feed_urls:
        return []

    headers = {"User-Agent": USER_AGENT}
    async with httpx.AsyncClient(headers=headers, follow_redirects=True) as client:
        batches = await asyncio.gather(*[_download_feed(client, url) for url in feed_urls])

    articles: list[dict[str, Any]] = []
    for batch in batches:
        articles.extend(batch)
    return articles[:limit]


async def fetch_twitter_reactions(topic: str, limit: int = 20) -> list[dict[str, Any]]:
    """Optional Twikit-based X/Twitter scraping. Disabled by default."""
    logger.info("Twikit disabled – skipping social reactions for: %s", topic)
    await asyncio.sleep(0)
    return []


async def research_topic(
    topic: str,
    rss_feeds: list[str] | None = None,
    reference: ReferenceInput | None = None,
    top_n: int = 12,
) -> ResearchResult:
    """Run full research pipeline for a user-provided strategic topic."""
    topic = topic.strip()
    if not topic:
        raise ValueError("topic is required")

    feeds = rss_feeds if rss_feeds is not None else resolve_feed_urls(topic)
    logger.info("Research start – topic=%r feeds=%d", topic, len(feeds))

    articles, _twitter = await asyncio.gather(
        fetch_rss_feeds(feeds, limit=100),
        fetch_twitter_reactions(topic),
    )

    tokens = _tokenize(topic)
    articles = _dedupe_articles(articles)
    window = _freshness_window_days()
    before = len(articles)
    # Hard drop older-than-window (incl. title "N주 전" / old calendar dates)
    articles = _filter_fresh_articles(articles, days=window)
    logger.info(
        "Research freshness window=%dd articles=%d→%d",
        window,
        before,
        len(articles),
    )

    # Drop obvious blog spam unless we have almost nothing left
    filtered = [a for a in articles if not _is_low_quality_article(a)]
    if len(filtered) >= 5:
        articles = filtered

    for a in articles:
        a["relevance"] = round(_score_relevance(a, tokens, topic=topic), 3)

    articles.sort(key=lambda a: a.get("relevance", 0), reverse=True)
    # Prefer articles with some relevance signal
    relevant = [a for a in articles if a.get("relevance", 0) >= 0.15]
    top = (relevant or articles)[:top_n]

    facts = _build_facts(top, limit=10)
    hooks = _build_hook_seeds(topic, top, reference)
    sources = [
        {
            "url": a.get("link", ""),
            "title": a.get("title", ""),
            "published": a.get("published", ""),
            "relevance": str(a.get("relevance", 0)),
        }
        for a in top
    ]

    ref_summary = reference.summary_for_prompt() if reference else ""

    result = ResearchResult(
        topic=topic,
        facts=facts,
        hooks=hooks,
        sources=sources,
        raw_articles=top,
        reference_summary=ref_summary,
        query_used=" | ".join(_topic_news_queries(topic)[:4]),
    )
    logger.info(
        "Research done – articles=%d facts=%d hooks=%d queries=%s",
        len(top),
        len(facts),
        len(hooks),
        result.query_used,
    )
    return result
