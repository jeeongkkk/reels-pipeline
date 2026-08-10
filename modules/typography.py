"""Shared typography options for card-news + reels UI/render."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


FONT_FAMILIES: dict[str, str] = {
    "pretendard": "Pretendard",
    "paperlogy": "Paperlogy",
    "freesentation": "Freesentation",
    "malgun": "맑은 고딕",
}

# UI order: thin → black
FONT_WEIGHTS: dict[str, str] = {
    "thin": "Thin (가장 얇음)",
    "extralight": "ExtraLight",
    "light": "Light",
    "regular": "Regular",
    "medium": "Medium",
    "semibold": "SemiBold",
    "bold": "Bold",
    "extrabold": "ExtraBold",
    "black": "Black (가장 굵음)",
}

WEIGHT_KEYS = tuple(FONT_WEIGHTS.keys())
FAMILY_KEYS = tuple(FONT_FAMILIES.keys())


@dataclass
class TypeStyle:
    family: str = "pretendard"
    title_weight: str = "extrabold"  # cover / orange header / outro main
    body_weight: str = "regular"  # body + summary items
    wordmark_weight: str = "bold"
    handle_weight: str = "medium"
    sub_weight: str = "regular"  # outro subtitle / source

    def normalized(self) -> TypeStyle:
        fam = (self.family or "pretendard").strip().lower()
        if fam not in FONT_FAMILIES:
            fam = "pretendard"
        return TypeStyle(
            family=fam,
            title_weight=_norm_weight(self.title_weight, "extrabold"),
            body_weight=_norm_weight(self.body_weight, "regular"),
            wordmark_weight=_norm_weight(self.wordmark_weight, "bold"),
            handle_weight=_norm_weight(self.handle_weight, "medium"),
            sub_weight=_norm_weight(self.sub_weight, "regular"),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self.normalized())


def _norm_weight(raw: str, default: str) -> str:
    w = (raw or default).strip().lower().replace(" ", "").replace("-", "").replace("_", "")
    aliases = {
        "extralight": "extralight",
        "ultralight": "extralight",
        "semilight": "light",
        "normal": "regular",
        "book": "regular",
        "demi": "semibold",
        "demibold": "semibold",
        "semibold": "semibold",
        "heavy": "extrabold",
        "extrabold": "extrabold",
        "ultrabold": "extrabold",
        "fat": "black",
    }
    if w in FONT_WEIGHTS:
        return w
    if w in aliases:
        return aliases[w]
    return default if default in FONT_WEIGHTS else "regular"


def type_style_from_mapping(data: dict[str, Any] | None = None) -> TypeStyle:
    d = data or {}
    return TypeStyle(
        family=str(d.get("family") or "pretendard"),
        title_weight=str(d.get("title_weight") or "extrabold"),
        body_weight=str(d.get("body_weight") or "regular"),
        wordmark_weight=str(d.get("wordmark_weight") or "bold"),
        handle_weight=str(d.get("handle_weight") or "medium"),
        sub_weight=str(d.get("sub_weight") or "regular"),
    ).normalized()


def default_type_style_for_brand(brand: dict[str, Any] | None = None) -> TypeStyle:
    cn = (brand or {}).get("card_news") or {}
    typo = (brand or {}).get("typography") or {}
    family = str(
        typo.get("family")
        or cn.get("font_family")
        or "pretendard"
    ).lower()
    if family == "pretendard":
        pass
    elif "paper" in family:
        family = "paperlogy"
    elif "free" in family:
        family = "freesentation"
    elif "malgun" in family or "맑은" in family:
        family = "malgun"
    return type_style_from_mapping(
        {
            "family": family,
            "title_weight": typo.get("title_weight") or "extrabold",
            "body_weight": typo.get("body_weight") or "regular",
            "wordmark_weight": typo.get("wordmark_weight") or "bold",
            "handle_weight": typo.get("handle_weight") or "medium",
            "sub_weight": typo.get("sub_weight") or "regular",
        }
    )
