"""Phase A smoke test – topic + reference → research → hook scoring.

Usage:
    cd reels-pipeline
    python test_phase_a.py
    python test_phase_a.py "B2B 마케팅 릴스"
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))


async def run(topic: str) -> int:
    from modules.hook_scoring import score_research_hooks
    from modules.project import StrategicInput, create_project, save_json
    from modules.reference import ReferenceInput
    from modules.research import research_topic

    print("=" * 60)
    print("Phase A - Research + Hook Scoring")
    print("=" * 60)
    print(f"Topic: {topic}")

    reference = ReferenceInput(
        urls=["https://www.instagram.com/reel/example/"],
        style_notes="문장 단위 자막, 하단, 첫 1초 강한 훅",
        follow_points=["hook", "caption", "tone"],
    )

    strategic = StrategicInput(
        topic=topic,
        target_audience="B2B 마케터",
        tone_override="semi-formal",
        reference=reference,
    )
    project_dir = create_project(strategic)
    print(f"Project: {project_dir}")

    print("\n[1] Research (RSS)...")
    result = await research_topic(topic, reference=reference)
    save_json(project_dir, "research.json", result.to_dict())

    print(f"  articles : {len(result.raw_articles)}")
    print(f"  facts    : {len(result.facts)}")
    print(f"  hooks    : {len(result.hooks)}")
    query_preview = (result.query_used[:80] + "...") if result.query_used else "(none)"
    print(f"  query    : {query_preview}")

    if result.sources:
        print("\n  Top sources:")
        for s in result.sources[:5]:
            title = (s.get("title") or "")[:60]
            print(f"    - [{s.get('relevance')}] {title}")

    print("\n[2] Hook scoring...")
    scored = await score_research_hooks(topic, result.hooks, reference=reference)
    save_json(project_dir, "hooks.json", [h.to_dict() for h in scored])

    for h in scored:
        mark = "PASS" if h.score >= 70 else "HOLD"
        print(f"  [{mark}] {h.score:5.1f} | {h.hook}")
        print(f"         {h.rationale}")

    print("\n" + "=" * 60)
    print("Phase A COMPLETE")
    print(f"Saved under: {project_dir}")
    print("=" * 60)
    return 0 if result.raw_articles or result.hooks else 1


def main() -> int:
    topic = " ".join(sys.argv[1:]).strip() or "B2B 마케팅 릴스"
    return asyncio.run(run(topic))


if __name__ == "__main__":
    raise SystemExit(main())
