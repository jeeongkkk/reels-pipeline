"""Build relevance-aware image search queries for card-news slides.

Stock APIs (Pexels) return random aesthetic shots when the query is vague
("Korea", "agreement", "transfer"). We expand each slide into concrete
news-visual English queries and score results by alt-text overlap.
"""

from __future__ import annotations

import re
from typing import Any

CORPORATE_BG_BASE = (
    "ultra-premium magazine editorial photography, award-winning commercial shot, "
    "highly detailed, sophisticated color grading, "
    "9:16 vertical portrait, no text, no watermark"
)

CORPORATE_BG_NEGATIVE = (
    "3D render, CGI, cartoon, plastic textures, stock photo look, "
    "abstract dark background, empty black void, geometric lines only, "
    "flat gradient, minimalist empty frame, stock handshake, "
    "whiteboard, smiling laptop stock photo, text, watermark, logo"
)

# Markers that mean the prompt already carries a magazine editorial lane
_MAGAZINE_STYLE_MARKERS = (
    "magazine",
    "vogue",
    "wired",
    "forbes",
    "monocle",
    "editorial style",
    "hasselblad",
    "ultra-premium magazine",
)

# Topic → (lane label, style phrase embedded in image_prompt)
_MAGAZINE_LANES: list[tuple[re.Pattern[str], str, str]] = [
    (
        re.compile(
            r"뷰티|화장품|크림|세럼|향수|용기|패키지|스킨케어|메이크업|브랜드\s*기획|럭셔리"
        ),
        "Vogue beauty",
        "Vogue magazine beauty editorial style, macro shot of elegant cosmetic textures, "
        "soft glowing light, flawless aesthetic, no people",
    ),
    (
        re.compile(
            r"AI|인공지능|테크|IT|솔루션|소프트웨어|앱|플랫폼|디지털|AX|반도체|칩|SaaS|클라우드|메모리|GDDR",
            re.I,
        ),
        "Wired tech",
        "Wired magazine style, sleek modern tech still life, precision hardware macro, "
        "cool neon ambient light, no people, futuristic but realistic",
    ),
    (
        re.compile(r"패션|의류|런웨이|컬렉션|룩북"),
        "Vogue fashion",
        "Vogue magazine fashion editorial style, refined garment fabric macro texture, "
        "dramatic soft fashion lighting, no people faces",
    ),
    (
        re.compile(r"음식|푸드|카페|레스토랑|미식|요리"),
        "Bon Appétit food",
        "Bon Appétit magazine food editorial style, appetizing real ingredients macro, "
        "warm natural food lighting, sophisticated plating, no people",
    ),
    (
        re.compile(r"건축|인테리어|공간|오피스\s*디자인|부동산"),
        "Architectural Digest",
        "Architectural Digest magazine style, refined interior architecture, "
        "natural luxury daylight, empty sophisticated space, no people",
    ),
    (
        re.compile(
            r"지원|공고|정책|예산|정부|바우처|수출|경영|B2B|중소|창업|벤처|투자|펀드|IR"
        ),
        "Forbes/Monocle conceptual",
        "Forbes or Monocle magazine style, minimalist corporate still life, "
        "brushed metal and glass desk objects, warm professional daylight, "
        "empty refined office atrium, no people",
    ),
]

_DEFAULT_LANE = (
    "Monocle conceptual",
    "Monocle magazine style, refined architectural interior detail, "
    "warm natural daylight, empty sophisticated space, no people",
)

# (korean pattern, english search candidates) – ordered best-first
_TOPIC_MAP: list[tuple[re.Pattern[str], list[str]]] = [
    (
        re.compile(r"정부\s*지원|지원\s*사업|창업\s*지원|보조금|중소벤처|소상공인|사업\s*공고|선정|바우처"),
        [
            "business documents desk dark office",
            "corporate policy paperwork close up",
            "startup office documentary photography",
            "financial paperwork dark editorial",
            "modern business presentation screen",
        ],
    ),
    (
        re.compile(r"특검|선관위|여야|국회|총선|대선|정치|검찰|법원"),
        [
            "abstract dark marble columns editorial",
            "conceptual scales of justice macro dark",
            "dark editorial abstract architecture geometry",
        ],
    ),
    (
        re.compile(r"이적|축구|FC|포르투|손흥민|황인범|선수|월드컵|프리미어리그"),
        [
            "professional football soccer stadium match",
            "soccer player press conference jersey",
            "football transfer news stadium",
            "korean soccer national team",
        ],
    ),
    (
        re.compile(r"공습|이란|미사일|전쟁|군사|군대|중동|이스라엘|하마스"),
        [
            "military fighter jet airstrike",
            "middle east conflict news",
            "war zone smoke explosion",
            "defense missile launch",
        ],
    ),
    (
        re.compile(r"반도체|AI\s*칩|엔비디아|nvidia|칩스|파운드리|스마트제조"),
        [
            "semiconductor wafer factory cleanroom",
            "AI chip circuit board close up",
            "smart factory robotics industrial",
            "microchip manufacturing",
        ],
    ),
    (
        re.compile(r"태양광|에너지\s*전환|재생에너지"),
        [
            "solar panel industrial plant",
            "renewable energy factory korea",
            "solar farm documentary photo",
        ],
    ),
    (
        re.compile(r"IPO|상장|주식|증시|코스피|나스닥|레버리지|투자|펀드|증권"),
        [
            "stock market trading floor charts",
            "candlestick stock chart screen",
            "wall street traders monitors",
            "financial investment graph",
        ],
    ),
    (
        re.compile(r"삼성|삼전|닉스|nike|애플|구글|메타|테슬라|빅테크"),
        [
            "tech company headquarters modern",
            "silicon valley office buildings",
            "smartphone technology close up",
            "corporate boardroom meeting",
        ],
    ),
    (
        re.compile(r"코드|해킹|보안|유출|털린|사이버|개발자"),
        [
            "computer code hacking dark screen",
            "cybersecurity lock digital",
            "developer coding laptop night",
            "data breach server room",
        ],
    ),
    (
        re.compile(r"마케팅|스타트업|창업|투자제안|IR|브랜드"),
        [
            "korean startup office documentary",
            "entrepreneurs pitch meeting korea",
            "seoul startup campus coworking",
            "business presentation charts editorial",
        ],
    ),
    (
        re.compile(r"트럼프|네타냐후|바이든|대통령|백악관"),
        [
            "political leaders handshake press",
            "white house press briefing",
            "world leaders summit conference",
            "diplomat meeting formal",
        ],
    ),
]

# Stock-photo cliché queries – never trust these as primary search
_STOCK_CLICHE_QUERIES = {
    "startup founder portrait",
    "laptop typing office",
    "whiteboard brainstorm team",
    "marketing meeting closeup",
    "business handshake office",
    "handshake",
    "business office",
    "marketing analytics dashboard",
    "Authority Reels",
}

# Alt-text / query words that mean the photo is almost certainly wrong
_REJECT_WORDS = {
    "wedding",
    "bride",
    "groom",
    "hanbok",
    "bridal",
    "fashion model",
    "lingerie",
    "swimsuit",
    "yoga",
    "food platter",
    "dessert",
    "cat",
    "dog pet",
    "baby shower",
}

# Stock aesthetic + literal public buildings that kill B2B trust
_NEWS_REJECT = _REJECT_WORDS | {
    "romantic",
    "couple kiss",
    "flower bouquet",
    "vintage portrait",
    "victorian",
    "ruffle",
    "garden aesthetic",
    "handshake deal",
    "business handshake",
    "stock photo",
    "posing for camera",
    "fake smile meeting",
    # Police / literal gov buildings (자주 지원사업 주제에 잘못 매칭됨)
    "police",
    "policeman",
    "police station",
    "precinct",
    "sheriff",
    "patrol",
    "law enforcement",
    "파출소",
    "경찰",
    "경찰서",
    "소방서",
    "fire station",
    "military base",
    "army barracks",
}

# Never use these as search queries
_BANNED_SEARCH_FRAGMENTS = (
    "government building",
    "ministry briefing",
    "press conference",
    "police",
    "parliament",
    "national assembly",
    "handshake",
    "whiteboard",
    "laptop typing",
    "founder portrait",
)


def _strip_md(text: str) -> str:
    return re.sub(r"[*_`\[\]]+", "", text or "").strip()


def _topic_queries(text: str) -> list[str]:
    out: list[str] = []
    for pattern, queries in _TOPIC_MAP:
        if pattern.search(text):
            for q in queries:
                if q not in out:
                    out.append(q)
    return out


def _person_or_org_queries(hook: str, body: str) -> list[str]:
    """Pull highlighted **terms** and obvious Latin names into search queries."""
    blob = f"{hook} {body}"
    out: list[str] = []
    for m in re.finditer(r"\*\*(.+?)\*\*", hook or ""):
        term = _strip_md(m.group(1))
        if len(term) >= 2:
            # Keep Korean proper nouns as-is (Google CSE) + english-ish hint
            out.append(term)
            out.append(f"{term} news")
    # Latin names / orgs already in text
    for m in re.finditer(r"[A-Za-z][A-Za-z0-9.\-]{2,}(?:\s+[A-Za-z][A-Za-z0-9.\-]{1,})*", blob):
        name = m.group(0).strip()
        if name.lower() in {"ai", "ipo", "fc", "ir", "cta", "b2b"}:
            continue
        if name not in out:
            out.append(name)
    return out[:6]


def build_query_candidates(slide: dict[str, Any]) -> list[str]:
    """Ordered English(+entity) search queries for one slide."""
    tag = str(slide.get("tag") or "")
    hook = str(slide.get("hook") or "")
    body = str(slide.get("body") or "")
    primary = str(slide.get("search_query") or slide.get("visual") or "").strip()
    alt_queries = slide.get("search_queries") or []
    if isinstance(alt_queries, str):
        alt_queries = [alt_queries]

    korean_blob = f"{tag} {_strip_md(hook)} {_strip_md(body)}"
    candidates: list[str] = []

    def _add(q: str) -> None:
        q = re.sub(r"\s+", " ", (q or "").strip())
        if len(q) < 3:
            return
        low = q.lower()
        if low in {"authority reels", "authority", "logo", "brand"}:
            return
        if low in _STOCK_CLICHE_QUERIES:
            return
        for cliche in ("founder portrait", "laptop typing", "whiteboard brainstorm", "business handshake"):
            if cliche in low:
                return
        if any(b in low for b in _BANNED_SEARCH_FRAGMENTS):
            return
        if q not in candidates:
            candidates.append(q)

    # Prefer slide's own abstract queries first
    _add(primary)
    for q in alt_queries:
        _add(str(q))

    for q in _topic_queries(korean_blob):
        _add(q)

    for q in _person_or_org_queries(hook, body):
        _add(q)

    if re.search(r"지원|정책|공고|바우처|창업|중소", korean_blob):
        _add("business documents desk dark office")
        _add("corporate paperwork close up editorial")
    elif "정치" in tag:
        _add("dark marble columns architecture editorial")
    elif "기업" in tag or "투자" in tag:
        _add("stock market trading screens dark")
    elif "국제" in tag:
        _add("world map globe light editorial")
    elif "마케팅" in tag or "창업" in tag:
        _add("startup coworking office documentary")

    if not candidates:
        candidates.append("corporate office documentary photography")

    return candidates[:8]


def resolve_magazine_lane(text: str) -> tuple[str, str]:
    """Return (lane_label, style_phrase) from slide/topic copy."""
    blob = text or ""
    for pattern, label, phrase in _MAGAZINE_LANES:
        if pattern.search(blob):
            return label, phrase
    return _DEFAULT_LANE


def slide_content_blob(slide: dict[str, Any]) -> str:
    return " ".join(
        str(slide.get(k) or "")
        for k in (
            "tag",
            "badge_text",
            "hook",
            "main_title",
            "body",
            "source_fact",
            "search_query",
        )
    ) + " " + " ".join(str(p) for p in (slide.get("body_points") or [])[:4])


def build_slide_bg_prompt(slide: dict[str, Any], *, index: int = 0) -> str:
    """Topic-adaptive premium magazine editorial — conceptual macro/architecture, no stock people."""
    tag = str(slide.get("tag") or "")
    custom = str(slide.get("image_prompt") or "").strip()
    blob = slide_content_blob(slide)
    _lane_label, style_phrase = resolve_magazine_lane(blob)

    # Rotating conceptual subjects so 7 slides never share the same scene
    conceptual_pool = (
        "macro close-up of brushed aluminum and frosted glass architectural joint",
        "empty sunlit atrium with polished concrete floor and long window shadows",
        "minimalist walnut desk still life with fountain pen and sealed documents, no people",
        "precision semiconductor wafer and circuit macro under cool teal light",
        "towering glass facade reflection of sky, Architectural Digest empty exterior",
        "server-rack LED bokeh and cable texture, Wired still life, no faces",
        "monochrome marble lobby column detail with soft volumetric daylight",
    )
    subject = conceptual_pool[index % len(conceptual_pool)]

    if re.search(r"뷰티|화장품|크림|세럼|향수|용기|패키지|스킨케어", blob):
        beauty = (
            "macro elegant cosmetic cream texture on marble, no people",
            "glass perfume bottle refraction still life, soft glow, no people",
            "luxury packaging edge and ribbon macro, flawless aesthetic, no people",
        )
        subject = beauty[index % len(beauty)]
    elif re.search(r"탄소|친환경|태양광|에너지|ESG", blob):
        subject = (
            "macro solar panel texture and brushed metal frame, "
            "premium sustainability still life, no people"
        )
    elif re.search(r"지원|공고|정책|예산|정부|바우처|수출", blob):
        corp = (
            "minimalist still life of sealed documents and fountain pen on walnut, no people",
            "empty refined atrium with glass balustrade and daylight, no people",
            "brushed steel nameplate and folder stack still life, warm daylight, no people",
        )
        subject = corp[index % len(corp)]
    elif re.search(r"반도체|AI|칩|디지털|AX|테크|IT|솔루션|메모리|GDDR", blob, re.I):
        tech = (
            "macro precision memory chip and glowing PCB traces, cool neon, no people",
            "server-rack LED bokeh and cable texture still life, no faces",
            "wafer edge macro under teal ambient light, Wired still life, no people",
            "empty glass tech atrium with cool reflections, no people",
        )
        subject = tech[index % len(tech)]
    elif re.search(r"투자|펀드|주식|금융|IR", blob):
        subject = (
            "macro of trading screen reflection on polished glass desk, "
            "empty finance loft, no people"
        )
    elif re.search(r"해석|대응|전략|요약|도입", tag):
        subject = (
            "glass strategy wall with soft marker lines in empty Monocle office, "
            "warm daylight, no people"
        )
    else:
        subject = conceptual_pool[index % len(conceptual_pool)]

    custom_low = custom.lower()
    banned_custom = any(
        b in custom_low
        for b in (
            "abstract dark",
            "geometric empty",
            "3d render",
            "cgi",
            "cartoon",
            "photojournalism",
            "documentary style",
            "smiling",
            "handshake",
            "businessman",
            "businesswoman",
            "men in suits",
            "people in suits",
            "executive portrait",
            "celebrating",
            "cheering",
        )
    )
    if custom and not banned_custom and len(custom) >= 24:
        base = re.sub(
            r"(?i)(?:high-end photojournalism|documentary style photography|"
            r"documentary photojournalism|candid documentary|candid moment|"
            r"raw and authentic|shot on Arri Alexa 65|"
            r"candid but polished executive portrait|"
            r"polished Korean executive[^,]*|"
            r"founders in[^,]*|"
            r"business people[^,]*)[^,]*,?\s*",
            "",
            custom,
        ).strip(" ,")
        if not has_magazine_style(base):
            base = f"{style_phrase}, {base}"
        if "no people" not in base.lower():
            base = f"{base}, no people, no faces"
    else:
        base = f"{style_phrase}, {subject}"
    return (
        f"{base}, photorealistic magazine editorial, conceptual still life or architecture, "
        f"no 3D render, no posed models, unique scene {index + 1}"
    )


def has_magazine_style(prompt: str) -> bool:
    low = (prompt or "").lower()
    return any(m in low for m in _MAGAZINE_STYLE_MARKERS)


def has_documentary_style(prompt: str) -> bool:
    """Backward-compatible alias → magazine style check."""
    return has_magazine_style(prompt)


def build_image_prompt(slide: dict[str, Any]) -> str:
    """Alias – content-aware magazine editorial prompt."""
    return build_slide_bg_prompt(slide, index=0)


def pexels_queries_from_slide(slide: dict[str, Any], *, index: int = 0) -> list[str]:
    """Derive unique English Pexels queries from each slide's image_prompt."""
    prompt = str(slide.get("image_prompt") or "").strip()
    candidates: list[str] = []

    def _add(q: str) -> None:
        q = re.sub(r"\s+", " ", (q or "").strip())
        if len(q) >= 6 and q not in candidates:
            candidates.append(q[:90])

    if prompt:
        subject = prompt
        for strip_pat in (
            r"High-end editorial photography[^,]*,\s*",
            r"ultra-premium magazine editorial photography[^,]*,\s*",
            r"award-winning commercial shot[^,]*,\s*",
            r"high-end photojournalism[^,]*,\s*",
            r"documentary style photography[^,]*,\s*",
            r"candid moment[^,]*,\s*",
            r"highly detailed[^,]*,\s*",
            r"raw and authentic[^,]*,\s*",
            r"shot on Arri Alexa 65[^,]*,\s*",
            r"shot on medium format camera \(Hasselblad\)[^,]*,\s*",
            r"85mm lens[^,]*,\s*",
            r"natural ambient lighting[^,]*,\s*",
            r"ultra-realistic texture[^,]*,\s*",
            r"vivid natural colors[^,]*,\s*",
            r"sophisticated color grading[^,]*,\s*",
            r"perfect composition[^,]*,\s*",
            r"8k resolution[^,]*,\s*",
            r"NO stock photo look[^,]*,\s*",
            r"NO plastic textures[^,]*,\s*",
            r"NO posed models[^,]*,\s*",
            r"NO artificial studio lighting[^,]*,\s*",
            r"hyper-realistic[^,]*,\s*",
            r"shallow depth of field[^,]*,\s*",
            r"cinematic studio lighting[^,]*,\s*",
            r"photorealistic[^,]*,\s*",
            r"8k detail[^,]*,\s*",
            r"no text[^,]*,\s*",
            r"no watermark[^,]*,\s*",
            r"no 3D render[^,]*,\s*",
            r"9:16[^,]*",
            r"vertical portrait[^,]*",
            r"variant \d+[^,]*",
            r"composition leaves lower third darker for typography[^,]*",
        ):
            subject = re.sub(strip_pat, "", subject, flags=re.I)
        subject = subject.strip(" ,")
        if len(subject) >= 8:
            _add(subject)
        # Short keyword chunks for stock search
        for chunk in re.split(r",\s*", subject):
            chunk = chunk.strip()
            if len(chunk) >= 8:
                _add(chunk)

    hook = _strip_md(str(slide.get("main_title") or slide.get("hook") or ""))
    if re.search(r"[A-Za-z]{4,}", hook):
        _add(hook[:60])

    for q in build_query_candidates(slide):
        _add(q)

    # Slide-index variety suffix
    variety = [
        "office documentary",
        "business meeting editorial",
        "startup workspace",
        "financial documents desk",
        "technology close up",
        "manufacturing facility",
        "strategy planning session",
    ]
    _add(variety[index % len(variety)])

    return candidates[:6] or ["corporate office documentary photography"]


def score_photo_relevance(
    *,
    alt: str,
    query: str,
    url: str = "",
    photographer: str = "",
) -> float:
    """Higher = better match. Negative = should skip."""
    hay = f"{alt} {url} {photographer}".lower()
    q_words = [w for w in re.split(r"[^a-z0-9]+", query.lower()) if len(w) >= 3]

    # Hard reject irrelevant aesthetics
    for bad in _NEWS_REJECT:
        if bad in hay:
            return -10.0

    score = 0.0
    for w in q_words:
        if w in hay:
            score += 2.5
        # partial
        elif any(w in token for token in hay.split() if len(token) > 3):
            score += 0.8

    # Prefer photos that at least have some alt text
    if alt and len(alt) > 8:
        score += 0.5
    else:
        score -= 0.5

    return score


def is_serious_news_slide(slide: dict[str, Any]) -> bool:
    text = f"{slide.get('tag','')} {slide.get('hook','')} {slide.get('body','')}"
    return bool(
        re.search(
            r"특검|정치|공습|전쟁|이적|투자|주식|해킹|유출|대통령|국회|군사|"
            r"지원|공고|선정|보조금|중소|정책|정부",
            text,
        )
    )
