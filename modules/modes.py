"""Production mode selection for Phase B+."""

from __future__ import annotations

from enum import Enum


class ProductionMode(str, Enum):
    """How the reel / card pack is produced after research/hooks."""

    CARD_NEWS = "card_news"  # High-end retina PNG card pack + ZIP
    VOICE_TTS = "voice_tts"  # Narration script + Edge-TTS + B-roll
    MUSIC_CAPTION = "music_caption"  # BGM + on-screen captions only (no voice)


MODE_LABELS = {
    ProductionMode.CARD_NEWS: "카드뉴스 PNG (하이엔드)",
    ProductionMode.VOICE_TTS: "대본 + TTS (나레이션)",
    ProductionMode.MUSIC_CAPTION: "음악 + 자막만 (보이스 없음)",
}


def parse_mode(value: str | ProductionMode | None) -> ProductionMode:
    if isinstance(value, ProductionMode):
        return value
    if not value:
        return ProductionMode.CARD_NEWS
    normalized = str(value).strip().lower().replace("-", "_").replace(" ", "_")
    aliases = {
        "card": ProductionMode.CARD_NEWS,
        "card_news": ProductionMode.CARD_NEWS,
        "cheddar": ProductionMode.CARD_NEWS,
        "cards": ProductionMode.CARD_NEWS,
        "png": ProductionMode.CARD_NEWS,
        "voice": ProductionMode.VOICE_TTS,
        "tts": ProductionMode.VOICE_TTS,
        "voice_tts": ProductionMode.VOICE_TTS,
        "narration": ProductionMode.VOICE_TTS,
        "music": ProductionMode.MUSIC_CAPTION,
        "caption": ProductionMode.MUSIC_CAPTION,
        "music_caption": ProductionMode.MUSIC_CAPTION,
        "music_only": ProductionMode.MUSIC_CAPTION,
    }
    return aliases.get(normalized, ProductionMode.CARD_NEWS)
