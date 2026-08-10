"""Brand accent palettes: box (cover/header) vs body highlight colors."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any


_HEX_RE = re.compile(r"^#?[0-9A-Fa-f]{6}$")


@dataclass(frozen=True)
class ColorSwatch:
    id: str
    label: str
    box: str
    highlight: str

    def to_dict(self) -> dict[str, str]:
        return {
            "id": self.id,
            "label": self.label,
            "box": normalize_hex(self.box),
            "highlight": normalize_hex(self.highlight),
        }


def normalize_hex(raw: str, fallback: str = "#FC4D01") -> str:
    def _ok(v: str) -> str | None:
        t = (v or "").strip()
        if not t:
            return None
        if not t.startswith("#"):
            t = f"#{t}"
        if re.fullmatch(r"#[0-9A-Fa-f]{6}", t):
            return "#" + t[1:].upper()
        return None

    return _ok(raw) or _ok(fallback) or "#FC4D01"


def _as_swatch(raw: dict[str, Any], *, fallback_box: str) -> ColorSwatch | None:
    if not isinstance(raw, dict):
        return None
    box = normalize_hex(str(raw.get("box") or raw.get("brand_color") or fallback_box), fallback_box)
    hi = normalize_hex(str(raw.get("highlight") or raw.get("highlight_color") or box), box)
    sid = str(raw.get("id") or raw.get("label") or box).strip() or box
    label = str(raw.get("label") or sid).strip() or sid
    return ColorSwatch(id=sid, label=label, box=box, highlight=hi)


DEFAULT_WITHCHOYOOL: tuple[ColorSwatch, ...] = (
    ColorSwatch("signature", "시그니처 주황", "#FC4D01", "#FC4D01"),
    ColorSwatch("coral_gold", "코랄 + 골드", "#FF6B4A", "#E8B923"),
    ColorSwatch("navy_amber", "네이비 + 앰버", "#1B3A5F", "#F59E0B"),
    ColorSwatch("charcoal_orange", "차콜 + 주황", "#2D2A26", "#FC4D01"),
    ColorSwatch("teal_coral", "틸 + 코랄", "#0F766E", "#FF7A59"),
)

DEFAULT_BEBESKIN: tuple[ColorSwatch, ...] = (
    ColorSwatch("sage", "세이지", "#5B8C7A", "#5B8C7A"),
    ColorSwatch("sage_peach", "세이지 + 피치", "#5B8C7A", "#E8A87C"),
    ColorSwatch("mint_coral", "민트 + 코랄", "#7CB7A0", "#E07A5F"),
    ColorSwatch("blush_sage", "블러시 + 세이지", "#D4A5A5", "#5B8C7A"),
    ColorSwatch("sky_soft", "스카이 + 민트", "#6B9AC4", "#7CB7A0"),
)


def palettes_for_brand(brand: dict[str, Any] | None = None) -> list[ColorSwatch]:
    brand = brand or {}
    cn = brand.get("card_news") or {}
    fallback = normalize_hex(str(cn.get("brand_color") or "#FC4D01"))
    raw_list = cn.get("color_palette") or brand.get("color_palette") or []
    out: list[ColorSwatch] = []
    if isinstance(raw_list, list):
        for item in raw_list:
            sw = _as_swatch(item, fallback_box=fallback)
            if sw:
                out.append(sw)
    if out:
        return out

    # Built-in fallbacks by profile / name
    profile_id = str((brand.get("profile") or {}).get("id") or "").lower()
    name = str((brand.get("brand") or {}).get("name") or "").lower()
    if profile_id == "bebeskin" or "bebe" in name:
        return list(DEFAULT_BEBESKIN)
    return list(DEFAULT_WITHCHOYOOL)


def default_box_color(brand: dict[str, Any] | None = None) -> str:
    cn = (brand or {}).get("card_news") or {}
    return normalize_hex(str(cn.get("brand_color") or "#FC4D01"))


def default_highlight_color(brand: dict[str, Any] | None = None) -> str:
    cn = (brand or {}).get("card_news") or {}
    box = default_box_color(brand)
    return normalize_hex(str(cn.get("highlight_color") or box), box)
