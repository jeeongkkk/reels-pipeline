"""SFX generation and placement helpers for Module 5."""

from __future__ import annotations

import wave
from pathlib import Path

import numpy as np

from modules.utils import ROOT_DIR, ensure_dir, get_logger, load_brand_config

logger = get_logger(__name__)
SAMPLE_RATE = 44100


def sfx_dir() -> Path:
    brand = load_brand_config()
    # Prefer settings path via brand relative to ROOT
    path = ROOT_DIR / "config" / "sfx"
    return ensure_dir(path)


def _write_wav(path: Path, signal: np.ndarray) -> Path:
    ensure_dir(path.parent)
    signal = np.clip(signal, -1.0, 1.0)
    pcm = (signal * 32767).astype(np.int16)
    with wave.open(str(path), "w") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(SAMPLE_RATE)
        wf.writeframes(pcm.tobytes())
    # MoviePy accepts wav; keep .mp3 extension alias by copying bytes for brand filenames
    return path


def generate_typing_click(path: Path) -> Path:
    """Short UI click / caption pop."""
    duration = 0.08
    t = np.linspace(0, duration, int(SAMPLE_RATE * duration), endpoint=False)
    env = np.exp(-t * 55)
    click = env * (0.55 * np.sin(2 * np.pi * 1800 * t) + 0.25 * np.sin(2 * np.pi * 3200 * t))
    return _write_wav(path, click)


def generate_swish(path: Path) -> Path:
    """Whoosh / transition swish."""
    duration = 0.28
    n = int(SAMPLE_RATE * duration)
    t = np.linspace(0, duration, n, endpoint=False)
    noise = np.random.default_rng(42).normal(0, 1, n)
    # Band-ish shaping via moving average + rising then falling envelope
    kernel = np.ones(40) / 40
    whoosh = np.convolve(noise, kernel, mode="same")
    env = np.sin(np.pi * (t / duration)) ** 1.4
    # Sweep emphasis
    sweep = np.sin(2 * np.pi * (400 + 1200 * t / duration) * t) * 0.15
    signal = 0.35 * whoosh * env + sweep * env
    peak = np.max(np.abs(signal)) + 1e-9
    signal = signal / peak * 0.7
    return _write_wav(path, signal)


def ensure_default_sfx() -> dict[str, Path]:
    """Create default SFX files if missing. Returns logical name → path."""
    directory = sfx_dir()
    brand = load_brand_config()
    cfg = brand.get("audio", {}).get("sfx", {})

    click_name = cfg.get("caption_pop", "typing_click.mp3")
    swish_name = cfg.get("transition", "swish.mp3")

    # Store as .wav (reliable); also write same basename with .wav even if yaml says .mp3
    click_wav = directory / (Path(click_name).stem + ".wav")
    swish_wav = directory / (Path(swish_name).stem + ".wav")

    if not click_wav.exists() or click_wav.stat().st_size < 100:
        generate_typing_click(click_wav)
        logger.info("Generated SFX: %s", click_wav)
    if not swish_wav.exists() or swish_wav.stat().st_size < 100:
        generate_swish(swish_wav)
        logger.info("Generated SFX: %s", swish_wav)

    return {"caption_pop": click_wav, "transition": swish_wav}


def load_sfx_array(path: Path, target_fps: int = 44100) -> np.ndarray:
    """Load mono/stereo wav as float64 array shaped (n, channels)."""
    with wave.open(str(path), "rb") as wf:
        channels = wf.getnchannels()
        rate = wf.getframerate()
        frames = wf.readframes(wf.getnframes())
        width = wf.getsampwidth()

    if width == 2:
        data = np.frombuffer(frames, dtype=np.int16).astype(np.float64) / 32768.0
    else:
        data = np.frombuffer(frames, dtype=np.uint8).astype(np.float64)
        data = (data - 128.0) / 128.0

    if channels > 1:
        data = data.reshape(-1, channels)
    else:
        data = data.reshape(-1, 1)

    if rate != target_fps and len(data) > 1:
        duration = len(data) / rate
        new_len = max(1, int(duration * target_fps))
        old_x = np.linspace(0, 1, len(data))
        new_x = np.linspace(0, 1, new_len)
        resampled = np.zeros((new_len, data.shape[1]), dtype=np.float64)
        for c in range(data.shape[1]):
            resampled[:, c] = np.interp(new_x, old_x, data[:, c])
        data = resampled

    return data


def resolve_sfx_paths(brand_config: dict | None = None) -> dict[str, Path]:
    paths = ensure_default_sfx()
    brand = brand_config or load_brand_config()
    cfg = brand.get("audio", {}).get("sfx", {})
    directory = sfx_dir()

    resolved: dict[str, Path] = {}
    for key, default_path in paths.items():
        name = cfg.get(key)
        if not name:
            resolved[key] = default_path
            continue
        candidate = directory / name
        alt = directory / (Path(name).stem + ".wav")
        if candidate.exists():
            resolved[key] = candidate
        elif alt.exists():
            resolved[key] = alt
        else:
            resolved[key] = default_path
    return resolved
