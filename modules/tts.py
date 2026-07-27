"""Module 3: TTS via Edge-TTS (default) or ElevenLabs (optional).

Goal: reduce robotic delivery with pacing, pitch, and breath pauses.
"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path

from modules.modes import ProductionMode, parse_mode
from modules.utils import ensure_dir, get_logger, get_settings

logger = get_logger(__name__)

MARKER_PATTERN = re.compile(r"\(\s*한숨\s*\)|\(\s*잠깐\s*\)|\.{3,}")


def preprocess_script_for_tts(text: str) -> str:
    """Remove non-speech markers and insert light breath pacing."""
    text = MARKER_PATTERN.sub(", ", text)
    text = re.sub(r"\[\d+\.\s*Visual:.*?\]", "", text)
    text = re.sub(r"[\[\]#*_`]", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        return text

    # Sentence boundaries → short pause tokens Edge-TTS treats as breaks
    text = re.sub(r"([.!?…])\s*", r"\1 ... ", text)
    # Soft pause after Korean connective endings (spoken rhythm)
    text = re.sub(r"(요|죠|네요|거든요)\s+", r"\1, ", text)
    # Numbers read more naturally: "2.3배" stays, ensure space after Latin acronyms
    text = re.sub(r"\s+", " ", text).strip()
    return text


async def _synthesize_edge(
    text: str,
    output_path: Path,
    voice: str,
    rate: str,
    pitch: str,
) -> Path:
    import edge_tts

    communicate = edge_tts.Communicate(text, voice=voice, rate=rate, pitch=pitch)
    await communicate.save(str(output_path))
    return output_path


async def _synthesize_elevenlabs(text: str, output_path: Path) -> Path | None:
    settings = get_settings()
    api_key = getattr(settings, "elevenlabs_api_key", "") or ""
    voice_id = getattr(settings, "elevenlabs_voice_id", "") or ""
    if not api_key or api_key.startswith("your_") or not voice_id:
        return None

    import httpx

    url = f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"
    headers = {
        "xi-api-key": api_key,
        "Accept": "audio/mpeg",
        "Content-Type": "application/json",
    }
    payload = {
        "text": text,
        "model_id": "eleven_multilingual_v2",
        "voice_settings": {
            "stability": 0.35,
            "similarity_boost": 0.75,
            "style": 0.45,
            "use_speaker_boost": True,
        },
    }
    async with httpx.AsyncClient(timeout=90.0) as client:
        resp = await client.post(url, headers=headers, json=payload)
        resp.raise_for_status()
        output_path.write_bytes(resp.content)
    logger.info("ElevenLabs TTS saved: %s", output_path)
    return output_path


async def synthesize_speech(
    text: str,
    output_path: Path,
    voice: str | None = None,
    rate: str | None = None,
    pitch: str | None = None,
) -> Path:
    """Synthesize speech – ElevenLabs if configured, else Edge-TTS SunHi."""
    settings = get_settings()
    clean_text = preprocess_script_for_tts(text)
    if not clean_text:
        raise ValueError("TTS text is empty after preprocessing")

    ensure_dir(output_path.parent)

    try:
        eleven = await _synthesize_elevenlabs(clean_text, output_path)
        if eleven is not None and output_path.exists() and output_path.stat().st_size > 100:
            return eleven
    except Exception as exc:  # noqa: BLE001
        logger.warning("ElevenLabs failed (%s) – falling back to Edge-TTS", exc)

    voice = voice or settings.tts_voice or "ko-KR-SunHiNeural"
    # Slightly slower + mild pitch lift = less robotic than flat +10%
    rate = rate or settings.tts_rate or "-5%"
    pitch = pitch or getattr(settings, "tts_pitch", None) or "+2Hz"

    logger.info("Edge-TTS start voice=%s rate=%s pitch=%s chars=%d", voice, rate, pitch, len(clean_text))
    await _synthesize_edge(clean_text, output_path, voice=voice, rate=rate, pitch=pitch)

    if not output_path.exists() or output_path.stat().st_size < 100:
        raise RuntimeError(f"TTS output missing or empty: {output_path}")

    logger.info("Edge-TTS saved: %s (%d bytes)", output_path, output_path.stat().st_size)
    return output_path


async def run_tts_for_mode(
    mode: str | ProductionMode,
    full_text: str,
    output_path: Path,
) -> Path | None:
    """Run TTS only for voice_tts mode. Returns None for music_caption."""
    prod_mode = parse_mode(mode)
    if prod_mode != ProductionMode.VOICE_TTS:
        logger.info("Skipping TTS for mode=%s", prod_mode.value)
        await asyncio.sleep(0)
        return None
    return await synthesize_speech(full_text, output_path)
