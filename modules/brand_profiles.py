"""Multi-brand profile registry for the studio.

Active profile drives brand.yaml path, topic categories, planner tone,
and card-news outro / IG handle.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from modules.utils import ROOT_DIR

_ACTIVE_BRAND_ID = "withchoyool"


@dataclass(frozen=True)
class BrandProfile:
    id: str
    label: str
    config_path: Path
    description: str = ""


PROFILES: tuple[BrandProfile, ...] = (
    BrandProfile(
        id="withchoyool",
        label="WITHCHOYOOL (비즈니스)",
        config_path=ROOT_DIR / "config" / "brand.yaml",
        description="B2B 마케팅·정부지원 인사이트 카드뉴스/릴스",
    ),
    BrandProfile(
        id="bebeskin",
        label="BebeSkin (아기 피부)",
        config_path=ROOT_DIR / "config" / "brand_bebeskin.yaml",
        description="아기 피부 관찰·보습·진료 보조 콘텐츠",
    ),
)


def list_brand_profiles() -> list[BrandProfile]:
    return list(PROFILES)


def get_brand_profile(profile_id: str | None = None) -> BrandProfile:
    pid = (profile_id or _ACTIVE_BRAND_ID or "withchoyool").strip().lower()
    for p in PROFILES:
        if p.id == pid:
            return p
    return PROFILES[0]


def get_active_brand_id() -> str:
    return _ACTIVE_BRAND_ID


def set_active_brand(profile_id: str) -> BrandProfile:
    global _ACTIVE_BRAND_ID
    profile = get_brand_profile(profile_id)
    _ACTIVE_BRAND_ID = profile.id
    return profile


def brand_config_path(profile_id: str | None = None) -> Path:
    return get_brand_profile(profile_id).config_path


def content_mode_from_brand(brand: dict[str, Any] | None = None) -> str:
    """Return planner/content mode: b2b_insight | parenting_care."""
    if brand is None:
        from modules.utils import load_brand_config

        brand = load_brand_config()
    mode = str((brand.get("profile") or {}).get("content_mode") or "").strip()
    if mode:
        return mode
    # Fallback by brand name
    name = str((brand.get("brand") or {}).get("name") or "").lower()
    if "bebe" in name or "베비" in name:
        return "parenting_care"
    return "b2b_insight"
