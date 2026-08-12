"""Card slide rendering - retina PNG via PIL (COVER / CONTENT / SUMMARY).

Primary path: modules.card_pil_render.render_all_pil
Legacy Playwright HTML capture is no longer used by the card_news pipeline.
"""

from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from modules.utils import ensure_dir, get_logger

logger = get_logger(__name__)

CARD_WIDTH = 1080
CARD_HEIGHT = 1350
DEVICE_SCALE = 2  # -> 2160x2700 (feed) or 2160x3840 (reels) via active format


_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="card-render")


def _render_all_sync(
    slides: list[dict[str, Any]],
    backgrounds: list[Path],
    output_dir: Path,
    brand_color: str,
    logo: str,
    highlight_mode: str = "color",
    ig_handle: str = "",
    source_credit: str = "",
    type_style: dict[str, str] | None = None,
    highlight_color: str = "",
    image_format: str = "",
) -> list[Path]:
    """Sync entry used by card_capture_worker (PIL, not Playwright)."""
    import inspect

    del highlight_mode  # legacy compat
    from modules.card_format import set_active_card_format
    from modules.card_pil_render import render_all_pil

    if image_format:
        set_active_card_format(image_format)

    ensure_dir(output_dir)
    kwargs: dict[str, Any] = {
        "brand_color": brand_color or "#fc4d01",
        "logo": logo,
        "ig_handle": ig_handle,
        "source_credit": source_credit,
        "type_style": type_style,
        "highlight_color": highlight_color,
    }
    # Older in-memory / Cloud workers may not accept image_format yet
    if "image_format" in inspect.signature(render_all_pil).parameters:
        kwargs["image_format"] = image_format
    return render_all_pil(slides, backgrounds, output_dir, **kwargs)


async def render_slides_to_pngs(
    slides: list[dict[str, Any]],
    backgrounds: list[Path],
    output_dir: Path,
    *,
    brand_color: str = "#fc4d01",
    logo: str = "authority",
    highlight_mode: str = "color",
    ig_handle: str = "",
    source_credit: str = "",
    type_style: dict[str, str] | None = None,
    highlight_color: str = "",
    image_format: str = "",
) -> list[Path]:
    """Render slides to retina PNGs via PIL Insight layouts."""
    if not backgrounds:
        raise ValueError("No background images for card slides")

    ensure_dir(output_dir)
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(
        _executor,
        lambda: _render_all_sync(
            slides,
            backgrounds,
            output_dir,
            brand_color,
            logo,
            highlight_mode,
            ig_handle,
            source_credit,
            type_style,
            highlight_color,
            image_format,
        ),
    )
