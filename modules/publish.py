"""Module 6: Publishing – Manual package / Instagrapi / Graph API."""

from __future__ import annotations

import asyncio
import json
import random
import shutil
import time
from dataclasses import asdict, dataclass
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any

from modules.text_quality import enforce_korean_copy
from modules.utils import ROOT_DIR, ensure_dir, get_logger, get_settings, load_brand_config

logger = get_logger(__name__)


class PublishMode(str, Enum):
    MANUAL = "manual"
    INSTAGRAPI = "instagrapi"
    GRAPH_API = "graph_api"


@dataclass
class PublishResult:
    status: str
    mode: str
    video_path: str
    caption: str
    package_dir: str | None = None
    media_id: str | None = None
    message: str = ""
    created_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def build_instagram_caption(
    hook: str,
    body_or_lines: str | list[str],
    cta: str,
    hashtags: list[str] | None = None,
) -> str:
    """Compose a paste-ready Instagram caption."""
    brand = load_brand_config()
    tagline = brand.get("brand", {}).get("tagline", "")

    if isinstance(body_or_lines, list):
        body = "\n".join(f"• {line}" for line in body_or_lines[:5])
    else:
        # Strip visual markers from spoken body for caption text
        import re

        body = re.sub(r"\[\d+\.\s*Visual:[^\]]+\]", "", body_or_lines or "")
        body = re.sub(r"\s+", " ", body).strip()
        if len(body) > 220:
            body = body[:217] + "..."

    default_tags = hashtags or [
        "#B2B마케팅",
        "#인스타릴스",
        "#숏폼마케팅",
        "#마케팅인사이트",
        "#AuthorityReels",
    ]

    parts = [hook.strip(), "", body, "", cta.strip() or tagline, "", " ".join(default_tags)]
    return enforce_korean_copy("\n".join(parts).strip())


def caption_from_script_json(script_data: dict[str, Any]) -> str:
    mode = script_data.get("mode", "voice_tts")
    if mode in ("music_caption", "card_news"):
        lines = script_data.get("caption_lines") or []
        return build_instagram_caption(
            script_data.get("hook", ""),
            lines,
            script_data.get("cta", ""),
        )
    return build_instagram_caption(
        script_data.get("hook", ""),
        script_data.get("body", "") or script_data.get("full_text", ""),
        script_data.get("cta", ""),
    )


def export_manual_package(
    video_path: Path,
    caption: str,
    project_dir: Path | None = None,
) -> Path:
    """Copy final reel + caption into a ready-to-upload folder."""
    video_path = Path(video_path)
    if not video_path.exists():
        raise FileNotFoundError(f"Video not found: {video_path}")

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    if project_dir is None:
        project_dir = video_path.parent
    package_dir = ensure_dir(Path(project_dir) / f"ready_to_upload_{stamp}")

    dest_video = package_dir / "reel.mp4"
    shutil.copy2(video_path, dest_video)
    (package_dir / "caption.txt").write_text(caption, encoding="utf-8")

    meta = {
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "source_video": str(video_path),
        "package_video": str(dest_video),
        "mode": "manual",
        "instructions": [
            "1. Open Instagram app (Reels)",
            "2. Upload reel.mp4 from this folder",
            "3. Paste caption.txt",
            "4. Add cover / music in-app if needed",
        ],
    }
    (package_dir / "publish_meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    logger.info("Manual package ready: %s", package_dir)
    return package_dir


async def publish_reel(
    video_path: Path,
    caption: str,
    project_dir: Path | None = None,
    mode: str | None = None,
) -> dict:
    """Publish a reel using the configured upload mode."""
    settings = get_settings()
    publish_mode = PublishMode((mode or settings.publish_mode or "manual").lower())
    video_path = Path(video_path)
    created = datetime.now().isoformat(timespec="seconds")

    if publish_mode == PublishMode.MANUAL:
        package = export_manual_package(video_path, caption, project_dir)
        result = PublishResult(
            status="ready_manual",
            mode="manual",
            video_path=str(video_path),
            caption=caption,
            package_dir=str(package),
            message="Upload package created. Use Instagram app to post.",
            created_at=created,
        )
        if project_dir:
            (Path(project_dir) / "publish_result.json").write_text(
                json.dumps(result.to_dict(), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        return result.to_dict()

    if publish_mode == PublishMode.INSTAGRAPI:
        result = await _publish_via_instagrapi(video_path, caption)
        result["created_at"] = created
        return result

    if publish_mode == PublishMode.GRAPH_API:
        result = await _publish_via_graph_api(video_path, caption)
        result["created_at"] = created
        return result

    raise ValueError(f"Unknown publish mode: {publish_mode}")


async def _publish_via_instagrapi(video_path: Path, caption: str) -> dict:
    """Upload via instagrapi with session persistence and human-like delays."""
    settings = get_settings()
    username = settings.instagram_username
    password = settings.instagram_password
    session_path = Path(settings.instagram_session_path)
    if not session_path.is_absolute():
        session_path = ROOT_DIR / session_path

    if not username or not password or password.startswith("your_"):
        logger.warning("Instagrapi credentials missing – falling back to manual package")
        package = export_manual_package(video_path, caption, video_path.parent)
        return {
            "status": "fallback_manual",
            "mode": "instagrapi",
            "path": str(video_path),
            "package_dir": str(package),
            "message": "Set INSTAGRAM_USERNAME/PASSWORD or use manual mode.",
        }

    delay = random.uniform(2.0, 5.0)
    logger.info("Human-like pre-upload delay: %.1fs", delay)
    await asyncio.sleep(delay)

    try:
        from instagrapi import Client

        cl = Client()
        if session_path.exists():
            try:
                cl.load_settings(str(session_path))
                cl.login(username, password)
            except Exception:  # noqa: BLE001
                cl = Client()
                cl.login(username, password)
        else:
            cl.login(username, password)

        ensure_dir(session_path.parent)
        cl.dump_settings(str(session_path))

        media = cl.clip_upload(str(video_path), caption)
        media_id = str(getattr(media, "id", None) or getattr(media, "pk", "") or "")
        logger.info("Instagrapi upload OK media_id=%s", media_id)
        return {
            "status": "uploaded",
            "mode": "instagrapi",
            "path": str(video_path),
            "media_id": media_id,
            "caption": caption,
            "message": "Uploaded via instagrapi",
        }
    except Exception as exc:  # noqa: BLE001
        logger.exception("Instagrapi upload failed")
        package = export_manual_package(video_path, caption, video_path.parent)
        return {
            "status": "error_fallback_manual",
            "mode": "instagrapi",
            "path": str(video_path),
            "package_dir": str(package),
            "message": f"Upload failed: {exc}",
        }


async def _publish_via_graph_api(video_path: Path, caption: str) -> dict:
    """Official Meta Graph API path – stub until tokens are configured."""
    settings = get_settings()
    token = getattr(settings, "meta_access_token", "") or ""
    if not token:
        package = export_manual_package(video_path, caption, video_path.parent)
        return {
            "status": "fallback_manual",
            "mode": "graph_api",
            "path": str(video_path),
            "package_dir": str(package),
            "message": "META_ACCESS_TOKEN not set. Manual package created.",
        }

    await asyncio.sleep(0)
    return {
        "status": "stub",
        "mode": "graph_api",
        "path": str(video_path),
        "caption": caption,
        "message": "Graph API upload not fully wired yet.",
    }
