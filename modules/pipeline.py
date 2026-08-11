"""End-to-end project render.

Runs: script -> tts/captions -> whisper -> Pexels assets -> assembly (+ SFX)
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from modules.modes import ProductionMode, parse_mode
from modules.project import StrategicInput, create_project, save_json
from modules.reference import ReferenceInput
from modules.utils import ROOT_DIR, ensure_dir, get_logger, load_brand_config

logger = get_logger(__name__)

ProgressFn = Callable[[int, str], None]


def _emit(on_progress: ProgressFn | None, pct: int, message: str) -> None:
    logger.info("[progress %d%%] %s", pct, message)
    if on_progress:
        try:
            on_progress(pct, message)
        except Exception:  # noqa: BLE001
            pass


def _default_bgm() -> Path:
    """Prefer improved studio BGM; regenerate if missing/outdated tiny file."""
    studio = ROOT_DIR / "fixtures" / "studio_bgm.mp3"
    legacy = ROOT_DIR / "fixtures" / "sample_bgm.mp3"
    from fixtures.generate_fixtures import generate_bgm_audio

    if not studio.exists() or studio.stat().st_size < 50_000:
        generate_bgm_audio(studio, duration=45.0)
    if not legacy.exists() or legacy.stat().st_size < 20_000:
        generate_bgm_audio(legacy, duration=45.0)
    return studio


async def render_project(
    topic: str,
    mode: str | ProductionMode = ProductionMode.CARD_NEWS,
    selected_hook: str = "",
    research_facts: list[str] | None = None,
    reference: ReferenceInput | None = None,
    project_dir: Path | None = None,
    on_progress: ProgressFn | None = None,
    use_tts: bool = True,
    brand_color: str = "#fc4d01",
    highlight_mode: str = "color",
    draft_mode: bool = False,
    make_video: bool = False,
    ig_handle: str = "",
    type_style: dict | None = None,
    highlight_color: str = "",
    image_format: str = "",
) -> Path:
    """Produce card ZIP (card_news) or final reel mp4 (other modes)."""
    prod_mode = parse_mode(mode)
    reference = reference or ReferenceInput()
    facts = research_facts or []

    if project_dir is None:
        strategic = StrategicInput(
            topic=topic,
            production_mode=prod_mode.value,
            reference=reference,
        )
        project_dir = create_project(strategic)

    ensure_dir(project_dir)
    logger.info("Render start mode=%s dir=%s", prod_mode.value, project_dir)

    if prod_mode == ProductionMode.CARD_NEWS:
        from modules.card_pipeline import render_card_news_project

        return await render_card_news_project(
            topic=topic,
            selected_hook=selected_hook,
            research_facts=facts,
            reference=reference,
            project_dir=project_dir,
            use_tts=use_tts,
            brand_color=brand_color,
            highlight_mode=highlight_mode,
            draft_mode=draft_mode,
            make_video=make_video,
            on_progress=on_progress,
            ig_handle=ig_handle,
            type_style=type_style,
            highlight_color=highlight_color,
            image_format=image_format,
        )

    # Video modes only – keep moviepy/whisper lazy so Streamlit Cloud card-news works
    from modules.assembly import TimedText, assemble_music_caption_reel, assemble_voice_reel
    from modules.assets import fetch_assets_for_markers
    from modules.script import generate_script
    from modules.tts import run_tts_for_mode

    _emit(on_progress, 42, "Claude 대본/자막 생성 중...")
    script = await generate_script(
        topic=topic,
        research_facts=facts,
        selected_hook=selected_hook or topic,
        mode=prod_mode,
        reference=reference,
    )
    save_json(project_dir, "script.json", script.to_dict())
    _emit(on_progress, 50, f"대본 완료 (source={script.source})")

    _emit(on_progress, 55, "Pexels B-roll 다운로드 중...")
    assets_dir = project_dir / "assets"
    assets = await fetch_assets_for_markers(script.visual_markers, assets_dir, min_clips=7)
    clip_paths = [a.local_path for a in assets]
    save_json(
        project_dir,
        "assets.json",
        [{"keyword": a.keyword, "path": str(a.local_path), "source": a.source} for a in assets],
    )
    _emit(on_progress, 62, f"B-roll {len(clip_paths)}개 준비됨")

    bgm = _default_bgm()
    output = project_dir / "final_reel.mp4"

    if prod_mode == ProductionMode.MUSIC_CAPTION:
        _emit(on_progress, 70, "음악+자막 영상 조립 중 (1~3분)...")
        assemble_music_caption_reel(
            background_video=clip_paths,
            bgm_audio=bgm,
            caption_lines=script.caption_lines or [script.hook, script.cta],
            caption_durations=script.caption_durations,
            output_path=output,
            brand_config=load_brand_config(),
        )
        _emit(on_progress, 90, "조립 완료")
        return output

    _emit(on_progress, 66, "Edge-TTS 음성 생성 중...")
    voice_path = project_dir / "voice.mp3"
    await run_tts_for_mode(prod_mode, script.full_text, voice_path)

    _emit(on_progress, 72, "Whisper 자막 타임스탬프 추출 중...")
    from modules.whisper_ts import extract_and_save

    words, phrases = extract_and_save(
        voice_path,
        project_dir / "timestamps.json",
        script_text=script.full_text,
        hook=script.hook,
    )
    timed = [
        TimedText(text=p.text, start=p.start, end=p.end)
        for p in phrases
    ] or [
        TimedText(text=w.text, start=w.start, end=w.end)
        for w in words
    ]
    word_timed = [TimedText(text=w.text, start=w.start, end=w.end) for w in words]
    _emit(on_progress, 78, f"자막 {len(timed)}구간 - 영상 렌더 시작 (보통 2~5분, CPU)")

    assemble_voice_reel(
        background_video=clip_paths,
        voice_audio=voice_path,
        bgm_audio=bgm,
        timed_texts=timed,
        output_path=output,
        brand_config=load_brand_config(),
        word_timestamps=word_timed,
        cta_text=script.cta,
    )
    _emit(on_progress, 90, "영상 렌더 완료")
    return output
