"""MoviePy slideshow assembly with Ken Burns zoom for card-news reels."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
from moviepy import AudioFileClip, ColorClip, CompositeVideoClip, ImageClip, VideoClip, concatenate_videoclips
from moviepy.audio.AudioClip import concatenate_audioclips
from PIL import Image

from modules.utils import ensure_dir, get_logger, load_brand_config

logger = get_logger(__name__)


def slide_dwell_seconds(text: str, *, char_sec: float = 0.15, min_sec: float = 2.0, max_sec: float = 4.0) -> float:
    """TTS-off dwell: chars * 0.15s, clamped to [2, 4]."""
    n = len(re_sub_space(text))
    return float(min(max_sec, max(min_sec, n * char_sec)))


def re_sub_space(text: str) -> str:
    import re

    return re.sub(r"\s+", "", text or "")


def compute_slide_durations(
    slides: list[dict[str, Any]],
    *,
    use_tts: bool,
    voice_duration: float | None = None,
) -> list[float]:
    weights = []
    for s in slides:
        blob = f"{s.get('tag', '')}{s.get('hook', '')}{s.get('body', '')}"
        # strip simple html tags for counting
        import re

        blob = re.sub(r"<[^>]+>", "", blob)
        weights.append(max(len(re_sub_space(blob)), 8))

    if not use_tts or not voice_duration or voice_duration <= 0:
        return [
            slide_dwell_seconds(f"{s.get('hook', '')}{s.get('body', '')}")
            for s in slides
        ]

    total_w = float(sum(weights)) or 1.0
    durs = [max(1.2, voice_duration * (w / total_w)) for w in weights]
    # Normalize to exact voice length
    scale = voice_duration / max(sum(durs), 1e-6)
    return [d * scale for d in durs]


def ken_burns_clip(
    image_path: Path,
    duration: float,
    size: tuple[int, int] = (1080, 1920),
    zoom: float = 1.03,
    fps: int = 30,
) -> VideoClip:
    """Slow center zoom. Pre-scales once; per-frame mostly numpy crop."""
    w, h = size
    zoom = max(float(zoom), 1.01)
    img = Image.open(image_path).convert("RGB")
    base = img.resize((int(w * zoom) + 2, int(h * zoom) + 2), Image.Resampling.LANCZOS)
    base_arr = np.asarray(base)
    bw, bh = int(base_arr.shape[1]), int(base_arr.shape[0])

    def make_frame(t: float) -> np.ndarray:
        progress = min(max(t / max(duration, 1e-6), 0.0), 1.0)
        scale = 1.0 + (zoom - 1.0) * progress
        cw = min(bw, max(w, int(round(bw / scale))))
        ch = min(bh, max(h, int(round(bh / scale))))
        x0 = max(0, (bw - cw) // 2)
        y0 = max(0, (bh - ch) // 2)
        crop = base_arr[y0 : y0 + ch, x0 : x0 + cw]
        if crop.shape[0] == h and crop.shape[1] == w:
            return crop
        return np.asarray(Image.fromarray(crop).resize((w, h), Image.Resampling.BILINEAR))

    return VideoClip(make_frame, duration=duration).with_fps(fps)


def assemble_card_slideshow(
    slide_pngs: list[Path],
    durations: list[float],
    output_path: Path,
    *,
    voice_audio: Path | None = None,
    bgm_audio: Path | None = None,
    brand_config: dict[str, Any] | None = None,
    zoom: float = 1.03,
    draft_mode: bool = False,
) -> Path:
    """Concatenate Ken Burns slides + optional voice/BGM.

    Default encode keeps high quality (crf 18, medium).
    draft_mode trades a bit of quality for speed (optional UI checkbox).
    """
    brand = brand_config or load_brand_config()
    video_cfg = brand.get("video", {})
    audio_cfg = brand.get("audio", {})
    size = (int(video_cfg.get("width", 1080)), int(video_cfg.get("height", 1920)))
    fps = 24 if draft_mode else int(video_cfg.get("fps", 30))
    bgm_vol = float(audio_cfg.get("bgm_volume_normal", 0.26))
    duck_vol = float(audio_cfg.get("bgm_volume_ducked", 0.14))

    if not slide_pngs:
        raise ValueError("No slide PNGs to assemble")
    if len(durations) != len(slide_pngs):
        durations = (durations + [2.5] * len(slide_pngs))[: len(slide_pngs)]

    ensure_dir(output_path.parent)
    pieces = [
        ken_burns_clip(png, max(dur, 0.8), size=size, zoom=zoom, fps=fps)
        for png, dur in zip(slide_pngs, durations)
    ]

    video = concatenate_videoclips(pieces, method="compose")
    total = float(video.duration)

    final_audio = None
    voice_clip = None
    bgm_clip = None

    if voice_audio and Path(voice_audio).exists():
        voice_clip = AudioFileClip(str(voice_audio))
        if voice_clip.duration > total:
            voice_clip = voice_clip.subclipped(0, total)

    if bgm_audio and Path(bgm_audio).exists():
        bgm_clip = AudioFileClip(str(bgm_audio))
        if bgm_clip.duration < total:
            loops = int(np.ceil(total / bgm_clip.duration))
            bgm_clip = concatenate_audioclips([bgm_clip] * loops)
        bgm_clip = bgm_clip.subclipped(0, total)
        vol = duck_vol if voice_clip is not None else bgm_vol
        bgm_clip = bgm_clip.with_volume_scaled(vol)

    if voice_clip is not None and bgm_clip is not None:
        from moviepy import CompositeAudioClip

        final_audio = CompositeAudioClip([bgm_clip, voice_clip])
    elif voice_clip is not None:
        final_audio = voice_clip
    elif bgm_clip is not None:
        final_audio = bgm_clip

    if final_audio is not None:
        video = video.with_audio(final_audio)

    if draft_mode:
        preset, crf = "ultrafast", "23"
    else:
        preset, crf = "medium", "18"

    video.write_videofile(
        str(output_path),
        fps=fps,
        codec="libx264",
        audio_codec="aac" if final_audio is not None else None,
        preset=preset,
        ffmpeg_params=["-crf", crf, "-threads", "0", "-pix_fmt", "yuv420p"],
        logger=None,
    )

    video.close()
    if voice_clip:
        voice_clip.close()
    if bgm_clip:
        bgm_clip.close()
    logger.info(
        "Card slideshow saved: %s (%.1fs, %d slides, draft=%s)",
        output_path,
        total,
        len(slide_pngs),
        draft_mode,
    )
    return output_path
