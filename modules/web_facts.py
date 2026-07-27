"""Live web fact scraping before card-script LLM generation.

Priority: Tavily (days window, multi-query) → SerpAPI → DuckDuckGo (optional).
When ``TAVILY_API_KEY`` is set and ``TAVILY_SKIP_DDG_FALLBACK=true``, DuckDuckGo is
not used (no SEO-noise fallback).
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any

import httpx

from modules.utils import get_logger, get_settings

logger = get_logger(__name__)

CURRENT_YEAR = datetime.now().year


def _fact_years(text: str) -> set[int]:
    return {int(y) for y in re.findall(r"(20\d{2})", text or "")}


def _is_stale_fact(text: str) -> bool:
    years = _fact_years(text)
    if not years:
        return False
    if any(y >= CURRENT_YEAR - 1 for y in years):
        return False
    return max(years) <= CURRENT_YEAR - 2


@dataclass
class WebFact:
    title: str
    snippet: str
    url: str = ""
    source: str = ""  # tavily | serpapi | duckduckgo

    def as_prompt_line(self) -> str:
        title = re.sub(r"\s+", " ", (self.title or "").strip())
        snip = re.sub(r"\s+", " ", (self.snippet or "").strip())
        if snip and snip not in title:
            return f"{title} — {snip[:220]}"
        return title

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class WebFactBundle:
    topic: str
    facts: list[WebFact] = field(default_factory=list)
    provider: str = ""
    query_used: str = ""
    days: int = 30

    def prompt_facts(self, limit: int = 3) -> list[str]:
        return [f.as_prompt_line() for f in self.facts[:limit] if f.as_prompt_line()]

    def to_dict(self) -> dict[str, Any]:
        return {
            "topic": self.topic,
            "provider": self.provider,
            "query_used": self.query_used,
            "days": self.days,
            "facts": [f.to_dict() for f in self.facts],
        }


def _api_key_configured(value: str) -> bool:
    key = (value or "").strip()
    return bool(key and not key.startswith("your_"))


def _tavily_configured() -> bool:
    return _api_key_configured(get_settings().tavily_api_key)


def _clean(text: str) -> str:
    text = re.sub(r"<[^>]+>", " ", text or "")
    return re.sub(r"\s+", " ", text).strip()


_SPAM_RE = re.compile(
    r"야동|성인동영상|포르노|주소콘|주소모아|주소월드|링크모음|토렌트|"
    r"카지노|바카라|성인사이트|성인웹|도박|대출광고|성인방송|bj야동",
    re.I,
)

# Portal menus / SEO crumbs / empty YouTube titles – not usable as card facts
_CRUMB_RE = re.compile(
    r"사업유형별|지원사업\s*소개|공고\s*조회|조회·신청|기업마당|"
    r"나라장터|YouTube|유튜브|TikTok|틱톡|특공\s*전략|이럴땐\s*청약|"
    r"^\s*#\d+|프리미엄콘텐츠|PDF북|한눈에\s*알아보기|"
    r"주얼리|워치|패션\s*트렌드|신청\s*방법.*신청",
    re.I,
)

_SEO_LIST = re.compile(
    r"목록\.|공고\s*목록|모음|총정리|알리미|유치여행사|관광객|"
    r"편리하지만\s*새로운\s*공격|트렌드마이크로",
    re.I,
)

# Soft topical anchors for common B2B / policy queries
_POLICY_ANCHORS = re.compile(
    r"지원|공고|선정|바우처|보조금|중소|창업|산업부|중기부|조달|"
    r"R&D|연구개발|정책|트렌드|예산|억원|사업",
    re.I,
)


def _topic_tokens(topic: str) -> list[str]:
    raw = re.findall(r"[가-힣A-Za-z0-9]{2,}", topic or "")
    stop = {"최근", "최신", "관련", "분석", "동향", "대한", "위한", "하는", "있는"}
    return [t for t in raw if t.lower() not in stop and t not in stop]


def _is_useful(title: str, snippet: str, topic: str = "") -> bool:
    blob = f"{title} {snippet}"
    if len(title) < 8:
        return False
    if _SPAM_RE.search(blob):
        return False
    if _CRUMB_RE.search(blob):
        return False
    # Keyword SEO soup: "A, B, C, D, E..."
    if blob.count(",") >= 4:
        return False
    if _SEO_LIST.search(blob):
        return False
    if len(re.findall(r"신청\s*방법|대상자\s*확인", blob)) >= 2:
        return False
    # Title == snippet repetition is usually a dead page
    t = re.sub(r"\s+", "", title)
    s = re.sub(r"\s+", "", snippet)
    if t and s and (t in s or s in t) and len(t) < 40 and not re.search(r"\d", blob):
        return False
    low = blob.lower()
    if any(x in low for x in ("로그인", "sign in", "cookie", "개인정보처리방침")):
        return False

    tokens = _topic_tokens(topic)
    if tokens:
        hits = sum(1 for t in tokens if t.lower() in low or t in blob)
        if hits == 0:
            if any(re.search(r"지원|정책|공고|바우처|창업", tok) for tok in tokens):
                if not _POLICY_ANCHORS.search(blob):
                    return False
            else:
                return False
    # Prefer concrete news: at least one digit OR a known agency verb
    if not re.search(r"\d", blob) and not re.search(
        r"선정|공고|발표|편성|지원금|바우처|억원|조\s*원", blob
    ):
        return False
    if _is_stale_fact(blob):
        return False
    return True


def _search_queries_for_topic(topic: str) -> list[str]:
    """Prefer 2026 news/policy phrasing so DDG does not drift into stale SEO."""
    base = (topic or "").strip()
    core = re.sub(r"(최근|최신|트렌드|동향|분석)\s*", "", base).strip() or base
    qs = [
        f"{core} 2026",
        f"2026 {core}",
        f"{base} 2026 공고",
        f"{base} 최신",
    ]
    if re.search(r"지원|정책|공고|바우처|창업|중소", base):
        qs = [
            f"2026 {core} 공고",
            f"2026 중소벤처기업부 지원사업",
            "2026 중소기업 혁신바우처 선정",
            "2026 창업지원사업 통합공고",
            f"{core} 2026 예산",
            f"{base} 중소벤처 공고",
            base,
        ]
    # de-dupe preserve order
    seen: set[str] = set()
    out: list[str] = []
    for q in qs:
        if q and q not in seen:
            seen.add(q)
            out.append(q)
    return out


async def _search_tavily(
    topic: str,
    limit: int = 5,
    *,
    days: int = 30,
) -> tuple[list[WebFact], list[str]]:
    """Multi-query Tavily news search restricted to the last ``days`` days."""
    settings = get_settings()
    key = (getattr(settings, "tavily_api_key", "") or "").strip()
    if not _api_key_configured(key):
        return [], []

    days = max(1, int(days))
    queries = _search_queries_for_topic(topic)
    out: list[WebFact] = []
    seen: set[str] = set()
    used_queries: list[str] = []

    async with httpx.AsyncClient(timeout=40.0) as client:
        for q in queries:
            if len(out) >= limit:
                break
            payload = {
                "api_key": key,
                "query": q,
                "search_depth": "advanced",
                "include_answer": False,
                "max_results": min(limit + 2, 10),
                "topic": "news",
                "days": days,
            }
            try:
                resp = await client.post("https://api.tavily.com/search", json=payload)
                resp.raise_for_status()
            except Exception as exc:  # noqa: BLE001
                logger.warning("Tavily query failed (%s) for %r", exc, q)
                continue

            used_queries.append(q)
            rows = (resp.json() or {}).get("results") or []
            for row in rows:
                title = _clean(str(row.get("title") or ""))
                snip = _clean(str(row.get("content") or row.get("snippet") or ""))
                url = str(row.get("url") or "")
                dedupe_key = re.sub(r"\s+", "", title.lower())
                if not dedupe_key or dedupe_key in seen:
                    continue
                if not _is_useful(title, snip, topic):
                    continue
                seen.add(dedupe_key)
                out.append(WebFact(title=title, snippet=snip, url=url, source="tavily"))
                if len(out) >= limit:
                    break

    logger.info(
        "Tavily collected %d facts (days=%d, queries=%d) for %r",
        len(out),
        days,
        len(used_queries),
        topic,
    )
    return out[:limit], used_queries


async def _search_serpapi(topic: str, limit: int = 5) -> list[WebFact]:
    settings = get_settings()
    key = (getattr(settings, "serpapi_api_key", "") or "").strip()
    if not key or key.startswith("your_"):
        return []

    params = {
        "engine": "google",
        "q": topic,
        "api_key": key,
        "hl": "ko",
        "gl": "kr",
        "num": limit,
        "tbm": "nws",
    }
    async with httpx.AsyncClient(timeout=40.0) as client:
        resp = await client.get("https://serpapi.com/search.json", params=params)
        resp.raise_for_status()
        data = resp.json() or {}

    rows = data.get("news_results") or data.get("organic_results") or []
    out: list[WebFact] = []
    for row in rows:
        title = _clean(str(row.get("title") or ""))
        snip = _clean(str(row.get("snippet") or row.get("content") or ""))
        url = str(row.get("link") or row.get("url") or "")
        if not _is_useful(title, snip, topic):
            continue
        out.append(WebFact(title=title, snippet=snip, url=url, source="serpapi"))
    return out[:limit]


def _search_duckduckgo_sync(topic: str, limit: int = 8) -> list[WebFact]:
    """DuckDuckGo via ddgs (no API key)."""
    try:
        from ddgs import DDGS
    except ImportError:
        try:
            from duckduckgo_search import DDGS  # type: ignore
        except ImportError as exc:
            raise RuntimeError(
                "ddgs 미설치. 실행: pip install ddgs"
            ) from exc

    queries = _search_queries_for_topic(topic)
    out: list[WebFact] = []
    seen: set[str] = set()

    with DDGS() as ddgs:
        for q in queries:
            try:
                rows = list(
                    ddgs.text(
                        q,
                        region="kr-kr",
                        safesearch="on",
                        max_results=limit,
                    )
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("DuckDuckGo query failed (%s) for %r", exc, q)
                continue
            for row in rows:
                title = _clean(str(row.get("title") or ""))
                snip = _clean(str(row.get("body") or row.get("snippet") or ""))
                url = str(row.get("href") or row.get("link") or "")
                key = re.sub(r"\s+", "", title.lower())
                if not key or key in seen:
                    continue
                if not _is_useful(title, snip, topic):
                    continue
                seen.add(key)
                out.append(
                    WebFact(title=title, snippet=snip, url=url, source="duckduckgo")
                )
                if len(out) >= limit:
                    return out
    return out


async def _search_duckduckgo(topic: str, limit: int = 8) -> list[WebFact]:
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, lambda: _search_duckduckgo_sync(topic, limit))


async def fetch_live_web_facts(
    topic: str,
    *,
    limit: int = 3,
    days: int | None = None,
) -> WebFactBundle:
    """Chain: Tavily → SerpAPI → DuckDuckGo (optional).

    ``days`` restricts Tavily to recent news (default from ``TAVILY_DAYS``, usually 30).
    When Tavily key is configured and ``TAVILY_SKIP_DDG_FALLBACK=true``, DuckDuckGo is
    never called.
    """
    topic = (topic or "").strip()
    if not topic:
        raise ValueError("topic is required for web fact search")

    settings = get_settings()
    tavily_days = max(1, int(days if days is not None else settings.tavily_days or 30))
    skip_ddg = bool(settings.tavily_skip_ddg_fallback) and _tavily_configured()

    providers: list[tuple[str, Any]] = []
    if _tavily_configured():
        providers.append(("tavily", "tavily"))
    if _api_key_configured(settings.serpapi_api_key):
        providers.append(("serpapi", "serpapi"))
    if not skip_ddg:
        providers.append(("duckduckgo", "duckduckgo"))

    collected: list[WebFact] = []
    used = ""
    query_used = topic
    tavily_queries: list[str] = []

    for name, kind in providers:
        try:
            if kind == "tavily":
                rows, tavily_queries = await _search_tavily(
                    topic, limit=max(limit + 2, 5), days=tavily_days
                )
            elif kind == "serpapi":
                rows = await _search_serpapi(topic, limit=max(limit + 2, 5))
            else:
                rows = await _search_duckduckgo(topic, limit=max(limit + 2, 5))
        except Exception as exc:  # noqa: BLE001
            logger.warning("Web search provider %s failed (%s)", name, exc)
            continue
        if not rows:
            logger.info("Web search provider %s returned empty", name)
            continue
        used = name
        collected = rows
        if tavily_queries:
            query_used = " | ".join(tavily_queries[:4])
        logger.info("Web facts from %s – %d hits for %r", name, len(rows), topic)
        break

    # Top up with DuckDuckGo only when DDG fallback is allowed
    if not skip_ddg and used and used != "duckduckgo" and len(collected) < limit:
        try:
            extra = await _search_duckduckgo(topic, limit=limit + 2)
            seen = {re.sub(r"\s+", "", f.title.lower()) for f in collected}
            for f in extra:
                key = re.sub(r"\s+", "", f.title.lower())
                if key in seen:
                    continue
                collected.append(f)
                if len(collected) >= limit:
                    break
            if not used and collected:
                used = "duckduckgo"
        except Exception as exc:  # noqa: BLE001
            logger.warning("DuckDuckGo top-up failed (%s)", exc)

    facts = collected[:limit]
    if len(facts) < limit:
        logger.warning(
            "Web facts shortfall (%d/%d, days=%d) for %r – LLM must not invent tips",
            len(facts),
            limit,
            tavily_days,
            topic,
        )

    return WebFactBundle(
        topic=topic,
        facts=facts,
        provider=used or "none",
        query_used=query_used,
        days=tavily_days,
    )
