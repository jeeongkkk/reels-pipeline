"""Short-form Reels generators (text tips + portfolio showcase).

Independent from card-news pipeline. Requires MoviePy 2.x + FFmpeg
(imageio-ffmpeg bundle is enough on most Windows installs).
"""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

import numpy as np
from moviepy import (
    AudioFileClip,
    ColorClip,
    CompositeVideoClip,
    ImageClip,
    VideoClip,
    concatenate_videoclips,
)
from moviepy.audio.AudioClip import concatenate_audioclips
from moviepy.video.fx import CrossFadeIn
from PIL import Image, ImageDraw, ImageFont

from modules.project import projects_root
from modules.utils import ROOT_DIR, ensure_dir, get_logger, resolve_font

logger = get_logger(__name__)

REEL_W = 1080
REEL_H = 1920
REEL_FPS = 30

MotionMode = Literal["zoom_in", "zoom_out", "pan_up", "pan_down"]


def _slugify(text: str, max_len: int = 36) -> str:
    text = (text or "").strip().lower()
    text = re.sub(r"[^\w\s-]", "", text, flags=re.UNICODE)
    text = re.sub(r"[\s_-]+", "-", text).strip("-")
    return (text[:max_len] or "reel").rstrip("-")


def create_reels_project(kind: str, title: str) -> Path:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    name = f"{stamp}_reel_{kind}_{_slugify(title)}"
    return ensure_dir(projects_root() / name)


def _hex_rgb(color: str) -> tuple[int, int, int]:
    c = (color or "#111111").strip().lstrip("#")
    if len(c) == 3:
        c = "".join(ch * 2 for ch in c)
    if len(c) != 6:
        return (17, 17, 17)
    return int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16)


def _load_font(
    size: int,
    *,
    bold: bool = True,
    family: str = "pretendard",
    weight: str | None = None,
) -> ImageFont.ImageFont:
    size = max(12, int(size))
    w = weight or ("bold" if bold else "regular")
    from modules.card_pil_render import _font as pil_font

    try:
        return pil_font(size, weight=w, family=family)
    except Exception:  # noqa: BLE001
        pass
    # Fallback original path
    candidates: list[Path] = []
    fonts = ROOT_DIR / "assets" / "fonts"
    if bold:
        candidates.extend(
            [
                fonts / "Pretendard" / "Pretendard-Bold.otf",
                fonts / "Pretendard" / "Pretendard-ExtraBold.otf",
                fonts / "Paperlogy" / "Paperlogy-7Bold.ttf",
                Path(resolve_font("Malgun-Gothic-Bold")),
                Path(resolve_font("Arial-Bold")),
            ]
        )
    else:
        candidates.extend(
            [
                fonts / "Pretendard" / "Pretendard-Regular.otf",
                fonts / "Pretendard" / "Pretendard-Medium.otf",
                Path(resolve_font("Malgun-Gothic")),
                Path(resolve_font("Arial-Bold")),
            ]
        )
    for path in candidates:
        if path and path.exists():
            try:
                return ImageFont.truetype(str(path), size=size)
            except OSError:
                continue
    return ImageFont.load_default()


def _wrap_lines(text: str, font: ImageFont.ImageFont, max_width: int) -> list[str]:
    plain = re.sub(r"\s+", " ", (text or "").strip())
    if not plain:
        return []
    chunks = [c.strip() for c in plain.replace("\\n", "\n").split("\n") if c.strip()]
    lines: list[str] = []
    for chunk in chunks or [plain]:
        if font.getlength(chunk) <= max_width:
            lines.append(chunk)
            continue
        words = chunk.split(" ")
        if len(words) > 1:
            cur = ""
            for w in words:
                trial = f"{cur} {w}".strip()
                if font.getlength(trial) <= max_width:
                    cur = trial
                else:
                    if cur:
                        lines.append(cur)
                    cur = w
            if cur:
                lines.append(cur)
        else:
            buf = ""
            for ch in chunk:
                trial = buf + ch
                if font.getlength(trial) <= max_width:
                    buf = trial
                else:
                    if buf:
                        lines.append(buf)
                    buf = ch
            if buf:
                lines.append(buf)
    return lines


def _rgba_text_image(
    lines: list[str],
    *,
    font: ImageFont.ImageFont,
    fill: tuple[int, int, int] = (255, 255, 255),
    line_gap: int = 18,
    align: str = "center",
    canvas_w: int = REEL_W,
    pad_x: int = 80,
) -> Image.Image:
    if not lines:
        return Image.new("RGBA", (canvas_w, 10), (0, 0, 0, 0))
    widths = []
    heights = []
    for ln in lines:
        box = font.getbbox(ln)
        widths.append(box[2] - box[0])
        heights.append(box[3] - box[1])
    content_h = sum(heights) + line_gap * (len(lines) - 1)
    img = Image.new("RGBA", (canvas_w, content_h + 8), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    y = 4
    for ln, tw, th in zip(lines, widths, heights):
        if align == "left":
            x = pad_x
        else:
            x = (canvas_w - tw) // 2
        draw.text((x, y), ln, font=font, fill=(*fill, 255))
        y += th + line_gap
    return img


def _fit_cover(img: Image.Image, w: int, h: int) -> Image.Image:
    img = img.convert("RGB")
    sw, sh = img.size
    target = w / h
    src = sw / max(sh, 1)
    if src > target:
        nw = int(sh * target)
        left = (sw - nw) // 2
        box = (left, 0, left + nw, sh)
    else:
        nh = int(sw / target)
        top = (sh - nh) // 2
        box = (0, top, sw, top + nh)
    return img.crop(box).resize((w, h), Image.Resampling.LANCZOS)


def _attach_bgm(video: VideoClip, bgm_path: Path | None, *, volume: float = 0.32) -> VideoClip:
    if not bgm_path or not Path(bgm_path).exists():
        return video
    total = float(video.duration or 0)
    if total <= 0:
        return video
    bgm = AudioFileClip(str(bgm_path))
    if bgm.duration < total:
        loops = int(np.ceil(total / max(bgm.duration, 0.01)))
        bgm = concatenate_audioclips([bgm] * loops)
    bgm = bgm.subclipped(0, total).with_volume_scaled(volume)
    return video.with_audio(bgm)


def _write_mp4(video: VideoClip, output_path: Path, *, fps: int = REEL_FPS) -> Path:
    ensure_dir(output_path.parent)
    has_audio = video.audio is not None
    duration = float(video.duration or 0)
    video.write_videofile(
        str(output_path),
        fps=fps,
        codec="libx264",
        audio_codec="aac" if has_audio else None,
        preset="medium",
        ffmpeg_params=["-crf", "18", "-threads", "0", "-pix_fmt", "yuv420p"],
        logger=None,
    )
    video.close()
    logger.info("Reels MP4 saved: %s (%.1fs)", output_path, duration)
    return output_path


def make_text_tips_reel(
    *,
    title: str,
    tips: list[str],
    bg_color: str = "#111111",
    bgm_path: Path | None = None,
    output_path: Path,
    duration_sec: float = 12.0,
    tip_interval: float = 1.6,
    fade_sec: float = 0.7,
    text_color: tuple[int, int, int] = (255, 255, 255),
    font_family: str = "pretendard",
    title_weight: str = "bold",
    body_weight: str = "semibold",
) -> Path:
    """Solid-bg vertical reel: title + tips fade in one by one (10–15s)."""
    clean_tips = [t.strip() for t in tips if str(t).strip()][:4]
    if len(clean_tips) < 1:
        raise ValueError("꿀팁 텍스트를 1개 이상 입력하세요.")
    title = (title or "").strip() or "TIP"
    duration_sec = float(min(15.0, max(10.0, duration_sec)))
    tip_interval = float(min(2.0, max(1.0, tip_interval)))
    fade_sec = float(min(1.2, max(0.35, fade_sec)))

    bg_rgb = _hex_rgb(bg_color)
    start_base = 1.1
    last_start = start_base + tip_interval * (len(clean_tips) - 1)
    needed = last_start + fade_sec + 2.8
    duration_sec = float(min(15.0, max(duration_sec, needed)))

    layers: list[Any] = [
        ColorClip(size=(REEL_W, REEL_H), color=bg_rgb).with_duration(duration_sec)
    ]

    title_font = _load_font(64, family=font_family, weight=title_weight)
    tip_font = _load_font(48, family=font_family, weight=body_weight)
    max_w = REEL_W - 160

    title_lines = _wrap_lines(title, title_font, max_w)
    title_img = _rgba_text_image(title_lines, font=title_font, fill=text_color, line_gap=14)
    title_y = int(REEL_H * 0.22)
    title_clip = (
        ImageClip(np.asarray(title_img), transparent=True)
        .with_start(0)
        .with_duration(duration_sec)
        .with_position(("center", title_y))
        .with_effects([CrossFadeIn(min(fade_sec, 0.9))])
    )
    layers.append(title_clip)

    tip_block_top = title_y + title_img.height + 70
    line_gap_block = 28
    running_y = tip_block_top
    for i, tip in enumerate(clean_tips):
        tip_lines = _wrap_lines(f"• {tip}", tip_font, max_w)
        tip_img = _rgba_text_image(
            tip_lines, font=tip_font, fill=text_color, line_gap=12, align="left", pad_x=100
        )
        t0 = start_base + i * tip_interval
        remaining = max(0.5, duration_sec - t0)
        tip_clip = (
            ImageClip(np.asarray(tip_img), transparent=True)
            .with_start(t0)
            .with_duration(remaining)
            .with_position((0, running_y))
            .with_effects([CrossFadeIn(fade_sec)])
        )
        layers.append(tip_clip)
        running_y += tip_img.height + line_gap_block

    video = CompositeVideoClip(layers, size=(REEL_W, REEL_H)).with_duration(duration_sec)
    video = _attach_bgm(video, bgm_path)
    return _write_mp4(video, output_path)


def ken_burns_motion_clip(
    image_path: Path,
    duration: float,
    *,
    size: tuple[int, int] = (REEL_W, REEL_H),
    mode: MotionMode = "zoom_in",
    zoom: float = 1.12,
    fps: int = REEL_FPS,
) -> VideoClip:
    """Ken Burns: slow zoom or vertical pan on a cover-fitted image."""
    w, h = size
    zoom = max(float(zoom), 1.06)
    base = _fit_cover(Image.open(image_path), w, h)
    over_w, over_h = int(w * zoom) + 2, int(h * zoom) + 2
    oversized = base.resize((over_w, over_h), Image.Resampling.LANCZOS)
    arr = np.asarray(oversized)
    bw, bh = int(arr.shape[1]), int(arr.shape[0])

    def make_frame(t: float) -> np.ndarray:
        p = min(max(t / max(duration, 1e-6), 0.0), 1.0)
        if mode == "zoom_out":
            p = 1.0 - p
            scale = 1.0 + (zoom - 1.0) * p
            cw = min(bw, max(w, int(round(bw / scale))))
            ch = min(bh, max(h, int(round(bh / scale))))
            x0 = max(0, (bw - cw) // 2)
            y0 = max(0, (bh - ch) // 2)
        elif mode == "pan_up":
            cw, ch = w, h
            x0 = max(0, (bw - cw) // 2)
            max_y = max(0, bh - ch)
            y0 = int(round(max_y * (1.0 - p)))
        elif mode == "pan_down":
            cw, ch = w, h
            x0 = max(0, (bw - cw) // 2)
            max_y = max(0, bh - ch)
            y0 = int(round(max_y * p))
        else:
            scale = 1.0 + (zoom - 1.0) * p
            cw = min(bw, max(w, int(round(bw / scale))))
            ch = min(bh, max(h, int(round(bh / scale))))
            x0 = max(0, (bw - cw) // 2)
            y0 = max(0, (bh - ch) // 2)

        crop = arr[y0 : y0 + ch, x0 : x0 + cw]
        if crop.shape[0] == h and crop.shape[1] == w:
            return crop
        return np.asarray(Image.fromarray(crop).resize((w, h), Image.Resampling.BILINEAR))

    return VideoClip(make_frame, duration=duration).with_fps(fps)


def make_portfolio_showcase_reel(
    *,
    image_paths: list[Path],
    bgm_path: Path | None = None,
    output_path: Path,
    per_image_sec: float = 3.6,
    crossfade_sec: float = 0.85,
    fps: int = REEL_FPS,
) -> Path:
    """Ken Burns + crossfade showcase for uploaded portfolio images."""
    paths = [Path(p) for p in image_paths if Path(p).exists()]
    if not paths:
        raise ValueError("포트폴리오 이미지를 1장 이상 업로드하세요.")

    per_image_sec = float(min(5.0, max(2.5, per_image_sec)))
    crossfade_sec = float(min(1.2, max(0.4, crossfade_sec)))
    modes: list[MotionMode] = ["zoom_in", "pan_up", "zoom_out", "pan_down"]

    clips: list[VideoClip] = []
    for i, path in enumerate(paths):
        mode = modes[i % len(modes)]
        clip = ken_burns_motion_clip(
            path,
            per_image_sec,
            size=(REEL_W, REEL_H),
            mode=mode,
            fps=fps,
        )
        if i > 0 and crossfade_sec > 0:
            clip = clip.with_effects([CrossFadeIn(crossfade_sec)])
        clips.append(clip)

    if len(clips) == 1:
        video = clips[0]
    else:
        video = concatenate_videoclips(
            clips,
            method="compose",
            padding=-crossfade_sec,
        )

    video = _attach_bgm(video, bgm_path)
    return _write_mp4(video, output_path, fps=fps)
