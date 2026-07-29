"""Card news backgrounds – real photo first, Fal Flux last-resort.

COVER waterfall: article og:image → Google CSE / Pexels → Fal.ai.
CONTENT/SUMMARY: solid white (Insight template).
"""

from __future__ import annotations

import base64
import hashlib
import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote, urljoin, urlparse

import httpx
from PIL import Image

from modules.card_visuals import (
    CORPORATE_BG_BASE,
    CORPORATE_BG_NEGATIVE,
    build_image_prompt,
    build_query_candidates,
    score_photo_relevance,
)
from modules.utils import ROOT_DIR, ensure_dir, get_logger, get_settings

logger = get_logger(__name__)

TEMPLATES_DIR = ROOT_DIR / "templates"

_OG_IMAGE_RE = re.compile(
    r'<meta[^>]+(?:property|name)=["\'](?:og:image|twitter:image(?::src)?)["\'][^>]+content=["\']([^"\']+)["\']'
    r'|<meta[^>]+content=["\']([^"\']+)["\'][^>]+(?:property|name)=["\'](?:og:image|twitter:image(?::src)?)["\']'
    r'|<link[^>]+rel=["\']image_src["\'][^>]+href=["\']([^"\']+)["\']',
    re.I,
)


def _safe_print(msg: str) -> None:
    """Print without crashing on Windows cp949 consoles."""
    try:
        print(msg, flush=True)
    except UnicodeEncodeError:
        try:
            import sys

            sys.stdout.buffer.write((msg + "\n").encode("utf-8", errors="replace"))
            sys.stdout.flush()
        except Exception:  # noqa: BLE001
            pass

DALLE_STYLE_SUFFIX = ""
DALLE_NEGATIVE = CORPORATE_BG_NEGATIVE

_BANNED_URL = re.compile(
    r"alamy|shutterstock|gettyimages|istockphoto|dreamstime|depositphotos|"
    r"freepik|adobe\.stock|photzy|expertphotography|lumecube|adaptalux|"
    r"monstercampaigns|watermark|police|파출소|wedding|handshake|"
    r"porn|xxx|adult|stock.?photo",
    re.I,
)


@dataclass
class BackgroundResult:
    path: Path
    source: str  # article_og | google_cse | pexels | web_photo | fal_flux | solid | cache | ...
    search_query: str = ""
    image_prompt: str = ""


def _parse_og_image(html: str, page_url: str) -> str | None:
    """Extract absolute og:image / twitter:image URL from HTML."""
    if not html:
        return None
    m = _OG_IMAGE_RE.search(html)
    if not m:
        return None
    raw = (m.group(1) or m.group(2) or m.group(3) or "").strip()
    if not raw:
        return None
    abs_url = urljoin(page_url, raw)
    if not abs_url.startswith(("http://", "https://")):
        return None
    if _BANNED_URL.search(abs_url):
        return None
    # Skip site chrome (logos / icons) – not usable COVER photos
    path_l = (urlparse(abs_url).path or "").lower()
    if re.search(r"logo|favicon|icon[_-]?|sprite|badge|button", path_l):
        return None
    return abs_url


async def _fetch_og_image_url(page_url: str, client: httpx.AsyncClient) -> str | None:
    try:
        resp = await client.get(page_url)
        if resp.status_code >= 400:
            return None
        ctype = (resp.headers.get("content-type") or "").lower()
        if "html" not in ctype and "text" not in ctype and ctype:
            # Some CDNs omit type – still try parse
            if "image/" in ctype:
                return page_url
        return _parse_og_image(resp.text[:250_000], str(resp.url))
    except Exception as exc:  # noqa: BLE001
        logger.debug("og:image fetch miss %s (%s)", page_url[:80], exc)
        return None


async def _cover_from_article_og(article_urls: list[str], dest: Path) -> Path | None:
    """Tier A: download og:image / twitter:image from scraped article pages."""
    urls = []
    seen: set[str] = set()
    for u in article_urls:
        u = (u or "").strip()
        if not u or not u.startswith("http") or u in seen:
            continue
        if _BANNED_URL.search(u):
            continue
        seen.add(u)
        urls.append(u)
    if not urls:
        return None

    tmp = dest.with_name(dest.stem + "_og.tmp")
    async with httpx.AsyncClient(
        timeout=httpx.Timeout(12.0, connect=4.0),
        follow_redirects=True,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml,image/*,*/*;q=0.8",
            "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8",
        },
    ) as client:
        for page in urls[:6]:
            og = await _fetch_og_image_url(page, client)
            if not og:
                continue
            try:
                await _download_url(og, tmp, client)
                if not _is_valid_image(tmp, min_bytes=8_000):
                    tmp.unlink(missing_ok=True)
                    continue
                out = _normalize_to_portrait(tmp)
                if _is_valid_image(out, min_bytes=8_000):
                    logger.info("COVER article og:image OK page=%s", page[:100])
                    return out
            except Exception as exc:  # noqa: BLE001
                logger.debug("og:image download miss (%s)", exc)
                continue
    return None


def _cover_search_queries(topic: str, slide: dict) -> list[str]:
    """Keyword queries for CSE / Pexels (topic + slide fields)."""
    out: list[str] = []

    def _add(q: str) -> None:
        q = re.sub(r"\s+", " ", (q or "").strip())
        if len(q) < 2 or q in out:
            return
        out.append(q)

    _add(topic)
    _add(str(slide.get("search_query") or ""))
    _add(str(slide.get("main_title") or slide.get("hook") or ""))
    for line in slide.get("title_lines") or []:
        _add(str(line))
    for q in build_query_candidates(slide):
        _add(q)
    # English soft fallbacks for Pexels when topic is Korean-only
    prompt = str(slide.get("image_prompt") or "")
    for m in re.finditer(r"[A-Za-z][A-Za-z0-9\- ]{3,40}", prompt):
        _add(m.group(0).strip())
    _add("South Korea business news")
    _add("government policy briefing Korea")
    _add("startup office documents desk")
    return out[:8]


async def _cover_from_cse_pexels(queries: list[str], dest: Path) -> tuple[Path | None, str, str]:
    """Tier B: Google CSE then Pexels real photos. Returns (path, source, query)."""
    for q in queries[:4]:
        try:
            hit = await _search_google_cse(q, dest)
            if hit and _is_valid_image(hit, min_bytes=12_000):
                return hit, "google_cse", q
        except GoogleCseUnavailable as exc:
            logger.warning("COVER CSE unavailable (%s) – jump to Pexels", exc)
            break
        except Exception as exc:  # noqa: BLE001
            logger.warning("COVER CSE miss q=%r (%s)", q, exc)

    # Pexels prefers Latin queries – filter / keep mixed
    pexels_qs = [
        q
        for q in queries
        if re.search(r"[A-Za-z]{3,}", q)
    ] or ["South Korea business news", "editorial news photography"]
    try:
        hit, _url = await _fetch_pexels_best(pexels_qs, dest)
        if hit and _is_valid_image(hit, min_bytes=12_000):
            return hit, "pexels", pexels_qs[0]
    except Exception as exc:  # noqa: BLE001
        logger.warning("COVER Pexels miss (%s)", exc)

    # Soft: DDG image search on best query
    for q in queries[:3]:
        try:
            hit = await _tier1_web_photo(q, dest)
            if hit and _is_valid_image(hit, min_bytes=12_000):
                return hit, "web_photo", q
        except Exception as exc:  # noqa: BLE001
            logger.debug("COVER web_photo miss (%s)", exc)

    return None, "", ""


async def fetch_backgrounds_for_slides(
    slides: list[dict],
    output_dir: Path,
    *,
    topic: str = "",
    article_urls: list[str] | None = None,
) -> list[BackgroundResult]:
    """Insight COVER: article og → CSE/Pexels → Fal. Other slides: white canvas."""
    from modules.fal_images import FalNotConfiguredError, generate_flux_background
    from PIL import Image

    ensure_dir(output_dir)
    cache_dir = ensure_dir(ROOT_DIR / "assets" / "card_backgrounds")
    article_urls = [u for u in (article_urls or []) if (u or "").strip()]
    if not slides:
        path = output_dir / "default.png"
        Image.new("RGB", (1080, 1440), (250, 250, 250)).save(path)
        return [BackgroundResult(path=path, source="solid")]

    results: list[BackgroundResult] = []

    def _solid(dest: Path, rgb: tuple[int, int, int] = (250, 250, 250)) -> Path:
        ensure_dir(dest.parent)
        Image.new("RGB", (1080, 1440), rgb).save(dest)
        return dest

    for i, slide in enumerate(slides):
        st = str(slide.get("slide_type") or "").upper()
        if st in {"TITLE"}:
            st = "COVER"
        is_cover = i == 0 or st == "COVER"
        dest = output_dir / f"slide_{i + 1:02d}_bg.jpg"
        found: Path | None = None
        source = "solid"
        prompt = ""
        used_q = ""

        if is_cover:
            prompt = str(slide.get("image_prompt") or "").strip()
            queries = _cover_search_queries(topic, slide)
            used_q = queries[0] if queries else topic
            cache_key = _cache_key(
                ["insight-cover-v4-real-first", topic, used_q, *article_urls[:2]],
                0,
            )
            cache_dest = cache_dir / f"{cache_key}.jpg"

            _safe_print(
                f"[Insight] COVER 실사 우선: og({len(article_urls)}) → CSE/Pexels → Fal"
            )
            logger.info(
                "[Insight] COVER real-first topic=%r articles=%d queries=%s",
                topic[:80],
                len(article_urls),
                queries[:4],
            )

            # A) Article og:image / thumbnail
            try:
                found = await _cover_from_article_og(article_urls, dest)
                if found:
                    source = "article_og"
                    _safe_print("[Insight] COVER ← 기사 og:image")
            except Exception as exc:  # noqa: BLE001
                logger.warning("COVER article og failed (%s)", exc)

            # B) CSE → Pexels → DDG web photo
            if found is None:
                try:
                    found, source, used_q = await _cover_from_cse_pexels(queries, dest)
                    if found:
                        _safe_print(f"[Insight] COVER ← {source} ({used_q[:60]})")
                except Exception as exc:  # noqa: BLE001
                    logger.warning("COVER CSE/Pexels failed (%s)", exc)

            # C) Fal only as last resort
            if found is None:
                fal_prompt = prompt or (
                    "messy real desk with crumpled receipts, iced coffee cup, "
                    "open laptop half out of frame, natural window light, "
                    "candid iPhone everyday snapshot, 9:16"
                )
                _safe_print("[Insight] COVER 실사 실패 → Fal 생성 (최후)")
                logger.info("[Insight] COVER Fal fallback prompt=%s", fal_prompt[:240])
                try:
                    flux = await generate_flux_background(dest, index=0, prompt=fal_prompt)
                    if flux and _is_valid_image(flux, min_bytes=20_000):
                        found = flux
                        source = "fal_flux"
                except FalNotConfiguredError:
                    logger.warning("FAL_KEY missing – COVER solid dark fallback")
                except Exception as exc:  # noqa: BLE001
                    logger.warning("COVER Fal failed (%s)", exc)

            if found is None:
                found = _solid(dest, (18, 18, 22))
                source = "solid_dark"
            else:
                try:
                    import shutil

                    shutil.copy2(found, cache_dest)
                except OSError:
                    pass
        else:
            found = _solid(dest.with_suffix(".png"), (255, 255, 255))
            source = "solid_white"
            _safe_print(f"[Insight] 슬라이드 {i + 1} ({st or 'CONTENT'}): 단색 배경")

        final = _finalize_slide_bg(found, output_dir, i) if found else found
        results.append(
            BackgroundResult(
                path=final if final else found,  # type: ignore[arg-type]
                source=source,
                search_query=used_q,
                image_prompt=prompt,
            )
        )
        logger.info("Slide %d background: %s", i + 1, source)

    return results


async def fetch_photos_for_slides(visuals: list[str], output_dir: Path) -> list[Path]:
    slides = [
        {
            "slide_type": "COVER" if i == 0 else "CONTENT",
            "image_prompt": f"moody scene {v}",
            "search_query": str(v),
        }
        for i, v in enumerate(visuals)
    ]
    return [
        r.path
        for r in await fetch_backgrounds_for_slides(
            slides, output_dir, topic=str(visuals[0] if visuals else "")
        )
    ]


def _safe_stem(text: str, index: int) -> str:
    raw = re.sub(r"[^\w\-]+", "_", (text or "bg").strip())[:40] or "bg"
    return f"{raw}_{index}"


def _cache_key(queries: list[str], index: int) -> str:
    blob = "|".join(queries[:3]) + f"#{index}"
    return hashlib.sha1(blob.encode("utf-8")).hexdigest()[:12]


def _is_valid_image(path: Path, min_bytes: int = 8_000) -> bool:
    if not path.exists() or path.stat().st_size < min_bytes:
        return False
    try:
        with Image.open(path) as img:
            img.verify()
        with Image.open(path) as img:
            w, h = img.size
            return w >= 400 and h >= 400
    except Exception:  # noqa: BLE001
        return False


def _placeholder_photo(path: Path, seed: str) -> Path:
    from PIL import ImageDraw

    ensure_dir(path.parent)
    h = abs(hash(seed))
    c1 = ((h >> 16) & 255, (h >> 8) & 255, h & 255)
    c2 = (max(10, c1[0] // 3), max(10, c1[1] // 3), max(10, c1[2] // 3))
    img = Image.new("RGB", (1080, 1440), c2)
    draw = ImageDraw.Draw(img)
    for y in range(1440):
        t = y / 1440
        col = tuple(int(c2[i] * (1 - t) + c1[i] * t * 0.35) for i in range(3))
        draw.line([(0, y), (1080, y)], fill=col)
    if path.suffix.lower() == ".png":
        img.save(path)
    else:
        img.save(path, quality=92)
    return path


def _normalize_to_portrait(path: Path, *, as_png: bool = False) -> Path:
    """Center-crop / resize to exactly 1080×1440."""
    try:
        with Image.open(path) as img:
            img = img.convert("RGB")
            tw, th = 1080, 1440
            src_w, src_h = img.size
            target_ratio = tw / th
            src_ratio = src_w / max(src_h, 1)
            if src_ratio > target_ratio:
                new_w = int(src_h * target_ratio)
                left = (src_w - new_w) // 2
                img = img.crop((left, 0, left + new_w, src_h))
            else:
                new_h = int(src_w / target_ratio)
                top = (src_h - new_h) // 2
                img = img.crop((0, top, src_w, top + new_h))
            img = img.resize((tw, th), Image.Resampling.LANCZOS)
            out = path.with_suffix(".png" if as_png else ".jpg")
            if as_png:
                img.save(out, optimize=True)
            else:
                img.save(out, quality=92, optimize=True)
            if out != path and path.exists():
                try:
                    path.unlink(missing_ok=True)
                except OSError:
                    pass
            return out
    except Exception as exc:  # noqa: BLE001
        logger.warning("Portrait normalize failed (%s) – keeping original", exc)
        return path


def _finalize_slide_bg(src: Path, output_dir: Path, index: int) -> Path:
    """Always write 1080×1440 PNG to templates/slide_bg_{n}.png + project copy."""
    ensure_dir(TEMPLATES_DIR)
    ensure_dir(output_dir)
    n = index + 1
    template_path = TEMPLATES_DIR / f"slide_bg_{n}.png"
    project_path = output_dir / f"slide_bg_{n}.png"

    normalized = _normalize_to_portrait(src, as_png=True)
    try:
        with Image.open(normalized) as img:
            img = img.convert("RGB").resize((1080, 1440), Image.Resampling.LANCZOS)
            img.save(template_path, optimize=True)
            img.save(project_path, optimize=True)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Finalize slide_bg failed (%s) – copy raw", exc)
        try:
            import shutil

            shutil.copy2(normalized, template_path)
            shutil.copy2(normalized, project_path)
        except OSError:
            return normalized
    return project_path


def _keyword_query(image_prompt: str, search_query: str, slide: dict) -> str:
    """Extract English search keywords from prompt / slide fields."""
    raw = " ".join(
        [
            image_prompt or "",
            search_query or "",
            str(slide.get("hook") or ""),
            str(slide.get("tag") or ""),
        ]
    )
    # Prefer Latin tokens for image search engines
    tokens = re.findall(r"[A-Za-z][A-Za-z0-9\-]{2,}", raw)
    stop = {
        "abstract",
        "unique",
        "composition",
        "variant",
        "frame",
        "slide",
        "with",
        "and",
        "the",
        "for",
        "from",
        "this",
        "that",
        "dark",
        "high",
        "end",
    }
    picked: list[str] = []
    for t in tokens:
        low = t.lower()
        if low in stop or low in picked:
            continue
        picked.append(low)
        if len(picked) >= 6:
            break
    if picked:
        return " ".join(picked)
    # Fallback: Korean stripped prompt words
    ko = re.sub(r"[^\w\s가-힣]", " ", raw)
    ko = re.sub(r"\s+", " ", ko).strip()
    return (ko[:80] or "editorial photography dark").strip()


async def _download_url(url: str, dest: Path, client: httpx.AsyncClient) -> Path:
    ensure_dir(dest.parent)
    async with client.stream("GET", url) as stream:
        stream.raise_for_status()
        with open(dest, "wb") as f:
            async for chunk in stream.aiter_bytes():
                f.write(chunk)
    return dest


def _ddg_image_urls_sync(query: str, limit: int = 6) -> list[str]:
    """DuckDuckGo image search (sync) – top high-res candidates."""
    try:
        from ddgs import DDGS
    except ImportError:
        try:
            from duckduckgo_search import DDGS  # type: ignore
        except ImportError:
            return []

    urls: list[str] = []
    seen: set[str] = set()
    try:
        with DDGS() as ddgs:
            rows = list(
                ddgs.images(
                    query,
                    region="wt-wt",
                    safesearch="on",
                    max_results=limit,
                )
            )
    except Exception as exc:  # noqa: BLE001
        logger.warning("DuckDuckGo images failed (%s)", exc)
        return []

    for row in rows:
        url = str(row.get("image") or row.get("url") or row.get("thumbnail") or "").strip()
        if not url or url in seen:
            continue
        if _BANNED_URL.search(url):
            continue
        seen.add(url)
        urls.append(url)
    return urls


async def _tier1_web_photo(query: str, dest: Path) -> Path | None:
    """Tier 1: real photo scrape (DDG → optional Unsplash). Hard 5s budget."""
    import asyncio

    settings = get_settings()
    q = (query or "").strip() or "editorial photography"
    tmp = dest.with_name(dest.stem + "_web.tmp")

    async def _run() -> Path | None:
        loop = asyncio.get_running_loop()
        urls = await loop.run_in_executor(None, lambda: _ddg_image_urls_sync(q, 6))

        # Optional Unsplash (no-key source endpoint as soft fallback URL)
        unsplash_key = (getattr(settings, "unsplash_access_key", "") or "").strip()
        if unsplash_key and not unsplash_key.startswith("your_"):
            try:
                async with httpx.AsyncClient(timeout=3.0) as client:
                    resp = await client.get(
                        "https://api.unsplash.com/search/photos",
                        params={"query": q, "per_page": 3, "orientation": "portrait"},
                        headers={"Authorization": f"Client-ID {unsplash_key}"},
                    )
                    if resp.status_code < 400:
                        for row in (resp.json() or {}).get("results") or []:
                            u = (
                                ((row.get("urls") or {}).get("regular"))
                                or ((row.get("urls") or {}).get("full"))
                                or ""
                            )
                            if u and u not in urls:
                                urls.insert(0, u)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Unsplash search skipped (%s)", exc)

        if not urls:
            return None

        async with httpx.AsyncClient(
            timeout=httpx.Timeout(4.0, connect=2.0),
            follow_redirects=True,
            headers={"User-Agent": "Mozilla/5.0 (compatible; AuthorityReels/1.0)"},
        ) as client:
            for url in urls[:5]:
                try:
                    await _download_url(url, tmp, client)
                    if _is_valid_image(tmp, min_bytes=12_000):
                        score = score_photo_relevance(alt=q, query=q, url=url)
                        if score < 0:
                            continue
                        out = _normalize_to_portrait(tmp)
                        logger.info("Tier1 web photo OK q=%r -> %s", q, out.name)
                        return out
                except Exception as exc:  # noqa: BLE001
                    logger.debug("Web photo download miss (%s)", exc)
                    continue
        return None

    try:
        return await asyncio.wait_for(_run(), timeout=5.0)
    except asyncio.TimeoutError:
        logger.warning("Tier1 web photo timed out (>5s) for %r", q)
        return None
    except Exception as exc:  # noqa: BLE001
        logger.warning("Tier1 web photo error (%s)", exc)
        return None


class GoogleCseUnavailable(RuntimeError):
    """CSE key/quota/permission blocked – skip remaining CSE attempts."""


async def _search_google_cse(search_query: str, dest: Path) -> Path | None:
    """Google Custom Search – CC-friendly real photos."""
    settings = get_settings()
    api_key = (settings.google_cse_api_key or "").strip()
    cx = (settings.google_cse_cx or "").strip()
    if not api_key or api_key.startswith("your_") or not cx or cx.startswith("your_"):
        logger.info("Google CSE not configured – skip for %r", search_query)
        raise GoogleCseUnavailable("CSE not configured")

    q = (search_query or "").strip()
    if not q:
        return None

    # CC-friendly first, then unrestricted if empty (still real photos)
    param_sets = [
        {
            "key": api_key,
            "cx": cx,
            "q": q,
            "searchType": "image",
            "rights": "cc_publicdomain,cc_attribute,cc_sharealike,cc_noncommercial,cc_nonderived",
            "imgSize": "large",
            "safe": "active",
            "num": 8,
        },
        {
            "key": api_key,
            "cx": cx,
            "q": q,
            "searchType": "image",
            "imgSize": "large",
            "safe": "active",
            "num": 8,
        },
    ]

    async with httpx.AsyncClient(timeout=40.0, follow_redirects=True) as client:
        for params in param_sets:
            try:
                resp = await client.get(
                    "https://www.googleapis.com/customsearch/v1", params=params
                )
                resp.raise_for_status()
                items = (resp.json() or {}).get("items") or []
            except httpx.HTTPStatusError as exc:
                code = exc.response.status_code if exc.response is not None else 0
                logger.warning("Google CSE HTTP %s", code)
                if code in {403, 429}:
                    raise GoogleCseUnavailable(f"CSE HTTP {code}") from exc
                continue
            except Exception as exc:  # noqa: BLE001
                logger.warning("Google CSE request failed (%s)", exc)
                continue

            ranked: list[tuple[float, str, str]] = []
            for item in items:
                link = (item.get("link") or "").strip()
                if not link:
                    continue
                title = str(item.get("title") or "")
                snippet = str(item.get("snippet") or "")
                score = score_photo_relevance(
                    alt=f"{title} {snippet}",
                    query=q,
                    url=link,
                )
                ranked.append((score, link, title))
            ranked.sort(key=lambda x: x[0], reverse=True)

            for score, link, title in ranked:
                if score < 0:
                    continue
                try:
                    tmp = dest.with_name(dest.stem + "_dl.tmp")
                    await _download_url(link, tmp, client)
                    if not _is_valid_image(tmp):
                        tmp.unlink(missing_ok=True)
                        continue
                    normalized = _normalize_to_portrait(tmp)
                    final = dest.with_suffix(".jpg")
                    if normalized.resolve() != final.resolve():
                        if final.exists():
                            final.unlink(missing_ok=True)
                        normalized.replace(final)
                    if _is_valid_image(final):
                        logger.info(
                            "Google CSE OK q=%r score=%.1f -> %s",
                            q,
                            score,
                            final,
                        )
                        return final
                except Exception as exc:  # noqa: BLE001
                    logger.warning("Google CSE download failed (%s)", exc)
                    continue
    return None


def _corporate_bg_prompt(index: int = 0) -> str:
    """Premium conceptual magazine fallback – never suited business people."""
    scenes = (
        "Monocle magazine style, empty sunlit glass atrium, polished concrete, no people",
        "Wired magazine style, macro precision chip and PCB glow, cool neon, no people",
        "Vogue magazine beauty editorial, macro cosmetic cream texture on marble, no people",
        "Architectural Digest style, brushed steel and frosted glass joint macro, no people",
        "Forbes magazine still life, fountain pen and sealed documents on walnut, empty desk, no people",
        "Wired magazine style, server-rack LED bokeh cable texture, no faces",
        "Monocle magazine style, marble lobby column detail, soft daylight, empty space, no people",
    )
    scene = scenes[index % len(scenes)]
    return f"{scene}, photorealistic magazine editorial, unique scene {index + 1}, 9:16 portrait"


async def _generate_dalle(image_prompt: str, dest: Path) -> Path | None:
    """OpenAI editorial photo fallback when Fal.ai is unavailable."""
    settings = get_settings()
    api_key = (settings.openai_api_key or "").strip()
    if not api_key or api_key.startswith("your_"):
        return None

    base = (image_prompt or CORPORATE_BG_BASE).strip()
    for bad in ("abstract dark", "geometric lines only", "empty black void", "flat gradient"):
        if bad in base.lower():
            base = CORPORATE_BG_BASE
            break
    if not any(
        m in base.lower()
        for m in ("magazine", "vogue", "wired", "forbes", "monocle", "editorial")
    ):
        base = f"{base}, Forbes or Monocle magazine style, warm professional lighting"
    base = f"{base}. Avoid: {CORPORATE_BG_NEGATIVE}"

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    attempts = (
        {
            "model": "gpt-image-1",
            "prompt": base[:32000],
            "n": 1,
            "size": "1024x1536",
            "quality": "medium",
        },
    )

    try:
        async with httpx.AsyncClient(timeout=180.0) as client:
            for payload in attempts:
                resp = await client.post(
                    "https://api.openai.com/v1/images/generations",
                    headers=headers,
                    json=payload,
                )
                if resp.status_code >= 400:
                    logger.warning(
                        "OpenAI image %s failed (%s): %s",
                        payload["model"],
                        resp.status_code,
                        resp.text[:300],
                    )
                    continue
                data = (resp.json() or {}).get("data") or []
                if not data:
                    continue
                ensure_dir(dest.parent)
                b64 = data[0].get("b64_json")
                url = data[0].get("url")
                if b64:
                    dest.write_bytes(base64.b64decode(b64))
                elif url:
                    await _download_url(url, dest, client)
                else:
                    continue
                dest = _normalize_to_portrait(dest)
                if _is_valid_image(dest, min_bytes=20_000):
                    logger.info("OpenAI image OK (%s) -> %s", payload["model"], dest.name)
                    return dest
    except Exception as exc:  # noqa: BLE001
        logger.warning("OpenAI image generation failed (%s)", exc)
    return None


async def _fetch_pexels_best(
    queries: list[str],
    dest: Path,
    *,
    exclude_urls: set[str] | None = None,
    page_offset: int = 0,
) -> tuple[Path | None, str]:
    """Try several queries; pick the highest alt-text relevance photo."""
    settings = get_settings()
    api_key = settings.pexels_api_key
    if not api_key or api_key.startswith("your_"):
        return None, ""

    exclude_urls = exclude_urls or set()
    headers = {"Authorization": api_key}
    best: tuple[float, str, str] | None = None  # score, url, query

    async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
        for qi, query in enumerate(queries[:5]):
            q = re.sub(r"\s+", " ", query.strip()) or "news editorial"
            if re.search(r"[가-힣]", q) and not re.search(r"[A-Za-z]{3,}", q):
                continue
            params = {
                "query": q,
                "orientation": "portrait",
                "per_page": 15,
                "page": 1 + ((page_offset + qi) % 4),
                "size": "large",
            }
            try:
                resp = await client.get(
                    "https://api.pexels.com/v1/search", headers=headers, params=params
                )
                resp.raise_for_status()
                photos = (resp.json() or {}).get("photos") or []
            except Exception as exc:  # noqa: BLE001
                logger.warning("Pexels search failed for %r (%s)", q, exc)
                continue

            for photo in photos:
                alt = str(photo.get("alt") or "")
                src = photo.get("src") or {}
                url = src.get("portrait") or src.get("large2x") or src.get("large")
                if not url or url in exclude_urls:
                    continue
                score = score_photo_relevance(
                    alt=alt,
                    query=q,
                    url=str(photo.get("url") or ""),
                    photographer=str((photo.get("photographer") or "")),
                )
                # Require at least weak positive match, or accept top result
                # of a strong topical query with score >= 0
                if score < 0:
                    continue
                # Weak/empty alt matches are how police stations sneak in
                if score < 2.0:
                    continue
                if best is None or score > best[0]:
                    best = (score, url, q)

        if not best:
            # Last attempt: first non-rejected photo of best English topical query
            for query in queries:
                if re.search(r"[가-힣]", query) and not re.search(r"[A-Za-z]{3,}", query):
                    continue
                params = {
                    "query": query,
                    "orientation": "portrait",
                    "per_page": 12,
                    "page": 1 + (page_offset % 4),
                    "size": "large",
                }
                try:
                    resp = await client.get(
                        "https://api.pexels.com/v1/search",
                        headers=headers,
                        params=params,
                    )
                    resp.raise_for_status()
                    photos = (resp.json() or {}).get("photos") or []
                except Exception:  # noqa: BLE001
                    continue
                for photo in photos:
                    alt = str(photo.get("alt") or "")
                    score = score_photo_relevance(alt=alt, query=query)
                    if score < 0:
                        continue
                    src = photo.get("src") or {}
                    url = src.get("portrait") or src.get("large2x") or src.get("large")
                    if url and url not in exclude_urls:
                        best = (score, url, query)
                        break
                if best:
                    break

        if not best:
            return None, ""

        score, url, used_q = best
        try:
            await _download_url(url, dest, client)
            dest = _normalize_to_portrait(dest)
            if _is_valid_image(dest):
                logger.info(
                    "Pexels OK q=%r score=%.1f -> %s", used_q, score, dest
                )
                return dest, url
        except Exception as exc:  # noqa: BLE001
            logger.warning("Pexels download failed (%s)", exc)
    return None, ""




def _slide_requires_real_entity(slide: dict) -> bool:
    """JSON flag wins; only named people/brands stay True."""
    raw = slide.get("requires_real_entity")
    if isinstance(raw, bool):
        return raw
    if str(raw).strip().lower() in {"true", "1", "yes"}:
        return True
    if str(raw).strip().lower() in {"false", "0", "no"}:
        return False
    blob = " ".join(
        str(slide.get(k) or "")
        for k in ("tag", "hook", "body", "search_query", "image_prompt")
    )
    if re.search(
        r"Elon|Musk|Apple|Google|Samsung|Tesla|NVIDIA|머스크|애플|삼성전자|테슬라",
        blob,
        re.I,
    ):
        return True
    return False


def _make_corporate_gradient(dest: Path, index: int) -> Path:
    """Instant dark corporate gradient – no API, no GPU."""
    from PIL import Image, ImageDraw

    ensure_dir(dest.parent)
    w, h = 1080, 1440
    schemes: list[tuple[tuple[int, int, int], tuple[int, int, int], tuple[int, int, int]]] = [
        ((10, 14, 26), (38, 32, 72), (90, 75, 180)),
        ((8, 12, 20), (22, 40, 62), (60, 110, 160)),
        ((14, 16, 22), (48, 38, 58), (140, 120, 90)),
        ((12, 18, 28), (30, 55, 50), (70, 130, 110)),
        ((16, 14, 24), (52, 36, 68), (110, 85, 170)),
        ((10, 16, 24), (35, 42, 58), (85, 100, 140)),
        ((14, 12, 20), (45, 30, 55), (120, 90, 150)),
    ]
    c_top, c_bot, accent = schemes[index % len(schemes)]
    img = Image.new("RGB", (w, h))
    draw = ImageDraw.Draw(img)
    for y in range(h):
        t = y / h
        col = tuple(int(c_top[i] * (1 - t) + c_bot[i] * t) for i in range(3))
        draw.line([(0, y), (w, y)], fill=col)
    # Minimal geometric accent lines
    step = 140 + (index * 17) % 60
    for x in range(-h, w + h, step):
        draw.line([(x, 0), (x + h // 2, h)], fill=accent, width=1)
    for x in range(0, w + h, step * 2):
        draw.line([(x, h), (x - h // 3, 0)], fill=tuple(max(0, c - 40) for c in accent), width=1)
    out = dest if dest.suffix.lower() == ".png" else dest.with_suffix(".png")
    img.save(out, optimize=True)
    return out


async def get_background_image(
    *,
    search_query: str,
    image_prompt: str,
    output_dir: Path,
    index: int = 0,
    slide: dict | None = None,
) -> BackgroundResult:
    """3-tier waterfall: web photo → Pollinations Flux → local SD-Turbo.

    Always finalizes to 1080×1440 PNG at templates/slide_bg_{n}.png.
    Never raises – placeholder is the absolute last resort.
    """
    ensure_dir(output_dir)
    cache_dir = ensure_dir(ROOT_DIR / "assets" / "card_backgrounds")

    slide = slide or {
        "search_query": search_query,
        "image_prompt": image_prompt,
        "hook": "",
        "body": "",
        "tag": "",
        "requires_real_entity": False,
    }

    from modules.card_visuals import _BANNED_SEARCH_FRAGMENTS

    queries = build_query_candidates(slide)
    if search_query and search_query not in queries:
        queries.insert(0, search_query)
    queries = [
        q for q in queries if not any(b in q.lower() for b in _BANNED_SEARCH_FRAGMENTS)
    ] or ["abstract dark editorial macro photography"]

    requires_real = _slide_requires_real_entity(slide)
    if requires_real:
        base_prompt = str(image_prompt or slide.get("image_prompt") or "").strip()
        if not base_prompt:
            hook = str(slide.get("hook") or search_query or "concept")[:80]
            base_prompt = (
                f"Professional editorial photo related to '{hook}', "
                f"variant {index + 1}"
            )
        else:
            base_prompt = f"{base_prompt.rstrip(',. ')}, variant {index + 1}"
        prompt = build_image_prompt({**slide, "image_prompt": base_prompt})
    else:
        prompt = _corporate_bg_prompt(index)
        base_prompt = prompt
    # Real entity → search by person/brand name. Abstract → never search photo stock.
    if requires_real:
        web_q = (
            str(slide.get("search_query") or "").strip()
            or _keyword_query(str(slide.get("hook") or ""), search_query, slide)
        )
    else:
        web_q = _keyword_query(base_prompt, search_query or queries[0], slide)

    key = _cache_key([web_q, "corp-v3", f"real{int(requires_real)}"], index)
    dest = output_dir / f"slide_{index + 1:02d}_{key}.jpg"
    cache_dest = cache_dir / f"{key}.jpg"
    template_png = TEMPLATES_DIR / f"slide_bg_{index + 1}.png"

    # Cache hit (project or template) – skip stale mismatched templates when abstract
    cache_candidates = [dest, cache_dest, output_dir / f"slide_bg_{index + 1}.png"]
    if requires_real:
        cache_candidates.insert(2, template_png)
    for candidate in cache_candidates:
        if _is_valid_image(candidate):
            final = _finalize_slide_bg(candidate, output_dir, index)
            logger.info("Background cache hit (%s)", candidate.name)
            return BackgroundResult(
                path=final,
                source="cache",
                search_query=web_q,
                image_prompt=prompt,
            )

    source = "placeholder"
    found: Path | None = None

    # ── Tier 1: web real photo ONLY for named entities (≤5s) ─
    if requires_real:
        try:
            logger.info("Slide %d Tier1 entity photo q=%r", index + 1, web_q)
            found = await _tier1_web_photo(web_q, dest)
            if found and _is_valid_image(found):
                source = "web_photo"
        except Exception as exc:  # noqa: BLE001
            logger.warning("Tier1 exception (%s)", exc)
            found = None
        if found is None:
            try:
                cse = await _search_google_cse(web_q, dest)
                if cse and _is_valid_image(cse):
                    found, source = cse, "google_cse"
            except Exception as exc:  # noqa: BLE001
                logger.warning("Google CSE miss (%s)", exc)
    else:
        logger.info(
            "Slide %d abstract → skip Tier1 web scrape (avoid stock/macro mismatch)",
            index + 1,
        )

    # ── Tier 2: Fal.ai Flux (card pipeline uses fetch_backgrounds_for_slides instead) ─
    if found is None:
        try:
            from modules.fal_images import generate_flux_background

            found = await generate_flux_background(dest, index=index)
            if found and _is_valid_image(found):
                source = "fal_flux"
        except Exception as exc:  # noqa: BLE001
            logger.warning("Fal.ai exception (%s)", exc)
            found = None

    # Absolute last resort
    if found is None or not _is_valid_image(found):
        found = _placeholder_photo(dest, f"{web_q}-{index}")
        source = "placeholder"

    # Cache + finalize PNG
    try:
        import shutil

        if found.exists():
            shutil.copy2(found, cache_dest)
    except OSError:
        pass

    final = _finalize_slide_bg(found, output_dir, index)
    return BackgroundResult(
        path=final,
        source=source,
        search_query=web_q,
        image_prompt=prompt,
    )


def _make_topic_gradient(dest: Path, slide: dict, index: int) -> Path:
    """Topic-tinted gradient fallback when Flux is unavailable."""
    from PIL import Image, ImageDraw

    from modules.card_visuals import build_slide_bg_prompt

    blob = " ".join(
        str(slide.get(k) or "")
        for k in ("tag", "hook", "body", "source_fact")
    )
    # Color palettes by topic
    if re.search(r"AI|인공지능", blob, re.I):
        c_top, c_bot, accent = (8, 14, 28), (18, 38, 72), (60, 130, 220)
    elif re.search(r"예산|억|조|투자", blob):
        c_top, c_bot, accent = (12, 10, 8), (45, 32, 18), (200, 160, 80)
    elif re.search(r"창업|중소|벤처", blob):
        c_top, c_bot, accent = (8, 18, 16), (22, 52, 42), (70, 180, 130)
    elif re.search(r"경고|방치", blob):
        c_top, c_bot, accent = (18, 8, 10), (55, 22, 18), (220, 100, 70)
    elif str(slide.get("type")) == "cta" or slide.get("tag") == "CTA":
        c_top, c_bot, accent = (14, 10, 28), (48, 32, 88), (130, 100, 240)
    else:
        c_top, c_bot, accent = (10, 14, 26), (38, 32, 72), (90, 75, 180)

    ensure_dir(dest.parent)
    w, h = 1080, 1440
    img = Image.new("RGB", (w, h))
    draw = ImageDraw.Draw(img)
    for y in range(h):
        t = y / h
        # Darker bottom half for text readability
        darken = 1.0 - (t * 0.35)
        col = tuple(
            int((c_top[i] * (1 - t) + c_bot[i] * t) * darken) for i in range(3)
        )
        draw.line([(0, y), (w, y)], fill=col)
    step = 120 + index * 15
    for x in range(-h, w + h, step):
        draw.line([(x, 0), (x + h // 2, h)], fill=accent, width=1)
    out = dest if dest.suffix.lower() == ".png" else dest.with_suffix(".png")
    img.save(out, optimize=True)
    return out
