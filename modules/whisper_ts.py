"""Faster-Whisper word/phrase timestamp extraction for Module 5."""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path

from modules.utils import ensure_dir, get_logger, get_settings

logger = get_logger(__name__)


@dataclass
class WordTimestamp:
    text: str
    start: float
    end: float

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class PhraseTimestamp:
    text: str
    start: float
    end: float

    def to_dict(self) -> dict:
        return asdict(self)


def _get_whisper_model():
    from faster_whisper import WhisperModel

    settings = get_settings()
    model_size = settings.whisper_model or "base"
    # CPU-friendly defaults for Windows laptops
    logger.info("Loading Faster-Whisper model=%s", model_size)
    return WhisperModel(model_size, device="cpu", compute_type="int8")


def transcribe_words(audio_path: Path) -> list[WordTimestamp]:
    """Extract word-level timestamps from an audio file."""
    model = _get_whisper_model()
    segments, info = model.transcribe(
        str(audio_path),
        language="ko",
        word_timestamps=True,
        vad_filter=True,
    )

    words: list[WordTimestamp] = []
    for segment in segments:
        if not segment.words:
            # fallback: whole segment as one "word"
            text = (segment.text or "").strip()
            if text:
                words.append(WordTimestamp(text=text, start=float(segment.start), end=float(segment.end)))
            continue
        for w in segment.words:
            text = (w.word or "").strip()
            if not text:
                continue
            words.append(
                WordTimestamp(
                    text=text,
                    start=float(w.start),
                    end=float(w.end),
                )
            )

    logger.info(
        "Whisper done – lang=%s duration=%.1fs words=%d",
        getattr(info, "language", "?"),
        getattr(info, "duration", 0.0) or 0.0,
        len(words),
    )
    return words


def _alnum_hangul(s: str) -> str:
    return re.sub(r"[^0-9A-Za-z가-힣]", "", s or "")


def group_phrases(
    words: list[WordTimestamp],
    max_chars: int = 18,
    max_gap_sec: float = 0.55,
) -> list[PhraseTimestamp]:
    """Group Whisper words into short captions, keeping spaces between tokens."""
    if not words:
        return []

    phrases: list[PhraseTimestamp] = []
    buf: list[WordTimestamp] = [words[0]]

    def flush() -> None:
        nonlocal buf
        if not buf:
            return
        # Whisper tokens are usually word pieces without leading spaces — join with space
        text = " ".join(w.text.strip() for w in buf if w.text and w.text.strip())
        text = re.sub(r"\s+", " ", text).strip()
        phrases.append(PhraseTimestamp(text=text, start=buf[0].start, end=buf[-1].end))
        buf = []

    def _ends_sentence(token: str) -> bool:
        t = token.strip()
        if not t:
            return False
        if t[-1] in ".!?…":
            return True
        return t.endswith(("다", "요", "까", "죠", "네", "음"))

    for prev, cur in zip(words, words[1:]):
        gap = cur.start - prev.end
        tentative = " ".join(w.text.strip() for w in buf + [cur] if w.text.strip())
        tentative_len = len(tentative)
        should_break = (
            gap > max_gap_sec
            or tentative_len > max_chars
            or _ends_sentence(prev.text)
        )
        if should_break:
            flush()
            buf = [cur]
        else:
            buf.append(cur)
    flush()
    return phrases


def phrases_from_script(
    script_text: str,
    words: list[WordTimestamp],
    max_chars: int = 18,
    hook: str | None = None,
) -> list[PhraseTimestamp]:
    """Use script wording (with spaces) + Whisper timeline for stable timing."""
    from modules.text_quality import enforce_korean_copy, split_caption_chunks

    script_text = enforce_korean_copy(script_text or "")
    hook_clean = enforce_korean_copy(hook or "")
    if hook_clean and script_text.startswith(hook_clean):
        rest = script_text[len(hook_clean) :].strip()
        chunks = [hook_clean] + split_caption_chunks(rest, max_chars=max_chars)
    else:
        chunks = split_caption_chunks(script_text, max_chars=max_chars)
    if not chunks:
        return group_phrases(words, max_chars=max_chars)
    if not words:
        dur = 2.2
        out: list[PhraseTimestamp] = []
        t = 0.0
        for c in chunks:
            out.append(PhraseTimestamp(text=c, start=t, end=t + dur))
            t += dur
        return out

    # Proportional timing avoids ASR↔script char-count drift (e.g. Whisper mishears)
    weights = [max(len(_alnum_hangul(c)), 1) for c in chunks]
    total_w = float(sum(weights))
    t0 = float(words[0].start)
    t1 = float(words[-1].end)
    span = max(t1 - t0, 0.5)

    phrases: list[PhraseTimestamp] = []
    cursor = 0.0
    for chunk, w in zip(chunks, weights):
        start = t0 + span * (cursor / total_w)
        cursor += w
        end = t0 + span * (cursor / total_w)
        # Snap to nearby word edges for slightly tighter sync
        start = _snap_time(start, words, prefer="start")
        end = max(_snap_time(end, words, prefer="end"), start + 0.65)
        phrases.append(PhraseTimestamp(text=chunk, start=start, end=end))

    # Ensure monotonic, non-overlapping lightly
    for i in range(1, len(phrases)):
        if phrases[i].start < phrases[i - 1].end:
            mid = (phrases[i - 1].end + phrases[i].start) / 2
            phrases[i - 1] = PhraseTimestamp(
                text=phrases[i - 1].text,
                start=phrases[i - 1].start,
                end=max(mid, phrases[i - 1].start + 0.5),
            )
            phrases[i] = PhraseTimestamp(
                text=phrases[i].text,
                start=mid,
                end=max(phrases[i].end, mid + 0.5),
            )
    return phrases


def _snap_time(t: float, words: list[WordTimestamp], prefer: str = "start") -> float:
    best = t
    best_d = 1e9
    for w in words:
        cand = w.start if prefer == "start" else w.end
        d = abs(cand - t)
        if d < best_d and d < 0.45:
            best_d = d
            best = cand
    return best


def save_timestamps(
    words: list[WordTimestamp],
    phrases: list[PhraseTimestamp],
    output_path: Path,
) -> Path:
    ensure_dir(output_path.parent)
    payload = {
        "words": [w.to_dict() for w in words],
        "phrases": [p.to_dict() for p in phrases],
    }
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return output_path


def extract_and_save(
    audio_path: Path,
    output_json: Path,
    script_text: str | None = None,
    hook: str | None = None,
) -> tuple[list[WordTimestamp], list[PhraseTimestamp]]:
    words = transcribe_words(audio_path)
    brand_max = 14
    try:
        from modules.utils import load_brand_config

        brand_max = int(load_brand_config().get("caption", {}).get("max_chars_per_line", 14))
    except Exception:  # noqa: BLE001
        pass
    if script_text and script_text.strip():
        phrases = phrases_from_script(script_text, words, max_chars=brand_max, hook=hook)
    else:
        phrases = group_phrases(words, max_chars=brand_max)
    save_timestamps(words, phrases, output_json)
    return words, phrases
