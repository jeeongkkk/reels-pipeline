"""Module 5: Video Assembly & Dynamic Editing.

Supports:
  - voice_tts: phrase captions + voice + BGM ducking
  - music_caption: sentence cards + BGM only
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
from moviepy import AudioFileClip, ColorClip, CompositeVideoClip, TextClip, VideoFileClip, concatenate_videoclips
from moviepy.audio.AudioClip import AudioArrayClip, concatenate_audioclips

from modules.sfx import load_sfx_array, resolve_sfx_paths
from modules.utils import ensure_dir, get_logger, load_brand_config, resolve_font

logger = get_logger(__name__)


@dataclass
class TimedText:
    text: str
    start: float
    end: float


def load_word_timestamps(path: Path) -> list[TimedText]:
    with open(path, encoding="utf-8") as f:
        data = json.load(f)

    # Prefer phrases (readable). Fall back to words.
    items = data.get("phrases") or data.get("words") or data
    return [
        TimedText(text=w["text"], start=float(w["start"]), end=float(w["end"]))
        for w in items
    ]


def _align_audio_channels(arr: np.ndarray, target_channels: int) -> np.ndarray:
    if arr.ndim == 1:
        if target_channels == 1:
            return arr
        return np.column_stack([arr] * target_channels)
    if arr.shape[1] == target_channels:
        return arr
    if arr.shape[1] == 1 and target_channels == 2:
        return np.column_stack([arr[:, 0], arr[:, 0]])
    return arr[:, :target_channels]


def _loop_audio_array(arr: np.ndarray, target_length: int) -> np.ndarray:
    if len(arr) >= target_length:
        return arr[:target_length]
    repeats = int(np.ceil(target_length / len(arr)))
    tiled = np.tile(arr, (repeats, 1) if arr.ndim == 2 else repeats)
    return tiled[:target_length]


def mix_audio_with_ducking(
    voice_clip: AudioFileClip,
    bgm_clip: AudioFileClip,
    timed_texts: list[TimedText],
    normal_volume: float = 0.28,
    duck_volume: float = 0.18,
    duck_lead_sec: float = 0.05,
    duck_tail_sec: float = 0.10,
) -> AudioArrayClip:
    """Keep BGM audible under voice (avoid 'silent underlay' feel)."""
    fps = int(voice_clip.fps or 44100)
    duration = voice_clip.duration
    total_samples = int(duration * fps)

    voice_arr = _align_audio_channels(voice_clip.to_soundarray(fps=fps), 2)
    bgm_arr = _align_audio_channels(bgm_clip.to_soundarray(fps=fps), voice_arr.shape[1])
    bgm_arr = _loop_audio_array(bgm_arr, total_samples)

    # Default: light underlay so BGM never disappears
    envelope = np.full(total_samples, duck_volume, dtype=np.float64)
    # Raise BGM slightly in gaps between captions
    covered = np.zeros(total_samples, dtype=bool)
    for item in timed_texts:
        start = max(0, int((item.start - duck_lead_sec) * fps))
        end = min(total_samples, int((item.end + duck_tail_sec) * fps))
        covered[start:end] = True
    envelope[~covered] = normal_volume

    mixed = voice_arr + (bgm_arr * envelope[:, np.newaxis])
    peak = np.max(np.abs(mixed))
    if peak > 0.95:
        mixed = mixed / peak * 0.95
    return AudioArrayClip(mixed, fps=fps)


def _scale_bgm(bgm_clip: AudioFileClip, duration: float, volume: float) -> AudioFileClip:
    clip = bgm_clip
    if clip.duration < duration:
        loops = int(np.ceil(duration / clip.duration))
        clip = concatenate_audioclips([clip] * loops)
    clip = clip.subclipped(0, duration)
    return clip.with_volume_scaled(volume)


def build_sfx_layers(
    timed_texts: list[TimedText],
    duration: float,
    brand_config: dict[str, Any] | None = None,
    cut_times: list[float] | None = None,
) -> list:
    """Place caption-pop at each text start and swish at B-roll cut points."""
    brand = brand_config or load_brand_config()
    audio_cfg = brand.get("audio", {})
    pop_vol = float(audio_cfg.get("sfx_caption_volume", 0.45))
    swish_vol = float(audio_cfg.get("sfx_transition_volume", 0.35))

    paths = resolve_sfx_paths(brand)
    layers = []

    def _place(path: Path, start: float, volume: float):
        if start < 0 or start >= duration:
            return
        clip = AudioFileClip(str(path))
        clip_dur = float(clip.duration or 0.05)
        # Keep duration explicit so CompositeAudioClip does not read past EOF
        placed = (
            clip.with_volume_scaled(volume)
            .with_duration(clip_dur)
            .with_start(start)
        )
        layers.append(placed)

    pop_path = paths.get("caption_pop")
    if pop_path and pop_path.exists():
        last_t = -1.0
        for item in timed_texts:
            if item.start - last_t < 0.35:
                continue
            try:
                _place(pop_path, max(0.0, item.start), pop_vol)
                last_t = item.start
            except Exception as exc:  # noqa: BLE001
                logger.warning("caption_pop SFX failed: %s", exc)
                break

    swish_path = paths.get("transition")
    if swish_path and swish_path.exists() and cut_times:
        for t in cut_times:
            if t <= 0.05 or t >= duration - 0.05:
                continue
            try:
                _place(swish_path, t, swish_vol)
            except Exception as exc:  # noqa: BLE001
                logger.warning("transition SFX failed: %s", exc)
                break

    logger.info("SFX layers: %d", len(layers))
    return layers


def mix_sfx_into_array(
    base_clip,
    timed_texts: list[TimedText],
    duration: float,
    brand_config: dict[str, Any] | None = None,
    cut_times: list[float] | None = None,
) -> AudioArrayClip:
    """Bake SFX into a sound array (numpy WAV load – avoids MoviePy short-clip bugs)."""
    brand = brand_config or load_brand_config()
    audio_cfg = brand.get("audio", {})
    pop_vol = float(audio_cfg.get("sfx_caption_volume", 0.45))
    swish_vol = float(audio_cfg.get("sfx_transition_volume", 0.35))
    paths = resolve_sfx_paths(brand)

    fps = int(getattr(base_clip, "fps", None) or 44100)
    total = int(duration * fps)
    base = base_clip.to_soundarray(fps=fps)
    base = _align_audio_channels(base, 2)
    if len(base) < total:
        pad = np.zeros((total - len(base), base.shape[1]), dtype=base.dtype)
        base = np.vstack([base, pad])
    else:
        base = base[:total]

    mixed = base.astype(np.float64)

    def _add(path: Path | None, start: float, volume: float) -> None:
        if not path or not path.exists() or start < 0 or start >= duration:
            return
        arr = load_sfx_array(path, target_fps=fps)
        arr = _align_audio_channels(arr, mixed.shape[1])
        start_i = int(start * fps)
        end_i = min(total, start_i + len(arr))
        if end_i <= start_i:
            return
        mixed[start_i:end_i] += arr[: end_i - start_i] * volume

    last_t = -1.0
    for item in timed_texts:
        if item.start - last_t < 0.35:
            continue
        _add(paths.get("caption_pop"), item.start, pop_vol)
        last_t = item.start

    for t in cut_times or []:
        _add(paths.get("transition"), t, swish_vol)

    peak = np.max(np.abs(mixed))
    if peak > 0.95:
        mixed = mixed / peak * 0.95

    logger.info("SFX baked into audio array")
    return AudioArrayClip(mixed, fps=fps)


def estimate_cut_times(clip_count: int, duration: float) -> list[float]:
    if duration <= 0:
        return []
    # ~3s fast cuts for short-form pacing
    n = max(int(round(duration / 3.0)), max(clip_count, 1))
    step = duration / n
    return [step * i for i in range(1, n)]


def _wrap_caption_lines(text: str, max_chars: int) -> str:
    # Preserve existing intentional line breaks
    raw_lines = [ln.strip() for ln in (text or "").splitlines() if ln.strip()]
    if not raw_lines:
        raw_lines = [re.sub(r"\s+", " ", (text or "").strip())]
    out: list[str] = []
    for line in raw_lines:
        line = re.sub(r"\s+", " ", line).strip()
        if len(line) <= max_chars:
            out.append(line)
            continue
        words = line.split(" ")
        buf = ""
        for w in words:
            cand = f"{buf} {w}".strip() if buf else w
            if len(cand) <= max_chars:
                buf = cand
            else:
                if buf:
                    out.append(buf)
                buf = w
        if buf:
            out.append(buf)
    return "\n".join(out[:3])  # max 3 lines on screen


def _hex_to_rgb(value: str, default: tuple[int, int, int] = (255, 255, 255)) -> tuple[int, int, int]:
    v = (value or "").strip().lstrip("#")
    if len(v) == 6:
        try:
            return int(v[0:2], 16), int(v[2:4], 16), int(v[4:6], 16)
        except ValueError:
            return default
    return default


def _render_caption_image(
    text: str,
    *,
    font_path: str,
    font_size: int,
    max_width: int,
    color: str,
    stroke_color: str,
    stroke_width: int,
    bg_opacity: float = 0.0,
    max_chars: int = 14,
    highlight_word: str | None = None,
    highlight_color: str = "#FFE566",
) -> np.ndarray:
    """Stroke + shadow captions (no box). Optional active-word highlight."""
    from PIL import Image, ImageDraw, ImageFont

    from modules.text_quality import fix_korean_spacing

    text = fix_korean_spacing((text or "").replace("\n", " "))
    try:
        font = ImageFont.truetype(font_path, font_size)
    except OSError:
        font = ImageFont.load_default()

    wrapped = _wrap_caption_lines(text, max_chars)
    lines = [ln for ln in wrapped.split("\n") if ln]
    pad_x, pad_y = 24, 16
    line_gap = 10

    dummy = Image.new("RGBA", (8, 8), (0, 0, 0, 0))
    draw = ImageDraw.Draw(dummy)

    # Measure with highlight-aware layout (same string length)
    line_sizes = [draw.textbbox((0, 0), ln, font=font) for ln in lines]
    line_widths = [b[2] - b[0] for b in line_sizes]
    line_heights = [b[3] - b[1] for b in line_sizes]
    text_w = max(line_widths) if line_widths else 1
    text_h = sum(line_heights) + line_gap * max(len(lines) - 1, 0)

    img_w = min(max_width, text_w + pad_x * 2 + 8)
    img_h = text_h + pad_y * 2 + 8
    img = Image.new("RGBA", (img_w, img_h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    if bg_opacity > 0.05:
        alpha = int(255 * min(bg_opacity, 0.7))
        draw.rounded_rectangle((0, 0, img_w - 1, img_h - 1), radius=20, fill=(10, 12, 18, alpha))

    fill = _hex_to_rgb(color)
    stroke = _hex_to_rgb(stroke_color, (0, 0, 0))
    hi = _hex_to_rgb(highlight_color, (255, 229, 102))
    active = (highlight_word or "").strip()

    y = pad_y
    sw = max(3, min(int(stroke_width), 8))
    for ln, lh, lw in zip(lines, line_heights, line_widths):
        x = (img_w - lw) // 2
        # Soft drop shadow for punch without a box
        draw.text((x + 3, y + 3), ln, font=font, fill=(0, 0, 0, 180))

        if active and active in ln:
            # Draw word-by-word with highlight on active token
            cursor = x
            # Reconstruct tokens preserving spaces
            parts = re.split(r"(\s+)", ln)
            for part in parts:
                if not part:
                    continue
                part_fill = hi if part.strip() == active or part.strip() == active.strip(".,!?…") else fill
                bbox = draw.textbbox((0, 0), part, font=font)
                pw = bbox[2] - bbox[0]
                draw.text(
                    (cursor, y),
                    part,
                    font=font,
                    fill=(*part_fill, 255),
                    stroke_width=sw,
                    stroke_fill=(*stroke, 255),
                )
                cursor += pw
        else:
            draw.text(
                (x, y),
                ln,
                font=font,
                fill=(*fill, 255),
                stroke_width=sw,
                stroke_fill=(*stroke, 255),
            )
        y += lh + line_gap

    return np.array(img)


def create_cta_card(
    text: str,
    video_size: tuple[int, int],
    start: float,
    duration: float,
    brand_config: dict[str, Any] | None = None,
):
    """End-card CTA overlay so the finale isn't a blank stock screen."""
    from moviepy import ImageClip
    from PIL import Image, ImageDraw, ImageFont

    brand = brand_config or load_brand_config()
    caption = brand.get("caption", {})
    font_path = resolve_font(caption.get("font", "Malgun-Gothic-Bold"))
    w, h = video_size
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    # Dim veil
    draw.rectangle((0, int(h * 0.55), w, h), fill=(8, 10, 16, 170))
    try:
        title_font = ImageFont.truetype(font_path, 52)
        body_font = ImageFont.truetype(font_path, 40)
    except OSError:
        title_font = ImageFont.load_default()
        body_font = title_font

    title = "지금 바로 해보세요"
    body = (text or "").strip() or "댓글에 '비밀' 남겨주시면 자료 보내드릴게요"
    body = _wrap_caption_lines(body, 18)
    # Title
    tb = draw.textbbox((0, 0), title, font=title_font)
    tw = tb[2] - tb[0]
    draw.text(
        ((w - tw) // 2, int(h * 0.62)),
        title,
        font=title_font,
        fill=(255, 229, 102, 255),
        stroke_width=4,
        stroke_fill=(0, 0, 0, 255),
    )
    y = int(h * 0.70)
    for ln in body.split("\n"):
        bb = draw.textbbox((0, 0), ln, font=body_font)
        lw = bb[2] - bb[0]
        draw.text(
            ((w - lw) // 2, y),
            ln,
            font=body_font,
            fill=(255, 255, 255, 255),
            stroke_width=4,
            stroke_fill=(0, 0, 0, 255),
        )
        y += (bb[3] - bb[1]) + 12

    clip = ImageClip(np.array(img), is_mask=False).with_duration(duration).with_start(start)
    return clip


def create_caption_clips(
    timed_texts: list[TimedText],
    video_size: tuple[int, int],
    brand_config: dict[str, Any] | None = None,
    word_timestamps: list[TimedText] | None = None,
) -> list:
    """Trend captions: thick stroke + shadow, optional karaoke word highlight."""
    from moviepy import ImageClip

    brand = brand_config or load_brand_config()
    caption = brand.get("caption", {})

    font = resolve_font(caption.get("font", "Malgun-Gothic-Bold"))
    font_size = int(caption.get("font_size", 52))
    color = caption.get("color", "#FFFFFF")
    stroke_color = caption.get("stroke_color", "#000000")
    stroke_width = int(caption.get("stroke_width", 6))
    bg_opacity = float(caption.get("bg_opacity", 0.0))
    highlight = caption.get("highlight_color", "#FFE566")
    safe_bottom = float(caption.get("safe_bottom_ratio", 0.30))
    max_width = int(video_size[0] * float(caption.get("max_width_ratio", 0.86)))
    max_line_chars = int(caption.get("max_chars_per_line", 14))
    dynamic = bool(caption.get("dynamic", True))
    clips = []

    words = word_timestamps or []

    for item in timed_texts:
        raw = (item.text or "").strip()
        if not raw:
            continue
        duration = max(item.end - item.start, 0.55)

        # Karaoke: one clip per active word inside the phrase window
        active_words = [
            w
            for w in words
            if w.start >= item.start - 0.05 and w.end <= item.end + 0.08 and (w.text or "").strip()
        ]
        if dynamic and len(active_words) >= 2:
            for wi, w in enumerate(active_words):
                w_dur = max(w.end - w.start, 0.12)
                if wi + 1 < len(active_words):
                    w_dur = max(active_words[wi + 1].start - w.start, 0.12)
                try:
                    rgba = _render_caption_image(
                        raw,
                        font_path=font,
                        font_size=font_size,
                        max_width=max_width,
                        color=color,
                        stroke_color=stroke_color,
                        stroke_width=stroke_width,
                        bg_opacity=bg_opacity,
                        max_chars=max_line_chars,
                        highlight_word=w.text.strip(),
                        highlight_color=highlight,
                    )
                    clip = ImageClip(rgba, is_mask=False).with_duration(w_dur)
                except Exception as exc:  # noqa: BLE001
                    logger.warning("Dynamic caption fail: %s", exc)
                    continue
                cap_h = int(rgba.shape[0])
                y = int(video_size[1] * (1.0 - safe_bottom) - cap_h)
                y = min(y, int(video_size[1] * 0.62))
                y = max(int(video_size[1] * 0.50), y)
                clips.append(clip.with_start(float(w.start)).with_position(("center", int(y))))
            continue

        try:
            rgba = _render_caption_image(
                raw,
                font_path=font,
                font_size=font_size,
                max_width=max_width,
                color=color,
                stroke_color=stroke_color,
                stroke_width=stroke_width,
                bg_opacity=bg_opacity,
                max_chars=max_line_chars,
            )
            clip = ImageClip(rgba, is_mask=False).with_duration(duration)
        except Exception as exc:  # noqa: BLE001
            logger.warning("PIL caption failed (%s) – TextClip fallback", exc)
            try:
                clip = TextClip(
                    font=font,
                    text=_wrap_caption_lines(raw, max_line_chars).replace("\n", " "),
                    font_size=font_size,
                    color=color,
                    stroke_color=stroke_color,
                    stroke_width=min(stroke_width, 6),
                    method="label",
                ).with_duration(duration)
                rgba = None
            except Exception:  # noqa: BLE001
                continue

        cap_h = int(rgba.shape[0] if rgba is not None else getattr(clip, "h", 80))
        y = int(video_size[1] * (1.0 - safe_bottom) - cap_h)
        y = min(y, int(video_size[1] * 0.62))
        y = max(int(video_size[1] * 0.50), y)
        clips.append(clip.with_start(float(item.start)).with_position(("center", int(y))))

    return clips


def prepare_background_video(
    video_path: Path,
    target_duration: float,
    target_size: tuple[int, int],
) -> VideoFileClip:
    clip = VideoFileClip(str(video_path))
    if clip.duration < target_duration:
        loops = int(np.ceil(target_duration / clip.duration))
        clip = concatenate_videoclips([clip] * loops)
    clip = clip.subclipped(0, target_duration)
    clip = clip.resized(new_size=target_size)
    return clip


def build_background_from_clips(
    clip_paths: list[Path],
    target_duration: float,
    target_size: tuple[int, int],
    cut_min: float = 2.5,
    cut_max: float = 3.5,
) -> CompositeVideoClip | VideoFileClip:
    """Fast-cut B-roll: switch every 2.5–3.5s, cycling source clips."""
    if not clip_paths:
        return ColorClip(size=target_size, color=(18, 28, 48)).with_duration(target_duration)

    pieces = []
    t = 0.0
    idx = 0
    seed = 17

    while t < target_duration - 0.05:
        seed = (seed * 1103515245 + 12345) & 0x7FFFFFFF
        span = cut_min + (cut_max - cut_min) * ((seed % 1000) / 1000.0)
        span = min(span, target_duration - t)
        path = clip_paths[idx % len(clip_paths)]
        idx += 1
        try:
            src = VideoFileClip(str(path))
            reuse = idx // max(len(clip_paths), 1)
            start = min((reuse * 1.1) % max(src.duration * 0.5, 0.1), max(src.duration - 0.4, 0))
            use = min(span, max(src.duration - start, 0.3))
            piece = src.subclipped(start, start + use).resized(new_size=target_size)
            if use < span - 0.05:
                loops = int(np.ceil(span / max(use, 0.1)))
                piece = concatenate_videoclips([piece] * loops).subclipped(0, span)
            pieces.append(piece)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Skip clip %s: %s", path, exc)
            pieces.append(ColorClip(size=target_size, color=(18, 28, 48)).with_duration(span))
        t += span

    if not pieces:
        return ColorClip(size=target_size, color=(18, 28, 48)).with_duration(target_duration)

    bg = concatenate_videoclips(pieces, method="compose")
    if bg.duration < target_duration:
        pad = ColorClip(size=target_size, color=(18, 28, 48)).with_duration(target_duration - bg.duration)
        bg = concatenate_videoclips([bg, pad], method="compose")
    return bg.subclipped(0, target_duration)


def assemble_voice_reel(
    background_video: Path | list[Path],
    voice_audio: Path,
    bgm_audio: Path,
    timed_texts: list[TimedText],
    output_path: Path,
    brand_config: dict[str, Any] | None = None,
    word_timestamps: list[TimedText] | None = None,
    cta_text: str = "",
) -> Path:
    brand = brand_config or load_brand_config()
    video_cfg = brand.get("video", {})
    audio_cfg = brand.get("audio", {})
    edit_cfg = brand.get("edit", {})
    target_size = (video_cfg.get("width", 1080), video_cfg.get("height", 1920))
    normal_vol = float(audio_cfg.get("bgm_volume_normal", 0.28))
    duck_vol = float(audio_cfg.get("bgm_volume_ducked", 0.18))
    cut_min = float(edit_cfg.get("cut_min_sec", 2.5))
    cut_max = float(edit_cfg.get("cut_max_sec", 3.5))

    ensure_dir(output_path.parent)
    voice_clip = AudioFileClip(str(voice_audio))
    bgm_clip = AudioFileClip(str(bgm_audio))
    duration = voice_clip.duration

    clip_list = background_video if isinstance(background_video, list) else [background_video]
    if isinstance(background_video, list):
        bg_video = build_background_from_clips(
            background_video,
            duration,
            target_size,
            cut_min=cut_min,
            cut_max=cut_max,
        )
    else:
        bg_video = prepare_background_video(background_video, duration, target_size)

    caption_clips = create_caption_clips(
        timed_texts,
        target_size,
        brand,
        word_timestamps=word_timestamps,
    )
    # CTA card on last ~2.8s
    cta_dur = min(2.8, max(duration * 0.12, 1.8))
    cta_start = max(duration - cta_dur, 0)
    layers = [bg_video, *caption_clips]
    layers.append(
        create_cta_card(
            cta_text or brand.get("script", {}).get("cta_template", ""),
            target_size,
            cta_start,
            cta_dur,
            brand,
        )
    )

    cut_times = estimate_cut_times(max(len(clip_list), int(duration / 3)), duration)
    base_audio = mix_audio_with_ducking(voice_clip, bgm_clip, timed_texts, normal_vol, duck_vol)
    final_audio = mix_sfx_into_array(
        base_audio,
        timed_texts,
        duration,
        brand,
        cut_times=cut_times,
    )

    composite = CompositeVideoClip(layers, size=target_size)
    composite = composite.with_audio(final_audio).with_duration(duration)
    composite.write_videofile(
        str(output_path),
        fps=video_cfg.get("fps", 30),
        codec="libx264",
        audio_codec="aac",
        logger=None,
    )

    voice_clip.close()
    bgm_clip.close()
    bg_video.close()
    composite.close()
    logger.info("Voice reel saved: %s (%.1fs) cuts≈%d", output_path, duration, len(cut_times))
    return output_path


def assemble_music_caption_reel(
    background_video: Path | list[Path],
    bgm_audio: Path,
    caption_lines: list[str],
    caption_durations: list[float],
    output_path: Path,
    brand_config: dict[str, Any] | None = None,
) -> Path:
    brand = brand_config or load_brand_config()
    video_cfg = brand.get("video", {})
    audio_cfg = brand.get("audio", {})
    target_size = (video_cfg.get("width", 1080), video_cfg.get("height", 1920))
    bgm_vol = audio_cfg.get("bgm_volume_normal", 0.35)

    if not caption_durations or len(caption_durations) != len(caption_lines):
        caption_durations = [2.5] * len(caption_lines)

    timed: list[TimedText] = []
    t = 0.0
    for line, dur in zip(caption_lines, caption_durations):
        timed.append(TimedText(text=line, start=t, end=t + dur))
        t += dur
    duration = max(t, 5.0)

    ensure_dir(output_path.parent)
    clip_list = background_video if isinstance(background_video, list) else [background_video]
    if isinstance(background_video, list):
        bg_video = build_background_from_clips(background_video, duration, target_size)
    else:
        bg_video = prepare_background_video(background_video, duration, target_size)

    caption_clips = create_caption_clips(timed, target_size, brand)
    bgm_clip = _scale_bgm(AudioFileClip(str(bgm_audio)), duration, bgm_vol)
    final_audio = mix_sfx_into_array(
        bgm_clip,
        timed,
        duration,
        brand,
        cut_times=estimate_cut_times(len(clip_list), duration),
    )

    composite = CompositeVideoClip([bg_video, *caption_clips], size=target_size)
    composite = composite.with_audio(final_audio).with_duration(duration)
    composite.write_videofile(
        str(output_path),
        fps=video_cfg.get("fps", 30),
        codec="libx264",
        audio_codec="aac",
        logger=None,
    )

    bg_video.close()
    bgm_clip.close()
    composite.close()
    logger.info("Music-caption reel saved: %s (%.1fs)", output_path, duration)
    return output_path


# Backwards-compatible alias used by test_module5.py
def create_word_caption_clips(word_timestamps, video_size, brand_config=None):
    timed = [
        TimedText(text=getattr(w, "text", w["text"]), start=getattr(w, "start", w["start"]), end=getattr(w, "end", w["end"]))
        if not isinstance(w, TimedText)
        else w
        for w in word_timestamps
    ]
    return create_caption_clips(timed, video_size, brand_config)


def assemble_reel(
    background_video: Path,
    voice_audio: Path,
    bgm_audio: Path,
    word_timestamps,
    output_path: Path,
    brand_config: dict[str, Any] | None = None,
    normal_bgm_volume: float | None = None,
    duck_bgm_volume: float | None = None,
) -> Path:
    timed = []
    for w in word_timestamps:
        if isinstance(w, TimedText):
            timed.append(w)
        else:
            timed.append(
                TimedText(
                    text=getattr(w, "text", w.get("text")),
                    start=float(getattr(w, "start", w.get("start"))),
                    end=float(getattr(w, "end", w.get("end"))),
                )
            )
    return assemble_voice_reel(
        background_video=background_video,
        voice_audio=voice_audio,
        bgm_audio=bgm_audio,
        timed_texts=timed,
        output_path=output_path,
        brand_config=brand_config,
    )
