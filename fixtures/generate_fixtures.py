"""Generate sample fixtures for Module 5 POC testing.

Run: python fixtures/generate_fixtures.py
Creates sample_bg.mp4, sample_voice.mp3, sample_bgm.mp3 if missing.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np

FIXTURES_DIR = Path(__file__).resolve().parent
SAMPLE_RATE = 44100


def _write_wav_as_mp3_fallback(wav_path: Path, mp3_path: Path) -> None:
    """Write WAV file; rename to .mp3 extension for MoviePy compatibility in POC."""
    import wave

    # If scipy available, use it; otherwise raw WAV with mp3 extension works for MoviePy
    mp3_path.write_bytes(wav_path.read_bytes())
    wav_path.unlink(missing_ok=True)


def generate_voice_audio(output_path: Path, duration: float = 15.0) -> Path:
    """Generate synthetic voice-like audio with amplitude bursts per word."""
    timestamps_path = FIXTURES_DIR / "sample_timestamps.json"
    with open(timestamps_path, encoding="utf-8") as f:
        data = json.load(f)

    n_samples = int(duration * SAMPLE_RATE)
    t = np.linspace(0, duration, n_samples, endpoint=False)
    signal = np.zeros(n_samples, dtype=np.float64)

    for word in data["words"]:
        start = int(word["start"] * SAMPLE_RATE)
        end = int(word["end"] * SAMPLE_RATE)
        word_t = t[start:end]
        if len(word_t) == 0:
            continue
        # Speech-like formant mix
        freq = 180 + (hash(word["text"]) % 120)
        burst = (
            0.35 * np.sin(2 * np.pi * freq * word_t)
            + 0.15 * np.sin(2 * np.pi * (freq * 2.1) * word_t)
        )
        envelope = np.linspace(0.1, 1.0, len(word_t)) * np.linspace(1.0, 0.1, len(word_t))
        signal[start:end] = burst * envelope

    signal = signal / (np.max(np.abs(signal)) + 1e-9) * 0.85

    import wave

    wav_path = output_path.with_suffix(".wav")
    with wave.open(str(wav_path), "w") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(SAMPLE_RATE)
        pcm = (signal * 32767).astype(np.int16)
        wf.writeframes(pcm.tobytes())

    _write_wav_as_mp3_fallback(wav_path, output_path)
    print(f"  [ok] voice -> {output_path}")
    return output_path


def generate_bgm_audio(output_path: Path, duration: float = 45.0) -> Path:
    """Generate a richer soft corporate ambient BGM (still $0 / local)."""
    n_samples = int(duration * SAMPLE_RATE)
    t = np.linspace(0, duration, n_samples, endpoint=False)
    rng = np.random.default_rng(7)

    # Soft pad (minor-ish chord stack)
    pad = (
        0.10 * np.sin(2 * np.pi * 130.81 * t)  # C3
        + 0.07 * np.sin(2 * np.pi * 164.81 * t)  # E3
        + 0.06 * np.sin(2 * np.pi * 196.00 * t)  # G3
        + 0.04 * np.sin(2 * np.pi * 261.63 * t)  # C4
    )
    pad *= 0.75 + 0.25 * np.sin(2 * np.pi * 0.08 * t)

    # Gentle pulse / pluck every ~2s
    pulse = np.zeros_like(t)
    for beat in np.arange(0, duration, 2.0):
        idx = int(beat * SAMPLE_RATE)
        length = int(0.35 * SAMPLE_RATE)
        if idx + length >= n_samples:
            break
        env = np.linspace(1.0, 0.0, length) ** 2
        tt = np.arange(length) / SAMPLE_RATE
        pulse[idx : idx + length] += env * 0.09 * np.sin(2 * np.pi * 392.0 * tt)

    # Very light filtered noise bed
    noise = rng.normal(0, 0.015, n_samples)
    kernel = np.ones(120) / 120
    noise = np.convolve(noise, kernel, mode="same")

    bgm = pad + pulse + noise
    # Fade in/out
    fade = int(0.8 * SAMPLE_RATE)
    bgm[:fade] *= np.linspace(0, 1, fade)
    bgm[-fade:] *= np.linspace(1, 0, fade)
    peak = np.max(np.abs(bgm)) + 1e-9
    bgm = bgm / peak * 0.55

    import wave

    wav_path = output_path.with_suffix(".wav")
    with wave.open(str(wav_path), "w") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(SAMPLE_RATE)
        pcm = (bgm * 32767).astype(np.int16)
        wf.writeframes(pcm.tobytes())

    _write_wav_as_mp3_fallback(wav_path, output_path)
    print(f"  [ok] bgm -> {output_path}")
    return output_path


def generate_background_video(output_path: Path, duration: float = 15.0) -> Path:
    """Generate a 9:16 gradient background video using MoviePy."""
    from moviepy import ColorClip, CompositeVideoClip
    from moviepy.video.VideoClip import VideoClip

    w, h = 1080, 1920

    def make_frame(t):
        progress = t / duration
        r = int(20 + progress * 30)
        g = int(40 + progress * 20)
        b = int(80 + (1 - progress) * 40)
        frame = np.zeros((h, w, 3), dtype=np.uint8)
        frame[:, :] = [r, g, b]
        return frame

    gradient = VideoClip(make_frame, duration=duration)
    accent = ColorClip(size=(w, 80), color=(255, 215, 0)).with_opacity(0.15)
    accent = accent.with_position(("center", h * 0.45)).with_duration(duration)

    video = CompositeVideoClip([gradient, accent], size=(w, h))
    video.write_videofile(
        str(output_path),
        fps=30,
        codec="libx264",
        audio=False,
        logger=None,
    )
    video.close()
    print(f"  [ok] video -> {output_path}")
    return output_path


def generate_all(force: bool = False) -> dict[str, Path]:
    """Generate all fixture files if they don't exist."""
    paths = {
        "voice": FIXTURES_DIR / "sample_voice.mp3",
        "bgm": FIXTURES_DIR / "sample_bgm.mp3",
        "video": FIXTURES_DIR / "sample_bg.mp4",
        "timestamps": FIXTURES_DIR / "sample_timestamps.json",
    }

    print("Generating fixtures...")
    if force or not paths["voice"].exists():
        generate_voice_audio(paths["voice"])
    if force or not paths["bgm"].exists():
        generate_bgm_audio(paths["bgm"])
    if force or not paths["video"].exists():
        generate_background_video(paths["video"])

    print("Fixtures ready.")
    return paths


if __name__ == "__main__":
    generate_all()
