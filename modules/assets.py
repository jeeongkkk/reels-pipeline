"""Module 4: Visual Asset Fetching.

Uses Pexels when API key is set; otherwise generates local placeholder B-roll.
Skips mostly-blank / white-screen clips that look unfinished.
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from pathlib import Path

import httpx
import numpy as np

from modules.utils import ensure_dir, get_logger, get_settings

logger = get_logger(__name__)

# Queries that often return blank phone UIs
_BAD_QUERY_PARTS = ("blank", "white screen", "empty screen", "smartphone screen", "phone screen")


@dataclass
class VideoAsset:
    keyword: str
    local_path: Path
    pexels_id: int | None = None
    duration: float = 0.0
    source: str = "unknown"


def parse_visual_keyword(marker: str) -> str:
    if "Visual:" in marker:
        return marker.split("Visual:")[-1].strip().rstrip("]")
    return marker.strip()


def normalize_pexels_query(keyword: str) -> str:
    """Force English-ish stock queries; fall back to brand prefer list."""
    raw = (keyword or "").strip()
    low = raw.lower()
    if any(b in low for b in _BAD_QUERY_PARTS):
        raw = "laptop typing marketing office"

    ascii_ratio = sum(1 for c in raw if ord(c) < 128) / max(len(raw), 1)
    if ascii_ratio < 0.6 or len(raw) < 3:
        try:
            from modules.utils import load_brand_config

            prefer = load_brand_config().get("visual", {}).get("prefer") or []
            if prefer:
                idx = abs(hash(raw)) % len(prefer)
                return prefer[idx]
        except Exception:  # noqa: BLE001
            pass
        return "business meeting office"

    cleaned = re.sub(r"[^\w\s\-]", " ", raw)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned or "business meeting office"


def is_mostly_blank_video(path: Path, sample_t: float = 0.5) -> bool:
    """Detect near-white / empty UI frames that look unfinished on Reels."""
    try:
        from moviepy import VideoFileClip

        clip = VideoFileClip(str(path))
        t = min(max(sample_t, 0.1), max(clip.duration - 0.05, 0.1))
        frame = clip.get_frame(t)
        clip.close()
        # mean brightness + low contrast → blank
        mean = float(np.mean(frame))
        std = float(np.std(frame))
        if mean > 225 and std < 28:
            logger.warning("Blank-ish clip skipped: %s (mean=%.1f std=%.1f)", path.name, mean, std)
            return True
        if mean > 245:
            return True
    except Exception as exc:  # noqa: BLE001
        logger.debug("Blank check failed for %s: %s", path, exc)
    return False


async def _generate_placeholder(keyword: str, output_path: Path, duration: float = 3.0) -> VideoAsset:
    """Create a simple 9:16 color clip so pipeline works without Pexels."""
    from moviepy import ColorClip

    ensure_dir(output_path.parent)
    h = abs(hash(keyword)) % (256 * 256 * 256)
    color = ((h >> 16) & 255, (h >> 8) & 255, h & 255)
    color = tuple(max(20, int(c * 0.35)) for c in color)

    clip = ColorClip(size=(1080, 1920), color=color).with_duration(duration)
    clip.write_videofile(str(output_path), fps=24, codec="libx264", audio=False, logger=None)
    clip.close()
    logger.info("Placeholder B-roll: %s -> %s", keyword, output_path)
    return VideoAsset(keyword=keyword, local_path=output_path, duration=duration, source="placeholder")


async def fetch_pexels_video(keyword: str, output_dir: Path, attempt: int = 0) -> VideoAsset | None:
    settings = get_settings()
    ensure_dir(output_dir)
    query = normalize_pexels_query(keyword)
    safe = re.sub(r"[^\w\-]+", "_", query)[:40] or "clip"
    out = output_dir / f"{safe}_{attempt}.mp4" if attempt else output_dir / f"{safe}.mp4"

    api_key = settings.pexels_api_key
    if not api_key or api_key.startswith("your_"):
        return await _generate_placeholder(query, out)

    headers = {"Authorization": api_key}
    params = {"query": query, "orientation": "portrait", "per_page": 8, "size": "medium"}

    try:
        async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
            resp = await client.get("https://api.pexels.com/videos/search", headers=headers, params=params)
            resp.raise_for_status()
            data = resp.json()
            videos = data.get("videos") or []
            if not videos:
                logger.warning("No Pexels results for %r – placeholder", query)
                return await _generate_placeholder(query, out)

            # Try several results until one is not blank
            for video in videos[:6]:
                files = sorted(
                    video.get("video_files") or [],
                    key=lambda f: abs((f.get("width") or 0) - 1080),
                )
                file_url = files[0].get("link") if files else None
                if not file_url:
                    continue
                candidate = output_dir / f"{safe}_{video.get('id', attempt)}.mp4"
                async with client.stream("GET", file_url) as stream:
                    stream.raise_for_status()
                    with open(candidate, "wb") as f:
                        async for chunk in stream.aiter_bytes():
                            f.write(chunk)

                if is_mostly_blank_video(candidate):
                    try:
                        candidate.unlink(missing_ok=True)
                    except OSError:
                        pass
                    continue

                logger.info("Pexels OK query=%r id=%s -> %s", query, video.get("id"), candidate)
                return VideoAsset(
                    keyword=query,
                    local_path=candidate,
                    pexels_id=video.get("id"),
                    duration=float(video.get("duration") or 0),
                    source="pexels",
                )

            logger.warning("All Pexels candidates blank for %r – placeholder", query)
            return await _generate_placeholder(query, out)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Pexels fetch failed (%s) – placeholder", exc)
        return await _generate_placeholder(query, out)


def _extra_cut_queries() -> list[str]:
    try:
        from modules.utils import load_brand_config

        prefer = list(load_brand_config().get("visual", {}).get("prefer") or [])
    except Exception:  # noqa: BLE001
        prefer = []
    defaults = [
        "business meeting office",
        "laptop typing marketing",
        "team collaboration whiteboard",
        "office hallway walk",
        "coffee shop laptop work",
        "handshake closeup business",
        "marketing analytics laptop",
        "brainstorm sticky notes",
    ]
    merged: list[str] = []
    for q in prefer + defaults:
        if q and q not in merged:
            merged.append(q)
    return merged


async def fetch_assets_for_markers(
    visual_markers: list[str],
    output_dir: Path,
    min_clips: int = 6,
) -> list[VideoAsset]:
    """Fetch B-roll for markers, topping up to min_clips for fast cuts."""
    assets: list[VideoAsset] = []
    seen: set[str] = set()

    for marker in visual_markers:
        keyword = parse_visual_keyword(marker)
        if not keyword:
            continue
        q = normalize_pexels_query(keyword)
        if q in seen:
            continue
        seen.add(q)
        asset = await fetch_pexels_video(keyword, output_dir)
        if asset:
            assets.append(asset)

    # Top up for fast-cut pacing
    for i, q in enumerate(_extra_cut_queries()):
        if len(assets) >= min_clips:
            break
        if q in seen:
            continue
        seen.add(q)
        asset = await fetch_pexels_video(q, output_dir, attempt=100 + i)
        if asset:
            assets.append(asset)

    if not assets:
        assets.append(await _generate_placeholder("default office", output_dir / "default.mp4"))
    return assets
