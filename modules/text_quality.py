"""Text quality helpers – Korean-only enforcement and cleanup."""

from __future__ import annotations

import re

# CJK Unified Ideographs (Chinese / Hanja) – strip from Korean reels copy
_HANJA_RE = re.compile(r"[\u3400-\u4DBF\u4E00-\u9FFF\uF900-\uFAFF]+")
# Keep Hangul, ASCII, digits, common punctuation/spacing
_ALLOWED_RE = re.compile(
    r"[^"
    r"\uAC00-\uD7A3"  # Hangul syllables
    r"\u1100-\u11FF"  # Jamo
    r"\u3130-\u318F"  # Compatibility Jamo
    r"a-zA-Z0-9"
    r"\s"
    r".,!?;:'\"%\-–—~/()\[\]…·･・*"
    r"]+"
)


# Common LLM Hanja leaks → Korean replacements
_HANJA_REPLACEMENTS = {
    "提高": "높이는",
    "戦略": "전략",
    "戰略": "전략",
    "成功": "성공",
    "開始": "시작",
    "利用": "활용",
    "重要": "중요",
    "確認": "확인",
}


def strip_hanja_and_cjk(text: str) -> str:
    """Remove/replace Chinese/Hanja characters that often leak from LLMs."""
    text = text or ""
    for src, dst in _HANJA_REPLACEMENTS.items():
        text = text.replace(src, dst)
    text = _HANJA_RE.sub("", text)
    return re.sub(r"\s+", " ", text).strip()


def fix_korean_spacing(text: str) -> str:
    """Light spacing repairs for common LLM / ASR glues (not a full spellchecker)."""
    text = text or ""
    text = re.sub(r"\s+", " ", text).strip()
    # Glue number + unit first: "2.3 배" / "2026 년"
    text = re.sub(r"(\d+(?:\.\d+)?)\s*(배|년|%|원|명|개|회)", r"\1\2", text)
    # Then split unit from following verb: "2.3배높이는" → "2.3배 높이는"
    text = re.sub(r"(\d+(?:\.\d+)?배)(?=[가-힣])", r"\1 ", text)
    # Latin ↔ Hangul
    text = re.sub(r"([A-Za-z])(?=[가-힣])", r"\1 ", text)
    text = re.sub(r"([가-힣])(?=[A-Za-z])", r"\1 ", text)
    # Common glued verb fragments from LLM
    text = re.sub(r"(높이는)(할\s*수)", r"\1 \2", text)
    text = re.sub(r"(할)(수)", r"\1 \2", text)
    text = re.sub(r"(수)(있는)", r"\1 \2", text)
    # CTR / decimal glitches: "CTR2. 300" / "2.300배"
    text = re.sub(r"CTR\s*2\.\s*3+", "CTR 2.3", text, flags=re.I)
    text = re.sub(r"(\d)\.\s*(\d)", r"\1.\2", text)
    text = re.sub(r"(\d\.\d)\d+(배)", r"\1\2", text)
    text = re.sub(r"\s+", " ", text).strip()
    text = re.sub(r"\s+([,.!?…])", r"\1", text)
    return text


# Literary / textbook endings → spoken 해요체
_LITERARY_REPLACEMENTS = [
    (r"예상됩니다\.?", "예상돼요."),
    (r"중요합니다\.?", "중요해요."),
    (r"필요합니다\.?", "필요해요."),
    (r"가능합니다\.?", "가능해요."),
    (r"있습니다\.?", "있어요."),
    (r"없습니다\.?", "없어요."),
    (r"입니다\.?", "이에요."),
    (r"합니다\.?", "해요."),
    (r"됩니다\.?", "돼요."),
    (r"것입니다\.?", "거예요."),
    (r"할 수 있습니다\.?", "할 수 있어요."),
    (r"높히는", "높이는"),
]


def conversationalize_korean(text: str) -> str:
    """Push LLM copy toward spoken Instagram creator tone."""
    text = fix_korean_spacing(text or "")
    for pattern, repl in _LITERARY_REPLACEMENTS:
        text = re.sub(pattern, repl, text)
    # After literary fixes, re-space common glues
    text = re.sub(r"(높이는)(할)", r"\1 \2", text)
    text = re.sub(r"(할)(수)(있는)", r"\1 \2 \3", text)
    text = re.sub(r"(할)\s*(수)\s*(있는)", r"\1 \2 \3", text)
    return re.sub(r"\s+", " ", text).strip()


def enforce_korean_copy(text: str) -> str:
    """Clean LLM output for on-screen / spoken Korean copy."""
    text = strip_hanja_and_cjk(text)
    text = _ALLOWED_RE.sub(" ", text)
    text = fix_korean_spacing(text)
    text = conversationalize_korean(text)
    return text


def split_caption_chunks(text: str, max_chars: int = 14) -> list[str]:
    """Split copy into short on-screen lines, keeping Korean word spaces."""
    text = enforce_korean_copy(text)
    if not text:
        return []

    pieces = re.split(r"(?<=[.!?…?])\s+", text)
    chunks: list[str] = []

    def _flush_words(part: str) -> None:
        words = part.split(" ")
        buf = ""
        for w in words:
            if not w:
                continue
            cand = f"{buf} {w}".strip() if buf else w
            if len(cand) <= max_chars:
                buf = cand
                continue
            if buf:
                chunks.append(buf)
            while len(w) > max_chars:
                chunks.append(w[:max_chars])
                w = w[max_chars:]
            buf = w
        if buf:
            chunks.append(buf)

    for part in pieces:
        part = part.strip()
        if not part:
            continue
        if len(part) <= max_chars:
            chunks.append(part)
        else:
            _flush_words(part)
    return chunks


def sanitize_hook(text: str, max_chars: int = 28) -> str:
    text = enforce_korean_copy(text)
    text = text.strip("\"'`")
    if len(text) > max_chars:
        text = text[: max_chars - 1].rstrip() + "…"
    return text


def sanitize_script_fields(data: dict) -> dict:
    """Sanitize hook/body/cta/caption_lines in script JSON-like dict."""
    out = dict(data)
    if "hook" in out:
        out["hook"] = enforce_korean_copy(str(out.get("hook") or ""))
    if "cta" in out:
        out["cta"] = enforce_korean_copy(str(out.get("cta") or ""))
    if "body" in out:
        # Keep visual markers intact – sanitize around them
        body = str(out.get("body") or "")
        parts = re.split(r"(\[\d+\.\s*Visual:[^\]]+\])", body)
        cleaned = []
        for p in parts:
            if re.match(r"\[\d+\.\s*Visual:", p or ""):
                cleaned.append(p)
            else:
                cleaned.append(enforce_korean_copy(p))
        out["body"] = " ".join(x for x in cleaned if x).strip()
    if "full_text" in out:
        out["full_text"] = enforce_korean_copy(str(out.get("full_text") or ""))
    if "caption_lines" in out and isinstance(out["caption_lines"], list):
        out["caption_lines"] = [enforce_korean_copy(str(x)) for x in out["caption_lines"] if str(x).strip()]
    return out


KOREAN_ONLY_RULE = (
    "모든 한국어 문장은 반드시 한글만 사용하세요. "
    "중국어·일본어·한자(예: 提高, 戦略) 절대 금지. "
    "숫자와 영문 고유명사(CTR, AI, B2B, Instagram)만 예외로 허용. "
    "한국어 띄어쓰기를 반드시 지키세요 (예: '여러분 팀에서도', 'CTR 2.3배 높이는')."
)

SPOKEN_STYLE_RULE = (
    "절대 문어체·교과서체를 쓰지 마세요. "
    "인스타 크리에이터처럼 친근한 구어체(해요체/해체)만 사용. "
    "금지 예시: '~입니다', '~합니다', '~예상됩니다', '~중요합니다', '~것입니다'. "
    "권장 예시: '~이에요', '~해요', '~예상돼요', '~중요해요', '~거예요'. "
    "숫자·단위는 'CTR 2.3배'처럼 정확히 띄어 쓰고 문법 오류 없이 최종 검수하세요. "
    "호흡을 위해 문장을 짧게 끊고, 말하듯 자연스러운 리듬으로 쓰세요."
)

CONSULTANT_STYLE_RULE = (
    "당신은 B2B 기업 대표를 컨설팅하는 최고급 전략 기획자입니다. "
    "전문적인 경어체·비즈니스 카피라이팅만 사용하세요. "
    "예: '대표님, 2026년 트렌드는 이것입니다', '더 이상 헛돈 쓰지 마십시오'. "
    "해요체(~해요, ~이에요)는 쓰지 말고, '~입니다', '~하십시오', '~마십시오'를 쓰세요. "
    "검색 원문·웹 제목·포털 메뉴·TikTok/YouTube 광고 문구는 절대 복사하지 마세요."
)

EDITOR_STYLE_RULE = (
    "당신은 탑티어 B2B 마케터이자 인사이트 컨설턴트입니다. "
    "중소기업 실무자·대표에게 직접 말하듯, 숫자·사례·행동 지시로 단문을 치십시오. "
    "보도자료 요약·영혼 없는 추상어 금지: "
    "'중요하다', '기대된다', '필요성이 대두된다', '지속 가능한 성장', "
    "'중요합니다', '예상됩니다', '모색해야', '관심을 기울여야'. "
    "스크랩 팩트(숫자·기관·정책명·기업명)에 근거하되 제목 복붙 금지. "
    "출처명·언론사명·포털명을 본문에 넣지 마십시오."
)


def enforce_consultant_copy(text: str) -> str:
    """Card-news B2B consultant tone – no 해요체 conversationalize."""
    text = strip_hanja_and_cjk(text)
    text = _ALLOWED_RE.sub(" ", text)
    return fix_korean_spacing(text)


def enforce_editor_copy(text: str) -> str:
    """Editorial business-report tone – same cleanup, no conversationalize."""
    return enforce_consultant_copy(text)
