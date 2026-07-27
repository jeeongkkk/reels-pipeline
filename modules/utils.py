"""Shared utilities: config loading, logging, paths."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import yaml
import os

from dotenv import load_dotenv
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT_DIR = Path(__file__).resolve().parent.parent
load_dotenv(ROOT_DIR / ".env")

# Streamlit Community Cloud: secrets → env vars bridge
try:
    import streamlit as st

    for key, val in st.secrets.items():
        if isinstance(val, str) and key.upper() == key and key not in os.environ:
            os.environ[key] = val
except Exception:
    pass


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(ROOT_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    groq_api_key: str = ""  # deprecated – kept for legacy env files
    groq_model: str = "llama-3.3-70b-versatile"
    anthropic_api_key: str = ""  # optional premium – not used in default hybrid stack
    anthropic_model: str = "claude-3-5-sonnet-20241022"
    gemini_api_key: str = ""
    gemini_model: str = "gemini-3.1-flash-lite"
    openai_planner_model: str = "gpt-4o-mini"
    fal_key: str = ""
    pexels_api_key: str = ""
    google_cse_api_key: str = ""
    google_cse_cx: str = ""
    openai_api_key: str = ""
    unsplash_access_key: str = ""
    tavily_api_key: str = ""
    tavily_days: int = 7  # Tavily search window – only facts from last N days
    tavily_skip_ddg_fallback: bool = True  # skip DuckDuckGo when Tavily key is set
    serpapi_api_key: str = ""
    instagram_username: str = ""
    instagram_password: str = ""
    instagram_session_path: str = "./config/instagram_session.json"
    publish_mode: str = "manual"
    tts_voice: str = "ko-KR-SunHiNeural"
    tts_rate: str = "-5%"
    tts_pitch: str = "+2Hz"
    elevenlabs_api_key: str = ""
    elevenlabs_voice_id: str = ""
    whisper_model: str = "base"
    output_width: int = 1080
    output_height: int = 1920
    output_fps: int = 30
    bgm_volume_normal: float = 0.30
    bgm_volume_ducked: float = 0.12
    projects_dir: str = "./projects"
    sfx_dir: str = "./config/sfx"
    brand_config: str = "./config/brand.yaml"
    news_rss_feeds: str = ""


def get_settings() -> Settings:
    return Settings()


def load_brand_config(path: Path | None = None) -> dict[str, Any]:
    config_path = path or (ROOT_DIR / "config" / "brand.yaml")
    with open(config_path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def get_logger(name: str) -> logging.Logger:
    # Windows cp949 consoles crash on en-dash / emoji – force replace
    import sys

    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            pass

    root = logging.getLogger()
    if not root.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(
            logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
        )

        def _emit_safe(record, _orig=handler.emit):  # type: ignore[misc]
            try:
                _orig(record)
            except UnicodeEncodeError:
                try:
                    msg = handler.format(record) + "\n"
                    sys.stdout.buffer.write(msg.encode("utf-8", errors="replace"))
                    sys.stdout.flush()
                except Exception:  # noqa: BLE001
                    pass

        handler.emit = _emit_safe  # type: ignore[method-assign]
        root.addHandler(handler)
        root.setLevel(logging.INFO)
    return logging.getLogger(name)


def ensure_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def resolve_font(font_key: str) -> str:
    """Resolve a logical font name to an existing TTF path on this OS."""
    font_map: dict[str, list[str]] = {
        "Arial-Bold": [
            "C:/Windows/Fonts/arialbd.ttf",
            "C:/Windows/Fonts/arial.ttf",
            "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
            "/System/Library/Fonts/Supplemental/Arial.ttf",
            "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
        ],
        "Malgun-Gothic-Bold": [
            "C:/Windows/Fonts/malgunbd.ttf",
            "C:/Windows/Fonts/malgun.ttf",
        ],
        "Malgun-Gothic": [
            "C:/Windows/Fonts/malgun.ttf",
        ],
    }

    candidates = font_map.get(font_key, [font_key])
    for candidate in candidates:
        if Path(candidate).exists():
            return candidate

    # Last resort: let MoviePy/Pillow try the raw string
    return font_key
