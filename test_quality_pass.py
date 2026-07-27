"""Quality pass smoke test – hook LLM rewrite + Korean sanitize + BGM.

Usage:
    python test_quality_pass.py
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))


async def main() -> int:
    from modules.hook_scoring import rewrite_hooks_with_llm, score_research_hooks
    from modules.pipeline import _default_bgm
    from modules.text_quality import enforce_korean_copy, sanitize_hook

    print("=" * 60)
    print("Quality Pass checks")
    print("=" * 60)

    dirty = "CTR 2.3배提高하는 전략을 찾으세요"
    clean = enforce_korean_copy(dirty)
    print(f"[1] Korean sanitize: {dirty!r} -> {clean!r}")
    assert "提" not in clean and "高" not in clean

    seeds = [
        "인스타그램 릴스 마케팅 성공 전략",
        "LG유플러스, 마케팅 전 여정에 AI…릴스 영상도 10분만에",
    ]
    print("\n[2] Hook LLM rewrite...")
    rewritten = await rewrite_hooks_with_llm("B2B 마케팅 릴스", seeds)
    for h in rewritten:
        print(f"  - {h}")

    scored = await score_research_hooks("B2B 마케팅 릴스", seeds, rewrite=True)
    print("\n[3] Scored hooks:")
    for h in scored[:5]:
        print(f"  [{h.source}] {h.score:5.1f} | {h.hook}")

    print("\n[4] BGM...")
    bgm = _default_bgm()
    print(f"  {bgm} ({bgm.stat().st_size} bytes)")

    print("\nOK")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
