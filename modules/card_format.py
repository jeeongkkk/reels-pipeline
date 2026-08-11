"""Instagram card output formats (feed 4:5 vs reels 9:16).

Logical canvas is @1x; PNG export multiplies by RETINA (2x).
"""

from __future__ import annotations

from dataclasses import dataclass

RETINA = 2
# Legacy layout baseline (pre-format-select 3:4 canvas)
_BASELINE_H = 1440


@dataclass(frozen=True)
class CardOutputFormat:
    id: str
    label: str
    logical_w: int
    logical_h: int
    # Safe-zone margins as fraction of canvas (text stays inside)
    margin_x: float
    margin_top: float
    margin_bottom: float

    @property
    def output_w(self) -> int:
        return int(self.logical_w * RETINA)

    @property
    def output_h(self) -> int:
        return int(self.logical_h * RETINA)

    @property
    def resolution_label(self) -> str:
        return f"{self.output_w}×{self.output_h}"

    @property
    def v_scale(self) -> float:
        """Scale vertical gaps relative to the old 1440-tall layout."""
        return self.logical_h / float(_BASELINE_H)

    def canvas_px(self, scale: int | None = None) -> tuple[int, int]:
        s = int(scale if scale is not None else RETINA)
        return self.logical_w * s, self.logical_h * s

    def safe_box(self, scale: int | None = None) -> tuple[int, int, int, int]:
        """Return (left, top, right, bottom) inclusive text bounds in pixels."""
        s = int(scale if scale is not None else RETINA)
        w, h = self.canvas_px(s)
        left = int(round(w * self.margin_x))
        right = w - left
        top = int(round(h * self.margin_top))
        bottom = h - int(round(h * self.margin_bottom))
        return left, top, right, bottom


FORMATS: dict[str, CardOutputFormat] = {
    "feed_4x5": CardOutputFormat(
        id="feed_4x5",
        label="인스타 피드용 (4:5 비율)",
        logical_w=1080,
        logical_h=1350,  # → 2160×2700
        margin_x=100 / 1080,
        margin_top=0.08,
        margin_bottom=0.075,
    ),
    "reels_9x16": CardOutputFormat(
        id="reels_9x16",
        label="인스타 릴스용 (9:16 비율)",
        logical_w=1080,
        logical_h=1920,  # → 2160×3840
        margin_x=0.10,
        margin_top=0.15,
        margin_bottom=0.15,
    ),
}

FORMAT_OPTIONS: tuple[str, ...] = ("feed_4x5", "reels_9x16")
DEFAULT_FORMAT_ID = "feed_4x5"

_active: CardOutputFormat = FORMATS[DEFAULT_FORMAT_ID]


def resolve_card_format(format_id: str | CardOutputFormat | None = None) -> CardOutputFormat:
    if isinstance(format_id, CardOutputFormat):
        return format_id
    key = str(format_id or DEFAULT_FORMAT_ID).strip().lower()
    aliases = {
        "feed": "feed_4x5",
        "4:5": "feed_4x5",
        "4x5": "feed_4x5",
        "instagram_feed": "feed_4x5",
        "reels": "reels_9x16",
        "9:16": "reels_9x16",
        "9x16": "reels_9x16",
        "instagram_reels": "reels_9x16",
    }
    key = aliases.get(key, key)
    return FORMATS.get(key, FORMATS[DEFAULT_FORMAT_ID])


def set_active_card_format(format_id: str | CardOutputFormat | None = None) -> CardOutputFormat:
    global _active
    _active = resolve_card_format(format_id)
    return _active


def get_active_card_format() -> CardOutputFormat:
    return _active


def format_radio_label(format_id: str) -> str:
    fmt = resolve_card_format(format_id)
    return f"{fmt.label} → {fmt.resolution_label}"
