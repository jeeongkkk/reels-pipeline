"""Authority Reels Studio - local frontend for the reels pipeline.

Run:
    streamlit run app.py
    # or double-click run_studio.bat
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

# Windows cp949: avoid UnicodeEncodeError on progress logs (en-dash etc.)
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

import streamlit as st

from modules.modes import MODE_LABELS, ProductionMode
from modules.project import list_projects
from modules.reference import FOLLOW_POINT_OPTIONS, ReferenceInput
from modules.utils import ROOT_DIR, get_settings, load_brand_config

st.set_page_config(
    page_title="Authority Reels Studio",
    page_icon="🎬",
    layout="wide",
    initial_sidebar_state="expanded",
)

settings = get_settings()
brand = load_brand_config()
ref_defaults = brand.get("reference", {})


def _mask(value: str) -> str:
    if not value or value.startswith("your_"):
        return "미설정"
    if len(value) < 8:
        return "설정됨"
    return f"{value[:4]}…{value[-4:]}"


def _api_configured(value: str) -> bool:
    v = (value or "").strip()
    return bool(v and not v.startswith("your_"))


def _status_indicator(label: str, ok: bool, *, detail: str = "") -> str:
    color = "#22c55e" if ok else "#ef4444"
    status = "연결됨" if ok else "미설정"
    sub = f' <span style="opacity:0.55;font-size:0.82em;">({detail})</span>' if detail else ""
    return (
        f'<div style="display:flex;align-items:center;gap:0.45rem;margin:0.15rem 0;">'
        f'<span style="color:{color};font-size:0.95rem;line-height:1;">●</span>'
        f'<span><b>{label}</b> {status}{sub}</span>'
        f"</div>"
    )


def _render_api_status_sidebar() -> None:
    st.sidebar.markdown("**API 상태**")
    rows = [
        ("Gemini", _api_configured(settings.gemini_api_key), _mask(settings.gemini_api_key)),
        ("OpenAI", _api_configured(settings.openai_api_key), _mask(settings.openai_api_key)),
        ("Tavily", _api_configured(settings.tavily_api_key), _mask(settings.tavily_api_key)),
        ("Fal.ai", _api_configured(settings.fal_key), _mask(settings.fal_key)),
        ("Pexels", _api_configured(settings.pexels_api_key), _mask(settings.pexels_api_key)),
    ]
    html = "".join(_status_indicator(name, ok, detail=mask) for name, ok, mask in rows)
    st.sidebar.markdown(html, unsafe_allow_html=True)
    if _api_configured(settings.tavily_api_key):
        ddg = "OFF" if settings.tavily_skip_ddg_fallback else "ON"
        st.sidebar.caption(f"Tavily days={settings.tavily_days} · DDG fallback {ddg}")


def _load_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def _project_card_data(project: Path) -> dict:
    strategic = _load_json(project / "strategic_input.json")
    script = _load_json(project / "script.json")
    video = project / "final_reel.mp4"
    frames = sorted((project / "card_frames").glob("slide_*.png"))
    slides_zip = project / "card_slides.zip"
    return {
        "name": project.name,
        "path": project,
        "topic": strategic.get("topic", project.name),
        "mode": strategic.get("production_mode") or script.get("mode", "-"),
        "hook": script.get("hook", ""),
        "has_video": video.exists(),
        "video": video,
        "frames": frames,
        "slides_zip": slides_zip if slides_zip.exists() else None,
        "size_mb": round(video.stat().st_size / (1024 * 1024), 2) if video.exists() else 0,
    }


def _show_card_gallery(
    frames: list[Path],
    slides_zip: Path | None = None,
    *,
    key: str = "preview",
) -> None:
    """Flip-through card viewer — one large slide, drag or tap to browse."""
    if not frames:
        return

    n = len(frames)
    slider_key = f"{key}_slider"
    sig_key = f"{key}_sig"
    sig = "|".join(str(p) for p in frames)

    # New image pack → jump to first slide
    if st.session_state.get(sig_key) != sig:
        st.session_state[sig_key] = sig
        st.session_state[slider_key] = 1

    if slider_key not in st.session_state:
        st.session_state[slider_key] = 1

    # Keep in range if pack length changed
    st.session_state[slider_key] = max(1, min(int(st.session_state[slider_key]), n))

    st.markdown(f"**레티나 카드 {n}장** · 2160×3840")

    nav_l, nav_c, nav_r = st.columns([1, 1.2, 1])
    with nav_l:
        if st.button(
            "← 이전",
            key=f"{key}_prev",
            use_container_width=True,
            disabled=st.session_state[slider_key] <= 1,
        ):
            st.session_state[slider_key] = st.session_state[slider_key] - 1
            st.rerun()
    with nav_c:
        st.markdown(
            f"<div style='text-align:center;padding-top:0.45rem;font-weight:700;font-size:1.05rem;'>"
            f"{st.session_state[slider_key]} / {n}</div>",
            unsafe_allow_html=True,
        )
    with nav_r:
        if st.button(
            "다음 →",
            key=f"{key}_next",
            use_container_width=True,
            disabled=st.session_state[slider_key] >= n,
        ):
            st.session_state[slider_key] = st.session_state[slider_key] + 1
            st.rerun()

    st.slider(
        "슬라이드 넘기기",
        min_value=1,
        max_value=n,
        key=slider_key,
        label_visibility="collapsed",
        help="좌우로 드래그해서 카드를 쭉 넘겨보세요",
    )

    idx = int(st.session_state[slider_key]) - 1
    st.image(str(frames[idx]), use_container_width=True)
    st.caption(frames[idx].name)

    # Quick jump dots
    jump_cols = st.columns(n)
    for i in range(n):
        with jump_cols[i]:
            active = i == idx
            if st.button(
                f"{'●' if active else '○'}{i + 1}",
                key=f"{key}_dot_{i}",
                use_container_width=True,
            ):
                st.session_state[slider_key] = i + 1
                st.rerun()

    if slides_zip and slides_zip.exists():
        st.download_button(
            label="초고화질 카드뉴스 ZIP 다운로드",
            data=slides_zip.read_bytes(),
            file_name="card_slides_2160x3840.zip",
            mime="application/zip",
            type="primary",
            use_container_width=True,
            help="인스타 업로드용 레티나 PNG 패키지",
        )
        st.caption(f"ZIP 크기: {slides_zip.stat().st_size / (1024 * 1024):.1f} MB")


# ── Sidebar nav ──────────────────────────────────────────────
st.sidebar.title("Authority Reels")
st.sidebar.caption(brand.get("brand", {}).get("tagline", "Local Studio"))
page = st.sidebar.radio(
    "메뉴",
    ["🎬 새 제작", "📁 프로젝트", "📈 Analytics", "⚙️ 설정"],
    label_visibility="collapsed",
)

st.sidebar.divider()
st.sidebar.markdown(f"**Publish:** `{settings.publish_mode}`")
_render_api_status_sidebar()

# ── Page: Create ─────────────────────────────────────────────
if page == "🎬 새 제작":
    st.title("새 카드뉴스 제작")
    st.caption("카테고리 선택 → 그 안에서 주제 자동 선정 → 레티나 PNG + ZIP")

    from modules.topic_picker import list_categories

    categories = list_categories()
    cat_labels = [c.label for c in categories]
    cat_by_label = {c.label: c for c in categories}

    col_form, col_preview = st.columns([1.1, 1])

    with col_form:
        category_label = st.selectbox(
            "큰 카테고리 *",
            cat_labels,
            index=0,
            help="선택한 카테고리 안에서 최신 뉴스를 모아 주제를 자동 선정합니다.",
        )
        category = cat_by_label[category_label]
        st.caption(category.description)

        topic_mode = st.radio(
            "주제 방식",
            ["카테고리 안 자동 선정", "직접 입력"],
            horizontal=True,
            index=0,
        )

        manual_topic = ""
        selected_auto_topic = ""
        if topic_mode == "직접 입력":
            manual_topic = st.text_input(
                "주제 *",
                placeholder="예: 2026년 탄소중립 지원사업 2500억",
            )
        else:
            st.info(
                f"**{category.label}** 뉴스를 스캔해 후보를 보여 줍니다. "
                "목록에서 고른 뒤 제작하세요. (안 고르면 1위 자동)"
            )

            # Reset pick when category changes
            if st.session_state.get("topic_preview_cat") != category.id:
                st.session_state.pop("topic_preview", None)
                st.session_state.pop("topic_choice", None)
                st.session_state["topic_preview_cat"] = category.id

            preview_btn = st.button("주제 후보 불러오기", use_container_width=True)
            if preview_btn:
                with st.spinner("카테고리 뉴스 스캔 중..."):
                    import sys

                    if sys.platform == "win32":
                        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
                    from modules.topic_picker import pick_topic_for_category

                    pick = asyncio.run(pick_topic_for_category(category.id, top_n=8))
                    st.session_state["topic_preview"] = pick.to_dict()
                    st.session_state["topic_choice"] = pick.selected_topic

            preview = st.session_state.get("topic_preview")
            if preview and preview.get("category_id") == category.id:
                candidates = preview.get("candidates") or []
                options = [str(c.get("topic") or "").strip() for c in candidates if c.get("topic")]
                if options:
                    default_idx = 0
                    prev_choice = st.session_state.get("topic_choice")
                    if prev_choice in options:
                        default_idx = options.index(prev_choice)

                    selected_auto_topic = st.radio(
                        "후보에서 주제 선택",
                        options,
                        index=default_idx,
                        key="topic_choice_radio",
                    )
                    st.session_state["topic_choice"] = selected_auto_topic

                    # Show source under each option via captions for the selected one
                    chosen = next(
                        (c for c in candidates if c.get("topic") == selected_auto_topic),
                        None,
                    )
                    if chosen:
                        st.caption(
                            f"점수 `{chosen.get('score')}` · 출처: {chosen.get('source_title') or '-'}"
                        )
                    with st.expander("전체 후보 · 출처", expanded=False):
                        for i, c in enumerate(candidates, 1):
                            mark = "← 선택됨" if c.get("topic") == selected_auto_topic else ""
                            st.markdown(
                                f"{i}. **{c.get('topic')}** `{c.get('score')}` {mark}  \n"
                                f"<span style='opacity:0.65;font-size:0.85em;'>"
                                f"{c.get('source_title')}</span>",
                                unsafe_allow_html=True,
                            )
                else:
                    st.warning("후보가 없습니다. 다시 불러오거나 직접 입력으로 전환하세요.")
            else:
                st.caption("아직 후보가 없습니다. 「주제 후보 불러오기」를 눌러 주세요.")

        audience = st.selectbox(
            "타겟",
            ["B2B 마케터", "스타트업 창업자", "마케팅 대행사", "일반"],
        )
        tone = st.selectbox("톤", ["brand default", "casual", "semi-formal", "formal"])
        mode = st.radio(
            "제작 모드",
            list(ProductionMode),
            format_func=lambda m: MODE_LABELS.get(m, m.value),
            horizontal=True,
            index=0,
        )

        use_tts = False
        brand_color = "#fc4d01"
        highlight_mode = "color"
        make_video = False
        draft_mode = False
        if mode == ProductionMode.CARD_NEWS:
            st.markdown("**카드뉴스 옵션**")
            brand_color = st.color_picker(
                "브랜드 포인트 컬러 (표지·소제목 박스)",
                value="#fc4d01",
            )
            highlight_mode = "box"
            st.caption(
                "표지 IG 고정: @with.choyool · 본문: #fafafa + WITHCHOYOOL 로고 · "
                "포인트 컬러 기본 #fc4d01"
            )
        else:
            use_tts = True
        with st.expander("📋 스크랩 직접 입력 (선택)", expanded=False):
            custom_scrap = st.text_area(
                "내용 붙여넣기 (한 줄에 하나의 팩트·기사 요약·URL)",
                placeholder=(
                    "예:\n"
                    "중소기업 기술보호 바우처 최대 5000만원 지원\n"
                    "신청기간 2026.8.1~8.31\n"
                    "https://www.mss.go.kr/site/smba/ex/..."
                ),
                height=120,
                key="custom_scrap_input",
            )
            st.caption(
                "여기에 넣은 내용이 자동 웹 스크랩보다 **우선** 반영됩니다. "
                "기사 본문, 보도자료 요약, URL 모두 가능."
            )

        with st.expander("레퍼런스 (선택)", expanded=False):
            ref_urls = st.text_area(
                "URL (한 줄에 하나)",
                placeholder="https://www.instagram.com/reel/...",
                height=70,
            )
            ref_notes = st.text_area(
                "스타일 메모",
                placeholder=ref_defaults.get(
                    "style_notes_placeholder",
                    "문장 단위 자막, 하단, 첫 1초 강한 훅",
                ),
                height=80,
            )
            ref_follow = st.multiselect(
                "따라갈 포인트",
                options=list(FOLLOW_POINT_OPTIONS),
                default=ref_defaults.get("follow_points_default", ["hook", "caption", "tone"]),
            )

        make_pkg = st.checkbox("업로드 패키지도 만들기 (PNG + ZIP + caption.txt)", value=True)
        st.caption("카테고리 스캔 + 카드뉴스 6~8장 · 보통 1~3분")
        can_run = bool(manual_topic.strip()) if topic_mode == "직접 입력" else True
        run_btn = st.button(
            "카드뉴스 제작 실행",
            type="primary",
            use_container_width=True,
            disabled=not can_run,
        )

        if run_btn:
            reference = ReferenceInput(
                urls=[u.strip() for u in ref_urls.splitlines() if u.strip()],
                style_notes=ref_notes.strip(),
                follow_points=ref_follow or ["hook", "caption", "tone"],
            )
            progress = st.progress(0, text="준비 중...")
            status = st.empty()
            log_box = st.empty()
            logs: list[str] = []

            def on_progress(pct: int, message: str) -> None:
                logs.append(f"[{pct:3d}%] {message}")
                progress.progress(min(max(pct, 0), 100), text=message)
                status.info(message)
                log_box.code("\n".join(logs[-12:]), language=None)

            try:
                from modules.studio import run_full_studio_pipeline
                import sys

                if sys.platform == "win32":
                    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

                use_auto = topic_mode == "카테고리 안 자동 선정"
                run_topic = ""
                if use_auto:
                    run_topic = (
                        selected_auto_topic
                        or st.session_state.get("topic_choice")
                        or ""
                    ).strip()
                else:
                    run_topic = manual_topic.strip()

                # Parse user custom scraps
                user_facts: list[str] = []
                raw_scrap = (st.session_state.get("custom_scrap_input") or "").strip()
                if raw_scrap:
                    user_facts = [
                        ln.strip()
                        for ln in raw_scrap.splitlines()
                        if ln.strip()
                    ]

                result = asyncio.run(
                    run_full_studio_pipeline(
                        topic=run_topic,
                        mode=mode,
                        reference=reference,
                        target_audience=audience,
                        tone_override=tone,
                        make_publish_package=make_pkg,
                        on_progress=on_progress,
                        use_tts=use_tts,
                        brand_color=brand_color,
                        highlight_mode=highlight_mode,
                        draft_mode=draft_mode,
                        make_video=make_video,
                        category_id=category.id if use_auto else None,
                        custom_facts=user_facts or None,
                        ig_handle="",
                    )
                )
                progress.progress(100, text="완료")
                status.success("제작 완료 - 아래에서 ZIP을 다운로드하세요")
                st.session_state["last_result"] = result.to_dict()
                st.session_state["selected_project"] = Path(result.project_dir).name
            except Exception as exc:  # noqa: BLE001
                progress.progress(100, text="실패")
                err = f"{type(exc).__name__}: {exc}" if str(exc) else type(exc).__name__
                status.error(f"제작 실패: {err}")
                log_box.code("\n".join(logs[-20:] + [f"ERROR: {err}"]), language=None)

    with col_preview:
        st.subheader("결과 미리보기")
        last = st.session_state.get("last_result")

        if not last:
            recent = list_projects(3)
            for p in recent:
                frames = sorted((p / "card_frames").glob("slide_*.png"))
                video = p / "final_reel.mp4"
                if frames or video.exists():
                    script = _load_json(p / "script.json")
                    zip_p = p / "card_slides.zip"
                    last = {
                        "project_dir": str(p),
                        "video_path": str(video) if video.exists() else "",
                        "frames_dir": str(p / "card_frames") if frames else "",
                        "slides_zip": str(zip_p) if zip_p.exists() else "",
                        "hook": script.get("hook", ""),
                        "mode": script.get("mode", ""),
                        "script_source": script.get("source", ""),
                        "caption": (p / "caption.txt").read_text(encoding="utf-8")
                        if (p / "caption.txt").exists()
                        else "",
                        "package_dir": str(
                            next(iter(sorted(p.glob("ready_to_upload_*"), reverse=True)), "")
                        ),
                    }
                    st.session_state["last_result"] = last
                    break

        if last:
            if last.get("frames_dir"):
                frames_dir = Path(last["frames_dir"])
            else:
                frames_dir = Path(last["project_dir"]) / "card_frames"
            frames = sorted(frames_dir.glob("slide_*.png")) if frames_dir.exists() else []
            zip_path = Path(last["slides_zip"]) if last.get("slides_zip") else frames_dir.parent / "card_slides.zip"

            if frames:
                _show_card_gallery(
                    frames,
                    zip_path if zip_path.exists() else None,
                    key="create_preview",
                )
            elif last.get("video_path") and Path(last["video_path"]).exists():
                st.video(last["video_path"])

            st.markdown(f"**훅:** {last.get('hook', '')}")
            st.markdown(f"**모드:** `{last.get('mode')}` · source=`{last.get('script_source')}`")
            st.markdown(f"**프로젝트:** `{Path(last['project_dir']).name}`")
            with st.expander("캡션"):
                st.text(last.get("caption", ""))
            if last.get("package_dir"):
                st.info(f"업로드 패키지: `{last['package_dir']}`")
            if last.get("video_path") and Path(last["video_path"]).exists():
                st.download_button(
                    "영상 다운로드",
                    data=Path(last["video_path"]).read_bytes(),
                    file_name="final_reel.mp4",
                    mime="video/mp4",
                    use_container_width=True,
                )
        else:
            st.info("왼쪽에서 제작을 실행하면 여기에 카드 PNG가 표시됩니다.")

# ── Page: Projects ───────────────────────────────────────────
elif page == "📁 프로젝트":
    st.title("프로젝트")
    projects = list_projects(30)
    if not projects:
        st.warning("아직 프로젝트가 없습니다. '새 제작'에서 만들어 보세요.")
    else:
        cards = [_project_card_data(p) for p in projects]
        names = [c["name"] for c in cards]
        default_idx = 0
        if st.session_state.get("selected_project") in names:
            default_idx = names.index(st.session_state["selected_project"])

        selected = st.selectbox("프로젝트 선택", names, index=default_idx)
        card = next(c for c in cards if c["name"] == selected)
        st.session_state["selected_project"] = selected

        left, right = st.columns([1.2, 1])
        with left:
            if card["frames"]:
                _show_card_gallery(
                    card["frames"],
                    card.get("slides_zip"),
                    key=f"proj_{selected}",
                )
            if card["has_video"]:
                st.video(str(card["video"]))
                st.caption(f"{card['size_mb']} MB")
                st.download_button(
                    "영상 다운로드",
                    data=card["video"].read_bytes(),
                    file_name=f"{selected}_final_reel.mp4",
                    mime="video/mp4",
                )
            elif not card["frames"]:
                st.warning("카드 PNG / final_reel.mp4 없음")

        with right:
            st.markdown(f"**주제:** {card['topic']}")
            st.markdown(f"**모드:** `{card['mode']}`")
            st.markdown(f"**훅:** {card['hook'] or '-'}")

            script = _load_json(card["path"] / "script.json")
            research = _load_json(card["path"] / "research.json")
            web_facts = _load_json(card["path"] / "web_facts.json")
            if script:
                with st.expander("대본 / 자막", expanded=True):
                    st.caption(f"source=`{script.get('source', '-')}`")
                    if script.get("caption_lines"):
                        for line in script["caption_lines"]:
                            st.write(f"• {line}")
                    else:
                        st.write(script.get("full_text") or script.get("body") or "")
                    st.caption(f"CTA: {script.get('cta', '')}")

            if web_facts.get("facts"):
                with st.expander("웹 검색 팩트 (LLM 컨텍스트)", expanded=True):
                    st.caption(
                        f"provider=`{web_facts.get('provider', '-')}` · "
                        f"days={web_facts.get('days', '-')} · "
                        f"{web_facts.get('query_used', '')}"
                    )
                    for i, fact in enumerate(web_facts["facts"][:5], 1):
                        line = fact.get("title") or ""
                        snip = fact.get("snippet") or ""
                        st.write(f"{i}. {line}" + (f" — {snip[:160]}" if snip else ""))

            if research.get("facts"):
                with st.expander("RSS 리서치 팩트", expanded=False):
                    st.caption(research.get("query_used") or "")
                    for fact in research["facts"][:8]:
                        st.write(f"• {fact}")

            caption_file = card["path"] / "caption.txt"
            if caption_file.exists():
                with st.expander("인스타 캡션"):
                    st.text(caption_file.read_text(encoding="utf-8"))

            packages = sorted(card["path"].glob("ready_to_upload_*"), reverse=True)
            if packages:
                st.success(f"업로드 패키지: `{packages[0].name}`")

            # Quick re-package
            if card["has_video"] and st.button("업로드 패키지 다시 만들기", use_container_width=True):
                from modules.publish import caption_from_script_json, export_manual_package

                script_data = _load_json(card["path"] / "script.json")
                caption = (
                    caption_from_script_json(script_data)
                    if script_data
                    else (caption_file.read_text(encoding="utf-8") if caption_file.exists() else card["topic"])
                )
                pkg = export_manual_package(card["video"], caption, card["path"])
                st.success(f"생성됨: {pkg}")

# ── Page: Analytics ──────────────────────────────────────────
elif page == "📈 Analytics":
    st.title("Analytics")
    st.caption("인스타 인사이트를 입력하면 훅 점수 가중치가 학습됩니다.")

    from modules.analytics import (
        ReelMetrics,
        list_metrics,
        load_learned_weights,
        record_and_learn,
        summarize_metrics,
    )

    projects = list_projects(20)
    names = [p.name for p in projects] or ["(없음)"]
    selected = st.selectbox("프로젝트", names)

    script = _load_json(ROOT_DIR / "projects" / selected / "script.json") if selected != "(없음)" else {}
    strategic = _load_json(ROOT_DIR / "projects" / selected / "strategic_input.json") if selected != "(없음)" else {}

    c1, c2, c3, c4 = st.columns(4)
    views = c1.number_input("조회수", min_value=0, value=0, step=100)
    likes = c2.number_input("좋아요", min_value=0, value=0, step=10)
    saves = c3.number_input("저장", min_value=0, value=0, step=5)
    shares = c4.number_input("공유", min_value=0, value=0, step=5)
    c5, c6, c7 = st.columns(3)
    comments = c5.number_input("댓글", min_value=0, value=0, step=1)
    follows = c6.number_input("팔로우", min_value=0, value=0, step=1)
    watch = c7.number_input("평균 시청(초)", min_value=0.0, value=0.0, step=0.5)

    hook = st.text_input("훅", value=script.get("hook", ""))
    mode_val = st.text_input("모드", value=script.get("mode") or strategic.get("production_mode", "voice_tts"))

    if st.button("성과 저장 + 학습", type="primary", disabled=selected == "(없음)"):
        metrics = ReelMetrics(
            project_id=selected,
            views=int(views),
            likes=int(likes),
            saves=int(saves),
            shares=int(shares),
            comments=int(comments),
            follows=int(follows),
            avg_watch_time=float(watch),
            hook=hook,
            mode=mode_val,
            topic=strategic.get("topic", ""),
        )
        result = asyncio.run(record_and_learn(metrics))
        st.success(f"Engagement: {metrics.engagement_score()}")
        st.json(result.get("weights"))

    st.subheader("요약")
    st.json(summarize_metrics())
    st.subheader("현재 가중치")
    st.json(load_learned_weights())
    rows = list_metrics(15)
    if rows:
        st.dataframe([r.to_dict() for r in rows], use_container_width=True)

# ── Page: Settings ───────────────────────────────────────────
else:
    st.title("설정")
    st.caption("API 키는 `.env`에서 관리합니다. 값은 마스킹되어 표시됩니다.")

    st.subheader("연결 상태")
    core_html = "".join(
        _status_indicator(name, ok, detail=_mask(key))
        for name, ok, key in [
            ("Gemini", _api_configured(settings.gemini_api_key), settings.gemini_api_key),
            ("OpenAI", _api_configured(settings.openai_api_key), settings.openai_api_key),
            ("Tavily", _api_configured(settings.tavily_api_key), settings.tavily_api_key),
            ("Fal.ai", _api_configured(settings.fal_key), settings.fal_key),
            ("Pexels", _api_configured(settings.pexels_api_key), settings.pexels_api_key),
        ]
    )
    st.markdown(core_html, unsafe_allow_html=True)
    st.write(
        {
            "GEMINI_MODEL": settings.gemini_model,
            "OPENAI_PLANNER_MODEL": settings.openai_planner_model,
            "TAVILY_DAYS": settings.tavily_days,
            "TAVILY_SKIP_DDG_FALLBACK": settings.tavily_skip_ddg_fallback,
            "GOOGLE_CSE": _mask(settings.google_cse_api_key),
            "OPENAI": _mask(settings.openai_api_key),
            "PEXELS": _mask(settings.pexels_api_key),
            "PUBLISH_MODE": settings.publish_mode,
            "TTS_VOICE": settings.tts_voice,
            "WHISPER_MODEL": settings.whisper_model,
        }
    )

    st.subheader("브랜드")
    st.json(brand.get("brand", {}))
    st.subheader("제작 모드")
    st.json(brand.get("production", {}))

    st.info(
        "키 수정: `reels-pipeline/.env` 파일을 메모장으로 열고 저장한 뒤, "
        "이 페이지를 새로고침하세요."
    )
