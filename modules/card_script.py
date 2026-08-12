"""Generate research-backed B2B card-news slide scripts via hybrid LLM stack."""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any

from modules.reference import ReferenceInput
from modules.openai_llm import OpenAINotConfiguredError, OpenAIRateLimitError
from modules.planner import (
    DETAIL_MAX_LINES,
    DETAIL_MIN_LINES,
    SLIDE_TAGS,
    SLIDE_TYPES,
    SUMMARY_ITEM_MAX_CHARS,
    SUMMARY_MAX_ITEMS,
    SUMMARY_MIN_ITEMS,
    clip_detail_line,
    clip_explanation_line,
    default_slide_type,
    editorial_hook_body,
    looks_like_marketing_copy,
    parse_planner_slide,
    run_planner_llm,
    slide_role,
    strip_source_noise,
)
from modules.text_quality import (
    KOREAN_ONLY_RULE,
    enforce_editor_copy,
)
from modules.utils import get_logger, get_settings, load_brand_config

logger = get_logger(__name__)

CURRENT_YEAR = datetime.now().year  # 2026
CURRENT_YEAR_LABEL = f"{CURRENT_YEAR}년"

_WEAK_COVER_RE = re.compile(
    r"(전망|동향|여건|분석|종합|브리핑|정리|업데이트|리포트)$"
)
_COVER_TENSION = ("모르면", "놓치면", "틀린", "착각", "대부분", "지금", "아직", "절대")
_COVER_PUNCH = ("날림", "증발", "폭등", "붕괴", "필수", "금지", "위험", "기회", "착각", "%", "억", "조")


def _is_weak_cover_lines(lines: list[str]) -> bool:
    if not lines:
        return True
    joined = " ".join(lines)
    if _WEAK_COVER_RE.search(lines[-1].strip()) or _WEAK_COVER_RE.search(joined):
        return True
    if not any(t in joined for t in _COVER_TENSION) and not any(
        p in joined for p in _COVER_PUNCH
    ):
        return True
    # All lines similar length → monotonous
    lengths = [len(x) for x in lines if x]
    if len(lengths) >= 2 and max(lengths) - min(lengths) <= 2:
        if not any(t in joined for t in _COVER_TENSION):
            return True
    return False


def _punch_up_cover_lines(topic: str, hook: str, lines: list[str]) -> list[str]:
    """Rewrite flat cover into setup / tension / punch when LLM output is bland."""
    clean = [re.sub(r"\s+", " ", (x or "").strip()).rstrip(".。…!") for x in lines if str(x).strip()]
    if clean and not _is_weak_cover_lines(clean):
        return clean[:3]

    seed = (hook or topic or "").strip()
    seed = re.sub(r"\s+", " ", seed)
    # Prefer concrete noun chunk from topic
    core = re.split(r"[·|,/\-–—]", topic or seed)[0].strip()
    core = re.sub(r"^(최근|최신)\s*", "", core)
    if len(core) > 16:
        core = core[:16].rstrip()
    setup = core or "이번 이슈"
    tension = "이거 모르면"
    punch = "기회 다 날림"
    if re.search(r"바우처|지원|공고", topic or ""):
        punch = "지원금 다 날림"
    elif re.search(r"증시|코스피|주식|투자", topic or ""):
        punch = "장밋빛 착각"
        tension = "대부분 놓치는"
    elif re.search(r"\d+\s*%|\d+\s*억", seed):
        m = re.search(r"(\d+(?:\.\d+)?\s*(?:%|억|조|만))", seed)
        punch = f"{m.group(1)} 놓침" if m else punch
    return [setup[:14], tension, punch][:3]


def _ensure_star_emphasis(lines: list[str]) -> list[str]:
    """Only emphasize strong tokens. Never mid-word / particle wraps (억지 강조 금지)."""
    out = [_sanitize_emphasis_marks(str(x)) for x in lines if str(x).strip()]
    if not out:
        return out
    if any("*" in x for x in out):
        return out
    # Prefer a line that already has a strong numeric claim
    for i in range(len(out) - 1, -1, -1):
        m = re.search(
            r"(\d{1,3}(?:,\d{3})*(?:\.\d+)?\s*(?:억|조|만|원|%)|\d+(?:\.\d+)?(?:억|조|만|%|원)|D-\d+)",
            out[i],
        )
        if m:
            token = m.group(1)
            out[i] = out[i].replace(token, f"*{token}*", 1)
            return out
    # Whole-word only (never substrings inside other Hangul words)
    for i in range(len(out) - 1, -1, -1):
        m = re.search(r"(?<![가-힣A-Za-z0-9])(필수|금지|최대|전액|즉시)(?![가-힣A-Za-z0-9])", out[i])
        if m:
            token = m.group(1)
            out[i] = out[i].replace(token, f"*{token}*", 1)
            return out
    # No safe token → leave plain white (better than fake highlight)
    return out


def _sanitize_emphasis_marks(text: str) -> str:
    """Drop forced/awkward *spans* like particles or 1~2 char scraps."""
    bad_alone = {
        "에",
        "을",
        "를",
        "이",
        "가",
        "은",
        "는",
        "의",
        "과",
        "와",
        "시",
        "로",
        "으로",
        "기업",
        "개발에",
        "신청 시",
        "신청시",
    }

    def repl(m: re.Match[str]) -> str:
        inner = (m.group(1) or "").strip()
        if not inner:
            return ""
        if inner in bad_alone:
            return inner
        if len(inner) <= 1:
            return inner
        # Particle-only tails
        if re.fullmatch(r"[은는이가을를의과와에도만]+", inner):
            return inner
        return f"*{inner}*"

    return re.sub(r"\*([^*]+)\*", repl, text or "")

_DEFAULT_COVER_IMAGE_PROMPT = (
    "messy real desk with crumpled receipts, iced coffee cup, open laptop half out of frame, "
    "tangled phone charger, natural window light, slightly imperfect framing, "
    "candid everyday workspace snapshot, shot on iPhone, 9:16"
)

_EMPTY_BODY = re.compile(
    r"업데이트되었습니다\.?$|정보가 업데이트|확인하셨습니까\?$",
)

# If LLM drifts into generic marketing tips, treat as failure
_GENERIC_MARKETING = re.compile(
    r"놓치는 한 가지|과감히 빼세요|한 장\s*=\s*문장|숫자로 증명|훅으로 열고|"
    r"메시지 하나에|저장해두고 다음 콘텐츠|"
    r"내딛어보세요|첫걸음|"
    r"자금을 확보할 수 있어요|다양한 정부지원사업으로|"
    r"신뢰받는 동반자|다양한 지원사업을 운영|"
    r"더 많은 정보를 원하세요|버튼을 클릭|"
    r"확인하셨습니까|헛돈|체크리스트\s*받|"
    r"댓글에\s*['\"]?\s*자료|자료\s*받기|"
    r"중요하다|기대된다|필요성이\s*대두|지속\s*가능한\s*성장|"
    r"중요합니다|예상됩니다|모색해야|관심을\s*기울|시사하는\s*바|"
    r"긍정적인\s*영향|성장을\s*도모|실전\s*목표",
)

_SLOGAN_ONLY = re.compile(
    r"신뢰받는 동반자|성장을 함께|글로벌 도약|"
    r"다양한 지원사업을 운영하고|"
    r"한곳에서 제공해요\.?$|"
    r"사업유형별|지원사업\s*소개|공고\s*조회|조회·신청|"
    r"YouTube|유튜브|TikTok|틱톡|특공\s*전략|이럴땐\s*청약|"
    r"PDF북|한눈에\s*알아보기|웹에서\s*확인",
)

_CRUMB_COPY = re.compile(
    r"TikTok|YouTube|유튜브|틱톡|\|\s*|PDF북|"
    r"한눈에\s*알아보기|조회·신청|사업유형별|"
    r"주얼리|워치|패션|Innovation Lab",
)

_NEGATIVE_NEWS = re.compile(
    r"허위|과장광고|송치|사기|피의자|불구속|형사|벌금|고발|마약|살인|성범죄",
    re.I,
)

_SEO_LIST = re.compile(
    r"목록\.|공고\s*목록|모음|총정리|한눈에|PDF|알리미|링크모음|"
    r"신청\s*방법.*신청|유치여행사|관광객|주소월드",
    re.I,
)

_TOPIC_OFFTOPIC = re.compile(
    r"편리하지만\s*새로운\s*공격|트렌드마이크로|AI\s*2026\]|"
    r"주얼리|워치|패션|Innovation\s*Lab",
    re.I,
)

_OFFTOPIC_SNIPPET = re.compile(
    r"주얼리|워치|패션|야동|TikTok|틱톡|PDF북|한눈에\s*알아보기|"
    r"알려드립니다|오늘은\s*.*알려|신청\s*방법.*신청\s*방법|"
    r"김희수|황희곤|이승호|대표\)이|대표\)가|"
    r"목록\.|공고\s*목록|모음|총정리|알리미|유치여행사|관광객",
    re.I,
)

_BLOG_OPENER = re.compile(
    r"^(?:2월이 시작|오늘은|안녕하세요|알려드립니다|많이 올라오고)",
    re.I,
)

from modules.card_visuals import CORPORATE_BG_BASE, build_slide_bg_prompt

_FACT_SIGNAL = re.compile(
    r"\d|"
    r"[億억만조원%]|공고|선정|바우처|산업부|중기부|중소벤처|"
    r"창업지원|통합공고|R&D|지원금",
)


@dataclass
class CardSlide:
    tag: str
    hook: str
    body: str
    badge_text: str = ""
    main_title: str = ""
    slide_type: str = "CONTENT"  # COVER | CONTENT | SUMMARY
    body_points: list[str] = field(default_factory=list)
    title_lines: list[str] = field(default_factory=list)
    category_tag: str = ""
    section_number: str = ""
    section_title: str = ""
    content_variant: str = "A"  # A = punch lines | B = statement + detail lines
    explanations: list[str] = field(default_factory=list)
    main_statement: str = ""
    detailed_lines: list[str] = field(default_factory=list)
    main_text: str = ""  # legacy join of explanations / type-B body
    sub_point_title: str = ""  # deprecated
    sub_point_text: str = ""  # deprecated
    summary_list: list[str] = field(default_factory=list)
    search_query: str = "moody empty office interior"
    image_prompt: str = ""
    search_queries: list[str] = field(default_factory=list)
    type: str = "body"  # cover | body | summary
    visual: str = ""
    source_fact: str = ""
    requires_real_entity: bool = False
    cta: str = ""

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        if not d.get("main_title"):
            d["main_title"] = d.get("hook") or ""
        if not d.get("hook"):
            d["hook"] = d.get("main_title") or ""
        variant = str(d.get("content_variant") or "A").upper()
        if variant == "B":
            if not d.get("main_text"):
                d["main_text"] = "\n".join(
                    [x for x in [d.get("main_statement"), *(d.get("detailed_lines") or [])] if x]
                )
        else:
            if not d.get("explanations") and d.get("main_text"):
                d["explanations"] = [
                    x.strip().rstrip(".。")
                    for x in str(d["main_text"]).split("\n")
                    if x.strip()
                ]
            if not d.get("main_text") and d.get("explanations"):
                d["main_text"] = "\n".join(d["explanations"])
        if not d.get("main_text") and d.get("body"):
            d["main_text"] = d["body"]
        if not d.get("summary_list") and d.get("body_points") and d.get("slide_type") == "SUMMARY":
            d["summary_list"] = d["body_points"]
        if not d.get("visual"):
            d["visual"] = d.get("search_query") or "insight"
        return d


@dataclass
class CardScriptResult:
    mode: str = "card_news"
    hook: str = ""
    cta: str = ""
    slides: list[CardSlide] = field(default_factory=list)
    source: str = "stub"

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "hook": self.hook,
            "cta": self.cta,
            "slides": [s.to_dict() for s in self.slides],
            "source": self.source,
            "body": "\n".join(s.hook for s in self.slides),
            "full_text": " ".join(
                enforce_editor_copy(re.sub(r"<[^>]+>", "", f"{s.hook}. {s.body}"))
                for s in self.slides
            ),
            "caption_lines": [s.hook for s in self.slides],
            "visual_markers": [
                f"[{i+1}. Visual: {s.search_query or s.visual}]" for i, s in enumerate(self.slides)
            ],
        }


def _extract_json(text: str) -> dict[str, Any]:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{[\s\S]*\}", text)
        if not match:
            raise
        return json.loads(match.group(0))


def _looks_generic_marketing(slides: list[CardSlide]) -> bool:
    blob = " ".join(f"{s.hook} {s.body}" for s in slides)
    hits = len(_GENERIC_MARKETING.findall(blob))
    return hits >= 2


def _normalize_compare(text: str) -> str:
    return re.sub(r"[^\w가-힣]", "", (text or "").lower())


def _is_verbatim_title_paste(hook: str, body: str, facts: list[str]) -> bool:
    """Only reject when a scraped *title* is pasted wholesale (≥15 chars)."""
    blob = f"{hook} {body}"
    if _CRUMB_COPY.search(blob):
        return True
    blob_n = _normalize_compare(blob)
    for fact in facts:
        if not fact:
            continue
        title_n = _normalize_compare(fact.split("—")[0])
        if len(title_n) >= 15 and title_n in blob_n:
            return True
    return False


def _parse_fact_parts(fact: str) -> dict[str, Any]:
    title = re.sub(r"\s+", " ", (fact or "").split("—")[0].strip())
    snippet = ""
    if "—" in (fact or ""):
        snippet = re.sub(r"\s+", " ", fact.split("—", 1)[-1].strip())
    nums = re.findall(r"\d+(?:\.\d+)?(?:억|조|만|원|%|곳|건|명|개|년)?", fact or "")
    nums = [n for n in nums if not re.fullmatch(r"20\d{2}년?", n)]
    agencies = re.findall(
        r"산업부|중기부|중소벤처|과기부|KBIZ|K-Startup|창업진흥원|통합공고",
        fact or "",
    )
    subject = re.sub(
        r"TikTok|YouTube|유튜브|\|.*|한눈에\s*알아보기|PDF북",
        "",
        title,
    ).strip()
    if re.search(r"CKP|경영지|Startup|창업알리미|총정리", subject, re.I):
        subject = "정부지원사업"
    subject = re.sub(r"^\d{4}\s*", "", subject).strip()
    subject = re.sub(r"^\d{1,2}년\s*\d{1,2}월.*", "", subject).strip()
    if re.match(r"^[년월일()\s]+$", subject) or len(subject) < 3:
        subject = "정부지원사업"
    if len(subject) > 50:
        subject = subject[:47].rstrip()
    return {
        "title": title,
        "snippet": snippet,
        "nums": nums,
        "agencies": agencies,
        "subject": subject or "지원사업",
    }


def _fact_years(fact: str) -> set[int]:
    return {int(y) for y in re.findall(r"(20\d{2})", fact or "")}


def _is_stale_fact(fact: str) -> bool:
    """Reject old-year facts and month-old relative Korean dates."""
    blob = fact or ""
    if re.search(r"지난\s*달|지난달|한\s*달여|한\s*달\s*전|한달\s*전", blob):
        return True
    m = re.search(r"(\d+)\s*(?:개월|달)\s*전", blob)
    if m and int(m.group(1)) >= 1:
        return True
    m = re.search(r"(\d+)\s*주\s*전", blob)
    if m and int(m.group(1)) >= 2:
        return True
    years = _fact_years(blob)
    if not years:
        return False
    if any(y >= CURRENT_YEAR - 1 for y in years):
        return False
    return max(years) <= CURRENT_YEAR - 2


def _topic_coherence(topic: str, fact: str) -> float:
    """Higher = more aligned with user topic."""
    blob = (fact or "").lower()
    core = re.sub(r"(최근|최신|트렌드|동향|분석)\s*", "", topic or "").strip()
    tokens = re.findall(r"[가-힣A-Za-z0-9]{2,}", core)
    stop = {"정부", "지원", "사업", "관련", "최근", "트렌드"}
    tokens = [t for t in tokens if t not in stop]
    if not tokens:
        return 0.0
    hits = sum(1 for t in tokens if t.lower() in blob or t in fact)
    score = hits / max(len(tokens), 1)
    if re.search(r"정부\s*지원|지원\s*사업", topic or ""):
        if _TOPIC_OFFTOPIC.search(fact or "") and not re.search(
            r"지원|공고|바우처|중소|창업|산업부|중기부", fact or ""
        ):
            return -3.0
        if re.search(r"지원|공고|바우처|중소|창업|산업부|중기부|억|조", fact or ""):
            score += 0.5
    return score


def _fact_freshness_score(fact: str) -> float:
    years = _fact_years(fact)
    score = 0.0
    if CURRENT_YEAR in years:
        score += 3.0
    if (CURRENT_YEAR - 1) in years:
        score += 2.0
    if years and max(years) <= CURRENT_YEAR - 2:
        score -= 5.0
    if _FACT_SIGNAL.search(fact or ""):
        score += 1.0
    if re.search(r"억|조|원", fact or ""):
        score += 1.5
    return score


def _scrub_stale_years(text: str) -> str:
    """Never show 2024/2023 on slides in 2026."""
    out = text or ""
    for old in range(2020, CURRENT_YEAR - 1):
        out = out.replace(f"{old}년", CURRENT_YEAR_LABEL)
        out = re.sub(rf"\b{old}\b", str(CURRENT_YEAR), out)
    return out


def _rank_facts(facts: list[str], *, topic: str = "") -> list[str]:
    fresh = [f for f in facts if f and not _is_stale_fact(f)]
    pool = fresh or [f for f in facts if f]
    return sorted(
        pool,
        key=lambda f: (_topic_coherence(topic, f), _fact_freshness_score(f)),
        reverse=True,
    )


def _is_keyword_soup(text: str) -> bool:
    t = text or ""
    if t.count(",") >= 3:
        return True
    if len(re.findall(r"신청|방법|확인|대상|지원금", t)) >= 4:
        return True
    return False


def _best_number(fact: str) -> str:
    """Prefer 억/조/원 amounts – never bare '26' from '26년' or fraud '%'."""
    nums = re.findall(
        r"\d+(?:\.\d+)?(?:억|조|만|원|%|곳|건|명|개)",
        fact or "",
    )
    nums = [n for n in nums if not re.fullmatch(r"\d+%", n)]
    for unit in ("조", "억", "만", "원"):
        for n in nums:
            if unit in n:
                return n
    return nums[0] if nums else ""


def _core_topic_label(subject: str, *, max_len: int = 14) -> str:
    s = subject or ""
    s = re.sub(r"\d+(?:\.\d+)?(?:억|조|만|원|%|곳|건|명|개|년)?", " ", s)
    s = re.sub(
        r"정부지원사업|지원사업|한눈에.*|PDF북|제공과|종류와|"
        r"\[.*?\]|경영지도사|PM과|\.\.\.",
        " ",
        s,
    )
    s = re.sub(r"[,·|].*", "", s)
    s = re.sub(r"\s+", " ", s).strip()
    stop = {
        "관련", "최신", "지원", "사업", "정부", "대한", "위한", "올해", "내년",
        "선정", "공고", "예산", "통합", "제공", "확인", "방법", "트렌드",
    }
    words = [w for w in re.findall(r"[가-힣A-Za-z&]{2,}", s) if w not in stop]
    if words:
        s = " ".join(words[:3])
    if len(s) > max_len:
        s = s[: max_len - 1].rstrip()
    return s or "지원사업"


def _extract_usable_sentence(snippet: str, topic: str = "") -> str:
    """One clean policy sentence – drop spam tails and keyword soup."""
    if not snippet or _OFFTOPIC_SNIPPET.search(snippet) or _is_keyword_soup(snippet):
        return ""
    # Cut at unrelated tail (e.g. watch jewelry after policy text)
    chunks = re.split(
        r"(?<=[.!?])\s+|(?<=\.)(?=이번\s*시즌)|(?<=\.)(?=Innovation)",
        snippet,
    )
    for part in chunks:
        part = part.strip()
        if len(part) < 18:
            continue
        if _OFFTOPIC_SNIPPET.search(part) or _is_keyword_soup(part):
            continue
        if _BLOG_OPENER.search(part):
            continue
        if re.search(r"덴탈|치과의사|웰로|페이지\s*$", part):
            continue
        if _CRUMB_COPY.search(part):
            continue
        if re.search(r"있어요|해요\.?$|이에요|돼요|하세요", part):
            continue
        if strip_source_noise(part) != part and not re.search(r"\d|억|조", part):
            continue
        if not re.search(r"지원|공고|선정|예산|억|조|바우처|창업|중소|R&D|AI", part):
            continue
        return clean[:120]
    return ""


def _synthesize_body(
    *,
    tag: str,
    num: str,
    agency: str,
    subj: str,
    snippet: str,
    topic: str = "",
    fact: str = "",
    phase: str = "fact",
) -> str:
    """Editorial body – structured by slide phase, never raw paste."""
    p = _parse_fact_parts(fact or f"{subj} — {snippet}")
    clean = strip_source_noise(_extract_usable_sentence(snippet, topic))
    if clean and phase in {"fact", "intro"}:
        return enforce_editor_copy(_scrub_stale_years(clean))

    role = slide_role(
        {"intro": 0, "fact": 1, "interpret": 3, "action": 5, "summary": 6}.get(phase, 1)
    )
    _, body = editorial_hook_body(
        role=role,
        topic=topic,
        fact=fact or f"{subj} — {snippet}",
        parsed={
            "num": num,
            "agency": agency,
            "label": _core_topic_label(subj),
            "snippet": snippet,
        },
        all_facts=[fact] if fact else [],
    )
    return enforce_editor_copy(_scrub_stale_years(body))


def _fact_to_editorial_copy(
    fact: str,
    index: int,
    *,
    topic: str = "",
    all_facts: list[str] | None = None,
) -> tuple[str, str]:
    """Turn one fact into hook/body for a specific slide role."""
    role = slide_role(index)
    p = _parse_fact_parts(fact)
    parsed = {
        "num": _best_number(fact),
        "agency": p["agencies"][0] if p["agencies"] else "",
        "label": _core_topic_label(p["subject"]),
        "snippet": p["snippet"],
    }
    hook, body = editorial_hook_body(
        role=role,
        topic=topic,
        fact=fact,
        parsed=parsed,
        all_facts=all_facts or [fact],
    )
    return _scrub_stale_years(hook), _scrub_stale_years(body)


def _slide_references_fact(slide: CardSlide, fact: str) -> bool:
    """Slide should contain at least one signal from its assigned fact."""
    if slide.type in {"cta", "summary"}:
        return True
    blob = f"{slide.hook} {slide.body}"
    if _FACT_SIGNAL.search(blob):
        return True
    p = _parse_fact_parts(fact)
    for n in p["nums"]:
        if n and n in blob:
            return True
    for a in p["agencies"]:
        if a and a in blob:
            return True
    subj_key = re.sub(r"[^\w가-힣]", "", p["subject"])[:8]
    blob_key = re.sub(r"[^\w가-힣]", "", blob)
    return bool(subj_key and len(subj_key) >= 4 and subj_key in blob_key)


def _slide_has_bad_copy(slide: CardSlide, facts: list[str]) -> bool:
    """True if slide uses portal/ad crumbs or pastes a full scraped title."""
    blob = f"{slide.hook} {slide.body}"
    if _GENERIC_MARKETING.search(blob) or _SLOGAN_ONLY.search(blob):
        return True
    if looks_like_marketing_copy(blob):
        return True
    if strip_source_noise(blob) != blob and re.search(r"정책브리핑|웰로|네이트", blob):
        return True
    if _is_verbatim_title_paste(slide.hook, slide.body, facts):
        return True
    hook_plain = re.sub(r"[*\s|·.…]+", "", slide.hook or "")
    body_plain = re.sub(r"[*\s|·.…]+", "", slide.body or "")
    if (
        hook_plain
        and body_plain
        and len(hook_plain) >= 12
        and hook_plain == body_plain
    ):
        return True
    if _EMPTY_BODY.search(slide.body or "") and not re.search(
        r"\d|억|조|원|%", slide.body or ""
    ):
        return True
    if re.search(r"202[0-4]\s*년", f"{slide.hook} {slide.body}"):
        return True
    return False


def _infer_requires_real_entity(
    *,
    hook: str,
    body: str,
    search_query: str,
    raw: Any,
) -> bool:
    """Only True for clear named people / brands / logos."""
    if isinstance(raw, bool):
        return raw
    if str(raw).strip().lower() in {"true", "1", "yes"}:
        return True
    if str(raw).strip().lower() in {"false", "0", "no"}:
        return False

    blob = f"{hook} {body} {search_query}"
    # Named entities / brands
    if re.search(
        r"Elon|Musk|Apple|Google|Samsung|Tesla|NVIDIA|OpenAI|Microsoft|Amazon|"
        r"머스크|일론|애플|삼성전자|테슬라|엔비디아|구글|메타",
        blob,
        re.I,
    ):
        return True
    # Abstract policy / trends → AI image
    if re.search(
        r"지원|트렌드|전략|마케팅|정책|공고|바우처|창업|중소|보조금|선정",
        blob,
    ):
        return False
    return False


def _scrub_non_korean_noise(text: str) -> str:
    """Drop accidental CJK / kana from LLM output."""
    repl = {
        "最近": "최근",
        "现在": "지금",
        "什么": "",
        "的": "",
    }
    out = text or ""
    for a, b in repl.items():
        out = out.replace(a, b)
    # Strip CJK ideographs + Japanese kana outside Hangul/ASCII
    out = re.sub(r"[\u4e00-\u9fff\u3040-\u30ff\u31f0-\u31ff]+", "", out)
    return re.sub(r"\s+", " ", out).strip()


def _short_fact(fact: str, limit: int = 72) -> str:
    text = re.sub(r"\s+", " ", (fact or "").strip())
    text = re.split(r"\s—\s", text)[0].strip()
    if len(text) > limit:
        text = text[: limit - 1] + "…"
    return text


def _is_on_topic_fact(topic: str, fact: str) -> bool:
    """Drop spam / off-topic / nav-crumb junk before it reaches the LLM."""
    from modules.web_facts import _CRUMB_RE, _SPAM_RE, _is_useful

    title = (fact or "").split("—")[0].strip()
    snip = (fact or "").split("—", 1)[-1].strip() if "—" in (fact or "") else ""
    if _SPAM_RE.search(fact or "") or _CRUMB_RE.search(fact or ""):
        return False
    if _OFFTOPIC_SNIPPET.search(fact or "") or _is_keyword_soup(fact or ""):
        return False
    if _NEGATIVE_NEWS.search(fact or ""):
        return False
    if _is_stale_fact(fact or ""):
        return False
    if _SEO_LIST.search(fact or "") or _TOPIC_OFFTOPIC.search(fact or ""):
        if not re.search(r"지원|공고|바우처|중소|창업|산업부|중기부|억|조", fact or ""):
            return False
    if _topic_coherence(topic, fact or "") < -1:
        return False
    if re.search(r"정부\s*지원|지원\s*사업", topic or ""):
        if not re.search(
            r"지원|공고|바우처|중소|창업지원|산업부|중기부|예산|억|조|선정|정부|보조금",
            fact or "",
        ):
            return False
    if (fact or "").count("|") >= 1 and re.search(r"TikTok|틱톡", fact or "", re.I):
        return False
    if (fact or "").count("|") >= 2 and not re.search(r"\d", fact or ""):
        return False
    return _is_useful(title or fact, snip, topic)


def _extract_stat_hints(facts: list[str]) -> list[str]:
    """Pull numeric/policy hints from facts – for PAS fallback only."""
    hints: list[str] = []
    for f in facts:
        m = re.search(
            r"\d+(?:\.\d+)?(?:억|조|만|원|%|곳|건|명|개|년)?[^\s—|]{0,20}",
            f or "",
        )
        if m:
            hints.append(re.sub(r"\s+", " ", m.group(0)).strip())
    if not hints:
        hints = ["2026년 통합공고", "창업·R&D·바우처", "중소·벤처 지원"]
    return hints[:4]


def _detail_points_from_copy(main_title: str, body: str, fact: str, topic: str) -> list[str]:
    """Build 3 deep editorial bullets for DETAIL/SUMMARY (no hard char clip)."""
    parsed = _parse_fact_parts(fact)
    num = parsed.get("num") or ""
    agency = parsed.get("agency") or ""
    label = parsed.get("label") or topic_short_label(topic)
    points: list[str] = []

    if num and agency:
        points.append(
            enforce_editor_copy(
                f"{agency} 기준 {num} 규모가 핵심이며, 신청·집행 일정이 겹치면 캐파 리스크가 커집니다"
            )
        )
    if body and len(body) >= 12:
        points.append(enforce_editor_copy(body))
    if main_title and len(main_title) >= 8 and len(points) < 3:
        points.append(
            enforce_editor_copy(
                f"{label} 관련 적합성·재무·서류를 공고별로 분리해 지금 점검하십시오"
            )
        )
    if len(points) < 3:
        points.append(
            enforce_editor_copy(
                f"{CURRENT_YEAR_LABEL} {label} 공고축을 한 장으로 정리해 팀 공유가 필요합니다"
            )
        )
    if len(points) < 3:
        points.append(enforce_editor_copy("R&D·실증·매출 3축이 연결돼야 선정·수주 확률이 올라갑니다"))

    deduped: list[str] = []
    seen: set[str] = set()
    for p in points:
        key = re.sub(r"\s+", "", p)[:48]
        if key in seen:
            continue
        seen.add(key)
        deduped.append(p)
    return deduped[:4]


def topic_short_label(topic: str) -> str:
    s = re.sub(r"(최근|최신|트렌드|동향|분석)\s*", "", topic or "").strip()
    return s or topic or "해당 주제"


def _apply_slide_bg_prompt(slide: CardSlide, index: int) -> None:
    slide.image_prompt = build_slide_bg_prompt(slide.to_dict(), index=index)


def _corporate_visuals(*, slide: CardSlide | None = None, variant: int = 0) -> tuple[str, list[str], str]:
    if slide is not None:
        prompt = build_slide_bg_prompt(slide.to_dict(), index=variant)
    else:
        prompt = f"{CORPORATE_BG_BASE}, variant {variant + 1}"
    return (
        "content-matched corporate background",
        ["minimal business presentation backdrop"],
        prompt,
    )


def _topic_anchor_facts(topic: str) -> list[str]:
    """Last-resort anchors when search returns junk – no invented numbers."""
    y = CURRENT_YEAR_LABEL
    if re.search(r"정부\s*지원|지원\s*사업|트렌드", topic):
        return [
            f"{y} 중소벤처기업부 — 창업·R&D·바우처 통합공고 축별 선정 진행",
            f"{y} 중소기업 혁신바우처 — 제조·서비스 업종별 지원 한도·마감 확인",
            f"{y} 창업지원사업 — 예비·초기·도약 단계별 공고·서류 요건",
            f"{y} 소상공인 정책자금·경영안정 — 지자체·중기부 연계 공고",
            f"{y} R&D 지원사업 — 산업부·과기부 연구개발 과제 모집",
            f"{y} 정부지원사업 — 사업 적합성·재무·서류 완성도 선별 기준",
        ]
    return [f"{y} {topic} — 최신 공고·선정·예산 동향"]


def _fact_dedupe_key(fact: str) -> str:
    title = re.sub(r"\s+", "", (fact or "").split("—")[0].lower())
    title = re.sub(r"[^\w가-힣]", "", title)[:40]
    return title


def _fallback(
    topic: str,
    selected_hook: str,
    facts: list[str],
    *,
    prefer_facts: list[str] | None = None,
) -> CardScriptResult:
    """Fact-driven slides – editorial 7-slide structure."""
    primary_pool = prefer_facts if prefer_facts else facts
    clean = _rank_facts(
        [
            f
            for f in primary_pool
            if f and len(f.strip()) >= 8 and _is_on_topic_fact(topic, f)
        ],
        topic=topic,
    )[:6]
    if len(clean) < 6:
        extra = _rank_facts(
            [
                f
                for f in facts
                if f and len(f.strip()) >= 8 and _is_on_topic_fact(topic, f)
            ],
            topic=topic,
        )
        for f in extra:
            if f not in clean:
                clean.append(f)
            if len(clean) >= 6:
                break
    deduped: list[str] = []
    seen_keys: set[str] = set()
    for f in clean:
        key = _fact_dedupe_key(f)
        if key in seen_keys:
            continue
        seen_keys.add(key)
        deduped.append(f)
    clean = deduped
    anchors = _topic_anchor_facts(topic)
    ai = 0
    while len(clean) < 6 and ai < len(anchors):
        if anchors[ai] not in clean:
            clean.append(anchors[ai])
        ai += 1
    hook = selected_hook or topic

    slides: list[CardSlide] = []
    fact_indices = [0, 1, 2, 1, 2, 2, 0]
    for i in range(7):
        role = slide_role(i)
        fi = min(fact_indices[i], len(clean) - 1)
        fact = clean[fi] if clean else f"{topic} — {CURRENT_YEAR_LABEL} 동향"
        h, b = _fact_to_editorial_copy(fact, i, topic=topic, all_facts=clean)
        slide_type = default_slide_type(i)
        if slide_type == "COVER":
            cs = CardSlide(
                tag=role.tag,
                hook=h,
                body="",
                badge_text="인사이트",
                main_title=h,
                slide_type="COVER",
                title_lines=[h[:12], h[12:24] or "핵심 정리", "TOP 포인트"][:3],
                category_tag="#인사이트",
                image_prompt=_DEFAULT_COVER_IMAGE_PROMPT,
                type="cover",
                source_fact=fact,
            )
        elif slide_type == "SUMMARY":
            pts = _detail_points_from_copy(h, b, fact, topic)
            pts = [clip_detail_line(p, max_chars=SUMMARY_ITEM_MAX_CHARS) for p in pts if p]
            while len(pts) < SUMMARY_MIN_ITEMS:
                pts.append(f"실행 포인트 {len(pts) + 1}")
            cs = CardSlide(
                tag=role.tag,
                hook=h,
                body="",
                main_title="핵심 체크 포인트",
                slide_type="SUMMARY",
                summary_list=pts[:SUMMARY_MAX_ITEMS],
                body_points=pts[:SUMMARY_MAX_ITEMS],
                type="summary",
                source_fact=fact,
            )
        else:
            # Alternate A/B for fallback rhythm: 1,2 -> A ; 3,4,5 -> B
            use_b = i >= 3
            if use_b:
                statement = clip_detail_line(h, max_chars=28) or role.tag
                detail_lines = [
                    "핵심 수치 먼저 확인",
                    "제출 서류 목록 정리",
                    "마감 일정 캘린더 고정",
                ]
                blob = "\n".join([statement, *detail_lines])
                cs = CardSlide(
                    tag=role.tag,
                    hook=h,
                    body=blob,
                    main_title=h,
                    slide_type="CONTENT",
                    section_number=str(i),
                    section_title=(h[:20] or role.tag).rstrip(".。"),
                    content_variant="B",
                    main_statement=statement,
                    detailed_lines=detail_lines,
                    explanations=[],
                    main_text=blob,
                    type="body",
                    source_fact=fact,
                )
            else:
                raw_ex = [
                    clip_explanation_line(x)
                    for x in re.split(r"[\n.。]", b or h)
                    if x.strip()
                ]
                raw_ex = [x for x in raw_ex if x][:3] or [
                    clip_explanation_line(h),
                    "숫자·기한부터 오늘 확인",
                ]
                cs = CardSlide(
                    tag=role.tag,
                    hook=h,
                    body=b,
                    main_title=h,
                    slide_type="CONTENT",
                    section_number=str(i),
                    section_title=(h[:20] or role.tag).rstrip(".。"),
                    content_variant="A",
                    explanations=raw_ex,
                    type="body",
                    source_fact=fact,
                )
                cs.main_text = "\n".join(cs.explanations)
                cs.body_points = list(cs.explanations)
        slides.append(cs)

    return CardScriptResult(
        hook=enforce_editor_copy(hook),
        cta="",
        slides=slides,
        source="editorial_fallback",
    )


def _parse_slide(raw: dict[str, Any], *, index: int = 0, total: int = 7) -> CardSlide:
    normalized = parse_planner_slide(raw, index)
    role = slide_role(index)

    slide_type = str(normalized.get("slide_type") or default_slide_type(index)).upper()
    if slide_type in {"TITLE"}:
        slide_type = "COVER"
    elif slide_type in {"DETAIL", "QUOTE", "BODY"}:
        slide_type = "CONTENT"
    if slide_type not in SLIDE_TYPES:
        slide_type = default_slide_type(index)
    if index == 0:
        slide_type = "COVER"
    elif index >= 6:
        slide_type = "SUMMARY"

    main_title = _scrub_non_korean_noise(
        strip_source_noise(normalized.get("main_title") or normalized.get("hook") or "")
    )
    main_title = _scrub_stale_years(main_title)
    badge = enforce_editor_copy(
        strip_source_noise(str(normalized.get("badge_text") or ""))
    )[:32]

    title_lines = [
        _scrub_stale_years(_scrub_non_korean_noise(str(x).strip()))
        for x in (normalized.get("title_lines") or [])
        if str(x).strip()
    ][:4]
    category_tag = str(normalized.get("category_tag") or "").strip()
    section_number = str(normalized.get("section_number") or "").strip()
    section_title = _scrub_stale_years(
        _scrub_non_korean_noise(strip_source_noise(str(normalized.get("section_title") or "")))
    ).rstrip(".。…!")
    explanations = [
        clip_explanation_line(
            _scrub_stale_years(_scrub_non_korean_noise(str(x).strip())).rstrip(".。…!")
        )
        for x in (normalized.get("explanations") or [])
        if str(x).strip()
    ]
    explanations = [x for x in explanations if x][:3]
    summary_list = [
        clip_detail_line(
            enforce_editor_copy(strip_source_noise(str(p))).rstrip(".。…!"),
            max_chars=SUMMARY_ITEM_MAX_CHARS,
        )
        for p in (normalized.get("summary_list") or [])
        if str(p).strip()
    ]
    summary_list = [x for x in summary_list if x][:SUMMARY_MAX_ITEMS]

    detailed_lines = [
        clip_detail_line(_scrub_stale_years(_scrub_non_korean_noise(str(x))))
        for x in (normalized.get("detailed_lines") or [])
        if str(x).strip()
    ]
    detailed_lines = [x for x in detailed_lines if x][:DETAIL_MAX_LINES]

    content_variant = str(normalized.get("content_variant") or "A").strip().upper()
    if content_variant not in {"A", "B"}:
        content_variant = "B" if (
            str(normalized.get("main_statement") or "").strip() and detailed_lines
        ) else "A"
    main_statement = clip_detail_line(
        _scrub_stale_years(_scrub_non_korean_noise(str(normalized.get("main_statement") or ""))),
        max_chars=28,
    )

    if slide_type == "CONTENT" and content_variant == "A" and len(explanations) < 2:
        blob = str(normalized.get("main_text") or normalized.get("body") or "")
        explanations = [
            clip_explanation_line(
                _scrub_stale_years(_scrub_non_korean_noise(c.strip())).rstrip(".。…!")
            )
            for c in re.split(r"[\n.。]", blob)
            if c.strip()
        ]
        explanations = [x for x in explanations if x][:3]
    if slide_type == "CONTENT" and content_variant == "A" and len(explanations) < 2:
        explanations = [
            clip_explanation_line(section_title or main_title),
            "숫자·기한부터 오늘 확인",
        ]
    if slide_type == "CONTENT" and content_variant == "B":
        if not main_statement:
            main_statement = clip_detail_line(section_title or main_title, max_chars=28)
        if not detailed_lines:
            detailed_lines = [clip_detail_line(x) for x in explanations if x]
        while len(detailed_lines) < DETAIL_MIN_LINES:
            detailed_lines.append(
                ["핵심 수치 먼저 확인", "제출 서류 목록 정리", "마감 일정 캘린더 고정"][
                    len(detailed_lines) % 3
                ]
            )
        detailed_lines = [x for x in detailed_lines if x][:DETAIL_MAX_LINES]
        explanations = []
        main_text = "\n".join([main_statement, *detailed_lines]).strip()
    else:
        if slide_type != "CONTENT":
            content_variant = ""
            main_statement = ""
            detailed_lines = []
        main_text = "\n".join(explanations)

    body = main_text
    body_points = summary_list if slide_type == "SUMMARY" else explanations

    if slide_type == "COVER":
        if not title_lines and main_title:
            title_lines = [main_title]
        if not main_title and title_lines:
            main_title = " ".join(title_lines)
        if not category_tag:
            category_tag = "#인사이트"
        legacy_type = "cover"
        content_variant = ""
        main_statement = ""
        detailed_lines = []
        prompt = str(normalized.get("image_prompt") or "").strip()
        # Reject studio/AI-glamour leftovers – force UGC everyday snaps
        low = prompt.lower()
        bad_empty = (
            not prompt
            or "annie leibovitz" in low
            or "studio lighting" in low
            or "cinematic lighting" in low
            or "vogue" in low
            or "magazine cover" in low
            or "glossy skin" in low
            or "no people" in low
            or "empty office" in low
            or "empty atrium" in low
            or "dark cinematic empty" in low
            or "moody empty" in low
        )
        if bad_empty:
            prompt = _DEFAULT_COVER_IMAGE_PROMPT
    elif slide_type == "SUMMARY":
        if len(summary_list) < SUMMARY_MIN_ITEMS:
            while len(summary_list) < SUMMARY_MIN_ITEMS:
                summary_list.append(f"실행 포인트 {len(summary_list) + 1}")
        body_points = summary_list[:SUMMARY_MAX_ITEMS]
        if not main_title:
            main_title = "핵심 체크 포인트"
        legacy_type = "summary"
        content_variant = ""
        main_statement = ""
        detailed_lines = []
        prompt = ""
    else:
        if not section_number:
            section_number = str(index)
        if not section_title:
            section_title = main_title or role.tag
        if not main_title:
            main_title = section_title
        legacy_type = "body"
        prompt = ""

    slide = CardSlide(
        tag=enforce_editor_copy(normalized.get("tag") or role.tag)[:16],
        hook=main_title.rstrip(".。…!"),
        body=body,
        badge_text=(badge or role.tag).rstrip(".。…!"),
        main_title=main_title.rstrip(".。…!"),
        slide_type=slide_type,
        body_points=body_points,
        title_lines=[t.rstrip(".。…!") for t in title_lines],
        category_tag=category_tag,
        section_number=section_number,
        section_title=section_title.rstrip(".。…!"),
        content_variant=content_variant,
        explanations=explanations,
        main_statement=main_statement,
        detailed_lines=detailed_lines,
        main_text=main_text,
        sub_point_title="",
        sub_point_text="",
        summary_list=summary_list if slide_type == "SUMMARY" else [],
        search_query="everyday desk phone snapshot" if slide_type == "COVER" else "",
        image_prompt=prompt,
        search_queries=[],
        type=legacy_type,
        visual="",
        source_fact=str(normalized.get("source_fact") or ""),
        requires_real_entity=False,
        cta="",
    )
    return slide


async def generate_card_script(
    topic: str,
    research_facts: list[str],
    selected_hook: str = "",
    reference: ReferenceInput | None = None,
) -> CardScriptResult:
    settings = get_settings()
    brand = load_brand_config()
    from modules.brand_profiles import content_mode_from_brand

    ref_block = (
        reference.summary_for_prompt()
        if reference
        else (
            "baby skin observation card news"
            if content_mode_from_brand(brand) == "parenting_care"
            else "B2B card news"
        )
    )

    logo = brand.get("brand", {}).get("name", "Authority")

    # ── Chain step 1: live web search BEFORE LLM ─────────────
    from modules.web_facts import fetch_live_web_facts

    web_bundle = await fetch_live_web_facts(topic, limit=8, days=None)
    # RSS first (news), then live web – sorted by freshness (2026 > 2025)
    rss_facts = _rank_facts(
        [f for f in research_facts if f and _is_on_topic_fact(topic, f)],
        topic=topic,
    )
    web_facts = _rank_facts(
        [f for f in web_bundle.prompt_facts(10) if _is_on_topic_fact(topic, f)],
        topic=topic,
    )
    logger.info(
        "Web fact chain provider=%s facts=%d (on-topic, fresh-priority)",
        web_bundle.provider,
        len(web_facts),
    )

    primary_facts: list[str] = []
    if len(rss_facts) >= 6:
        primary_facts = rss_facts[:8]
    else:
        for f in rss_facts + web_facts:
            if f not in primary_facts:
                primary_facts.append(f)
        if len(primary_facts) < 6:
            for f in _rank_facts(
                list(research_facts) + list(web_bundle.prompt_facts(10)),
                topic=topic,
            ):
                if f and f not in primary_facts and not re.search(
                    r"야동|주소콘|주소월드|링크모음", f
                ):
                    primary_facts.append(f)
                if len(primary_facts) >= 8:
                    break
    primary_facts = _rank_facts(primary_facts, topic=topic)[:8]
    secondary = primary_facts[3:] if len(primary_facts) > 3 else []

    if not primary_facts:
        logger.warning("No web/RSS facts – fact-driven fallback only")
        return _fallback(topic, selected_hook, research_facts, prefer_facts=research_facts)

    meta_web = web_bundle

    async def _once() -> CardScriptResult:
        data = await run_planner_llm(
            topic=topic,
            scraped_data=primary_facts[:8],
            extra_facts=secondary,
            reference_block=ref_block,
            current_year_label=CURRENT_YEAR_LABEL,
        )
        slides_raw = [s for s in (data.get("slides") or [])[:7] if isinstance(s, dict)]
        slides = [_parse_slide(s, index=i, total=7) for i, s in enumerate(slides_raw)]
        if len(slides) < 7:
            fb = _fallback(
                topic, selected_hook, primary_facts, prefer_facts=rss_facts or research_facts
            )
            slides = fb.slides[:7]
        slides = slides[:7]
        if len(slides) < 7:
            raise ValueError(f"need 7 slides, got {len(slides)}")
        if _looks_generic_marketing(slides):
            raise ValueError("generic marketing copy detected")
        bad = sum(1 for s in slides if _slide_has_bad_copy(s, primary_facts))
        if bad >= 3:
            raise ValueError(f"too many bad slides ({bad})")
        weak_facts = sum(
            1
            for i, s in enumerate(slides[:6])
            if not _slide_references_fact(s, primary_facts[i] if i < len(primary_facts) else "")
        )
        if weak_facts >= 4:
            raise ValueError(f"slides missing fact signals ({weak_facts})")
        spam_blob = " ".join(f"{s.hook} {s.body}" for s in slides)
        if re.search(r"야동|주소콘|주소월드|링크모음|성인사이트", spam_blob):
            raise ValueError("off-topic spam content in slides")
        slides = [s for s in slides if len(re.sub(r"[*\s]", "", s.hook)) >= 4]
        if len(slides) < 7:
            raise ValueError("too few slides after scrub")
        for i, s in enumerate(slides):
            role = slide_role(i)
            st = str(s.slide_type or "").upper().strip()
            if st in {"TITLE"}:
                st = "COVER"
            elif st in {"DETAIL", "QUOTE", "BODY"}:
                st = "CONTENT"
            if st not in SLIDE_TYPES:
                st = default_slide_type(i)
            if i == 0:
                st = "COVER"
            elif i == 6:
                st = "SUMMARY"
            else:
                st = "CONTENT"
            s.slide_type = st
            s.type = "cover" if st == "COVER" else ("summary" if st == "SUMMARY" else "body")
            s.tag = enforce_editor_copy(s.tag or role.tag)
            if not (s.main_title or "").strip():
                s.main_title = s.hook or role.tag
            s.hook = s.main_title
            if st == "COVER":
                if not s.title_lines:
                    s.title_lines = [s.main_title]
                s.title_lines = _punch_up_cover_lines(
                    topic, selected_hook or s.hook or s.main_title, list(s.title_lines)
                )
                s.main_title = " ".join(s.title_lines)
                s.hook = s.main_title
                if not s.category_tag:
                    s.category_tag = "#인사이트"
                if not s.image_prompt:
                    s.image_prompt = _DEFAULT_COVER_IMAGE_PROMPT
                else:
                    low = s.image_prompt.lower()
                    if (
                        "annie leibovitz" in low
                        or "studio lighting" in low
                        or "cinematic lighting" in low
                        or "vogue" in low
                        or "no people" in low
                        or "empty office" in low
                        or "dark cinematic empty" in low
                        or "moody empty" in low
                    ):
                        s.image_prompt = _DEFAULT_COVER_IMAGE_PROMPT
            elif st == "CONTENT":
                if not s.section_number:
                    s.section_number = str(i)
                if not s.section_title:
                    s.section_title = (s.main_title or "").rstrip(".。…!")
                variant = str(s.content_variant or "").strip().upper()
                if variant not in {"A", "B"}:
                    variant = "B" if (s.main_statement and s.detailed_lines) else "A"
                # Rhythm fallback: odd content slides A, even B when LLM forgot
                if variant == "A" and not s.explanations and s.main_statement and s.detailed_lines:
                    variant = "B"
                s.content_variant = variant
                if variant == "B":
                    if not s.main_statement:
                        s.main_statement = clip_detail_line(
                            s.section_title or s.main_title or "", max_chars=28
                        )
                    s.detailed_lines = [
                        clip_detail_line(x) for x in (s.detailed_lines or []) if str(x).strip()
                    ]
                    if not s.detailed_lines and s.explanations:
                        s.detailed_lines = [clip_detail_line(x) for x in s.explanations if x]
                    while len(s.detailed_lines) < DETAIL_MIN_LINES:
                        s.detailed_lines.append(
                            ["핵심 수치 먼저 확인", "제출 서류 목록 정리", "마감 일정 캘린더 고정"][
                                len(s.detailed_lines) % 3
                            ]
                        )
                    s.detailed_lines = [x for x in s.detailed_lines if x][:DETAIL_MAX_LINES]
                    s.detailed_lines = _ensure_star_emphasis(s.detailed_lines)
                    s.explanations = []
                    s.main_text = "\n".join([s.main_statement, *s.detailed_lines]).strip()
                    s.body = s.main_text
                    s.body_points = []
                else:
                    s.content_variant = "A"
                    s.main_statement = ""
                    s.detailed_lines = []
                    if not s.explanations:
                        blob = s.main_text or s.body or s.main_title or ""
                        s.explanations = [
                            clip_explanation_line(x.strip().rstrip(".。…!"))
                            for x in re.split(r"[\n.。]", blob)
                            if x.strip()
                        ]
                        s.explanations = [x for x in s.explanations if x][:3]
                    if len(s.explanations) < 2:
                        s.explanations = [
                            clip_explanation_line(s.section_title or s.main_title or ""),
                            "숫자·기한부터 오늘 확인",
                        ]
                    s.explanations = [
                        clip_explanation_line(x.rstrip(".。…!")) for x in s.explanations[:3]
                    ]
                    s.explanations = [x for x in s.explanations if x]
                    s.explanations = _ensure_star_emphasis(s.explanations)
                    s.main_text = "\n".join(s.explanations)
                    s.body = s.main_text
                    s.body_points = list(s.explanations)
                s.sub_point_title = ""
                s.sub_point_text = ""
                s.image_prompt = ""
            else:
                if not s.summary_list:
                    s.summary_list = list(s.body_points or [])[:SUMMARY_MAX_ITEMS]
                while len(s.summary_list) < SUMMARY_MIN_ITEMS:
                    s.summary_list.append(f"실행 포인트 {len(s.summary_list) + 1}")
                s.summary_list = [
                    clip_detail_line(x.rstrip(".。…!"), max_chars=SUMMARY_ITEM_MAX_CHARS)
                    for x in s.summary_list[:SUMMARY_MAX_ITEMS]
                ]
                s.summary_list = [x for x in s.summary_list if x]
                s.summary_list = _ensure_star_emphasis(s.summary_list)
                s.body_points = s.summary_list
                s.main_title = (s.main_title or "핵심 체크 포인트").rstrip(".。…!")
                s.image_prompt = ""
            if i < len(primary_facts):
                s.source_fact = primary_facts[i]
            s.requires_real_entity = False
            s.cta = ""
        return CardScriptResult(
            hook=enforce_editor_copy(
                _scrub_non_korean_noise(str(data.get("hook") or selected_hook or topic))
            ),
            cta="",
            slides=slides,
            source=f"gpt-4o-mini+{meta_web.provider}",
        )

    try:
        result = await _once()
    except OpenAINotConfiguredError:
        logger.warning("OpenAI key missing – editorial fallback")
        result = _fallback(
            topic, selected_hook, primary_facts, prefer_facts=rss_facts or research_facts
        )
        result.source = f"editorial_fallback+{meta_web.provider}"
    except OpenAIRateLimitError as exc:
        logger.warning("OpenAI rate limit – editorial fallback (%s)", exc)
        result = _fallback(
            topic, selected_hook, primary_facts, prefer_facts=rss_facts or research_facts
        )
        result.source = f"editorial_fallback+{meta_web.provider}"
    except Exception as exc:  # noqa: BLE001
        logger.warning("Card script attempt1 failed (%s) – retry", exc)
        try:
            result = await _once()
        except Exception as exc2:  # noqa: BLE001
            logger.warning("Card script attempt2 failed (%s) – editorial fallback", exc2)
            result = _fallback(
                topic, selected_hook, primary_facts, prefer_facts=rss_facts or research_facts
            )
            result.source = f"editorial_fallback+{meta_web.provider}"

    result._web_facts = meta_web  # type: ignore[attr-defined]
    return result
