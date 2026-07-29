"""Card-news end-to-end: script → real photos → retina PNGs → ZIP.

MoviePy / video assembly is intentionally removed from this path.
Primary deliverable = ultra-HD PNG pack (2160×3840) as card_slides.zip.
"""

from __future__ import annotations

import io
import zipfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

from modules.card_assets import fetch_backgrounds_for_slides
from modules.card_render import render_slides_to_pngs
from modules.card_script import generate_card_script
from modules.project import save_json
from modules.utils import ensure_dir, get_logger, load_brand_config

logger = get_logger(__name__)
ProgressFn = Callable[[int, str], None]


def _emit(on_progress: ProgressFn | None, pct: int, message: str) -> None:
    logger.info("[card %d%%] %s", pct, message)
    if on_progress:
        try:
            on_progress(pct, message)
        except Exception:  # noqa: BLE001
            pass


def build_slides_zip_bytes(pngs: list[Path]) -> bytes:
    """Pack PNGs into an in-memory ZIP for Streamlit download_button."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for p in pngs:
            zf.write(p, arcname=p.name)
    return buf.getvalue()


def _zip_slides(pngs: list[Path], zip_path: Path) -> Path:
    ensure_dir(zip_path.parent)
    data = build_slides_zip_bytes(pngs)
    zip_path.write_bytes(data)
    return zip_path


async def render_card_news_project(
    topic: str,
    *,
    selected_hook: str = "",
    research_facts: list[str] | None = None,
    reference=None,
    project_dir: Path,
    use_tts: bool = False,  # unused – kept for API compat
    brand_color: str = "#fc4d01",
    highlight_mode: str = "color",
    draft_mode: bool = False,  # unused
    make_video: bool = False,  # ignored – video path removed
    on_progress: ProgressFn | None = None,
    ig_handle: str = "",
) -> Path:
    """Card script → hybrid backgrounds → retina PNG → ZIP.

    Returns path to ``card_slides.zip``.
    """
    if make_video:
        logger.info("make_video ignored – card_news outputs PNG/ZIP only")

    brand = load_brand_config()
    logo = (
        brand.get("card_news", {}).get("brand_name")
        or (brand.get("brand", {}) or {}).get("name")
        or "WITHCHOYOOL"
    )
    default_color = brand.get("card_news", {}).get("brand_color", "#fc4d01")
    color = brand_color or default_color
    facts = research_facts or []

    _emit(on_progress, 42, "웹 검색 팩트 수집 + 카드뉴스 대본 생성 중...")
    script = await generate_card_script(
        topic=topic,
        research_facts=facts,
        selected_hook=selected_hook or topic,
        reference=reference,
    )
    save_json(project_dir, "script.json", script.to_dict())
    web_meta = getattr(script, "_web_facts", None)
    if web_meta is not None:
        save_json(project_dir, "web_facts.json", web_meta.to_dict())
        _emit(
            on_progress,
            48,
            f"웹 팩트 {len(web_meta.facts)}건 ({web_meta.provider})",
        )
    slides = [s.to_dict() for s in script.slides]
    _emit(on_progress, 50, f"슬라이드 {len(slides)}장 확정 (source={script.source})")

    _emit(on_progress, 55, "표지(COVER) 실사 우선 (og → CSE/Pexels → Fal)...")
    photos_dir = ensure_dir(project_dir / "card_photos")
    article_urls: list[str] = []
    if web_meta is not None:
        article_urls = [
            str(f.url).strip()
            for f in (web_meta.facts or [])
            if str(getattr(f, "url", "") or "").strip().startswith("http")
        ]
    bg_results = await fetch_backgrounds_for_slides(
        slides,
        photos_dir,
        topic=topic,
        article_urls=article_urls,
    )
    backgrounds = [r.path for r in bg_results]
    save_json(
        project_dir,
        "assets.json",
        [
            {
                "search_query": r.search_query,
                "image_prompt": r.image_prompt,
                "path": str(r.path),
                "source": r.source,
            }
            for r in bg_results
        ],
    )
    sources = ", ".join(sorted({r.source for r in bg_results}))
    _emit(on_progress, 62, f"배경 {len(backgrounds)}장 ({sources})")

    frames_dir = ensure_dir(project_dir / "card_frames")
    _emit(on_progress, 68, "레티나 PNG 합성 중 (Insight · COVER/CONTENT/SUMMARY)...")
    pngs = await render_slides_to_pngs(
        slides,
        backgrounds,
        frames_dir,
        brand_color=color,
        logo=logo,
        highlight_mode=highlight_mode,
        ig_handle=ig_handle,
    )

    _emit(on_progress, 88, "초고화질 PNG ZIP 패키징 중...")
    zip_path = _zip_slides(pngs, project_dir / "card_slides.zip")
    save_json(
        project_dir,
        "card_output.json",
        {
            "frames_dir": str(frames_dir),
            "slides_zip": str(zip_path),
            "slide_count": len(pngs),
            "resolution": "2160x3840",
            "device_scale_factor": 2,
            "make_video": False,
        },
    )
    _emit(on_progress, 92, f"카드 {len(pngs)}장 PNG ZIP 완료")
    return zip_path
