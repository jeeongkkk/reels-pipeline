"""Phase D smoke test – Pexels B-roll + SFX layering.

Usage:
    python test_phase_d.py
    python test_phase_d.py music_caption "B2B 마케팅 릴스"
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))


async def run(mode: str, topic: str) -> int:
    from modules.pipeline import render_project
    from modules.reference import ReferenceInput
    from modules.research import research_topic
    from modules.sfx import ensure_default_sfx

    print("=" * 60)
    print("Phase D - Pexels + SFX")
    print("=" * 60)

    sfx = ensure_default_sfx()
    print(f"SFX ready: {list(sfx.keys())}")

    reference = ReferenceInput(
        style_notes="문장 단위 자막, 하단, 첫 1초 강한 훅",
        follow_points=["hook", "caption", "tone"],
    )
    research = await research_topic(topic, reference=reference)
    hook = research.hooks[0] if research.hooks else topic

    output = await render_project(
        topic=topic,
        mode=mode,
        selected_hook=hook,
        research_facts=research.facts,
        reference=reference,
    )
    size_mb = output.stat().st_size / (1024 * 1024)
    print(f"Output: {output}")
    print(f"Size  : {size_mb:.2f} MB")
    return 0


def main() -> int:
    args = sys.argv[1:]
    mode = args[0] if args else "music_caption"
    topic = " ".join(args[1:]) if len(args) > 1 else "B2B 마케팅 릴스"
    return asyncio.run(run(mode, topic))


if __name__ == "__main__":
    raise SystemExit(main())
