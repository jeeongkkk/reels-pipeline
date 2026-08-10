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
from modules.brand_profiles import (
    get_active_brand_id,
    list_brand_profiles,
    set_active_brand,
)

st.sidebar.title("Authority Reels")
profiles = list_brand_profiles()
profile_labels = {p.id: p.label for p in profiles}
current_id = st.session_state.get("brand_profile_id") or get_active_brand_id()
if current_id not in profile_labels:
    current_id = "withchoyool"
selected_profile_id = st.sidebar.selectbox(
    "브랜드 프로필",
    options=list(profile_labels.keys()),
    format_func=lambda i: profile_labels[i],
    index=list(profile_labels.keys()).index(current_id),
    help="WITHCHOYOOL 비즈니스 / BebeSkin 아기 피부 콘텐츠",
)
if selected_profile_id != st.session_state.get("brand_profile_id"):
    st.session_state["brand_profile_id"] = selected_profile_id
    # Reset topic preview when brand switches
    for k in ("topic_preview", "topic_choice", "topic_preview_cat", "topic_choice_radio"):
        st.session_state.pop(k, None)
set_active_brand(selected_profile_id)
brand = load_brand_config()  # reload after profile switch
ref_defaults = brand.get("reference", {})
cn_defaults = brand.get("card_news") or {}
active_brand_name = (
    cn_defaults.get("brand_name")
    or (brand.get("brand") or {}).get("name")
    or "WITHCHOYOOL"
)
active_brand_color = str(cn_defaults.get("brand_color") or "#fc4d01")
active_ig = str(cn_defaults.get("ig_handle") or "")
st.sidebar.caption(
    (brand.get("brand") or {}).get("tagline")
    or brand.get("brand", {}).get("tagline", "Local Studio")
)

page = st.sidebar.radio(
    "메뉴",
    ["🎬 새 제작", "🎥 새 릴스 제작", "📁 프로젝트", "📈 Analytics", "⚙️ 설정"],
    label_visibility="collapsed",
)

st.sidebar.divider()
st.sidebar.markdown(f"**Publish:** `{settings.publish_mode}`")
st.sidebar.caption(f"활성 브랜드: **{active_brand_name}**")
_render_api_status_sidebar()

# ── Page: Create ─────────────────────────────────────────────
if page == "🎬 새 제작":
    st.title("새 카드뉴스 제작")
    is_bebe = selected_profile_id == "bebeskin"
    if is_bebe:
        st.caption(
            f"{active_brand_name} · 아기 피부 카테고리 자동 스크랩 → 레티나 PNG + ZIP "
            "(진단·처방 단정 없이 관찰·보습·기록 톤)"
        )
    else:
        st.caption("카테고리 선택 → 그 안에서 주제 자동 선정 → 레티나 PNG + ZIP")

    from modules.topic_picker import list_categories

    categories = list_categories(selected_profile_id)
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

        audience_opts = (
            ["영유아 보호자", "육아 초보 부모", "소아과 방문 전 보호자", "일반"]
            if is_bebe
            else ["B2B 마케터", "스타트업 창업자", "마케팅 대행사", "일반"]
        )
        audience = st.selectbox(
            "타겟",
            audience_opts,
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
        brand_color = active_brand_color
        body_highlight_color = active_brand_color
        highlight_mode = "color"
        make_video = False
        draft_mode = False
        type_style = None
        if mode == ProductionMode.CARD_NEWS:
            st.markdown("**카드뉴스 옵션**")
            from modules.brand_colors import (
                default_box_color,
                default_highlight_color,
                palettes_for_brand,
            )
            from modules.typography import (
                FONT_FAMILIES,
                FONT_WEIGHTS,
                default_type_style_for_brand,
            )

            # Reset colors when brand profile changes
            palette_brand_key = f"palette_for_{selected_profile_id}"
            if st.session_state.get("palette_brand_key") != palette_brand_key:
                st.session_state["palette_brand_key"] = palette_brand_key
                st.session_state["card_box_color"] = default_box_color(brand)
                st.session_state["card_highlight_color"] = default_highlight_color(brand)
                st.session_state["card_palette_id"] = ""

            if "card_box_color" not in st.session_state:
                st.session_state["card_box_color"] = default_box_color(brand)
            if "card_highlight_color" not in st.session_state:
                st.session_state["card_highlight_color"] = default_highlight_color(brand)

            st.markdown("**추천 팔레트** (탭하면 표지 박스 / 본문 강조에 바로 적용)")
            swatches = palettes_for_brand(brand)
            chip_cols = st.columns(min(5, max(1, len(swatches))))
            for i, sw in enumerate(swatches):
                with chip_cols[i % len(chip_cols)]:
                    active = st.session_state.get("card_palette_id") == sw.id
                    label = f"{'● ' if active else ''}{sw.label}"
                    if st.button(
                        label,
                        key=f"palette_chip_{selected_profile_id}_{sw.id}",
                        use_container_width=True,
                        help=f"박스 {sw.box} · 강조 {sw.highlight}",
                    ):
                        st.session_state["card_box_color"] = sw.box
                        st.session_state["card_highlight_color"] = sw.highlight
                        st.session_state["card_palette_id"] = sw.id
                        st.rerun()
                    st.markdown(
                        f'<div style="display:flex;gap:4px;margin:-0.35rem 0 0.6rem 0;">'
                        f'<div style="flex:1;height:8px;border-radius:4px;background:{sw.box};"></div>'
                        f'<div style="flex:1;height:8px;border-radius:4px;background:{sw.highlight};"></div>'
                        f"</div>",
                        unsafe_allow_html=True,
                    )

            c_box, c_hi = st.columns(2)
            with c_box:
                brand_color = st.color_picker(
                    "표지·소제목 박스 색",
                    key="card_box_color",
                )
            with c_hi:
                body_highlight_color = st.color_picker(
                    "본문 강조(*별표*) 색",
                    key="card_highlight_color",
                )
            matched = next(
                (
                    s
                    for s in swatches
                    if s.box.upper() == str(brand_color).upper()
                    and s.highlight.upper() == str(body_highlight_color).upper()
                ),
                None,
            )
            st.session_state["card_palette_id"] = matched.id if matched else ""

            highlight_mode = "box"
            typo_default = default_type_style_for_brand(brand)
            fam_keys = list(FONT_FAMILIES.keys())
            weight_keys = list(FONT_WEIGHTS.keys())
            c_font, c_title, c_body = st.columns(3)
            with c_font:
                font_family = st.selectbox(
                    "글씨체",
                    fam_keys,
                    index=fam_keys.index(typo_default.family)
                    if typo_default.family in fam_keys
                    else 0,
                    format_func=lambda k: FONT_FAMILIES[k],
                    key="card_font_family",
                )
            with c_title:
                title_weight = st.selectbox(
                    "제목 굵기 (표지·소제목·아웃트로)",
                    weight_keys,
                    index=weight_keys.index(typo_default.title_weight)
                    if typo_default.title_weight in weight_keys
                    else weight_keys.index("extrabold"),
                    format_func=lambda k: FONT_WEIGHTS[k],
                    key="card_title_weight",
                )
            with c_body:
                body_weight = st.selectbox(
                    "본문 굵기",
                    weight_keys,
                    index=weight_keys.index(typo_default.body_weight)
                    if typo_default.body_weight in weight_keys
                    else weight_keys.index("regular"),
                    format_func=lambda k: FONT_WEIGHTS[k],
                    key="card_body_weight",
                )
            with st.expander("타이포 상세 (워드마크·핸들·서브)", expanded=False):
                w1, w2, w3 = st.columns(3)
                with w1:
                    wordmark_weight = st.selectbox(
                        "워드마크 굵기",
                        weight_keys,
                        index=weight_keys.index(typo_default.wordmark_weight)
                        if typo_default.wordmark_weight in weight_keys
                        else weight_keys.index("bold"),
                        format_func=lambda k: FONT_WEIGHTS[k],
                        key="card_wordmark_weight",
                    )
                with w2:
                    handle_weight = st.selectbox(
                        "IG 핸들 굵기",
                        weight_keys,
                        index=weight_keys.index(typo_default.handle_weight)
                        if typo_default.handle_weight in weight_keys
                        else weight_keys.index("medium"),
                        format_func=lambda k: FONT_WEIGHTS[k],
                        key="card_handle_weight",
                    )
                with w3:
                    sub_weight = st.selectbox(
                        "서브/출처 굵기",
                        weight_keys,
                        index=weight_keys.index(typo_default.sub_weight)
                        if typo_default.sub_weight in weight_keys
                        else weight_keys.index("regular"),
                        format_func=lambda k: FONT_WEIGHTS[k],
                        key="card_sub_weight",
                    )
            type_style = {
                "family": font_family,
                "title_weight": title_weight,
                "body_weight": body_weight,
                "wordmark_weight": wordmark_weight,
                "handle_weight": handle_weight,
                "sub_weight": sub_weight,
            }
            st.caption(
                f"표지 IG: {active_ig or '@…'} · 워드마크: {active_brand_name} · "
                f"박스 {brand_color} · 본문강조 {body_highlight_color} · "
                f"{FONT_FAMILIES.get(font_family, font_family)} / "
                f"제목 {FONT_WEIGHTS.get(title_weight, title_weight)} · "
                f"본문 {FONT_WEIGHTS.get(body_weight, body_weight)}"
            )
        else:
            use_tts = True
            type_style = None
            body_highlight_color = ""
        with st.expander("📋 스크랩 직접 입력 (선택)", expanded=False):
            custom_scrap = st.text_area(
                "내용 붙여넣기 (한 줄에 하나의 팩트·기사 요약·URL)",
                placeholder=(
                    "예:\n"
                    "목욕 후 3분 이내 보습이 피부장벽에 도움\n"
                    "동일 구도 사진으로 경과 비교\n"
                    "https://…"
                    if is_bebe
                    else (
                        "예:\n"
                        "중소기업 기술보호 바우처 최대 5000만원 지원\n"
                        "신청기간 2026.8.1~8.31\n"
                        "https://www.mss.go.kr/site/smba/ex/..."
                    )
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
                        ig_handle=active_ig,
                        type_style=type_style,
                        highlight_color=body_highlight_color or brand_color,
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

# ── Page: New Reels (independent from card-news) ─────────────
elif page == "🎥 새 릴스 제작":
    st.title("새 릴스 제작")
    st.caption(
        f"{active_brand_name} · 텍스트 꿀팁 릴스 · 포트폴리오 쇼케이스 · 1080×1920 MP4"
    )

    reel_kind = st.radio(
        "릴스 유형",
        ["정보성 텍스트 릴스", "포트폴리오 쇼케이스"],
        horizontal=True,
    )

    run_tips = False
    run_folio = False
    tip_title = tip1 = tip2 = tip3 = tip4 = ""
    bg_hex = "#111111"
    tip_interval = 1.5
    duration_sec = 12.0
    reel_font_family = "pretendard"
    reel_title_weight = "bold"
    reel_body_weight = "semibold"
    folio_title = "portfolio"
    folio_images = None
    per_image = 3.6
    crossfade = 0.85
    bgm_file = None

    col_form, col_preview = st.columns([1.1, 1])

    with col_form:
        if reel_kind == "정보성 텍스트 릴스":
            tip_title = st.text_input(
                "제목 *",
                placeholder="예: B2B 마케터가 놓치는 3가지",
                key="reel_tip_title",
            )
            tip1 = st.text_input("꿀팁 1 *", key="reel_tip_1", placeholder="첫 번째 핵심 포인트")
            tip2 = st.text_input("꿀팁 2 *", key="reel_tip_2", placeholder="두 번째 핵심 포인트")
            tip3 = st.text_input("꿀팁 3 *", key="reel_tip_3", placeholder="세 번째 핵심 포인트")
            tip4 = st.text_input("꿀팁 4 (선택)", key="reel_tip_4", placeholder="네 번째 포인트")
            bg_hex = st.text_input(
                "배경색 (HEX)",
                value="#1F3D34" if selected_profile_id == "bebeskin" else "#111111",
                key="reel_tip_bg",
            )
            tip_interval = st.slider("텍스트 등장 간격(초)", 1.0, 2.0, 1.5, 0.1, key="reel_tip_gap")
            duration_sec = st.slider("영상 길이(초)", 10.0, 15.0, 12.0, 0.5, key="reel_tip_dur")
            from modules.typography import FONT_FAMILIES, FONT_WEIGHTS

            fam_keys = list(FONT_FAMILIES.keys())
            weight_keys = list(FONT_WEIGHTS.keys())
            rf1, rf2, rf3 = st.columns(3)
            with rf1:
                reel_font_family = st.selectbox(
                    "글씨체",
                    fam_keys,
                    index=0,
                    format_func=lambda k: FONT_FAMILIES[k],
                    key="reel_font_family",
                )
            with rf2:
                reel_title_weight = st.selectbox(
                    "제목 굵기",
                    weight_keys,
                    index=weight_keys.index("bold"),
                    format_func=lambda k: FONT_WEIGHTS[k],
                    key="reel_title_weight",
                )
            with rf3:
                reel_body_weight = st.selectbox(
                    "본문 굵기",
                    weight_keys,
                    index=weight_keys.index("semibold"),
                    format_func=lambda k: FONT_WEIGHTS[k],
                    key="reel_body_weight",
                )
            bgm_file = st.file_uploader(
                "BGM 업로드 (mp3/wav, 선택)",
                type=["mp3", "wav", "m4a", "aac"],
                key="reel_tip_bgm",
            )
            run_tips = st.button(
                "텍스트 릴스 생성",
                type="primary",
                use_container_width=True,
                key="reel_tip_run",
            )
        else:
            folio_title = st.text_input(
                "프로젝트 이름",
                value="portfolio",
                key="reel_folio_title",
                help="저장 폴더 이름에만 사용됩니다.",
            )
            folio_images = st.file_uploader(
                "포트폴리오 이미지 (PNG/JPG, 다중)",
                type=["png", "jpg", "jpeg", "webp"],
                accept_multiple_files=True,
                key="reel_folio_imgs",
            )
            per_image = st.slider("이미지당 길이(초)", 2.5, 5.0, 3.6, 0.1, key="reel_folio_sec")
            crossfade = st.slider("디졸브(초)", 0.4, 1.2, 0.85, 0.05, key="reel_folio_xfade")
            bgm_file = st.file_uploader(
                "BGM 업로드 (mp3/wav, 선택)",
                type=["mp3", "wav", "m4a", "aac"],
                key="reel_folio_bgm",
            )
            run_folio = st.button(
                "포트폴리오 릴스 생성",
                type="primary",
                use_container_width=True,
                key="reel_folio_run",
            )

        st.caption("MoviePy + FFmpeg · 생성 시간은 이미지/길이에 따라 수십 초~수 분")

    with col_preview:
        result = st.session_state.get("reel_result")
        if result and Path(result.get("path", "")).exists():
            out_path = Path(result["path"])
            st.success(result.get("message", "생성 완료"))
            st.video(str(out_path))
            st.caption(f"`{out_path.parent.name}` · {out_path.stat().st_size / (1024 * 1024):.1f} MB")
            st.download_button(
                "MP4 다운로드",
                data=out_path.read_bytes(),
                file_name=out_path.name,
                mime="video/mp4",
                use_container_width=True,
                key="reel_dl",
            )
        else:
            st.info("왼쪽에서 옵션을 넣고 생성하면 여기에 미리보기가 표시됩니다.")

    # ── Run handlers (outside columns to avoid nested layout glitches) ──
    if reel_kind == "정보성 텍스트 릴스" and run_tips:
        tips = [t for t in [tip1, tip2, tip3, tip4] if (t or "").strip()]
        if not (tip_title or "").strip():
            st.error("제목을 입력하세요.")
        elif len(tips) < 3:
            st.error("꿀팁을 3개 이상 입력하세요.")
        else:
            try:
                from modules.reels_maker import create_reels_project, make_text_tips_reel

                project_dir = create_reels_project("tips", tip_title)
                bgm_path = None
                if bgm_file is not None:
                    bgm_path = project_dir / f"bgm{Path(bgm_file.name).suffix.lower()}"
                    bgm_path.write_bytes(bgm_file.getvalue())
                out = project_dir / "final_reel.mp4"
                with st.spinner("텍스트 릴스 렌더링 중…"):
                    make_text_tips_reel(
                        title=tip_title.strip(),
                        tips=tips,
                        bg_color=bg_hex or "#111111",
                        bgm_path=bgm_path,
                        output_path=out,
                        duration_sec=float(duration_sec),
                        tip_interval=float(tip_interval),
                        font_family=reel_font_family,
                        title_weight=reel_title_weight,
                        body_weight=reel_body_weight,
                    )
                st.session_state["reel_result"] = {
                    "path": str(out),
                    "message": f"텍스트 릴스 완료 · {project_dir.name}",
                }
                st.session_state["selected_project"] = project_dir.name
                st.rerun()
            except Exception as exc:  # noqa: BLE001
                st.error(f"생성 실패: {exc}")

    if reel_kind == "포트폴리오 쇼케이스" and run_folio:
        if not folio_images:
            st.error("이미지를 1장 이상 업로드하세요.")
        else:
            try:
                from modules.reels_maker import create_reels_project, make_portfolio_showcase_reel

                project_dir = create_reels_project("folio", folio_title or "portfolio")
                img_dir = project_dir / "images"
                img_dir.mkdir(parents=True, exist_ok=True)
                saved: list[Path] = []
                for i, up in enumerate(folio_images):
                    ext = Path(up.name).suffix.lower() or ".jpg"
                    dest = img_dir / f"img_{i + 1:02d}{ext}"
                    dest.write_bytes(up.getvalue())
                    saved.append(dest)
                bgm_path = None
                if bgm_file is not None:
                    bgm_path = project_dir / f"bgm{Path(bgm_file.name).suffix.lower()}"
                    bgm_path.write_bytes(bgm_file.getvalue())
                out = project_dir / "final_reel.mp4"
                with st.spinner("포트폴리오 릴스 렌더링 중…"):
                    make_portfolio_showcase_reel(
                        image_paths=saved,
                        bgm_path=bgm_path,
                        output_path=out,
                        per_image_sec=float(per_image),
                        crossfade_sec=float(crossfade),
                    )
                st.session_state["reel_result"] = {
                    "path": str(out),
                    "message": f"포트폴리오 릴스 완료 · {project_dir.name}",
                }
                st.session_state["selected_project"] = project_dir.name
                st.rerun()
            except Exception as exc:  # noqa: BLE001
                st.error(f"생성 실패: {exc}")

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
