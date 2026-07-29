"""High-level studio orchestration for the local frontend."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from modules.hook_scoring import score_research_hooks
from modules.modes import ProductionMode, parse_mode
from modules.pipeline import render_project
from modules.project import StrategicInput, create_project, save_json
from modules.publish import caption_from_script_json, export_manual_package
from modules.reference import ReferenceInput
from modules.research import research_topic
from modules.text_quality import enforce_korean_copy
from modules.utils import get_logger

logger = get_logger(__name__)

ProgressFn = Callable[[int, str], None]


def _emit(on_progress: ProgressFn | None, pct: int, message: str) -> None:
    logger.info("[progress %d%%] %s", pct, message)
    if on_progress:
        try:
            on_progress(pct, message)
        except Exception:  # noqa: BLE001 – UI callback must not kill pipeline
            pass


@dataclass
class StudioRunResult:
    project_dir: str
    video_path: str
    mode: str
    hook: str
    caption: str
    package_dir: str | None = None
    research_count: int = 0
    script_source: str = ""
    frames_dir: str = ""
    slides_zip: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


async def run_full_studio_pipeline(
    topic: str = "",
    mode: str | ProductionMode = ProductionMode.CARD_NEWS,
    reference: ReferenceInput | None = None,
    target_audience: str = "B2B 마케터",
    tone_override: str = "brand default",
    selected_hook: str | None = None,
    make_publish_package: bool = True,
    on_progress: ProgressFn | None = None,
    use_tts: bool = True,
    brand_color: str = "#fc4d01",
    highlight_mode: str = "color",
    draft_mode: bool = False,
    make_video: bool = False,
    category_id: str | None = None,
    custom_facts: list[str] | None = None,
    ig_handle: str = "",
) -> StudioRunResult:
    """One-shot: (optional category topic pick) → research → hook → render."""
    from modules.topic_picker import pick_topic_for_category

    topic = (topic or "").strip()
    reference = reference or ReferenceInput()
    prod_mode = parse_mode(mode)

    topic_pick_meta: dict[str, Any] | None = None
    if category_id:
        _emit(on_progress, 3, f"카테고리 '{category_id}' 안 주제 후보 수집 중...")
        pick = await pick_topic_for_category(category_id)
        topic_pick_meta = pick.to_dict()
        if topic:
            # User already chose from candidate list – keep their pick
            topic_pick_meta["selected_topic"] = topic
            topic_pick_meta["user_override"] = True
            _emit(on_progress, 8, f"주제 선택됨: {topic}")
        else:
            topic = pick.selected_topic
            _emit(
                on_progress,
                8,
                f"주제 자동 확정: {topic} (후보 {len(pick.candidates)}개 / 기사 {pick.article_count}건)",
            )

    if not topic:
        raise ValueError("topic is required (or select a category for auto-pick)")

    _emit(on_progress, 5 if not category_id else 10, "프로젝트 폴더 생성 중...")
    strategic = StrategicInput(
        topic=topic,
        target_audience=target_audience,
        tone_override=tone_override,
        production_mode=prod_mode.value,
        reference=reference,
    )
    project_dir = create_project(strategic)
    if topic_pick_meta:
        save_json(project_dir, "topic_pick.json", topic_pick_meta)

    _emit(on_progress, 12, "뉴스 RSS 리서치 중...")
    research = await research_topic(topic, reference=reference)
    save_json(project_dir, "research.json", research.to_dict())
    _emit(on_progress, 22, f"리서치 완료 - 기사 {len(research.raw_articles)}건")

    _emit(on_progress, 28, "훅 LLM 재작성 + 점수화 중...")
    scored = await score_research_hooks(topic, research.hooks, reference=reference)
    save_json(project_dir, "hooks.json", [h.to_dict() for h in scored])

    hook = selected_hook or (scored[0].hook if scored else (research.hooks[0] if research.hooks else topic))
    save_json(project_dir, "selected_hook.json", {"hook": hook})
    _emit(on_progress, 35, f"훅 확정: {hook}")

    # Merge auto research + user custom facts
    merged_facts = list(research.facts)
    if custom_facts:
        for cf in custom_facts:
            cf = cf.strip()
            if cf and cf not in merged_facts:
                merged_facts.insert(0, cf)
        _emit(on_progress, 37, f"사용자 스크랩 {len(custom_facts)}건 합산 → 총 {len(merged_facts)}건")

    _emit(on_progress, 40, f"렌더 시작 ({prod_mode.value})...")
    primary_path = await render_project(
        topic=topic,
        mode=prod_mode,
        selected_hook=hook,
        research_facts=merged_facts,
        reference=reference,
        project_dir=project_dir,
        on_progress=on_progress,
        use_tts=use_tts,
        brand_color=brand_color,
        highlight_mode=highlight_mode,
        draft_mode=draft_mode,
        make_video=make_video,
        ig_handle=ig_handle,
    )

    script_data: dict[str, Any] = {}
    script_file = project_dir / "script.json"
    if script_file.exists():
        script_data = json.loads(script_file.read_text(encoding="utf-8"))

    _emit(on_progress, 92, "캡션 정리 중...")
    caption = caption_from_script_json(script_data) if script_data else topic
    caption = enforce_korean_copy(caption)
    (project_dir / "caption.txt").write_text(caption, encoding="utf-8")

    frames_dir = project_dir / "card_frames"
    slides_zip = project_dir / "card_slides.zip"
    video_path = project_dir / "final_reel.mp4"
    if not video_path.exists():
        video_path = primary_path if primary_path.suffix.lower() == ".mp4" else Path("")

    package_dir = None
    if make_publish_package:
        _emit(on_progress, 96, "업로드 패키지 생성 중...")
        if video_path and Path(video_path).exists() and Path(video_path).suffix.lower() == ".mp4":
            package_dir = export_manual_package(Path(video_path), caption, project_dir)
        elif slides_zip.exists():
            # Image pack: zip + caption in ready_to_upload
            from datetime import datetime
            import shutil

            pkg = project_dir / f"ready_to_upload_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
            pkg.mkdir(parents=True, exist_ok=True)
            shutil.copy2(slides_zip, pkg / "card_slides.zip")
            (pkg / "caption.txt").write_text(caption, encoding="utf-8")
            if frames_dir.exists():
                for png in sorted(frames_dir.glob("slide_*.png")):
                    shutil.copy2(png, pkg / png.name)
            package_dir = pkg

    _emit(on_progress, 100, "완료!")
    return StudioRunResult(
        project_dir=str(project_dir),
        video_path=str(video_path) if video_path and Path(video_path).exists() else "",
        mode=prod_mode.value,
        hook=hook,
        caption=caption,
        package_dir=str(package_dir) if package_dir else None,
        research_count=len(research.raw_articles),
        script_source=str(script_data.get("source", "")),
        frames_dir=str(frames_dir) if frames_dir.exists() else "",
        slides_zip=str(slides_zip) if slides_zip.exists() else "",
    )
