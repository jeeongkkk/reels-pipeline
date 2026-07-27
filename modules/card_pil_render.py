"""PIL Insight card compositor – COVER / CONTENT / SUMMARY (no AI-looking chrome)."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont

from modules.utils import ROOT_DIR, ensure_dir, get_logger

logger = get_logger(__name__)

CARD_W = 1080
CARD_H = 1920
RETINA = 2

PURE_WHITE = (255, 255, 255)
INK = (17, 17, 17)  # #111111
SOFT_INK = (51, 51, 51)  # #333333 – Type B detail lines
WHITE = (255, 255, 255)
BRAND_GRAY = (100, 100, 100)
COBALT = (0, 85, 255)  # #0055FF

MARGIN_X = 72
BRAND_DEFAULT = "Authority Reels"

# Highlight box breathing room (logical px, multiplied by scale)
BOX_PAD_X = 28
BOX_PAD_Y = 25  # legacy symmetric fallback
BOX_PAD_TOP = 25
BOX_PAD_BOTTOM = 35
BOX_MIN_GAP = 10  # minimum clear air between stacked cover boxes

COVER_TITLE_BASE = 104
# COVER hook hierarchy (logical px @ 1080)
COVER_LEAD_BASE = 88   # setup lines (smaller)
COVER_PUNCH_BASE = 120  # last 1~2 punch lines (bigger, scroll-stop)
CONTENT_SECTION_BASE = 44
CONTENT_LINE_BASE = 78  # Type A body lines
CONTENT_STATEMENT_BASE = 90  # Type B main_statement (Black, >=90px)
CONTENT_DETAIL_BASE = 55  # Type B detailed_lines (Regular)
DETAIL_LINE_FACTOR = 1.7  # 1.6~1.8x font size between detail lines
SUMMARY_HEAD_BASE = 70  # ExtraBold in cobalt box (absolute grid)
SUMMARY_NUM_SIZE = 70  # Bold cobalt numbers
SUMMARY_ITEM_SIZE = 65  # Bold/ExtraBold body – match number weight
SUMMARY_INK = (17, 17, 17)  # #111111
# Absolute layout on 1080x1920 logical canvas (multiplied by RETINA scale)
SUMMARY_TITLE_X = 120
SUMMARY_TITLE_Y = 350
SUMMARY_LIST_Y0 = 600
SUMMARY_LIST_STEP = 140
SUMMARY_NUM_X = 120
SUMMARY_TEXT_X = 220

# COVER – lead = adaptive contrast text (no box); punch = #0055FF box + centered white
COVER_HL = (0, 85, 255)  # #0055FF cobalt
COVER_LEAD_FILL = (255, 255, 255)  # fallback if sampling fails
COVER_PAD_X = 35
COVER_PAD_Y = 20  # equal top/bottom so glyph can sit dead-center
COVER_LINE_MARGIN = 15
COVER_DIM_ALPHA = 0.50  # baseline; actual alpha is adaptive per photo
COVER_DIM_MIN = 0.38
COVER_DIM_MAX = 0.64
COVER_BLOCK_MAX_W = 0.90
COVER_LEAD_DARK = (17, 17, 17)  # #111111 on bright regions

# Type A Black lines: max(bbox, font.size) + extra leading (logical px)
CONTENT_A_LINE_GAP = 40

# Push body typography down toward optical center
CONTENT_TOP_SHIFT = 0.12

FONTS_DIR = ROOT_DIR / "assets" / "fonts" / "Pretendard"
WIN_FONTS = Path("C:/Windows/Fonts")


def _resolve_font(name: str) -> Path | None:
    cand = FONTS_DIR / name
    if cand.exists() and cand.stat().st_size > 1000:
        return cand
    mapping = {
        "Pretendard-Regular.otf": ("malgun.ttf", "arial.ttf"),
        "Pretendard-Medium.otf": ("malgun.ttf", "arial.ttf"),
        "Pretendard-Bold.otf": ("malgunbd.ttf", "arialbd.ttf"),
        "Pretendard-ExtraBold.otf": ("malgunbd.ttf", "arialbd.ttf"),
        "Pretendard-Black.otf": ("malgunbd.ttf", "arialbd.ttf"),
    }
    for sys_name in mapping.get(name, ("malgunbd.ttf", "malgun.ttf")):
        p = WIN_FONTS / sys_name
        if p.exists():
            return p
    return None


def _font(size: int, *, weight: str = "bold") -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    size = max(12, int(size))
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
    """Return (left, top, right, bottom) relative to draw origin."""
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


def _advance_y(text: str, font: ImageFont.ImageFont, *, margin: int) -> int:
    """Next-line Y offset from measured glyph box + explicit margin (never overlaps)."""
    return _text_height(text, font) + max(0, int(margin))


def _clean_display(text: str) -> str:
    """Strip trailing periods / AI punctuation before draw (titles, Type A lines)."""
    t = re.sub(r"\s+", " ", (text or "").strip())
    t = t.rstrip(".。…!")
    return t


def _as_lines(raw: Any) -> list[str]:
    if isinstance(raw, list):
        return [_clean_display(str(x)) for x in raw if str(x).strip()]
    if isinstance(raw, str) and raw.strip():
        # Prefer explicit newlines from LLM only – never character-wrap
        return [_clean_display(p) for p in raw.split("\n") if p.strip()]
    return []


def _line_height(font_size: int, *, scale: int, factor: float = 1.65) -> int:
    return max(1, int(font_size * scale * factor))


def _detail_lines_of(slide: dict[str, Any]) -> list[str]:
    """Type B detail lines – array only, prose is retired."""
    lines = _as_lines(slide.get("detailed_lines"))
    if lines:
        return lines
    # Legacy prose payload: split on sentence boundaries, never wrap
    legacy = str(slide.get("detailed_paragraph") or "").strip()
    if not legacy:
        return []
    parts = [p.strip() for p in re.split(r"(?<=[.。!?])\s+|[\n·•]", legacy) if p.strip()]
    return [_clean_display(p) for p in parts][:4]


def _is_content_type_b(slide: dict[str, Any]) -> bool:
    raw = str(slide.get("content_variant") or slide.get("layout_variant") or "").strip().upper()
    if raw in {"B", "TYPE_B", "TYPE-B", "DETAIL", "DETAILED", "PARAGRAPH"}:
        return True
    if raw in {"A", "TYPE_A", "TYPE-A", "PUNCH", "BULLET"}:
        return False
    statement = str(slide.get("main_statement") or "").strip()
    return bool(statement and _detail_lines_of(slide))


def _fit_single_line(
    text: str,
    *,
    max_width: int,
    base_size: int,
    scale: int,
    weight: str = "black",
    min_size: int = 28,
) -> tuple[int, ImageFont.ImageFont]:
    """Shrink font to fit one semantic line – never wrap mid-phrase."""
    text = _clean_display(text)
    size = base_size
    while size >= min_size:
        font = _font(size * scale, weight=weight)
        if _text_width(text, font) <= max_width:
            return size, font
        size -= 2
    return min_size, _font(min_size * scale, weight=weight)


def _hex_rgb(color: str) -> tuple[int, int, int]:
    c = (color or "#0055FF").lstrip("#")
    if len(c) == 6:
        return int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16)
    return COBALT


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


def _boost_photo_pop(img: Image.Image, *, contrast: float = 1.14, vibrance: float = 1.22) -> Image.Image:
    """Lift contrast + color for scroll-stopping COVER photos (before soft dim)."""
    from PIL import ImageEnhance

    out = img.convert("RGB")
    out = ImageEnhance.Contrast(out).enhance(contrast)
    out = ImageEnhance.Color(out).enhance(vibrance)
    out = ImageEnhance.Brightness(out).enhance(1.03)
    return out


def _draw_full_dim(base: Image.Image, alpha: float = 0.28) -> None:
    """Soft black veil so photo stays readable; default ~25-30%."""
    w, h = base.size
    alpha = max(0.0, min(1.0, float(alpha)))
    overlay = Image.new("RGBA", (w, h), (0, 0, 0, int(255 * alpha)))
    base.paste(Image.alpha_composite(base.convert("RGBA"), overlay).convert("RGB"))


def _clamp_box(
    box: tuple[int, int, int, int], w: int, h: int
) -> tuple[int, int, int, int]:
    l, t, r, b = box
    l = max(0, min(w - 1, l))
    t = max(0, min(h - 1, t))
    r = max(l + 1, min(w, r))
    b = max(t + 1, min(h, b))
    return l, t, r, b


def _sample_region_rgb(
    img: Image.Image, box: tuple[int, int, int, int]
) -> tuple[float, float, float]:
    """Average RGB in box (downsampled for speed)."""
    w, h = img.size
    l, t, r, b = _clamp_box(box, w, h)
    crop = img.crop((l, t, r, b)).convert("RGB").resize((24, 24), Image.Resampling.BILINEAR)
    pixels = list(crop.getdata())
    n = max(1, len(pixels))
    return (
        sum(p[0] for p in pixels) / n,
        sum(p[1] for p in pixels) / n,
        sum(p[2] for p in pixels) / n,
    )


def _sample_luminance(img: Image.Image, box: tuple[int, int, int, int]) -> float:
    r, g, b = _sample_region_rgb(img, box)
    return 0.299 * r + 0.587 * g + 0.114 * b


def _adaptive_cover_dim_alpha(img: Image.Image) -> float:
    """Brighter title-band photo → stronger black veil."""
    w, h = img.size
    band = (int(w * 0.04), int(h * 0.26), int(w * 0.96), int(h * 0.58))
    lum = _sample_luminance(img, band)
    # Map lum ~40→DIM_MIN, ~200→DIM_MAX
    t = (lum - 40.0) / 160.0
    t = max(0.0, min(1.0, t))
    alpha = COVER_DIM_MIN + t * (COVER_DIM_MAX - COVER_DIM_MIN)
    logger.info("COVER adaptive dim lum=%.1f alpha=%.2f", lum, alpha)
    return alpha


def _contrast_ink_for_region(
    img: Image.Image, box: tuple[int, int, int, int]
) -> tuple[int, int, int]:
    """Pick white vs near-black ink from local luminance / blue-heaviness."""
    r, g, b = _sample_region_rgb(img, box)
    lum = 0.299 * r + 0.587 * g + 0.114 * b
    blue_heavy = b >= r + 18 and b >= g + 12
    # Bright (or bright-warm) → dark ink; dark / blue-tinted → white
    if lum >= 148:
        return COVER_LEAD_DARK
    if lum >= 128 and not blue_heavy:
        return COVER_LEAD_DARK
    return WHITE


def _draw_text_legible(
    draw: ImageDraw.ImageDraw,
    xy: tuple[float, float],
    text: str,
    *,
    font: ImageFont.ImageFont,
    fill: tuple[int, int, int] = WHITE,
    scale: int = 1,
) -> None:
    """White (or colored) text with thin black stroke for bright photo backgrounds."""
    stroke = max(1, 2 * scale)
    try:
        draw.text(
            xy,
            text,
            font=font,
            fill=fill,
            stroke_width=stroke,
            stroke_fill=(0, 0, 0),
        )
    except TypeError:
        # Older Pillow without stroke – manual 8-dir halo
        x, y = xy
        for dx, dy in (
            (-stroke, 0),
            (stroke, 0),
            (0, -stroke),
            (0, stroke),
            (-stroke, -stroke),
            (stroke, -stroke),
            (-stroke, stroke),
            (stroke, stroke),
        ):
            draw.text((x + dx, y + dy), text, font=font, fill=(0, 0, 0))
        draw.text(xy, text, font=font, fill=fill)


def _draw_highlight_line(
    draw: ImageDraw.ImageDraw,
    text: str,
    *,
    x: int,
    y: int,
    font: ImageFont.ImageFont,
    font_size: int,
    scale: int,
    brand: tuple[int, int, int],
    pad_x: int | None = None,
    pad_y: int | None = None,
    pad_top: int | None = None,
    pad_bottom: int | None = None,
    after_gap: int | None = None,
) -> int:
    """Flat cobalt highlight – no shadow. Asymmetric pad for Hangul."""
    del font_size
    text = _clean_display(text)
    pad_x = pad_x if pad_x is not None else BOX_PAD_X * scale
    if pad_top is not None or pad_bottom is not None:
        default_y = pad_y if pad_y is not None else BOX_PAD_Y * scale
        pt = pad_top if pad_top is not None else default_y
        pb = pad_bottom if pad_bottom is not None else default_y
    elif pad_y is not None:
        pt = pb = pad_y
    else:
        pt = BOX_PAD_TOP * scale
        pb = BOX_PAD_BOTTOM * scale

    # textbbox-relative placement (same math as COVER)
    left, top, right, bottom = _text_bbox(text, font)
    text_x = x + pad_x - left
    text_y = y + pt - top
    abs_l, abs_t = text_x + left, text_y + top
    abs_r, abs_b = text_x + right, text_y + bottom
    box = [abs_l - pad_x, abs_t - pt, abs_r + pad_x, abs_b + pb]
    draw.rectangle(box, fill=brand)
    draw.text((text_x, text_y), text, fill=WHITE, font=font)
    gap = after_gap if after_gap is not None else BOX_MIN_GAP * scale
    return int(box[3]) + max(BOX_MIN_GAP * scale, gap)


def _draw_cover_lead(
    draw: ImageDraw.ImageDraw,
    text: str,
    *,
    current_x: int,
    current_y: int,
    font: ImageFont.ImageFont,
    scale: int,
    base: Image.Image,
) -> int:
    """Setup line – adaptive white/dark ink from local photo luminance, NO blue box."""
    text = _clean_display(text)
    left, top, right, bottom = _text_bbox(text, font)
    tw = max(1, right - left)
    th = max(1, bottom - top)
    text_x = current_x - left
    text_y = current_y - top
    pad = 12 * scale
    sample = (
        int(text_x) - pad,
        int(text_y) - pad,
        int(text_x + tw) + pad,
        int(text_y + th) + pad,
    )
    fill = _contrast_ink_for_region(base, sample)
    draw.text((text_x, text_y), text, fill=fill, font=font)
    return current_y + th + COVER_LINE_MARGIN * scale


def _draw_cover_highlight(
    draw: ImageDraw.ImageDraw,
    text: str,
    *,
    current_x: int,
    current_y: int,
    font: ImageFont.ImageFont,
    scale: int,
    fill: tuple[int, int, int] = COVER_HL,
) -> int:
    """Punch line – flat #0055FF box, white Black type vertically centered. No shadow."""
    text = _clean_display(text)
    pad_x = COVER_PAD_X * scale
    pad_y = COVER_PAD_Y * scale
    line_margin = COVER_LINE_MARGIN * scale

    left, top, right, bottom = _text_bbox(text, font)
    text_width = max(1, right - left)
    text_height = max(1, bottom - top)

    box_left = current_x - pad_x
    box_top = current_y
    box_right = current_x + text_width + pad_x
    box_bottom = current_y + text_height + pad_y * 2

    draw.rectangle([box_left, box_top, box_right, box_bottom], fill=fill)
    text_x = current_x - left
    text_y = int(round(current_y + (box_bottom - box_top) / 2 - (top + bottom) / 2))
    draw.text((text_x, text_y), text, fill=WHITE, font=font)
    return int(box_bottom) + line_margin


def _brand_label(logo: str) -> str:
    name = re.sub(r"^@", "", logo or "").strip() or BRAND_DEFAULT
    if name.lower() in {"authority", "authority reels"}:
        return "Authority Reels"
    return name


def _render_cover(
    base: Image.Image,
    slide: dict[str, Any],
    *,
    brand_color: str,
    logo: str,
    scale: int,
) -> Image.Image:
    """COVER – adaptive dim + lead ink; punch = centered white on cobalt box."""
    del brand_color
    dim_alpha = _adaptive_cover_dim_alpha(base)
    _draw_full_dim(base, alpha=dim_alpha)
    draw = ImageDraw.Draw(base)
    w, h = CARD_W * scale, CARD_H * scale
    current_x = MARGIN_X * scale
    max_block = int(w * COVER_BLOCK_MAX_W)
    pad_w = COVER_PAD_X * 2 * scale
    text_max = max_block - pad_w - 8 * scale

    # Brand / tag also follow local contrast after dim
    brand_font = _font(26 * scale, weight="medium")
    brand_y = int(h * 0.09)
    brand_fill = _contrast_ink_for_region(
        base,
        (current_x, brand_y, current_x + 420 * scale, brand_y + 40 * scale),
    )
    draw.text((current_x, brand_y), _brand_label(logo), font=brand_font, fill=brand_fill)

    lines = _as_lines(slide.get("title_lines"))
    if not lines:
        title = _clean_display(str(slide.get("main_title") or slide.get("hook") or ""))
        lines = [title] if title else []
    lines = [ln for ln in (_clean_display(x) for x in lines[:4]) if ln]
    if not lines:
        return base

    n = len(lines)
    if n <= 1:
        punch_from = 0
    else:
        punch_from = max(1, n - 2)

    prepared: list[tuple[str, ImageFont.ImageFont, bool]] = []
    for i, clean in enumerate(lines):
        is_punch = i >= punch_from
        _, font = _fit_single_line(
            clean,
            max_width=text_max,
            base_size=COVER_PUNCH_BASE if is_punch else COVER_LEAD_BASE,
            scale=scale,
            weight="black",
            min_size=64 if is_punch else 52,
        )
        prepared.append((clean, font, is_punch))

    stack_h = 0
    for clean, font, is_punch in prepared:
        th = _text_height(clean, font)
        if is_punch:
            stack_h += th + COVER_PAD_Y * 2 * scale
        else:
            stack_h += th
    stack_h += COVER_LINE_MARGIN * scale * max(0, len(prepared) - 1)
    current_y = max(int(h * 0.28), (h - stack_h) // 2 - 40 * scale)

    for clean, font, is_punch in prepared:
        if is_punch:
            current_y = _draw_cover_highlight(
                draw,
                clean,
                current_x=current_x,
                current_y=current_y,
                font=font,
                scale=scale,
            )
        else:
            current_y = _draw_cover_lead(
                draw,
                clean,
                current_x=current_x,
                current_y=current_y,
                font=font,
                scale=scale,
                base=base,
            )

    tag = _clean_display(str(slide.get("category_tag") or slide.get("badge_text") or ""))
    if tag and not tag.startswith("#"):
        tag = f"#{tag}"
    if tag:
        tag_font = _font(28 * scale, weight="medium")
        ty = h - 140 * scale
        tag_fill = _contrast_ink_for_region(
            base,
            (current_x, ty - 10 * scale, current_x + 360 * scale, ty + 40 * scale),
        )
        draw.text((current_x, ty), tag, font=tag_font, fill=tag_fill)
        tw = _text_width(tag, tag_font)
        draw.line(
            [(current_x, ty + 34 * scale), (current_x + tw, ty + 34 * scale)],
            fill=tag_fill,
            width=max(2, scale),
        )

    return base


def _render_content_type_a(
    draw: ImageDraw.ImageDraw,
    *,
    mx: int,
    y: int,
    max_w: int,
    h: int,
    scale: int,
    explanations: list[str],
) -> None:
    """Type A – punchy short lines, large Black type, pushed toward center."""
    y = max(y, int(h * (0.40 + CONTENT_TOP_SHIFT)))
    for line in explanations[:3]:
        size, font = _fit_single_line(
            line,
            max_width=max_w,
            base_size=CONTENT_LINE_BASE,
            scale=scale,
            weight="black",
            min_size=40,
        )
        clean = _clean_display(line)
        left, top, _, _ = _text_bbox(clean, font)
        draw.text((mx - left, y - top), clean, fill=INK, font=font)
        # Black weight needs real leading: max(bbox, font.size) + 30~40px
        font_px = int(getattr(font, "size", size * scale) or (size * scale))
        line_h = max(_text_height(clean, font), font_px)
        y += line_h + CONTENT_A_LINE_GAP * scale
        if y > h - 100 * scale:
            break


def _render_content_type_b(
    draw: ImageDraw.ImageDraw,
    *,
    mx: int,
    max_w: int,
    h: int,
    scale: int,
    main_statement: str,
    detailed_lines: list[str],
) -> None:
    """Type B – Black claim (>=90px) then Regular detail lines at 1.7x spacing."""
    statement = _clean_display(main_statement)
    if statement:
        _, font = _fit_single_line(
            statement,
            max_width=max_w,
            base_size=CONTENT_STATEMENT_BASE,
            scale=scale,
            weight="black",
            min_size=64,
        )
        y = int(h * (0.30 + CONTENT_TOP_SHIFT))
        left, top, _, _ = _text_bbox(statement, font)
        draw.text((mx - left, y - top), statement, fill=INK, font=font)

    if not detailed_lines:
        return
    # No textwrap: each array item is drawn as its own line
    y = int(h * (0.45 + CONTENT_TOP_SHIFT))
    for raw in detailed_lines[:4]:
        line = _clean_display(raw)
        if not line:
            continue
        size, font = _fit_single_line(
            line,
            max_width=max_w,
            base_size=CONTENT_DETAIL_BASE,
            scale=scale,
            weight="regular",
            min_size=38,
        )
        left, top, _, _ = _text_bbox(line, font)
        draw.text((mx - left, y - top), line, fill=SOFT_INK, font=font)
        # Generous 1.6~1.8x rhythm driven by nominal font size
        y += _line_height(size, scale=scale, factor=DETAIL_LINE_FACTOR)
        if y > h - 90 * scale:
            break


def _render_content(
    base: Image.Image,
    slide: dict[str, Any],
    *,
    brand_color: str,
    logo: str,
    scale: int,
) -> Image.Image:
    draw = ImageDraw.Draw(base)
    brand = _hex_rgb(brand_color)
    w, h = CARD_W * scale, CARD_H * scale
    mx = MARGIN_X * scale
    max_w = int(w * 0.86)

    bfont = _font(24 * scale, weight="medium")
    blabel = _brand_label(logo)
    bw = _text_width(blabel, bfont)
    draw.text((w - mx - bw, int(h * 0.055)), blabel, fill=BRAND_GRAY, font=bfont)

    num = str(slide.get("section_number") or "").strip()
    stitle = _clean_display(str(slide.get("section_title") or slide.get("main_title") or ""))
    section = f"{num}. {stitle}".strip() if num else stitle
    section = _clean_display(section)

    # Section highlight, shifted down with the rest of the body block
    y = int(h * (0.20 + CONTENT_TOP_SHIFT))
    sec_size, sec_font = _fit_single_line(
        section,
        max_width=max_w - (BOX_PAD_X * 2 + 10) * scale,
        base_size=CONTENT_SECTION_BASE,
        scale=scale,
        weight="bold",
        min_size=28,
    )
    y = _draw_highlight_line(
        draw,
        section,
        x=mx,
        y=y,
        font=sec_font,
        font_size=sec_size,
        scale=scale,
        brand=brand,
        after_gap=20 * scale,
    )

    if _is_content_type_b(slide):
        _render_content_type_b(
            draw,
            mx=mx,
            max_w=max_w,
            h=h,
            scale=scale,
            main_statement=str(slide.get("main_statement") or slide.get("main_title") or ""),
            detailed_lines=_detail_lines_of(slide),
        )
        return base

    explanations = _as_lines(slide.get("explanations"))
    if not explanations:
        explanations = _as_lines(slide.get("main_text") or slide.get("body") or "")
        if not explanations:
            explanations = _as_lines(slide.get("body_points") or [])
    _render_content_type_a(
        draw,
        mx=mx,
        y=y + 24 * scale,
        max_w=max_w,
        h=h,
        scale=scale,
        explanations=explanations,
    )
    return base


def _render_summary(
    base: Image.Image,
    slide: dict[str, Any],
    *,
    brand_color: str,
    logo: str,
    scale: int,
) -> Image.Image:
    """SUMMARY – absolute 1080×1920 grid (no dynamic vertical centering)."""
    draw = ImageDraw.Draw(base)
    brand = _hex_rgb(brand_color)
    w, h = CARD_W * scale, CARD_H * scale
    mx = MARGIN_X * scale

    bfont = _font(24 * scale, weight="medium")
    blabel = _brand_label(logo)
    bw = _text_width(blabel, bfont)
    draw.text((w - mx - bw, int(h * 0.055)), blabel, fill=BRAND_GRAY, font=bfont)

    title = _clean_display(str(slide.get("main_title") or slide.get("hook") or "핵심 체크 포인트"))
    title_font = _font(SUMMARY_HEAD_BASE * scale, weight="extrabold")
    # Title box: absolute X=120, Y=350 — same generous asymmetric padding as COVER
    _draw_highlight_line(
        draw,
        title,
        x=SUMMARY_TITLE_X * scale,
        y=SUMMARY_TITLE_Y * scale,
        font=title_font,
        font_size=SUMMARY_HEAD_BASE,
        scale=scale,
        brand=brand,
        pad_x=COVER_PAD_X * scale,
        pad_top=COVER_PAD_Y * scale,
        pad_bottom=COVER_PAD_Y * scale,
        after_gap=0,
    )

    raw_items = _as_lines(slide.get("summary_list") or slide.get("body_points") or [])[:4]
    items: list[str] = []
    for item in raw_items:
        clean = re.sub(r"^\d+[\.\)]\s*", "", item).strip()
        clean = _clean_display(clean)
        if clean:
            items.append(clean)

    # Fixed hanging indent: numbers X=120 / body X=220 Bold 65px
    num_font = _font(SUMMARY_NUM_SIZE * scale, weight="bold")
    body_font = _font(SUMMARY_ITEM_SIZE * scale, weight="extrabold")
    body_max = w - SUMMARY_TEXT_X * scale - mx
    for i, clean in enumerate(items):
        y = (SUMMARY_LIST_Y0 + i * SUMMARY_LIST_STEP) * scale
        num = f"{i + 1}."
        n_left, n_top, _, _ = _text_bbox(num, num_font)
        draw.text(
            (SUMMARY_NUM_X * scale - n_left, y - n_top),
            num,
            fill=brand,
            font=num_font,
        )
        fitted = body_font
        if _text_width(clean, body_font) > body_max:
            _, fitted = _fit_single_line(
                clean,
                max_width=body_max,
                base_size=SUMMARY_ITEM_SIZE,
                scale=scale,
                weight="extrabold",
                min_size=44,
            )
        b_left, b_top, _, _ = _text_bbox(clean, fitted)
        draw.text(
            (SUMMARY_TEXT_X * scale - b_left, y - b_top),
            clean,
            fill=SUMMARY_INK,
            font=fitted,
        )

    return base


def _normalize_slide_type(slide: dict[str, Any], index: int, total: int) -> str:
    raw = str(slide.get("slide_type") or "").upper().strip()
    if raw in {"TITLE", "COVER"}:
        return "COVER"
    if raw in {"DETAIL", "CONTENT", "QUOTE", "BODY"}:
        return "CONTENT"
    if raw == "SUMMARY":
        return "SUMMARY"
    if index <= 0:
        return "COVER"
    if index >= total - 1:
        return "SUMMARY"
    return "CONTENT"


def render_slide_pil(
    slide: dict[str, Any],
    background: Path,
    output_path: Path,
    *,
    brand_color: str = "#0055FF",
    logo: str = "authority",
    slide_index: int = 1,
    slide_total: int = 7,
) -> Path:
    scale = RETINA
    w, h = CARD_W * scale, CARD_H * scale
    ensure_dir(output_path.parent)

    slide_type = _normalize_slide_type(slide, slide_index - 1, slide_total)

    if slide_type == "COVER":
        if background.exists():
            with Image.open(background) as raw:
                # Keep raw phone-snap look – no commercial vibrance boost
                base = _fit_cover(raw, w, h)
        else:
            base = Image.new("RGB", (w, h), (12, 12, 16))
        img = _render_cover(base, slide, brand_color=brand_color, logo=logo, scale=scale)
    elif slide_type == "SUMMARY":
        base = Image.new("RGB", (w, h), PURE_WHITE)
        img = _render_summary(base, slide, brand_color=brand_color, logo=logo, scale=scale)
    else:
        base = Image.new("RGB", (w, h), PURE_WHITE)  # pages 2+ pure white
        img = _render_content(base, slide, brand_color=brand_color, logo=logo, scale=scale)

    img.save(output_path, format="PNG", optimize=True)
    logger.info("PIL insight slide %d (%s) -> %s", slide_index, slide_type, output_path.name)
    return output_path


def render_all_pil(
    slides: list[dict[str, Any]],
    backgrounds: list[Path],
    output_dir: Path,
    *,
    brand_color: str = "#0055FF",
    logo: str = "authority",
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
            )
        )
    return out
