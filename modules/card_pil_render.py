"""PIL Dark Minimal Magazine compositor – COVER / CONTENT / OUTRO."""

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

# ── Dark Minimal Magazine palette ─────────────────────────────
BG_DARK = (21, 21, 21)  # #151515
ACCENT = (221, 81, 56)  # #DD5138 terracotta
PURE_WHITE = (255, 255, 255)
SOFT_WHITE = (221, 221, 221)  # #DDDDDD secondary
INK = PURE_WHITE

MARGIN_X = 80
BRAND_FALLBACK = "WITH CHOYOOL"
DEFAULT_BRAND_COLOR = "#DD5138"

COVER_DIM_ALPHA = 0.62
COVER_TITLE_BASE = 92
COVER_TITLE_MIN = 56
CONTENT_HEADER_BASE = 42
CONTENT_BODY_BASE = 52
CONTENT_LINE_FACTOR = 1.8
OUTRO_TITLE_BASE = 110
HEADER_PAD_X = 22
HEADER_PAD_TOP = 12
HEADER_PAD_BOTTOM = 16
LOGO_TOP_Y = 120  # logical px
LOGO_MAX_W = 280
LOGO_MAX_H = 160

FONTS_DIR = ROOT_DIR / "assets" / "fonts" / "Pretendard"
WIN_FONTS = Path("C:/Windows/Fonts")
LOGO_CANDIDATES = (
    ROOT_DIR / "logo.png",
    ROOT_DIR / "assets" / "logo.png",
)


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


def _parse_emphasis(text: str) -> list[tuple[str, bool]]:
    """Split '*강조*' markers into (segment, emphasized) parts."""
    raw = text or ""
    parts: list[tuple[str, bool]] = []
    i = 0
    while i < len(raw):
        if raw[i] == "*":
            j = raw.find("*", i + 1)
            if j > i + 1:
                parts.append((raw[i + 1 : j], True))
                i = j + 1
                continue
        nxt = raw.find("*", i)
        if nxt < 0:
            chunk = raw[i:]
            if chunk:
                parts.append((chunk, False))
            break
        chunk = raw[i:nxt]
        if chunk:
            parts.append((chunk, False))
        i = nxt
    if not parts:
        return [(raw.replace("*", ""), False)]
    return parts


def _plain_from_emphasis(text: str) -> str:
    return "".join(seg for seg, _ in _parse_emphasis(text))


def _fit_single_line(
    text: str,
    *,
    max_width: int,
    base_size: int,
    scale: int,
    weight: str = "black",
    min_size: int = 28,
) -> tuple[int, ImageFont.ImageFont]:
    plain = _plain_from_emphasis(text)
    size = base_size
    while size >= min_size:
        font = _font(size * scale, weight=weight)
        if _text_width(plain, font) <= max_width:
            return size, font
        size -= 2
    return min_size, _font(min_size * scale, weight=weight)


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


def _logo_to_rgba(img: Image.Image) -> Image.Image:
    """Knock out near-black backdrop so logo sits on #151515 cleanly."""
    rgba = img.convert("RGBA")
    pixels = rgba.load()
    w, h = rgba.size
    for y in range(h):
        for x in range(w):
            r, g, b, a = pixels[x, y]
            if r < 28 and g < 28 and b < 28:
                pixels[x, y] = (r, g, b, 0)
    return rgba


def _find_logo_path() -> Path | None:
    for p in LOGO_CANDIDATES:
        if p.exists() and p.stat().st_size > 100:
            return p
    return None


def draw_logo(
    base: Image.Image,
    *,
    scale: int,
    position: str = "top",
    brand_name: str = BRAND_FALLBACK,
) -> None:
    """Place brand logo. position: 'top' (center) | 'bottom_right'."""
    w, h = base.size
    logo_path = _find_logo_path()
    draw = ImageDraw.Draw(base)

    if logo_path:
        with Image.open(logo_path) as raw:
            logo = _logo_to_rgba(raw)
        max_w = LOGO_MAX_W * scale
        max_h = LOGO_MAX_H * scale
        if position == "bottom_right":
            max_w = int(LOGO_MAX_W * 0.85 * scale)
            max_h = int(LOGO_MAX_H * 0.85 * scale)
        lw, lh = logo.size
        ratio = min(max_w / max(lw, 1), max_h / max(lh, 1))
        nw, nh = max(1, int(lw * ratio)), max(1, int(lh * ratio))
        logo = logo.resize((nw, nh), Image.Resampling.LANCZOS)
        if position == "bottom_right":
            x = w - MARGIN_X * scale - nw
            y = h - 160 * scale - nh
        else:
            x = (w - nw) // 2
            y = LOGO_TOP_Y * scale
        base.paste(logo, (x, y), logo)
        return

    # Fallback text wordmark
    label = re.sub(r"^@", "", brand_name or "").strip() or BRAND_FALLBACK
    font = _font(28 * scale, weight="medium")
    tw = _text_width(label, font)
    th = _text_height(label, font)
    if position == "bottom_right":
        x = w - MARGIN_X * scale - int(tw)
        y = h - 180 * scale
    else:
        x = (w - int(tw)) // 2
        y = LOGO_TOP_Y * scale
    left, top, _, _ = _text_bbox(label, font)
    draw.text((x - left, y - top), label, fill=PURE_WHITE, font=font)
    # Thin vertical accent bar (Young Boss cue)
    bar_x = x - 18 * scale
    draw.line(
        [(bar_x, y), (bar_x, y + th)],
        fill=PURE_WHITE,
        width=max(2, scale),
    )


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
    """Center-draw a line with optional *emphasis* segments in accent color."""
    segments = _parse_emphasis(text)
    plain = "".join(seg for seg, _ in segments)
    if not plain:
        return y
    total_w = _text_width(plain, font)
    left0, top0, _, bottom0 = _text_bbox(plain, font)
    x = (canvas_w - int(total_w)) // 2
    cursor = x
    for seg, emph in segments:
        if not seg:
            continue
        fill = accent if emph else default_fill
        sl, st, _, _ = _text_bbox(seg, font)
        draw.text((cursor - sl, y - top0), seg, fill=fill, font=font)
        cursor += int(_text_width(seg, font))
    return y + (bottom0 - top0)


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
    """Tight terracotta box behind white header, centered."""
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
    stitle = _clean_display(str(slide.get("section_title") or slide.get("main_title") or ""))
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
) -> Image.Image:
    """COVER – deep dim, no boxes, pure white centered Black type + top logo."""
    del brand_color
    _draw_full_dim(base, alpha=COVER_DIM_ALPHA)
    draw = ImageDraw.Draw(base)
    w, h = CARD_W * scale, CARD_H * scale
    draw_logo(base, scale=scale, position="top", brand_name=logo)

    lines = _as_lines(slide.get("title_lines"))
    if not lines:
        title = _clean_display(str(slide.get("main_title") or slide.get("hook") or ""))
        lines = [title] if title else []
    lines = [ln for ln in lines[:4] if ln]
    if not lines:
        return base

    max_w = int(w * 0.86)
    prepared: list[tuple[str, ImageFont.ImageFont]] = []
    for clean in lines:
        _, font = _fit_single_line(
            clean,
            max_width=max_w,
            base_size=COVER_TITLE_BASE,
            scale=scale,
            weight="black",
            min_size=COVER_TITLE_MIN,
        )
        prepared.append((clean, font))

    gap = int(28 * scale)
    stack_h = sum(_text_height(t, f) for t, f in prepared) + gap * max(0, len(prepared) - 1)
    y = max(int(h * 0.32), (h - stack_h) // 2 - 20 * scale)

    for clean, font in prepared:
        y = _draw_centered_line(draw, clean, y=y, canvas_w=w, font=font, fill=PURE_WHITE)
        y += gap

    return base


def _render_content(
    base: Image.Image,
    slide: dict[str, Any],
    *,
    brand_color: str,
    logo: str,
    scale: int,
) -> Image.Image:
    """CONTENT – solid #151515, terracotta header box, centered body + *emphasis*."""
    accent = _hex_rgb(brand_color)
    draw = ImageDraw.Draw(base)
    w, h = CARD_W * scale, CARD_H * scale
    draw_logo(base, scale=scale, position="top", brand_name=logo)

    header = _section_header(slide)
    max_w = int(w * 0.88)
    y = int(h * 0.28)

    if header:
        _, hfont = _fit_single_line(
            header,
            max_width=max_w - HEADER_PAD_X * 2 * scale,
            base_size=CONTENT_HEADER_BASE,
            scale=scale,
            weight="bold",
            min_size=28,
        )
        y = _draw_header_box(
            draw,
            header,
            y=y,
            canvas_w=w,
            font=hfont,
            accent=accent,
            scale=scale,
        )
        y += int(72 * scale)

    body_lines = _body_lines_of(slide)[:6]
    for raw in body_lines:
        if not raw.strip():
            continue
        size, font = _fit_single_line(
            raw,
            max_width=max_w,
            base_size=CONTENT_BODY_BASE,
            scale=scale,
            weight="bold",
            min_size=34,
        )
        y = _draw_centered_emphasis_line(
            draw,
            raw,
            y=y,
            canvas_w=w,
            font=font,
            accent=accent,
            default_fill=PURE_WHITE,
        )
        y += int(size * scale * (CONTENT_LINE_FACTOR - 1.0))
        if y > h - 140 * scale:
            break
    return base


def _render_outro(
    base: Image.Image,
    slide: dict[str, Any],
    *,
    brand_color: str,
    logo: str,
    scale: int,
) -> Image.Image:
    """OUTRO – huge slightly-left title + bottom-right logo."""
    del brand_color
    draw = ImageDraw.Draw(base)
    w, h = CARD_W * scale, CARD_H * scale

    lines = _as_lines(slide.get("title_lines"))
    if not lines:
        title = _clean_display(str(slide.get("main_title") or slide.get("hook") or ""))
        if title:
            # Break into short breath units for magazine impact
            if len(title) > 12 and " " not in title:
                # Hangul: split roughly into 2–3 chunks
                chunk = max(4, len(title) // 3)
                lines = [title[i : i + chunk] for i in range(0, len(title), chunk)][:3]
            else:
                lines = [p for p in re.split(r"\s+|/", title) if p][:4] or [title]
    if not lines:
        items = _as_lines(slide.get("summary_list") or [])
        if items:
            lines = ["당신의", "사수가", "되어드립니다"]
        else:
            lines = ["당신의", "사수가", "되어드립니다"]

    max_w = int(w * 0.78)
    prepared: list[tuple[str, ImageFont.ImageFont]] = []
    for clean in lines[:4]:
        _, font = _fit_single_line(
            clean,
            max_width=max_w,
            base_size=OUTRO_TITLE_BASE,
            scale=scale,
            weight="black",
            min_size=64,
        )
        prepared.append((clean, font))

    gap = int(18 * scale)
    stack_h = sum(_text_height(t, f) for t, f in prepared) + gap * max(0, len(prepared) - 1)
    y = max(int(h * 0.30), (h - stack_h) // 2 - 80 * scale)
    # Slightly left of center
    left_nudge = int(w * 0.08)

    for clean, font in prepared:
        plain = _plain_from_emphasis(clean)
        left, top, right, bottom = _text_bbox(plain, font)
        tw = right - left
        x = (w - tw) // 2 - left_nudge - left
        draw.text((x, y - top), plain, fill=PURE_WHITE, font=font)
        y += (bottom - top) + gap

    sub = _clean_display(str(slide.get("category_tag") or slide.get("badge_text") or ""))
    if not sub:
        # Optional soft subtitle from first summary item
        items = _as_lines(slide.get("summary_list") or [])
        if items:
            sub = items[0]
    if sub:
        sfont = _font(28 * scale, weight="medium")
        left, top, right, bottom = _text_bbox(sub, sfont)
        tw = right - left
        x = (w - tw) // 2 - left_nudge - left
        draw.text((x, y + 20 * scale - top), sub, fill=SOFT_WHITE, font=sfont)

    draw_logo(base, scale=scale, position="bottom_right", brand_name=logo)
    return base


def _normalize_slide_type(slide: dict[str, Any], index: int, total: int) -> str:
    raw = str(slide.get("slide_type") or "").upper().strip()
    if raw in {"TITLE", "COVER"}:
        return "COVER"
    if raw in {"DETAIL", "CONTENT", "QUOTE", "BODY"}:
        return "CONTENT"
    if raw in {"SUMMARY", "OUTRO", "CTA", "ENDING"}:
        return "OUTRO"
    if index <= 0:
        return "COVER"
    if index >= total - 1:
        return "OUTRO"
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
        img = _render_cover(base, slide, brand_color=color, logo=logo, scale=scale)
    elif slide_type == "OUTRO":
        base = Image.new("RGB", (w, h), BG_DARK)
        img = _render_outro(base, slide, brand_color=color, logo=logo, scale=scale)
    else:
        base = Image.new("RGB", (w, h), BG_DARK)
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
