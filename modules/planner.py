"""Card-news insight planner - B2B marketer storytelling (HOOK->SUMMARY)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from modules.openai_llm import complete_json
from modules.text_quality import EDITOR_STYLE_RULE, KOREAN_ONLY_RULE

CURRENT_YEAR = datetime.now().year
CURRENT_YEAR_LABEL = f"{CURRENT_YEAR}년"

SLIDE_TYPES: tuple[str, ...] = ("COVER", "CONTENT", "SUMMARY")

# Max characters per explanations[] line (renderer draws one array item = one line)
EXPLANATION_MAX_CHARS = 25
# Type B detail lines are a bit longer but still one screen line each
DETAIL_LINE_MAX_CHARS = 30
DETAIL_MIN_LINES = 3
DETAIL_MAX_LINES = 4
# Max characters for SUMMARY actionable takeaways
SUMMARY_ITEM_MAX_CHARS = 20
SUMMARY_MIN_ITEMS = 3
SUMMARY_MAX_ITEMS = 4


@dataclass(frozen=True)
class SlideRole:
    index: int
    phase: str
    tag: str
    layout: str
    purpose: str


# Forced narrative arc: Hook -> Problem -> Solution -> Benefit -> Action -> Insight -> Summary
SLIDE_ROLES: tuple[SlideRole, ...] = (
    SlideRole(1, "hook", "훅", "cover", "COVER - 호기심 훅 (title_lines 2~3)"),
    SlideRole(2, "problem", "문제", "body", "CONTENT PROBLEM - 숫자로 위기/손실"),
    SlideRole(3, "solution", "해법", "body", "CONTENT SOLUTION - 뉴스·팩트 전달"),
    SlideRole(4, "benefit", "이득", "body", "CONTENT BENEFIT - 도입 시 구체 이득"),
    SlideRole(5, "action", "행동", "body", "CONTENT ACTION - 당장 할 행동 지침"),
    SlideRole(6, "insight", "통찰", "body", "CONTENT INSIGHT - 전문가 시선 (요약 금지)"),
    SlideRole(7, "summary", "요약", "summary", "SUMMARY - 핵심 3~4개 넘버링"),
)

SLIDE_TAGS = [r.tag for r in SLIDE_ROLES]

# Abstract fluff that makes copy sound like a press-release summary
_FLUFF_RE = re.compile(
    r"중요하다|기대된다|필요성이\s*대두|지속\s*가능한\s*성장|"
    r"모색해야|중요합니다|예상됩니다|변화와\s*기회|동향을\s*살|"
    r"관심을\s*기울|주목할\s*만|긍정적인\s*영향|시사하는\s*바|"
    r"실전\s*목표|댓글에\s*자료|다양한\s*지원|성장을\s*도모|"
    r"필수적으로\s*고려|적극\s*검토가\s*필요"
)

# AI-ish narrative endings – rewritten to noun-style before drawing
_VERB_ENDING_RULES: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"할\s*수\s*있습니다$"), " 가능"),
    (re.compile(r"수\s*있습니다$"), " 가능"),
    (re.compile(r"해야\s*합니다$"), " 필수"),
    (re.compile(r"하셔야\s*합니다$"), " 필수"),
    (re.compile(r"하십시오$"), " 필수"),
    (re.compile(r"하세요$"), ""),
    (re.compile(r"됩니다$"), ""),
    (re.compile(r"입니다$"), ""),
    (re.compile(r"습니다$"), ""),
    (re.compile(r"합니다$"), ""),
    (re.compile(r"이다$"), ""),
    (re.compile(r"한다$"), ""),
    (re.compile(r"된다$"), ""),
    (re.compile(r"이에요$"), ""),
    (re.compile(r"해요$"), ""),
)


def to_noun_ending(text: str) -> str:
    """Force noun-style / punchy endings – kill AI narrative tails."""
    t = re.sub(r"\s+", " ", (text or "").strip())
    t = t.rstrip(".。…!?")
    for pattern, replacement in _VERB_ENDING_RULES:
        new = pattern.sub(replacement, t)
        if new != t:
            t = re.sub(r"\s+", " ", new).strip()
            break
    return t.rstrip(".。…!? ").strip()


def slide_role(index: int) -> SlideRole:
    return SLIDE_ROLES[min(max(index, 0), len(SLIDE_ROLES) - 1)]


def default_slide_type(index: int) -> str:
    if index <= 0:
        return "COVER"
    if index >= 6:
        return "SUMMARY"
    return "CONTENT"


def slide_type_for_role(role: SlideRole) -> str:
    if role.phase == "hook":
        return "COVER"
    if role.phase == "summary":
        return "SUMMARY"
    return "CONTENT"


def strip_source_noise(text: str) -> str:
    out = re.sub(r"https?://\S+", "", text or "")
    out = re.sub(r"\s+-\s+[^-]{0,40}$", "", out)
    out = re.sub(r"\s+", " ", out).strip(" .…-|")
    return out


def strip_terminal_period(text: str) -> str:
    return (text or "").strip().rstrip(".。…!")


def looks_like_marketing_copy(text: str) -> bool:
    """True when copy collapses into soul-less press-release fluff."""
    return bool(_FLUFF_RE.search(text or ""))


def clip_explanation_line(text: str, *, max_chars: int = EXPLANATION_MAX_CHARS) -> str:
    """Hard-cap one screen line without cutting mid-token when a space exists."""
    t = strip_terminal_period(strip_source_noise(text))
    if len(t) <= max_chars:
        return t
    cut = t[:max_chars]
    sp = cut.rfind(" ")
    if sp >= max(8, max_chars // 2):
        return cut[:sp].rstrip()
    return cut.rstrip()


def clip_detail_line(text: str, *, max_chars: int = DETAIL_LINE_MAX_CHARS) -> str:
    """One Type B detail line: noun-style, no period, capped width."""
    return clip_explanation_line(to_noun_ending(text), max_chars=max_chars)


def split_prose_to_detail_lines(
    prose: str,
    *,
    max_lines: int = DETAIL_MAX_LINES,
    max_chars: int = DETAIL_LINE_MAX_CHARS,
) -> list[str]:
    """Legacy prose -> punchy noun-style lines (paragraph structure is retired)."""
    raw = re.sub(r"\s+", " ", (prose or "").strip())
    if not raw:
        return []
    parts = [p.strip() for p in re.split(r"(?<=[.。!?])\s+|[\n·•]", raw) if p.strip()]
    out: list[str] = []
    for part in parts:
        line = clip_detail_line(part, max_chars=max_chars)
        if line and line not in out:
            out.append(line)
        if len(out) >= max_lines:
            break
    return out


def editorial_hook_body(fact: str, index: int, *, topic: str = "") -> tuple[str, str]:
    label = strip_source_noise(fact.split("-")[0] if "-" in fact else fact.split("—")[0])
    label = label[:40] or topic or "핵심"
    return strip_terminal_period(label), f"{strip_terminal_period(label)}부터 점검"


def build_planner_system_message() -> str:
    return (
        "You are a top-tier B2B marketer and insight consultant writing Korean "
        "Instagram insight carousels for SME owners and operators. "
        "Exactly 7 slides in this fixed story arc: "
        "1 COVER(HOOK) -> 2 PROBLEM -> 3 SOLUTION -> 4 BENEFIT -> 5 ACTION -> "
        "6 INSIGHT -> 7 SUMMARY. "
        "Never summarize like a press release. Punch with numbers, concrete cases, "
        "and direct action verbs. Address the reader as a busy SME decision-maker. "
        "FORBIDDEN fluff: 중요하다, 기대된다, 필요성이 대두된다, 지속 가능한 성장, "
        "모색해야, 중요합니다, 예상됩니다, and similar abstract filler. "
        "CONTENT must choose content_variant A or B for visual rhythm. "
        "Type A: explanations array (2-3 lines, each <=25 chars). "
        "Type B: main_statement (1 line, <=28 chars) + detailed_lines "
        "(ARRAY of 3-4 short fact fragments, each <=30 chars). "
        "NEVER write prose paragraphs. NEVER end any line with 습니다/합니다/이다/한다/"
        "됩니다/입니다/하세요. Use noun endings or sharp fragments such as "
        "'최대 80% 비용 지원', '사업계획서 준비 필수'. "
        "NEVER use a period (.) anywhere. "
        "Across slides 2-6 use at least two A and two B. "
        "Prefer A for PROBLEM/SOLUTION punch; B for BENEFIT/ACTION/INSIGHT detail. "
        "SUMMARY: main_title + summary_list of 3 or 4 actionable takeaways. "
        "Do NOT copy/paste earlier slide lines. Rewrite into executable next-steps "
        "or final insights, each <=20 chars, noun-style, no periods. "
        "image_prompt ONLY on COVER: English, UGC iPhone everyday snap – "
        "messy desk with receipts/coffee, hand holding a phone, natural back view, "
        "candid commute, crumpled papers. Ban glamorous portraits, studio lighting, "
        "cinematic/commercial looks, surreal backgrounds, and AI-perfect skin. "
        "Return ONLY valid JSON."
    )


def build_planner_prompt(
    *,
    topic: str,
    scraped_data: list[str],
    extra_facts: list[str] | None = None,
    reference_block: str = "",
    current_year_label: str = CURRENT_YEAR_LABEL,
) -> str:
    facts_block = "\n".join(
        f"{i + 1}. {f}" for i, f in enumerate(scraped_data[:8]) if f
    ) or "(수집된 데이터 없음)"
    extra = "\n".join(f"- {f}" for f in (extra_facts or [])[:4]) or "- (없음)"
    roles_table = "\n".join(
        f"| {r.index} | {r.phase} | {r.tag} | {r.layout} | {r.purpose} |"
        for r in SLIDE_ROLES
    )

    return f"""## 역할
당신은 **탑티어 B2B 마케터이자 인사이트 컨설턴트**다.
독자는 **중소기업 실무자·대표**. 보도자료 요약자가 아니다.
팩트를 재료로 쓰되, **직접 말하듯(Direct address)** 뼈를 때리는 단문으로 쓴다.

## 주제
{topic}

## 스크랩 팩트 (근거로만 사용, 제목 복붙 금지)
{facts_block}

추가:
{extra}

{f"참고: {reference_block}" if reference_block else ""}

## 스토리텔링 구조 (절대 순서 고정)
| 장 | phase | tag | layout | 역할 |
{roles_table}

| 장 | phase | 써야 할 것 | 예시 톤 |
| 1 | HOOK (COVER) | 호기심·긴장 훅. 모르면 손해/놓치는 포인트 | "2026 중소기업 바우처, 이거 모르면 지원금 다 날립니다" |
| 2 | PROBLEM | 독자가 겪는 위기. **숫자로 공포** | "문서 유출 한 번에 평균 3억 손해" |
| 3 | SOLUTION | 뉴스/팩트 핵심만. 누가·무엇을·선정/발표 | "사이버다임, 기술보호 바우처 공급기업 선정" |
| 4 | BENEFIT | 도입 시 **구체 이득** (%, 원, 기간, 기능) | "도입 비용 80% 국가 지원, 문서중앙화 완비" |
| 5 | ACTION | 당장 할 **행동 지침** (서류·기한·절차) | "신청 방법 및 필수 서류 3가지" |
| 6 | INSIGHT | 업계 트렌드에 대한 **전문가 해석**. 단순 요약 금지 | "바우처는 비용 절감이 아니라 리스크 이전 장치" |
| 7 | SUMMARY | 앞 내용 복붙 금지. 실행 가능한 액션/최종 인사이트 3~4개 (각 <=20자) | "핵심 체크 포인트" + 액션 리스트 |

## 금지 (Forbidden Fluff) - 한 줄이라도 나오면 실패
- 중요하다 / 기대된다 / 필요성이 대두된다 / 지속 가능한 성장
- 모색해야 / 중요합니다 / 예상됩니다 / 관심을 기울여야 / 주목할 만하다
- 긍정적인 영향 / 시사하는 바가 크다 / 다양한 지원 / 성장을 도모
- "실전 목표", 댓글 유도, 출처·언론사명 노출
- 보도자료 톤의 무미건조한 요약 문장

## 필수 문체 (전 슬라이드 공통, 예외 없음)
- **마침표(.) 사용 절대 금지**
- **'~습니다' / '~합니다' / '~이다' / '~한다' / '~됩니다' / '~입니다' / '~하세요' 종결 절대 금지**
- 무조건 **명사형 종결** 또는 짧고 날카로운 **개조식 팩트**
- 좋은 예: "최대 80% 비용 지원", "사업계획서 준비 필수", "마감 D-7 고정"
- 나쁜 예: "비용을 지원받을 수 있습니다", "준비가 필요합니다", "중요한 기회이다"
- **숫자·고유명·서류명·기한**을 반드시 넣을 것
- 줄글(문장 이어붙인 단락) 작성 금지

## CONTENT 레이아웃 변형 (리듬 필수)
CONTENT(2~6장)마다 content_variant를 "A" 또는 "B"로 선택.
5장 중 **A 최소 2장 + B 최소 2장**. 전부 A 금지.

### 타입 A (핵심 강조형) - 팩트를 세게 때릴 때
- content_variant: "A"
- explanations: 2~3개, 각 <=25자
- main_statement / detailed_lines: 빈 값 또는 생략
- 추천 phase: PROBLEM, SOLUTION

### 타입 B (상세 설명형) - 방법·혜택·이유를 쪼갤 때
- content_variant: "B"
- main_statement: 핵심 주장 1줄 (큰 글씨용, <=28자, 명사형 종결)
- detailed_lines: **문자열 배열 3~4개**, 각 항목 <=30자
  - 한 항목 = 화면 한 줄 = 개조식 팩트 1개
  - 문장 이어붙이기 금지, 마침표 금지, 서술형 종결 금지
- explanations: [] (비움)
- 추천 phase: BENEFIT, ACTION, INSIGHT
- `detailed_paragraph` 필드는 **폐기됨**. 절대 쓰지 말 것

## 필드
### COVER (1장)
- title_lines: 2~3개 (훅을 호흡 단위로 분해)
- category_tag: "#바우처" 등 짧은 해시태그
- image_prompt: **영어**, COVER만. **아이폰 폰카/UGC 일상 스냅**만 허용
  - 화려한 인물·비현실 배경·스튜디오 화보 절대 금지
  - **강제**: 영수증, 커피가 놓인 지저분한 책상, 폰을 든 손, 자연스러운 뒷모습 등
    누군가 일상에서 무심코 찍은 듯한 현실 사물/씬
  - 예: "messy desk with crumpled receipts, iced coffee, tangled charger, natural window light"
  - 예: "hand holding an iPhone over a cafe table with latte rings, candid imperfect framing"
  - 예: "back view of a person walking with a tote bag on a weekday street, phone snapshot"
  - **금지**: studio lighting, Annie Leibovitz, cinematic, commercial portrait, glossy skin, AI look
- main_title: title_lines 합친 백업

### CONTENT (2~6장)
- section_number: "1"~"5" (장 순서대로)
- section_title: phase를 드러내는 짧은 라벨 (마침표 없이)
- content_variant: "A" | "B"
- (A) explanations만 / (B) main_statement + detailed_lines만
- sub_point_title / sub_point_text / detailed_paragraph 사용 금지

### SUMMARY (7장)
- main_title: 위계 큰 제목 (예: "핵심 체크 포인트")
- summary_list: **정확히 3개 또는 4개**
- **앞 슬라이드 문장 복붙·단순 잘라내기 엄격 금지**
- 독자가 당장 머릿속에 새겨야 할 **실행 가능한 액션 플랜(Actionable takeaway)** 또는 **최종 인사이트**로 재가공
- 각 항목 **20자 이내**, 명사형 종결, 마침표 금지, 번호 없이
- 좋은 예: "마감 D-7 캘린더 고정", "서류 3종 오늘 준비", "지원율 80% 재확인"
- 나쁜 예: 앞장 문장 그대로 반복, "~습니다" 서술, 20자 초과 장문

{EDITOR_STYLE_RULE}
{KOREAN_ONLY_RULE}
연도 기준: {current_year_label}

## JSON 예시 (톤 참고 - 주제는 실제 스크랩에 맞출 것)
{{
  "hook": "바우처 모르면 지원금 증발",
  "cta": "",
  "slides": [
    {{
      "slide_type": "COVER",
      "title_lines": ["2026 기술보호 바우처", "이거 모르면", "지원금 다 날립니다"],
      "category_tag": "#바우처",
      "main_title": "2026 기술보호 바우처 이거 모르면 지원금 다 날립니다",
      "image_prompt": "messy Korean office desk with crumpled receipts, iced coffee cup, open laptop half out of frame, tangled charging cable, natural window light, candid iPhone snapshot"
    }},
    {{
      "slide_type": "CONTENT",
      "content_variant": "A",
      "section_number": "1",
      "section_title": "한 번의 유출 비용",
      "explanations": [
        "문서 유출 1건 평균 3억",
        "소송·가동중단까지 연쇄",
        "중소는 복구 버퍼 없음"
      ],
      "main_title": "한 번의 유출 비용"
    }},
    {{
      "slide_type": "CONTENT",
      "content_variant": "A",
      "section_number": "2",
      "section_title": "공급기업 선정 팩트",
      "explanations": [
        "사이버다임 공급기업 선정",
        "기술보호 바우처 대상",
        "지금이 신청 윈도우"
      ],
      "main_title": "공급기업 선정 팩트"
    }},
    {{
      "slide_type": "CONTENT",
      "content_variant": "B",
      "section_number": "3",
      "section_title": "받을 수 있는 이득",
      "main_statement": "도입비 80% 국가 지원",
      "detailed_lines": [
        "문서중앙화 + 권한관리 묶음",
        "유출 경로 사전 차단",
        "운영 리스크 국가와 분담",
        "미신청 시 전액 자비 부담"
      ],
      "explanations": [],
      "main_title": "받을 수 있는 이득"
    }},
    {{
      "slide_type": "CONTENT",
      "content_variant": "B",
      "section_number": "4",
      "section_title": "오늘 할 일",
      "main_statement": "서류 3종부터 고정",
      "detailed_lines": [
        "사업자등록증 사본 필수",
        "도입 범위 1페이지 정리",
        "마감 D-day 캘린더 등록",
        "담당자 1명 단독 지정"
      ],
      "explanations": [],
      "main_title": "오늘 할 일"
    }},
    {{
      "slide_type": "CONTENT",
      "content_variant": "B",
      "section_number": "5",
      "section_title": "전문가 한 줄",
      "main_statement": "바우처는 할인쿠폰 아님",
      "detailed_lines": [
        "본질은 리스크 이전 장치",
        "늦은 신청 = 비용 전가",
        "가격보다 타이밍 판단"
      ],
      "explanations": [],
      "main_title": "전문가 한 줄"
    }},
    {{
      "slide_type": "SUMMARY",
      "main_title": "핵심 체크 포인트",
      "summary_list": [
        "마감 D-7 캘린더 고정",
        "서류 3종 오늘 준비",
        "지원율 80% 재확인",
        "담당자 1명 단독 지정"
      ]
    }}
  ]
}}
slides 정확히 7개. CONTENT section_number는 1~5 순서.
Type A explanations 각 항목 25자 이내. Type B detailed_lines 3~4개, 각 30자 이내.
전 슬라이드 마침표 금지, '~습니다/~합니다/~이다' 종결 금지.
"""


async def run_planner_llm(
    *,
    topic: str,
    scraped_data: list[str],
    extra_facts: list[str] | None = None,
    reference_block: str = "",
    current_year_label: str = CURRENT_YEAR_LABEL,
) -> dict[str, Any]:
    return await complete_json(
        system=build_planner_system_message(),
        user=build_planner_prompt(
            topic=topic,
            scraped_data=scraped_data,
            extra_facts=extra_facts,
            reference_block=reference_block,
            current_year_label=current_year_label,
        ),
    )


def _as_str_list(raw: Any, *, limit: int = 8) -> list[str]:
    out: list[str] = []
    if isinstance(raw, list):
        for item in raw:
            t = strip_terminal_period(strip_source_noise(str(item or "").strip()))
            if t:
                out.append(t)
    elif isinstance(raw, str) and raw.strip():
        for part in re.split(r"[\n|/]", raw):
            t = strip_terminal_period(strip_source_noise(part.strip()))
            if t:
                out.append(t)
    return out[:limit]


def parse_planner_slide(raw: dict[str, Any], index: int) -> dict[str, Any]:
    role = slide_role(index)
    slide_type = str(raw.get("slide_type") or "").strip().upper()
    if slide_type in {"TITLE"}:
        slide_type = "COVER"
    elif slide_type in {"DETAIL", "QUOTE", "BODY"}:
        slide_type = "CONTENT"
    if slide_type not in SLIDE_TYPES:
        slide_type = default_slide_type(index)
    if index == 0:
        slide_type = "COVER"
    elif index == 6:
        slide_type = "SUMMARY"
    elif slide_type == "COVER":
        slide_type = "CONTENT"

    title_lines = _as_str_list(raw.get("title_lines"), limit=4)
    category_tag = strip_terminal_period(
        strip_source_noise(str(raw.get("category_tag") or raw.get("badge_text") or ""))
    )
    section_number = str(raw.get("section_number") or "").strip()
    section_title = strip_terminal_period(
        strip_source_noise(str(raw.get("section_title") or ""))
    )
    explanations = [
        clip_explanation_line(x)
        for x in _as_str_list(
            raw.get("explanations") or raw.get("main_text") or raw.get("body_points"),
            limit=4,
        )
        if clip_explanation_line(x)
    ]
    summary_list = [
        clip_detail_line(x, max_chars=SUMMARY_ITEM_MAX_CHARS)
        for x in _as_str_list(
            raw.get("summary_list") or raw.get("body_points"),
            limit=SUMMARY_MAX_ITEMS,
        )
        if clip_detail_line(x, max_chars=SUMMARY_ITEM_MAX_CHARS)
    ]
    main_title = strip_terminal_period(
        strip_source_noise(str(raw.get("main_title") or raw.get("hook") or ""))
    )
    badge = strip_terminal_period(
        strip_source_noise(str(raw.get("badge_text") or raw.get("tag") or role.tag))
    )

    main_statement = clip_detail_line(
        strip_source_noise(str(raw.get("main_statement") or "")), max_chars=28
    )
    detailed_lines = [
        clip_detail_line(x)
        for x in _as_str_list(raw.get("detailed_lines"), limit=DETAIL_MAX_LINES)
        if clip_detail_line(x)
    ]
    if not detailed_lines:
        # Legacy payloads may still send prose – shatter it into punch lines
        detailed_lines = split_prose_to_detail_lines(str(raw.get("detailed_paragraph") or ""))

    variant_raw = str(raw.get("content_variant") or raw.get("layout_variant") or "").strip().upper()
    if variant_raw in {"B", "TYPE_B", "TYPE-B", "DETAIL", "DETAILED", "PARAGRAPH"}:
        content_variant = "B"
    elif variant_raw in {"A", "TYPE_A", "TYPE-A", "PUNCH", "BULLET"}:
        content_variant = "A"
    elif main_statement and detailed_lines:
        content_variant = "B"
    else:
        content_variant = "A"

    if slide_type == "COVER":
        if not title_lines and main_title:
            parts = re.split(r"\s+", main_title)
            title_lines = []
            buf = ""
            for p in parts:
                trial = f"{buf} {p}".strip()
                if len(trial) <= 14:
                    buf = trial
                else:
                    if buf:
                        title_lines.append(buf)
                    buf = p
            if buf:
                title_lines.append(buf)
            title_lines = title_lines[:3] or [main_title]
        if not main_title and title_lines:
            main_title = " ".join(title_lines)
        if not category_tag:
            category_tag = "#인사이트"
        explanations = []
        summary_list = []
        section_number = ""
        section_title = ""
        content_variant = ""
        main_statement = ""
        detailed_lines = []
        legacy = "cover"
    elif slide_type == "SUMMARY":
        if not main_title:
            main_title = "핵심 체크 포인트"
        while len(summary_list) < SUMMARY_MIN_ITEMS:
            summary_list.append(f"실행 포인트 {len(summary_list) + 1}")
        summary_list = [strip_terminal_period(x) for x in summary_list[:SUMMARY_MAX_ITEMS]]
        title_lines = []
        explanations = []
        content_variant = ""
        main_statement = ""
        detailed_lines = []
        legacy = "summary"
    else:
        if not section_number:
            section_number = str(index)
        if not section_title:
            section_title = main_title or role.tag
        if not main_title:
            main_title = section_title

        if content_variant == "B":
            if not main_statement:
                main_statement = clip_detail_line(section_title or main_title, max_chars=28)
            if not detailed_lines and explanations:
                detailed_lines = [clip_detail_line(x) for x in explanations if x]
            while len(detailed_lines) < DETAIL_MIN_LINES:
                detailed_lines.append(
                    ["핵심 수치 먼저 확인", "제출 서류 목록 정리", "마감 일정 캘린더 고정"][
                        len(detailed_lines) % 3
                    ]
                )
            detailed_lines = [x for x in detailed_lines if x][:DETAIL_MAX_LINES]
            explanations = []
        else:
            content_variant = "A"
            main_statement = ""
            detailed_lines = []
            if len(explanations) < 2:
                legacy_blob = str(raw.get("main_text") or raw.get("body") or "")
                chunks = [
                    clip_explanation_line(c.strip())
                    for c in re.split(r"[\n.。]", legacy_blob)
                    if c.strip()
                ]
                for c in chunks:
                    if c and c not in explanations:
                        explanations.append(c)
                    if len(explanations) >= 2:
                        break
            if len(explanations) < 2:
                explanations = [
                    clip_explanation_line(section_title),
                    "숫자·기한부터 오늘 확인",
                ]
            explanations = [e for e in explanations if e][:3]
        title_lines = []
        summary_list = []
        legacy = "body"

    image_prompt = str(raw.get("image_prompt") or "").strip()
    if slide_type != "COVER":
        image_prompt = ""

    if content_variant == "B":
        body_join = "\n".join([main_statement, *detailed_lines]).strip()
    else:
        body_join = "\n".join(explanations) if explanations else main_title

    return {
        "phase": role.phase,
        "slide_type": slide_type,
        "type": legacy,
        "tag": role.tag,
        "badge_text": badge[:32],
        "main_title": main_title,
        "hook": main_title,
        "body": body_join,
        "body_points": summary_list if slide_type == "SUMMARY" else explanations,
        "title_lines": title_lines,
        "category_tag": category_tag,
        "section_number": section_number,
        "section_title": section_title,
        "content_variant": content_variant,
        "explanations": explanations,
        "main_statement": main_statement,
        "detailed_lines": detailed_lines,
        "main_text": body_join,
        "sub_point_title": "",
        "sub_point_text": "",
        "summary_list": summary_list,
        "image_prompt": image_prompt,
        "source_fact": str(raw.get("source_fact") or ""),
        "requires_real_entity": False,
        "cta": "",
    }
