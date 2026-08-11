"""PIL card compositor – COVER (photo) / CONTENT·SUMMARY (light studio)."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont

from modules.card_format import RETINA, get_active_card_format, set_active_card_format
from modules.utils import ROOT_DIR, ensure_dir, get_logger

logger = get_logger(__name__)

# Defaults kept for import compatibility; live size comes from active format
CARD_W = 1080
CARD_H = 1350  # Instagram feed 4:5 logical @1x → 2160×2700 @2x


# ── WITHCHOYOOL Business Studio palette ───────────────────────
BG_DARK = (21, 21, 21)
BG_LIGHT = (250, 250, 250)  # #fafafa
ACCENT = (252, 77, 1)  # #fc4d01
PURE_WHITE = (255, 255, 255)
SOFT_WHITE = (221, 221, 221)
INK = (17, 17, 17)  # #111111
INK_SOFT = (34, 34, 34)
SOURCE_GRAY = (136, 136, 136)  # #888888

MARGIN_X = 80
BRAND_FALLBACK = "WITHCHOYOOL"
DEFAULT_BRAND_COLOR = "#fc4d01"
COVER_IG_HANDLE = "@with.choyool"
TEXT_LOGO = "WITHCHOYOOL"

COVER_GRAD_BOTTOM_ALPHA = 240
COVER_GRAD_SOLID_RATIO = 0.22
COVER_GRAD_FADE_RATIO = 0.34
COVER_TEXT_Y_RATIO = 0.52
COVER_TITLE_BASE = 72
COVER_TITLE_MIN = 48
COVER_LEFT_RATIO = 0.11
COVER_MAX_W_RATIO = 0.82
COVER_LINE_GAP = 16
COVER_BOTTOM_MARGIN = 90
COVER_HANDLE_SIZE = 30
COVER_HANDLE_GAP = 22
COVER_BOX_PAD_X = 16
COVER_BOX_PAD_Y = 10
COVER_BOX_RADIUS = 8

CONTENT_LEFT = 100
CONTENT_HEADER_BASE = 50  # orange box subtitle ~50px
CONTENT_BODY_BASE = 38  # body ~38–40px (smaller, more air)
CONTENT_LINE_FACTOR = 1.95  # airier leading with smaller type
CONTENT_HEADER_GAP = 72  # box → first body line
SUMMARY_TITLE_BASE = 50
SUMMARY_ITEM_BASE = 38
SUMMARY_LINE_STEP = 1.95
SUMMARY_HEADER_GAP = 72

OUTRO_TITLE_BASE = 92  # 90–100
OUTRO_SUB_BASE = 34
OUTRO_TITLE_GAP = 18
OUTRO_SUB_GAP = 40
OUTRO_SOURCE_SIZE = 22  # 22–25
OUTRO_SOURCE_RIGHT = 80
OUTRO_SOURCE_BOTTOM = 80
OUTRO_HIGHLIGHT = "전략 기획실"
OUTRO_FIXED_LINES = ["당신의", "전략 기획실이", "되어드립니다."]
_QUOTE_CHARS = "'\"‘’“”‚‛«»‹›｀＇"

HEADER_PAD_X = 22
HEADER_PAD_TOP = 14
HEADER_PAD_BOTTOM = 16
WORDMARK_Y = 120
WORDMARK_SIZE = 42

FONTS_DIR = ROOT_DIR / "assets" / "fonts" / "Pretendard"
PAPERLOGY_DIR = ROOT_DIR / "assets" / "fonts" / "Paperlogy"
FREESENT_DIR = ROOT_DIR / "assets" / "fonts" / "Freesentation"
WIN_FONTS = Path("C:/Windows/Fonts")

# Active typography for current render batch (set by render_all_pil)
_ACTIVE_TYPE: dict[str, str] = {
    "family": "pretendard",
    "title_weight": "extrabold",
    "body_weight": "regular",
    "wordmark_weight": "bold",
    "handle_weight": "medium",
    "sub_weight": "regular",
}


def set_active_type_style(style: dict[str, str] | None = None) -> None:
    from modules.typography import type_style_from_mapping

    data = type_style_from_mapping(style or {}).to_dict()
    _ACTIVE_TYPE.clear()
    _ACTIVE_TYPE.update(data)


def get_active_type_style() -> dict[str, str]:
    return dict(_ACTIVE_TYPE)


def _resolve_font(name: str) -> Path | None:
    for folder in (FONTS_DIR, PAPERLOGY_DIR, FREESENT_DIR):
        cand = folder / name
        if cand.exists() and cand.stat().st_size > 1000:
            return cand
    mapping = {
        "Pretendard-Regular.otf": ("malgun.ttf", "arial.ttf"),
        "Pretendard-Medium.otf": ("malgun.ttf", "arial.ttf"),
        "Pretendard-SemiBold.otf": ("malgunbd.ttf", "arialbd.ttf"),
        "Pretendard-Bold.otf": ("malgunbd.ttf", "arialbd.ttf"),
        "Pretendard-ExtraBold.otf": ("malgunbd.ttf", "arialbd.ttf"),
        "Pretendard-Black.otf": ("malgunbd.ttf", "arialbd.ttf"),
        "Paperlogy-8ExtraBold.ttf": ("malgunbd.ttf", "arialbd.ttf"),
        "Paperlogy-9Black.ttf": ("malgunbd.ttf", "arialbd.ttf"),
        "Paperlogy-7Bold.ttf": ("malgunbd.ttf", "arialbd.ttf"),
        "Freesentation-8ExtraBold.ttf": ("malgunbd.ttf", "arialbd.ttf"),
        "Freesentation-9Black.ttf": ("malgunbd.ttf", "arialbd.ttf"),
        "malgun.ttf": ("malgun.ttf",),
        "malgunbd.ttf": ("malgunbd.ttf",),
    }
    for sys_name in mapping.get(name, ("malgunbd.ttf", "malgun.ttf")):
        p = WIN_FONTS / sys_name
        if p.exists():
            return p
    return None


def _weight_file_list(family: str, weight: str) -> list[str]:
    """Map logical weight → candidate font filenames (nearest fallbacks included)."""
    w = (weight or "regular").lower()
    fam = (family or "pretendard").lower()

    paper = {
        "thin": ["Paperlogy-1Thin.ttf", "Paperlogy-2ExtraLight.ttf", "Paperlogy-3Light.ttf"],
        "extralight": ["Paperlogy-2ExtraLight.ttf", "Paperlogy-3Light.ttf", "Paperlogy-1Thin.ttf"],
        "light": ["Paperlogy-3Light.ttf", "Paperlogy-4Regular.ttf", "Paperlogy-2ExtraLight.ttf"],
        "regular": ["Paperlogy-4Regular.ttf", "Paperlogy-5Medium.ttf", "Pretendard-Regular.otf"],
        "medium": ["Paperlogy-5Medium.ttf", "Paperlogy-6SemiBold.ttf", "Paperlogy-4Regular.ttf"],
        "semibold": ["Paperlogy-6SemiBold.ttf", "Paperlogy-7Bold.ttf", "Pretendard-SemiBold.otf"],
        "bold": ["Paperlogy-7Bold.ttf", "Paperlogy-8ExtraBold.ttf", "Pretendard-Bold.otf"],
        "extrabold": ["Paperlogy-8ExtraBold.ttf", "Paperlogy-9Black.ttf", "Pretendard-ExtraBold.otf"],
        "black": ["Paperlogy-9Black.ttf", "Paperlogy-8ExtraBold.ttf", "Pretendard-Black.otf"],
    }
    free = {
        "thin": ["Freesentation-1Thin.ttf", "Freesentation-2ExtraLight.ttf"],
        "extralight": ["Freesentation-2ExtraLight.ttf", "Freesentation-3Light.ttf"],
        "light": ["Freesentation-3Light.ttf", "Freesentation-4Regular.ttf"],
        "regular": ["Freesentation-4Regular.ttf", "Pretendard-Regular.otf"],
        "medium": ["Freesentation-5Medium.ttf", "Freesentation-4Regular.ttf"],
        "semibold": ["Freesentation-6SemiBold.ttf", "Freesentation-7Bold.ttf", "Pretendard-SemiBold.otf"],
        "bold": ["Freesentation-7Bold.ttf", "Freesentation-8ExtraBold.ttf", "Pretendard-Bold.otf"],
        "extrabold": ["Freesentation-8ExtraBold.ttf", "Freesentation-9Black.ttf", "Pretendard-ExtraBold.otf"],
        "black": ["Freesentation-9Black.ttf", "Freesentation-8ExtraBold.ttf", "Pretendard-Black.otf"],
    }
    # Pretendard pack in repo: Regular / SemiBold / Bold / ExtraBold / Black
    pret = {
        "thin": ["Pretendard-Regular.otf", "Paperlogy-1Thin.ttf"],
        "extralight": ["Pretendard-Regular.otf", "Paperlogy-2ExtraLight.ttf"],
        "light": ["Pretendard-Regular.otf", "Paperlogy-3Light.ttf"],
        "regular": ["Pretendard-Regular.otf"],
        "medium": ["Pretendard-SemiBold.otf", "Pretendard-Regular.otf", "Paperlogy-5Medium.ttf"],
        "semibold": ["Pretendard-SemiBold.otf", "Pretendard-Bold.otf"],
        "bold": ["Pretendard-Bold.otf", "Pretendard-ExtraBold.otf"],
        "extrabold": ["Pretendard-ExtraBold.otf", "Pretendard-Black.otf", "Pretendard-Bold.otf"],
        "black": ["Pretendard-Black.otf", "Pretendard-ExtraBold.otf"],
    }
    malgun = {
        "thin": ["malgun.ttf"],
        "extralight": ["malgun.ttf"],
        "light": ["malgun.ttf"],
        "regular": ["malgun.ttf"],
        "medium": ["malgun.ttf", "malgunbd.ttf"],
        "semibold": ["malgunbd.ttf", "malgun.ttf"],
        "bold": ["malgunbd.ttf"],
        "extrabold": ["malgunbd.ttf"],
        "black": ["malgunbd.ttf"],
    }

    table = pret
    if fam == "paperlogy":
        table = paper
    elif fam == "freesentation":
        table = free
    elif fam == "malgun":
        table = malgun
    return list(table.get(w) or table.get("regular") or ["Pretendard-Regular.otf"])


def _font(
    size: int,
    *,
    weight: str = "bold",
    family: str = "pretendard",
) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    size = max(12, int(size))
    for fname in _weight_file_list(family, weight):
        path = _resolve_font(fname)
        if path:
            try:
                return ImageFont.truetype(str(path), size=size)
            except OSError:
                continue
    return ImageFont.load_default()


def _text_width(text: str, font: ImageFont.ImageFont) -> float:
    try:
        return font.getlength(text)
    except AttributeError:
        return font.getsize(text)[0]


def _text_bbox(text: str, font: ImageFont.ImageFont) -> tuple[int, int, int, int]:
    try:
        box = font.getbbox(text or " ")
        return int(box[0]), int(box[1]), int(box[2]), int(box[3])
    except Exception:  # noqa: BLE001
        try:
            w, h = font.getsize(text or " ")
            return 0, 0, int(w), int(h)
        except Exception:  # noqa: BLE001
            size = int(getattr(font, "size", 40) or 40)
            return 0, 0, max(1, int(_text_width(text or " ", font))), size


def _text_height(text: str, font: ImageFont.ImageFont) -> int:
    left, top, right, bottom = _text_bbox(text, font)
    return max(1, bottom - top)


def _clean_display(text: str, *, keep_emphasis: bool = False) -> str:
    t = re.sub(r"[ \t]+", " ", (text or "").strip())
    t = t.rstrip(".。…!")
    if not keep_emphasis:
        t = t.replace("*", "")
    return t


def _as_lines(raw: Any, *, keep_emphasis: bool = False) -> list[str]:
    if isinstance(raw, list):
        return [
            _clean_display(str(x), keep_emphasis=keep_emphasis)
            for x in raw
            if str(x).strip()
        ]
    if isinstance(raw, str) and raw.strip():
        return [
            _clean_display(p, keep_emphasis=keep_emphasis)
            for p in raw.split("\n")
            if p.strip()
        ]
    return []


def _sanitize_emphasis_marks(text: str) -> str:
    """Drop awkward *spans* (particles / 1-char) before drawing."""
    bad_alone = {
        "에",
        "을",
        "를",
        "이",
        "가",
        "은",
        "는",
        "의",
        "과",
        "와",
        "시",
        "로",
        "으로",
        "기업",
        "개발에",
        "신청 시",
        "신청시",
    }

    def repl(m: re.Match[str]) -> str:
        inner = (m.group(1) or "").strip()
        if not inner or inner in bad_alone or len(inner) <= 1:
            return inner
        if re.fullmatch(r"[은는이가을를의과와에도만]+", inner):
            return inner
        return f"*{inner}*"

    return re.sub(r"\*([^*]+)\*", repl, text or "")


def _parse_emphasis(text: str) -> list[tuple[str, bool]]:
    """Split on '*' – even index = normal, odd index = accent."""
    raw = _sanitize_emphasis_marks(text or "")
    parts = raw.split("*")
    out: list[tuple[str, bool]] = []
    for i, part in enumerate(parts):
        if not part:
            continue
        out.append((part, i % 2 == 1))
    if not out:
        return [(raw.replace("*", ""), False)]
    return out


def _plain_from_emphasis(text: str) -> str:
    return _sanitize_emphasis_marks(text or "").replace("*", "")


def _draw_centered_emphasis_line(
    draw: ImageDraw.ImageDraw,
    text: str,
    *,
    y: int,
    canvas_w: int,
    font: ImageFont.ImageFont,
    accent: tuple[int, int, int],
    default_fill: tuple[int, int, int] = PURE_WHITE,
) -> int:
    """'*' split highlight: odd parts accent; advance X by font.getlength()."""
    text = _sanitize_emphasis_marks(text)
    plain = _plain_from_emphasis(text)
    if not plain.strip():
        return y
    parts = text.split("*")
    total_w = int(_text_width(plain, font))
    _, top0, _, bottom0 = _text_bbox(plain, font)
    current_x = (canvas_w - total_w) // 2
    for i, part in enumerate(parts):
        if not part:
            continue
        fill = accent if (i % 2 == 1) else default_fill
        draw.text((current_x, y - top0), part, fill=fill, font=font)
        current_x += int(_text_width(part, font))
    return y + (bottom0 - top0)


def _fit_single_line(
    text: str,
    *,
    max_width: int,
    base_size: int,
    scale: int,
    weight: str = "black",
    min_size: int = 28,
    family: str = "pretendard",
) -> tuple[int, ImageFont.ImageFont]:
    plain = _plain_from_emphasis(text)
    size = base_size
    while size >= min_size:
        font = _font(size * scale, weight=weight, family=family)
        if _text_width(plain, font) <= max_width:
            return size, font
        size -= 2
    return min_size, _font(min_size * scale, weight=weight, family=family)


def _hex_rgb(color: str) -> tuple[int, int, int]:
    c = (color or DEFAULT_BRAND_COLOR).lstrip("#")
    if len(c) == 6:
        return int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16)
    return ACCENT


def _fit_cover(img: Image.Image, w: int, h: int) -> Image.Image:
    img = img.convert("RGB")
    sw, sh = img.size
    target_ratio = w / h
    src_ratio = sw / max(sh, 1)
    if src_ratio > target_ratio:
        new_w = int(sh * target_ratio)
        left = (sw - new_w) // 2
        box = (left, 0, left + new_w, sh)
    else:
        new_h = int(sw / target_ratio)
        top = (sh - new_h) // 2
        box = (0, top, sw, top + new_h)
    return img.crop(box).resize((w, h), Image.Resampling.LANCZOS)


def _draw_full_dim(base: Image.Image, *, alpha: float) -> None:
    a = max(0.0, min(1.0, float(alpha)))
    overlay = Image.new("RGBA", base.size, (0, 0, 0, int(255 * a)))
    composited = Image.alpha_composite(base.convert("RGBA"), overlay)
    base.paste(composited.convert("RGB"))


def _apply_top_linear_gradient(
    base: Image.Image,
    *,
    top_alpha: int = 220,
    fade_ratio: float = 0.55,
) -> None:
    """Legacy top-down gradient (kept for optional use)."""
    w, h = base.size
    fade_h = max(1, int(h * max(0.35, min(0.7, fade_ratio))))
    overlay = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    top_a = max(0, min(255, int(top_alpha)))
    for y in range(fade_h):
        t = y / max(fade_h - 1, 1)
        a = int(top_a * (1.0 - t))
        if a <= 0:
            continue
        draw.line([(0, y), (w, y)], fill=(0, 0, 0, a))
    composited = Image.alpha_composite(base.convert("RGBA"), overlay)
    base.paste(composited.convert("RGB"))


def _apply_bottom_linear_gradient(
    base: Image.Image,
    *,
    bottom_alpha: int = COVER_GRAD_BOTTOM_ALPHA,
    solid_ratio: float = COVER_GRAD_SOLID_RATIO,
    fade_ratio: float = COVER_GRAD_FADE_RATIO,
) -> None:
    """Young Boss style: solid black bottom → soft fade upward (photo stays clear on top)."""
    w, h = base.size
    solid_h = max(1, int(h * max(0.15, min(0.45, solid_ratio))))
    fade_h = max(1, int(h * max(0.15, min(0.5, fade_ratio))))
    overlay = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    bot_a = max(0, min(255, int(bottom_alpha)))

    # Solid black footer
    for y in range(h - solid_h, h):
        draw.line([(0, y), (w, y)], fill=(0, 0, 0, bot_a))

    # Fade upward from solid edge
    for i in range(fade_h):
        # i=0 near photo (transparent), i=fade_h-1 near solid (opaque)
        y = h - solid_h - fade_h + i
        if y < 0 or y >= h:
            continue
        t = i / max(fade_h - 1, 1)
        a = int(bot_a * t)
        if a <= 0:
            continue
        draw.line([(0, y), (w, y)], fill=(0, 0, 0, a))

    composited = Image.alpha_composite(base.convert("RGBA"), overlay)
    base.paste(composited.convert("RGB"))


def _canvas_layout(scale: int) -> dict[str, int | float]:
    """Active format canvas + Instagram safe-zone box."""
    fmt = get_active_card_format()
    w, h = fmt.canvas_px(scale)
    left, top, right, bottom = fmt.safe_box(scale)
    return {
        "w": w,
        "h": h,
        "left": left,
        "top": top,
        "right": right,
        "bottom": bottom,
        "max_w": max(40 * scale, right - left),
        "vs": fmt.v_scale,
    }


def draw_text_wordmark(
    base: Image.Image,
    *,
    scale: int,
    y: int | None = None,
    label: str | None = None,
    accent: tuple[int, int, int] | None = None,
) -> int:
    """Top-center text logo in brand accent. Returns bottom Y."""
    style = get_active_type_style()
    draw = ImageDraw.Draw(base)
    w, _h = base.size
    lay = _canvas_layout(scale)
    font = _font(
        WORDMARK_SIZE * scale,
        weight=style.get("wordmark_weight", "bold"),
        family=style.get("family", "pretendard"),
    )
    text = (label or TEXT_LOGO).strip() or TEXT_LOGO
    fill = accent or ACCENT
    left, top, right, bottom = _text_bbox(text, font)
    tw = right - left
    th = bottom - top
    x = (w - tw) // 2 - left
    # Keep wordmark inside safe zone (critical for reels UI chrome)
    default_y = max(int(lay["top"]), int(WORDMARK_Y * scale * min(1.0, float(lay["vs"]))))
    yy = default_y if y is None else y
    draw.text((x, yy - top), text, fill=fill, font=font)
    return yy + th


def _draw_left_emphasis_line(
    draw: ImageDraw.ImageDraw,
    text: str,
    *,
    y: int,
    x: int,
    font: ImageFont.ImageFont,
    accent: tuple[int, int, int],
    default_fill: tuple[int, int, int] = INK,
) -> int:
    """Left-aligned *emphasis* line."""
    text = _sanitize_emphasis_marks(text)
    plain = _plain_from_emphasis(text)
    if not plain.strip():
        return y
    parts = text.split("*")
    _, top0, _, bottom0 = _text_bbox(plain, font)
    current_x = x
    for i, part in enumerate(parts):
        if not part:
            continue
        fill = accent if (i % 2 == 1) else default_fill
        draw.text((current_x, y - top0), part, fill=fill, font=font)
        current_x += int(_text_width(part, font))
    return y + (bottom0 - top0)


def _draw_left_header_box(
    draw: ImageDraw.ImageDraw,
    text: str,
    *,
    y: int,
    x: int,
    font: ImageFont.ImageFont,
    accent: tuple[int, int, int],
    scale: int,
) -> int:
    """Left-aligned accent box + white Bold subtitle."""
    plain = _clean_display(text)
    if not plain:
        return y
    left, top, right, bottom = _text_bbox(plain, font)
    tw = right - left
    th = bottom - top
    pad_x = HEADER_PAD_X * scale
    pad_top = HEADER_PAD_TOP * scale
    pad_bot = HEADER_PAD_BOTTOM * scale
    box = [x, y, x + tw + pad_x * 2, y + th + pad_top + pad_bot]
    draw.rectangle(box, fill=accent)
    draw.text((x + pad_x - left, y + pad_top - top), plain, fill=PURE_WHITE, font=font)
    return y + th + pad_top + pad_bot


def _draw_right_aligned_block(
    draw: ImageDraw.ImageDraw,
    lines: list[str],
    *,
    bottom: int,
    right: int,
    font: ImageFont.ImageFont,
    fill: tuple[int, int, int],
    line_gap: int = 6,
) -> None:
    """Right-align one or more lines; bottom is the baseline bottom of the block."""
    heights = [_text_height(ln, font) for ln in lines if ln]
    if not heights:
        return
    total = sum(heights) + line_gap * (len(heights) - 1)
    y = bottom - total
    for ln in lines:
        if not ln:
            continue
        left, top, r0, b0 = _text_bbox(ln, font)
        tw = r0 - left
        x = right - tw - left
        draw.text((x, y - top), ln, fill=fill, font=font)
        y += (b0 - top) + line_gap


def _wrap_source_lines(text: str, *, font: ImageFont.ImageFont, max_width: int) -> list[str]:
    """Wrap source credit to fit max_width (prefer ~2 lines)."""
    plain = (text or "").strip()
    if not plain:
        return []
    if _text_width(plain, font) <= max_width:
        return [plain]
    # Character-aware wrap for Korean (no spaces): binary search chunk sizes
    lines: list[str] = []
    rest = plain
    while rest:
        if _text_width(rest, font) <= max_width:
            lines.append(rest)
            break
        lo, hi = 1, len(rest)
        best = 1
        while lo <= hi:
            mid = (lo + hi) // 2
            chunk = rest[:mid]
            if _text_width(chunk, font) <= max_width:
                best = mid
                lo = mid + 1
            else:
                hi = mid - 1
        # Prefer breaking after " | " when possible
        cut = best
        pipe = rest.rfind("|", 0, best + 1)
        if pipe >= 4:
            cut = pipe + 1
        lines.append(rest[:cut].rstrip())
        rest = rest[cut:].lstrip(" |")
        if len(lines) >= 3:
            if rest:
                lines[-1] = (lines[-1] + rest)[: max(1, best)]
            break
    return [ln for ln in lines if ln]


def _strip_quotes(text: str) -> str:
    t = text or ""
    for ch in _QUOTE_CHARS:
        t = t.replace(ch, "")
    return t.strip()


def _find_outro_highlight(plain: str, highlight: str = "") -> tuple[int, str]:
    """Return (index, matched highlight) for brand outro accent phrase."""
    candidates = []
    hi = (highlight or OUTRO_HIGHLIGHT or "").strip()
    if hi:
        candidates.append(hi)
        candidates.append(hi.replace(" ", ""))
    if OUTRO_HIGHLIGHT not in candidates:
        candidates.append(OUTRO_HIGHLIGHT)
        candidates.append(OUTRO_HIGHLIGHT.replace(" ", ""))
    for cand in candidates:
        if not cand:
            continue
        idx = plain.find(cand)
        if idx >= 0:
            return idx, cand
    return -1, ""


def _draw_outro_title_line(
    draw: ImageDraw.ImageDraw,
    text: str,
    *,
    y: int,
    x: int,
    font: ImageFont.ImageFont,
    accent: tuple[int, int, int],
    default_fill: tuple[int, int, int] = INK,
    highlight: str = "",
) -> int:
    """Draw outro line; paint highlight segment in brand color."""
    plain = _strip_quotes(text)
    if not plain:
        return y
    idx, hi = _find_outro_highlight(plain, highlight)
    left0, top0, _, bottom0 = _text_bbox(plain, font)
    if idx < 0 or not hi:
        draw.text((x - left0, y - top0), plain, fill=default_fill, font=font)
        return y + (bottom0 - top0)

    before = plain[:idx]
    after = plain[idx + len(hi) :]
    cur_x = x
    if before:
        bl, _bt, _br, _bb = _text_bbox(before, font)
        draw.text((cur_x - bl, y - top0), before, fill=default_fill, font=font)
        cur_x += int(_text_width(before, font))
    hl, _ht, _hr, _hb = _text_bbox(hi, font)
    draw.text((cur_x - hl, y - top0), hi, fill=accent, font=font)
    cur_x += int(_text_width(hi, font))
    if after:
        al, _at, _ar, _ab = _text_bbox(after, font)
        draw.text((cur_x - al, y - top0), after, fill=default_fill, font=font)
    return y + (bottom0 - top0)


def _render_outro(
    base: Image.Image,
    slide: dict[str, Any],
    *,
    brand_color: str,
    logo: str,
    scale: int,
    source_credit: str = "",
) -> Image.Image:
    accent = _hex_rgb(brand_color or DEFAULT_BRAND_COLOR)
    draw = ImageDraw.Draw(base)
    lay = _canvas_layout(scale)
    w, h = int(lay["w"]), int(lay["h"])
    left_x = int(lay["left"])
    max_w = int(lay["max_w"])
    vs = float(lay["vs"])
    mark = (logo or str(slide.get("brand_name") or "") or TEXT_LOGO).strip()
    draw_text_wordmark(base, scale=scale, label=mark, accent=accent)

    lines = _as_lines(slide.get("title_lines"))
    if not lines:
        lines = list(OUTRO_FIXED_LINES)
    highlight = str(slide.get("outro_highlight") or OUTRO_HIGHLIGHT).strip()

    prepared: list[tuple[str, ImageFont.ImageFont]] = []
    style = get_active_type_style()
    fam = style.get("family", "pretendard")
    title_w = style.get("title_weight", "black")
    for clean in lines:
        _, font = _fit_single_line(
            clean,
            max_width=max_w,
            base_size=OUTRO_TITLE_BASE,
            scale=scale,
            weight=title_w,
            min_size=72,
            family=fam,
        )
        prepared.append((clean, font))

    sub = _clean_display(
        str(
            slide.get("subtitle")
            or slide.get("category_tag")
            or "단 하나의 실전 비즈니스 인사이트, 위드조율"
        )
    )
    sub_font = _font(
        OUTRO_SUB_BASE * scale,
        weight=style.get("sub_weight", "regular"),
        family=fam,
    )
    title_gap = int(OUTRO_TITLE_GAP * scale * vs)
    sub_gap = int(OUTRO_SUB_GAP * scale * vs)
    total_height = sum(_text_height(t, f) for t, f in prepared)
    total_height += title_gap * max(0, len(prepared) - 1)
    if sub:
        total_height += sub_gap + _text_height(sub, sub_font)

    safe_top = int(lay["top"])
    safe_bottom = int(lay["bottom"])
    y = max((h - total_height) // 2, safe_top + int(h * 0.04))
    if y + total_height > safe_bottom:
        y = max(safe_top, safe_bottom - total_height)
    for i, (clean, font) in enumerate(prepared):
        y = _draw_outro_title_line(
            draw,
            clean,
            y=y,
            x=left_x,
            font=font,
            accent=accent,
            default_fill=INK,
            highlight=highlight,
        )
        if i < len(prepared) - 1:
            y += title_gap
    if sub:
        y += sub_gap
        _draw_left_line(draw, sub, y=y, x=left_x, font=sub_font, fill=INK_SOFT)

    credit = (source_credit or str(slide.get("source_credit") or "")).strip()
    if credit:
        if not credit.startswith("출처"):
            credit = f"출처 | {credit}"
        sfont = _font(
            OUTRO_SOURCE_SIZE * scale,
            weight=style.get("sub_weight", "regular"),
            family=fam,
        )
        right = int(lay["right"])
        max_src_w = max(120 * scale, right - left_x)
        wrapped = _wrap_source_lines(credit, font=sfont, max_width=max_src_w)
        bottom = int(lay["bottom"])
        _draw_right_aligned_block(
            draw,
            wrapped,
            bottom=bottom,
            right=right,
            font=sfont,
            fill=SOURCE_GRAY,
            line_gap=max(4, int(6 * scale * vs)),
        )
    return base


def _draw_centered_line(
    draw: ImageDraw.ImageDraw,
    text: str,
    *,
    y: int,
    canvas_w: int,
    font: ImageFont.ImageFont,
    fill: tuple[int, int, int] = PURE_WHITE,
) -> int:
    """Draw one plain centered line; return next Y using 1.0 glyph height (caller adds leading)."""
    plain = _plain_from_emphasis(text)
    if not plain:
        return y
    left, top, right, bottom = _text_bbox(plain, font)
    tw = right - left
    x = (canvas_w - tw) // 2 - left
    draw.text((x, y - top), plain, fill=fill, font=font)
    return y + (bottom - top)


def _draw_left_line(
    draw: ImageDraw.ImageDraw,
    text: str,
    *,
    y: int,
    x: int,
    font: ImageFont.ImageFont,
    fill: tuple[int, int, int] = PURE_WHITE,
) -> int:
    """Draw one plain left-aligned line; return Y after glyph height."""
    plain = _plain_from_emphasis(text)
    if not plain:
        return y
    left, top, _, bottom = _text_bbox(plain, font)
    draw.text((x - left, y - top), plain, fill=fill, font=font)
    return y + (bottom - top)


def _draw_left_highlight_box(
    draw: ImageDraw.ImageDraw,
    text: str,
    *,
    y: int,
    x: int,
    font: ImageFont.ImageFont,
    accent: tuple[int, int, int],
    scale: int,
) -> int:
    """Brand-color rounded box behind hook line (Tipstagram-style punch)."""
    plain = _plain_from_emphasis(text)
    if not plain:
        return y
    left, top, right, bottom = _text_bbox(plain, font)
    tw = right - left
    th = bottom - top
    pad_x = COVER_BOX_PAD_X * scale
    pad_y = COVER_BOX_PAD_Y * scale
    radius = max(4, COVER_BOX_RADIUS * scale)
    box = [
        x - pad_x,
        y - pad_y,
        x + tw + pad_x,
        y + th + pad_y,
    ]
    draw.rounded_rectangle(box, radius=radius, fill=accent)
    draw.text((x - left, y - top), plain, fill=PURE_WHITE, font=font)
    return y + th + pad_y


def _normalize_ig_handle(raw: str) -> str:
    h = (raw or "").strip()
    if not h:
        return ""
    h = re.sub(r"^https?://(www\.)?instagram\.com/", "", h, flags=re.I)
    h = h.strip("/").split("?")[0].split("/")[0]
    h = h.lstrip("@").strip()
    if not h:
        return ""
    return f"@{h}"


def _cover_highlight_index(lines: list[str]) -> int:
    """Prefer *starred* line; else last line (punch)."""
    for i, ln in enumerate(lines):
        if "*" in (ln or ""):
            return i
    return max(0, len(lines) - 1)


def _fit_uniform_lines(
    lines: list[str],
    *,
    max_width: int,
    base_size: int,
    scale: int,
    weight: str = "extrabold",
    min_size: int = 44,
    family: str = "paperlogy",
) -> tuple[int, ImageFont.ImageFont]:
    """Largest single size that fits every line within max_width."""
    size = base_size
    while size >= min_size:
        font = _font(size * scale, weight=weight, family=family)
        if all(_text_width(_plain_from_emphasis(ln), font) <= max_width for ln in lines):
            return size, font
        size -= 2
    return min_size, _font(min_size * scale, weight=weight, family=family)


def _draw_header_box(
    draw: ImageDraw.ImageDraw,
    text: str,
    *,
    y: int,
    canvas_w: int,
    font: ImageFont.ImageFont,
    accent: tuple[int, int, int],
    scale: int,
) -> int:
    """Accent box + white Bold subtitle (font must be Bold/ExtraBold)."""
    plain = _clean_display(text)
    if not plain:
        return y
    left, top, right, bottom = _text_bbox(plain, font)
    tw = right - left
    th = bottom - top
    pad_x = HEADER_PAD_X * scale
    pad_top = HEADER_PAD_TOP * scale
    pad_bot = HEADER_PAD_BOTTOM * scale
    box_w = tw + pad_x * 2
    box_h = th + pad_top + pad_bot
    box_x = (canvas_w - box_w) // 2
    draw.rectangle(
        [box_x, y, box_x + box_w, y + box_h],
        fill=accent,
    )
    text_x = box_x + pad_x - left
    text_y = y + pad_top - top
    draw.text((text_x, text_y), plain, fill=PURE_WHITE, font=font)
    return y + box_h


def _fit_header_box_font(
    text: str,
    *,
    max_width: int,
    base_size: int,
    scale: int,
    min_size: int = 26,
) -> tuple[int, ImageFont.ImageFont]:
    """Font for orange subtitle boxes – uses active title weight/family."""
    style = get_active_type_style()
    return _fit_single_line(
        text,
        max_width=max_width,
        base_size=base_size,
        scale=scale,
        weight=style.get("title_weight", "extrabold"),
        min_size=min_size,
        family=style.get("family", "pretendard"),
    )


def _circled_num(num: str) -> str:
    n = re.sub(r"[^\d]", "", num or "")
    if not n:
        return ""
    try:
        i = int(n)
    except ValueError:
        return f"{n}."
    circled = "①②③④⑤⑥⑦⑧⑨⑩"
    if 1 <= i <= 10:
        return circled[i - 1]
    return f"{i}."


def _section_header(slide: dict[str, Any]) -> str:
    num = str(slide.get("section_number") or "").strip()
    stitle = _clean_display(
        str(
            slide.get("section_title")
            or slide.get("section_label")
            or slide.get("main_title")
            or ""
        )
    )
    mark = _circled_num(num)
    if mark and stitle:
        return f"{mark} {stitle}"
    return stitle or mark


def _detail_lines_of(slide: dict[str, Any]) -> list[str]:
    lines = _as_lines(slide.get("detailed_lines"), keep_emphasis=True)
    if lines:
        return lines
    legacy = str(slide.get("detailed_paragraph") or "").strip()
    if not legacy:
        return []
    parts = [p.strip() for p in re.split(r"(?<=[.。!?])\s+|[\n·•]", legacy) if p.strip()]
    return [_clean_display(p, keep_emphasis=True) for p in parts][:4]


def _body_lines_of(slide: dict[str, Any]) -> list[str]:
    explanations = _as_lines(slide.get("explanations"), keep_emphasis=True)
    if explanations:
        return explanations
    if str(slide.get("main_statement") or "").strip() or _detail_lines_of(slide):
        out: list[str] = []
        stmt = _clean_display(str(slide.get("main_statement") or ""), keep_emphasis=True)
        if stmt:
            out.append(stmt)
        out.extend(_detail_lines_of(slide))
        return out
    lines = _as_lines(slide.get("main_text") or slide.get("body") or "", keep_emphasis=True)
    if lines:
        return lines
    return _as_lines(slide.get("body_points") or [], keep_emphasis=True)


def _render_cover(
    base: Image.Image,
    slide: dict[str, Any],
    *,
    brand_color: str,
    logo: str,
    scale: int,
    ig_handle: str = "",
    highlight_color: str = "",
) -> Image.Image:
    """COVER – brand IG handle + left title; brand punch box."""
    del logo, highlight_color  # cover punch uses box (brand) color only
    accent = _hex_rgb(brand_color or DEFAULT_BRAND_COLOR)
    _apply_bottom_linear_gradient(base)
    draw = ImageDraw.Draw(base)
    lay = _canvas_layout(scale)
    w, h = int(lay["w"]), int(lay["h"])
    vs = float(lay["vs"])

    lines = _as_lines(slide.get("title_lines"))
    if not lines:
        title = _clean_display(str(slide.get("main_title") or slide.get("hook") or ""))
        lines = [title] if title else []
    lines = [ln for ln in lines[:4] if ln]
    handle = _normalize_ig_handle(ig_handle) or COVER_IG_HANDLE
    if not lines:
        return base

    left_x = int(lay["left"])
    max_w = int(lay["max_w"])
    style = get_active_type_style()
    fam = style.get("family", "pretendard")
    _, title_font = _fit_uniform_lines(
        lines,
        max_width=max_w - COVER_BOX_PAD_X * 2 * scale,
        base_size=COVER_TITLE_BASE,
        scale=scale,
        weight=style.get("title_weight", "extrabold"),
        min_size=COVER_TITLE_MIN,
        family=fam,
    )

    handle_font = _font(
        COVER_HANDLE_SIZE * scale,
        weight=style.get("handle_weight", "medium"),
        family=fam,
    )
    gap = int(COVER_LINE_GAP * scale * vs)
    hi = _cover_highlight_index(lines)
    box_extra = COVER_BOX_PAD_Y * scale

    stack_h = _text_height(handle, handle_font) + int(COVER_HANDLE_GAP * scale * vs)
    for i, t in enumerate(lines):
        stack_h += _text_height(t, title_font)
        if i == hi:
            stack_h += box_extra
        if i < len(lines) - 1:
            stack_h += gap

    max_bottom = int(lay["bottom"])
    min_y = max(int(lay["top"]), int(h * COVER_TEXT_Y_RATIO))
    y = max_bottom - stack_h
    if y < min_y:
        y = min_y

    y = _draw_left_line(draw, handle, y=y, x=left_x, font=handle_font, fill=SOFT_WHITE)
    y += int(COVER_HANDLE_GAP * scale * vs)

    for i, clean in enumerate(lines):
        if i == hi:
            y = _draw_left_highlight_box(
                draw,
                clean,
                y=y,
                x=left_x,
                font=title_font,
                accent=accent,
                scale=scale,
            )
        else:
            y = _draw_left_line(
                draw, clean, y=y, x=left_x, font=title_font, fill=PURE_WHITE
            )
        y += gap

    return base


def _content_start_y(
    *,
    canvas_h: int,
    total_height: int,
    wordmark_bottom: int,
    scale: int,
    safe_top: int | None = None,
    safe_bottom: int | None = None,
) -> int:
    top = int(safe_top if safe_top is not None else 0)
    bottom = int(safe_bottom if safe_bottom is not None else canvas_h)
    floor = max(top, wordmark_bottom + 48 * scale)
    usable = max(floor, bottom)
    y = (canvas_h - total_height) // 2
    y = max(y, floor)
    if y + total_height > usable:
        y = max(floor, usable - total_height)
    return y


def _render_content(
    base: Image.Image,
    slide: dict[str, Any],
    *,
    brand_color: str,
    logo: str,
    scale: int,
    highlight_color: str = "",
) -> Image.Image:
    box_accent = _hex_rgb(brand_color or DEFAULT_BRAND_COLOR)
    text_accent = _hex_rgb(highlight_color or brand_color or DEFAULT_BRAND_COLOR)
    draw = ImageDraw.Draw(base)
    lay = _canvas_layout(scale)
    w, h = int(lay["w"]), int(lay["h"])
    vs = float(lay["vs"])
    wordmark_bottom = draw_text_wordmark(
        base, scale=scale, label=(logo or TEXT_LOGO), accent=box_accent
    )

    header = _section_header(slide)
    left_x = int(lay["left"])
    max_w = int(lay["max_w"])
    body_lines = [ln for ln in _body_lines_of(slide)[:6] if ln.strip()]

    header_font = None
    header_h = 0
    if header:
        _, header_font = _fit_header_box_font(
            header,
            max_width=max_w - HEADER_PAD_X * 2 * scale,
            base_size=CONTENT_HEADER_BASE,
            scale=scale,
            min_size=36,
        )
        header_h = (
            _text_height(header, header_font)
            + HEADER_PAD_TOP * scale
            + HEADER_PAD_BOTTOM * scale
        )

    gap_after_header = int(CONTENT_HEADER_GAP * scale * vs) if header else 0
    style = get_active_type_style()
    fam = style.get("family", "pretendard")
    body_w = style.get("body_weight", "regular")
    prepared: list[tuple[str, int, ImageFont.ImageFont]] = []
    for raw in body_lines:
        size, font = _fit_single_line(
            raw,
            max_width=max_w,
            base_size=CONTENT_BODY_BASE,
            scale=scale,
            weight=body_w,
            min_size=30,
            family=fam,
        )
        prepared.append((raw, size, font))

    line_gaps = [
        int(size * scale * (CONTENT_LINE_FACTOR - 1.0) * vs) for _, size, _ in prepared
    ]
    total_height = header_h + gap_after_header
    for i, (raw, size, font) in enumerate(prepared):
        total_height += _text_height(_plain_from_emphasis(raw), font)
        if i < len(prepared) - 1:
            total_height += line_gaps[i]

    y = _content_start_y(
        canvas_h=h,
        total_height=total_height,
        wordmark_bottom=wordmark_bottom,
        scale=scale,
        safe_top=int(lay["top"]),
        safe_bottom=int(lay["bottom"]),
    )

    if header and header_font is not None:
        y = _draw_left_header_box(
            draw, header, y=y, x=left_x, font=header_font, accent=box_accent, scale=scale
        )
        y += gap_after_header

    for i, (raw, size, font) in enumerate(prepared):
        y = _draw_left_emphasis_line(
            draw, raw, y=y, x=left_x, font=font, accent=text_accent, default_fill=INK
        )
        if i < len(prepared) - 1:
            y += line_gaps[i]
    return base


def _render_summary(
    base: Image.Image,
    slide: dict[str, Any],
    *,
    brand_color: str,
    logo: str,
    scale: int,
    highlight_color: str = "",
) -> Image.Image:
    box_accent = _hex_rgb(brand_color or DEFAULT_BRAND_COLOR)
    text_accent = _hex_rgb(highlight_color or brand_color or DEFAULT_BRAND_COLOR)
    draw = ImageDraw.Draw(base)
    lay = _canvas_layout(scale)
    w, h = int(lay["w"]), int(lay["h"])
    vs = float(lay["vs"])
    wordmark_bottom = draw_text_wordmark(
        base, scale=scale, label=(logo or TEXT_LOGO), accent=box_accent
    )

    title = _clean_display(str(slide.get("main_title") or slide.get("hook") or "핵심 체크 포인트"))
    left_x = int(lay["left"])
    max_w = int(lay["max_w"])

    raw_items = _as_lines(slide.get("summary_list") or slide.get("body_points") or [])[:4]
    items: list[str] = []
    for item in raw_items:
        clean = re.sub(r"^\d+[\.\)]\s*", "", item).strip()
        clean = _clean_display(clean, keep_emphasis=True)
        if clean:
            items.append(clean)

    _, title_font = _fit_header_box_font(
        title,
        max_width=max_w - HEADER_PAD_X * 2 * scale,
        base_size=SUMMARY_TITLE_BASE,
        scale=scale,
        min_size=36,
    )
    item_fonts: list[ImageFont.ImageFont] = []
    style = get_active_type_style()
    fam = style.get("family", "pretendard")
    body_w = style.get("body_weight", "regular")
    for clean in items:
        _, f = _fit_single_line(
            clean,
            max_width=max_w - 40 * scale,
            base_size=SUMMARY_ITEM_BASE,
            scale=scale,
            weight=body_w,
            min_size=30,
            family=fam,
        )
        item_fonts.append(f)

    gap_after_title = int(SUMMARY_HEADER_GAP * scale * vs)
    line_gap = int(SUMMARY_ITEM_BASE * scale * (SUMMARY_LINE_STEP - 1.0) * vs)
    total_height = (
        _text_height(title, title_font)
        + HEADER_PAD_TOP * scale
        + HEADER_PAD_BOTTOM * scale
        + gap_after_title
    )
    for clean, font in zip(items, item_fonts):
        total_height += _text_height(_plain_from_emphasis(clean), font) + line_gap

    y = _content_start_y(
        canvas_h=h,
        total_height=total_height,
        wordmark_bottom=wordmark_bottom,
        scale=scale,
        safe_top=int(lay["top"]),
        safe_bottom=int(lay["bottom"]),
    )
    y = _draw_left_header_box(
        draw, title, y=y, x=left_x, font=title_font, accent=box_accent, scale=scale
    )
    y += gap_after_title
    for i, (clean, font) in enumerate(zip(items, item_fonts)):
        label = f"{i + 1}. {clean}"
        y = _draw_left_emphasis_line(
            draw, label, y=y, x=left_x, font=font, accent=text_accent, default_fill=INK
        )
        y += line_gap
    return base


def _normalize_slide_type(slide: dict[str, Any], index: int, total: int) -> str:
    raw = str(slide.get("slide_type") or "").upper().strip()
    has_summary = bool(_as_lines(slide.get("summary_list") or []))
    if raw in {"TITLE", "COVER"}:
        return "COVER"
    if raw in {"OUTRO", "CTA", "ENDING"}:
        return "OUTRO"
    if raw == "SUMMARY":
        return "SUMMARY"
    if raw in {"DETAIL", "CONTENT", "QUOTE", "BODY"}:
        return "CONTENT"
    if index <= 0:
        return "COVER"
    if index >= total - 1:
        return "OUTRO"
    if has_summary:
        return "SUMMARY"
    return "CONTENT"


def render_slide_pil(
    slide: dict[str, Any],
    background: Path,
    output_path: Path,
    *,
    brand_color: str = DEFAULT_BRAND_COLOR,
    logo: str = BRAND_FALLBACK,
    slide_index: int = 1,
    slide_total: int = 7,
    ig_handle: str = "",
    source_credit: str = "",
    highlight_color: str = "",
) -> Path:
    scale = RETINA
    fmt = get_active_card_format()
    w, h = fmt.canvas_px(scale)
    ensure_dir(output_path.parent)
    slide_type = _normalize_slide_type(slide, slide_index - 1, slide_total)
    color = brand_color or DEFAULT_BRAND_COLOR
    hi = highlight_color or color

    if slide_type == "COVER":
        if background.exists():
            with Image.open(background) as raw:
                base = _fit_cover(raw, w, h)
        else:
            base = Image.new("RGB", (w, h), BG_DARK)
        img = _render_cover(
            base,
            slide,
            brand_color=color,
            logo=logo,
            scale=scale,
            ig_handle=ig_handle,
            highlight_color=hi,
        )
    elif slide_type == "SUMMARY":
        base = Image.new("RGB", (w, h), BG_LIGHT)
        img = _render_summary(
            base, slide, brand_color=color, logo=logo, scale=scale, highlight_color=hi
        )
    elif slide_type == "OUTRO":
        base = Image.new("RGB", (w, h), BG_LIGHT)
        img = _render_outro(
            base,
            slide,
            brand_color=color,
            logo=logo,
            scale=scale,
            source_credit=source_credit,
        )
    else:
        base = Image.new("RGB", (w, h), BG_LIGHT)
        img = _render_content(
            base, slide, brand_color=color, logo=logo, scale=scale, highlight_color=hi
        )

    img.save(output_path, format="PNG", optimize=True)
    logger.info(
        "PIL magazine slide %d (%s, %s) -> %s",
        slide_index,
        slide_type,
        fmt.resolution_label,
        output_path.name,
    )
    return output_path


def render_all_pil(
    slides: list[dict[str, Any]],
    backgrounds: list[Path],
    output_dir: Path,
    *,
    brand_color: str = DEFAULT_BRAND_COLOR,
    logo: str = BRAND_FALLBACK,
    ig_handle: str = "",
    source_credit: str = "",
    type_style: dict[str, str] | None = None,
    highlight_color: str = "",
    image_format: str = "",
) -> list[Path]:
    ensure_dir(output_dir)
    if image_format:
        set_active_card_format(image_format)
    set_active_type_style(type_style)
    out: list[Path] = []
    total = len(slides)
    for i, slide in enumerate(slides):
        bg = backgrounds[i] if i < len(backgrounds) else Path()
        dest = output_dir / f"slide_{i + 1:02d}.png"
        out.append(
            render_slide_pil(
                slide,
                bg,
                dest,
                brand_color=brand_color,
                logo=logo,
                slide_index=i + 1,
                slide_total=total,
                ig_handle=ig_handle,
                source_credit=source_credit if i == total - 1 else "",
                highlight_color=highlight_color,
            )
        )
    return out
