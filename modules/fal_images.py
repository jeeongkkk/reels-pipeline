"""Fal.ai Flux Schnell – casual iPhone UGC snaps for COVER hooks."""

from __future__ import annotations

import asyncio
import os
import re
from pathlib import Path

import httpx

from modules.utils import ensure_dir, get_logger, get_settings

logger = get_logger(__name__)

FAL_MODEL = "fal-ai/flux/schnell"

# Anti-AI / anti-studio master suffix – phone-camera everyday realism
FAL_MASTER_STYLE_SUFFIX = (
    "casual smartphone photography, shot on iPhone 15 Pro, unedited raw photo, "
    "UGC (User Generated Content) style, natural everyday lighting, "
    "documentary style, candid, slightly imperfect, realistic textures, "
    "NO AI look, NO studio lighting, NO 3d render, NO glossy skin"
)

# Default COVER fallback – mundane everyday snap, not a portrait studio shot
FAL_BG_PROMPT = (
    "messy real desk with crumpled receipts, iced coffee cup, open laptop half out of frame, "
    "phone charger cable tangled, natural window light, slightly imperfect framing, "
    "candid everyday workspace snapshot, "
    "3:4 vertical, no text, no watermark, no logo"
)

FAL_NEGATIVE = (
    "studio lighting, softbox, cinematic color grade, Annie Leibovitz, "
    "Vogue cover, Fast Company cover, glossy skin, beauty retouch, "
    "perfect symmetry, CGI, 3D render, illustration, cartoon, "
    "posed model smiling at camera, commercial fashion shoot, "
    "empty black void, abstract dark gradient, masterpiece, 8k ultra detailed, "
    "text, watermark, logo"
)

_LEGACY_STUDIO_FLUFF = re.compile(
    r"(?:,\s*)?(?:"
    r"Annie Leibovitz|"
    r"high-end commercial photography|"
    r"Vogue or Fast Company|"
    r"magazine cover style|"
    r"teal and orange|"
    r"cinematic lighting|"
    r"shot on 35mm lens|"
    r"Hasselblad|"
    r"medium format|"
    r"masterpiece|"
    r"8k resolution|"
    r"ultra-premium|"
    r"award-winning|"
    r"perfect composition|"
    r"striking human expression|"
    r"close-up of a young (?:Korean )?founder|"
    r"confident stylish businesswoman|"
    r"people cheering in a pop-colored studio|"
    r"no people|"
    r"empty (?:space|atrium|office|center)|"
    r"abstract dark|"
    r"moody empty|"
    r"dark cinematic empty"
    r")[^,]*",
    re.I,
)


class FalNotConfiguredError(RuntimeError):
    pass


def _ensure_fal_key() -> str:
    settings = get_settings()
    key = (settings.fal_key or "").strip()
    if not key or key.startswith("your_"):
        raise FalNotConfiguredError("FAL_KEY not configured")
    os.environ["FAL_KEY"] = key
    return key


def apply_master_style(prompt: str) -> str:
    """Strip studio/AI-gloss leftovers; append iPhone UGC suffix."""
    text = re.sub(r"\s+", " ", (prompt or "").strip()).strip(" ,")
    if not text or re_is_abstract_dark(text) or re_is_studio_glamour(text):
        text = FAL_BG_PROMPT
    text = _LEGACY_STUDIO_FLUFF.sub("", text)
    text = re.sub(r",\s*,+", ", ", text).strip(" ,")
    low = text.lower()
    everyday_cues = (
        "desk",
        "receipt",
        "coffee",
        "phone",
        "hand",
        "laptop",
        "street",
        "cafe",
        "notebook",
        "bag",
        "back view",
        "from behind",
        "commute",
        "office desk",
        "workspace",
        "iphone",
        "smartphone",
        "ugc",
        "candid",
    )
    if not any(c in low for c in everyday_cues):
        text = (
            f"{text}, casual everyday object or candid back-view scene, "
            "messy real-life details"
        )
    if "iphone" not in low and "ugc" not in low and "smartphone photography" not in low:
        text = f"{text}, {FAL_MASTER_STYLE_SUFFIX}"
    if "3:4" not in text and "vertical" not in low:
        text = f"{text}, 3:4 vertical"
    return text


async def generate_flux_background(
    dest: Path,
    *,
    index: int = 0,
    seed: int | None = None,
    prompt: str | None = None,
) -> Path:
    """Generate one portrait COVER via fal-ai/flux/schnell (always fresh call)."""
    _ensure_fal_key()
    ensure_dir(dest.parent)
    use_prompt = apply_master_style(prompt or FAL_BG_PROMPT)
    use_prompt = f"{use_prompt}, unique candid frame variant {index + 1}"
    use_seed = (seed if seed is not None else index * 9973 + 4177) & 0x7FFFFFFF
    logger.info(
        "[UGC 톤 검증] Fal slide %d final_prompt=%s",
        index + 1,
        use_prompt[:320],
    )

    def _subscribe() -> dict:
        import fal_client

        iw, ih = 1080, 1350
        try:
            from modules.card_format import get_active_card_format

            fmt = get_active_card_format()
            iw, ih = fmt.logical_w, fmt.logical_h
        except Exception:  # noqa: BLE001
            pass

        return fal_client.subscribe(
            FAL_MODEL,
            arguments={
                "prompt": use_prompt,
                "negative_prompt": FAL_NEGATIVE,
                "image_size": {"width": iw, "height": ih},
                "num_inference_steps": 4,
                "seed": use_seed,
            },
        )

    loop = asyncio.get_running_loop()
    try:
        result = await loop.run_in_executor(None, _subscribe)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Fal.ai subscribe failed slide %d (%s)", index + 1, exc)
        raise

    images = (result or {}).get("images") or []
    if not images:
        raise RuntimeError("Fal.ai returned no images")

    url = str(images[0].get("url") or "")
    if not url:
        raise RuntimeError("Fal.ai image missing url")

    tmp = dest.with_suffix(".tmp.jpg")
    async with httpx.AsyncClient(timeout=httpx.Timeout(60.0, connect=10.0)) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        tmp.write_bytes(resp.content)

    if dest.exists() and dest.resolve() != tmp.resolve():
        dest.unlink(missing_ok=True)
    tmp.replace(dest)
    logger.info("Fal.ai Flux OK slide %d -> %s", index + 1, dest.name)
    return dest


def re_is_studio_glamour(prompt: str) -> bool:
    low = (prompt or "").lower()
    return any(
        k in low
        for k in (
            "annie leibovitz",
            "vogue",
            "fast company",
            "studio lighting",
            "beauty retouch",
            "glossy skin",
            "commercial photography",
            "magazine cover",
        )
    )


def re_is_abstract_dark(prompt: str) -> bool:
    low = (prompt or "").lower()
    banned = (
        "abstract dark",
        "dark minimalist background",
        "empty center area",
        "geometric design",
        "deep subtle gradient",
        "empty black void",
        "3d render only",
        "moody empty",
        "dark cinematic empty",
    )
    if any(
        k in low
        for k in (
            "iphone",
            "ugc",
            "smartphone",
            "receipt",
            "coffee",
            "desk",
            "candid",
            "everyday",
        )
    ):
        return False
    return any(b in low for b in banned)
