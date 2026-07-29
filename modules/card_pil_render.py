"""PIL card compositor – COVER (photo) / CONTENT·SUMMARY (light studio)."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont

from modules.utils import ROOT_DIR, ensure_dir, get_logger

logger = get_logger(__name__)

CARD_W = 1080
CARD_H = 1440  # Instagram feed 3:4
RETINA = 2

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
CONTENT_HEADER_BASE = 52  # orange box subtitle 50–55
CONTENT_BODY_BASE = 42  # body 40–45
CONTENT_LINE_FACTOR = 1.72  # airier leading with smaller type
CONTENT_HEADER_GAP = 88  # box → first body line
SUMMARY_TITLE_BASE = 52
SUMMARY_ITEM_BASE = 42
SUMMARY_LINE_STEP = 1.72
SUMMARY_HEADER_GAP = 88

OUTRO_TITLE_BASE = 96  # 90–100
OUTRO_SUB_BASE = 35
OUTRO_TITLE_GAP = 16
OUTRO_SUB_GAP = 36
OUTRO_SOURCE_SIZE = 24  # 22–25
OUTRO_SOURCE_RIGHT = 80
OUTRO_SOURCE_BOTTOM = 80
OUTRO_HIGHLIGHT = "전략 기획실"

HEADER_PAD_X = 22
HEADER_PAD_TOP = 14
HEADER_PAD_BOTTOM = 16
WORDMARK_Y = 120
WORDMARK_SIZE = 42

FONTS_DIR = ROOT_DIR / "assets" / "fonts" / "Pretendard"
PAPERLOGY_DIR = ROOT_DIR / "assets" / "fonts" / "Paperlogy"
FREESENT_DIR = ROOT_DIR / "assets" / "fonts" / "Freesentation"
WIN_FONTS = Path("C:/Windows/Fonts")


def _resolve_font(name: str) -> Path | None:
    for folder in (FONTS_DIR, PAPERLOGY_DIR, FREESENT_DIR):
        cand = folder / name
        if cand.exists() and cand.stat().st_size > 1000:
            return cand
    mapping = {
        "Pretendard-Regular.otf": ("malgun.ttf", "arial.ttf"),
        "Pretendard-Medium.otf": ("malgun.ttf", "arial.ttf"),
        "Pretendard-Bold.otf": ("malgunbd.ttf", "arialbd.ttf"),
        "Pretendard-ExtraBold.otf": ("malgunbd.ttf", "arialbd.ttf"),
        "Pretendard-Black.otf": ("malgunbd.ttf", "arialbd.ttf"),
        "Paperlogy-8ExtraBold.ttf": ("malgunbd.ttf", "arialbd.ttf"),
        "Paperlogy-9Black.ttf": ("malgunbd.ttf", "arialbd.ttf"),
        "Paperlogy-7Bold.ttf": ("malgunbd.ttf", "arialbd.ttf"),
        "Freesentation-8ExtraBold.ttf": ("malgunbd.ttf", "arialbd.ttf"),
        "Freesentation-9Black.ttf": ("malgunbd.ttf", "arialbd.ttf"),
    }
    for sys_name in mapping.get(name, ("malgunbd.ttf", "malgun.ttf")):
        p = WIN_FONTS / sys_name
        if p.exists():
            return p
    return None


def _font(
    size: int,
    *,
    weight: str = "bold",
    family: str = "pretendard",
) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    size = max(12, int(size))
    if family == "paperlogy":
        order = {
            "regular": ["Paperlogy-4Regular.ttf", "Pretendard-Regular.otf"],
            "medium": ["Paperlogy-5Medium.ttf", "Pretendard-Medium.otf"],
            "bold": ["Paperlogy-7Bold.ttf", "Paperlogy-8ExtraBold.ttf", "Pretendard-Bold.otf"],
            "extrabold": ["Paperlogy-8ExtraBold.ttf", "Paperlogy-9Black.ttf", "Pretendard-ExtraBold.otf"],
            "black": ["Paperlogy-9Black.ttf", "Paperlogy-8ExtraBold.ttf", "Pretendard-Black.otf"],
        }.get(weight, ["Paperlogy-8ExtraBold.ttf"])
    else:
        order = {
            "regular": ["Pretendard-Regular.otf"],
            "medium": ["Pretendard-Medium.otf", "Pretendard-Regular.otf"],
            "bold": ["Pretendard-Bold.otf", "Pretendard-ExtraBold.otf", "Pretendard-Black.otf"],
            "extrabold": ["Pretendard-ExtraBold.otf", "Pretendard-Black.otf", "Pretendard-Bold.otf"],
            "black": ["Pretendard-Black.otf", "Pretendard-ExtraBold.otf", "Pretendard-Bold.otf"],
        }.get(weight, ["Pretendard-Bold.otf"])
    for fname in order:
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


def draw_text_wordmark(
    base: Image.Image,
    *,
    scale: int,
    y: int | None = None,
) -> int:
    """Top-center text logo WITHCHOYOOL in #fc4d01 Bold. Returns bottom Y."""
    draw = ImageDraw.Draw(base)
    w, _h = base.size
    font = _font(WORDMARK_SIZE * scale, weight="bold", family="pretendard")
    label = TEXT_LOGO
    left, top, right, bottom = _text_bbox(label, font)
    tw = right - left
    th = bottom - top
    x = (w - tw) // 2 - left
    yy = (WORDMARK_Y * scale) if y is None else y
    draw.text((x, yy - top), label, fill=ACCENT, font=font)
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


def _draw_outro_title_line(
    draw: ImageDraw.ImageDraw,
    text: str,
    *,
    y: int,
    x: int,
    font: ImageFont.ImageFont,
    accent: tuple[int, int, int],
    default_fill: tuple[int, int, int] = INK,
) -> int:
    """Draw outro line; paint OUTRO_HIGHLIGHT segment in brand orange."""
    plain = (text or "").replace("'", "").replace("'", "").replace("'", "")
    plain = plain.strip()
    if not plain:
        return y
    hi = OUTRO_HIGHLIGHT
    idx = plain.find(hi)
    left0, top0, _, bottom0 = _text_bbox(plain, font)
    if idx < 0:
        draw.text((x - left0, y - top0), plain, fill=default_fill, font=font)
        return y + (bottom0 - top0)

    before = plain[:idx]
    after = plain[idx + len(hi) :]
    cur_x = x
    if before:
        bl, bt, br, _bb = _text_bbox(before, font)
        draw.text((cur_x - bl, y - top0), before, fill=default_fill, font=font)
        cur_x += int(_text_width(before, font))
    hl, _ht, hr, _hb = _text_bbox(hi, font)
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
    del logo
    accent = _hex_rgb(brand_color or DEFAULT_BRAND_COLOR)
    draw = ImageDraw.Draw(base)
    w, h = CARD_W * scale, CARD_H * scale
    draw_text_wordmark(base, scale=scale)

    left_x = CONTENT_LEFT * scale
    max_w = w - left_x - CONTENT_LEFT * scale
    lines = _as_lines(slide.get("title_lines"))
    if not lines:
        lines = ["당신의", "전략 기획실이", "되어드립니다."]
    # Strip leftover quotes from any pipeline copy
    lines = [
        ln.replace("'", "").replace("'", "").replace("'", "").strip()
        for ln in lines[:4]
        if ln.strip()
    ]

    prepared: list[tuple[str, ImageFont.ImageFont]] = []
    for clean in lines:
        _, font = _fit_single_line(
            clean,
            max_width=max_w,
            base_size=OUTRO_TITLE_BASE,
            scale=scale,
            weight="black",
            min_size=78,
            family="paperlogy",
        )
        prepared.append((clean, font))

    sub = _clean_display(
        str(
            slide.get("subtitle")
            or slide.get("category_tag")
            or "단 하나의 실전 비즈니스 인사이트, 위드조율"
        )
    )
    sub_font = _font(OUTRO_SUB_BASE * scale, weight="regular", family="pretendard")
    title_gap = OUTRO_TITLE_GAP * scale
    sub_gap = OUTRO_SUB_GAP * scale
    total_height = sum(_text_height(t, f) for t, f in prepared)
    total_height += title_gap * max(0, len(prepared) - 1)
    if sub:
        total_height += sub_gap + _text_height(sub, sub_font)

    y = max((h - total_height) // 2, int(h * 0.22))
    for i, (clean, font) in enumerate(prepared):
        y = _draw_outro_title_line(
            draw, clean, y=y, x=left_x, font=font, accent=accent, default_fill=INK
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
        sfont = _font(OUTRO_SOURCE_SIZE * scale, weight="regular", family="pretendard")
        right = w - OUTRO_SOURCE_RIGHT * scale
        max_src_w = right - (CONTENT_LEFT * scale)
        wrapped = _wrap_source_lines(credit, font=sfont, max_width=max(80, max_src_w))
        bottom = h - OUTRO_SOURCE_BOTTOM * scale
        _draw_right_aligned_block(
            draw,
            wrapped,
            bottom=bottom,
            right=right,
            font=sfont,
            fill=SOURCE_GRAY,
            line_gap=max(4, int(6 * scale)),
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
    """Bold/ExtraBold font reserved for orange subtitle boxes only."""
    return _fit_single_line(
        text,
        max_width=max_width,
        base_size=base_size,
        scale=scale,
        weight="extrabold",
        min_size=min_size,
        family="pretendard",
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
) -> Image.Image:
    """COVER – no logo; fixed @with.choyool + left title; brand punch box."""
    del logo, ig_handle  # cover IG is always hardcoded
    accent = _hex_rgb(brand_color or DEFAULT_BRAND_COLOR)
    _apply_bottom_linear_gradient(base)
    draw = ImageDraw.Draw(base)
    w, h = CARD_W * scale, CARD_H * scale

    lines = _as_lines(slide.get("title_lines"))
    if not lines:
        title = _clean_display(str(slide.get("main_title") or slide.get("hook") or ""))
        lines = [title] if title else []
    lines = [ln for ln in lines[:4] if ln]
    handle = COVER_IG_HANDLE
    if not lines:
        return base

    max_w = int(w * COVER_MAX_W_RATIO)
    left_x = int(w * COVER_LEFT_RATIO)
    _, title_font = _fit_uniform_lines(
        lines,
        max_width=max_w - COVER_BOX_PAD_X * 2 * scale,
        base_size=COVER_TITLE_BASE,
        scale=scale,
        weight="extrabold",
        min_size=COVER_TITLE_MIN,
        family="paperlogy",
    )

    handle_font = _font(COVER_HANDLE_SIZE * scale, weight="medium", family="pretendard")
    gap = int(COVER_LINE_GAP * scale)
    hi = _cover_highlight_index(lines)
    box_extra = COVER_BOX_PAD_Y * scale

    stack_h = _text_height(handle, handle_font) + COVER_HANDLE_GAP * scale
    for i, t in enumerate(lines):
        stack_h += _text_height(t, title_font)
        if i == hi:
            stack_h += box_extra
        if i < len(lines) - 1:
            stack_h += gap

    max_bottom = h - COVER_BOTTOM_MARGIN * scale
    y = max_bottom - stack_h
    min_y = int(h * COVER_TEXT_Y_RATIO)
    if y < min_y:
        y = min_y

    y = _draw_left_line(draw, handle, y=y, x=left_x, font=handle_font, fill=SOFT_WHITE)
    y += COVER_HANDLE_GAP * scale

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



def _content_start_y(*, canvas_h: int, total_height: int, wordmark_bottom: int, scale: int) -> int:
    y = (canvas_h - total_height) // 2
    floor = wordmark_bottom + 48 * scale
    return max(y, floor)


def _render_content(
    base: Image.Image,
    slide: dict[str, Any],
    *,
    brand_color: str,
    logo: str,
    scale: int,
) -> Image.Image:
    del logo
    accent = _hex_rgb(brand_color or DEFAULT_BRAND_COLOR)
    draw = ImageDraw.Draw(base)
    w, h = CARD_W * scale, CARD_H * scale
    wordmark_bottom = draw_text_wordmark(base, scale=scale)

    header = _section_header(slide)
    left_x = CONTENT_LEFT * scale
    max_w = w - left_x - CONTENT_LEFT * scale
    body_lines = [ln for ln in _body_lines_of(slide)[:6] if ln.strip()]

    header_font = None
    header_h = 0
    if header:
        _, header_font = _fit_header_box_font(
            header,
            max_width=max_w - HEADER_PAD_X * 2 * scale,
            base_size=CONTENT_HEADER_BASE,
            scale=scale,
            min_size=40,
        )
        header_h = (
            _text_height(header, header_font)
            + HEADER_PAD_TOP * scale
            + HEADER_PAD_BOTTOM * scale
        )

    gap_after_header = CONTENT_HEADER_GAP * scale if header else 0
    prepared: list[tuple[str, int, ImageFont.ImageFont]] = []
    for raw in body_lines:
        size, font = _fit_single_line(
            raw,
            max_width=max_w,
            base_size=CONTENT_BODY_BASE,
            scale=scale,
            weight="regular",
            min_size=34,
        )
        prepared.append((raw, size, font))

    line_gaps = [int(size * scale * (CONTENT_LINE_FACTOR - 1.0)) for _, size, _ in prepared]
    total_height = header_h + gap_after_header
    for i, (raw, size, font) in enumerate(prepared):
        total_height += _text_height(_plain_from_emphasis(raw), font)
        if i < len(prepared) - 1:
            total_height += line_gaps[i]

    y = _content_start_y(
        canvas_h=h, total_height=total_height, wordmark_bottom=wordmark_bottom, scale=scale
    )

    if header and header_font is not None:
        y = _draw_left_header_box(
            draw, header, y=y, x=left_x, font=header_font, accent=accent, scale=scale
        )
        y += gap_after_header

    for i, (raw, size, font) in enumerate(prepared):
        y = _draw_left_emphasis_line(
            draw, raw, y=y, x=left_x, font=font, accent=accent, default_fill=INK
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
) -> Image.Image:
    del logo
    accent = _hex_rgb(brand_color or DEFAULT_BRAND_COLOR)
    draw = ImageDraw.Draw(base)
    w, h = CARD_W * scale, CARD_H * scale
    wordmark_bottom = draw_text_wordmark(base, scale=scale)

    title = _clean_display(str(slide.get("main_title") or slide.get("hook") or "핵심 체크 포인트"))
    left_x = CONTENT_LEFT * scale
    max_w = w - left_x - CONTENT_LEFT * scale

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
        min_size=40,
    )
    item_fonts: list[ImageFont.ImageFont] = []
    for clean in items:
        _, f = _fit_single_line(
            clean,
            max_width=max_w - 40 * scale,
            base_size=SUMMARY_ITEM_BASE,
            scale=scale,
            weight="regular",
            min_size=34,
        )
        item_fonts.append(f)

    gap_after_title = SUMMARY_HEADER_GAP * scale
    line_gap = int(SUMMARY_ITEM_BASE * scale * (SUMMARY_LINE_STEP - 1.0))
    total_height = (
        _text_height(title, title_font)
        + HEADER_PAD_TOP * scale
        + HEADER_PAD_BOTTOM * scale
        + gap_after_title
    )
    for clean, font in zip(items, item_fonts):
        total_height += _text_height(_plain_from_emphasis(clean), font) + line_gap

    y = _content_start_y(
        canvas_h=h, total_height=total_height, wordmark_bottom=wordmark_bottom, scale=scale
    )
    y = _draw_left_header_box(
        draw, title, y=y, x=left_x, font=title_font, accent=accent, scale=scale
    )
    y += gap_after_title
    for i, (clean, font) in enumerate(zip(items, item_fonts)):
        label = f"{i + 1}. {clean}"
        y = _draw_left_emphasis_line(
            draw, label, y=y, x=left_x, font=font, accent=accent, default_fill=INK
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
) -> Path:
    scale = RETINA
    w, h = CARD_W * scale, CARD_H * scale
    ensure_dir(output_path.parent)
    slide_type = _normalize_slide_type(slide, slide_index - 1, slide_total)
    color = brand_color or DEFAULT_BRAND_COLOR

    if slide_type == "COVER":
        if background.exists():
            with Image.open(background) as raw:
                base = _fit_cover(raw, w, h)
        else:
            base = Image.new("RGB", (w, h), BG_DARK)
        img = _render_cover(
            base, slide, brand_color=color, logo=logo, scale=scale, ig_handle=ig_handle
        )
    elif slide_type == "SUMMARY":
        base = Image.new("RGB", (w, h), BG_LIGHT)
        img = _render_summary(base, slide, brand_color=color, logo=logo, scale=scale)
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
        img = _render_content(base, slide, brand_color=color, logo=logo, scale=scale)

    img.save(output_path, format="PNG", optimize=True)
    logger.info("PIL magazine slide %d (%s) -> %s", slide_index, slide_type, output_path.name)
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
) -> list[Path]:
    ensure_dir(output_dir)
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
            )
        )
    return out
