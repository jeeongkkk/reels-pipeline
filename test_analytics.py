"""Analytics smoke test – record metrics and update hook weights.

Usage:
    python test_analytics.py
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))


async def run() -> int:
    from modules.analytics import ReelMetrics, record_and_learn, summarize_metrics
    from modules.project import list_projects

    projects = list_projects(5)
    project_id = projects[0].name if projects else "demo_project"

    hook = "릴스, 지금 안 하면 늦다"
    mode = "voice_tts"
    script = ROOT / "projects" / project_id / "script.json"
    if script.exists():
        data = json.loads(script.read_text(encoding="utf-8"))
        hook = data.get("hook", hook)
        mode = data.get("mode", mode)

    print("=" * 60)
    print("Analytics - record + learn")
    print("=" * 60)
    print(f"project: {project_id}")

    samples = [
        ReelMetrics(
            project_id=project_id,
            views=12500,
            likes=480,
            saves=210,
            shares=55,
            comments=18,
            follows=12,
            avg_watch_time=9.5,
            hook=hook,
            mode=mode,
            topic="B2B 마케팅 릴스",
        ),
        ReelMetrics(
            project_id=f"{project_id}_low",
            views=180,
            likes=3,
            saves=1,
            shares=0,
            comments=0,
            follows=0,
            avg_watch_time=2.1,
            hook="오늘의 마케팅 정리",
            mode="music_caption",
            topic="B2B 마케팅 릴스",
        ),
    ]

    for m in samples:
        result = await record_and_learn(m)
        print(f"\nSaved {m.project_id} score={m.engagement_score()}")
        print("weights:", json.dumps(result["weights"], ensure_ascii=False))

    print("\nSummary:")
    print(json.dumps(summarize_metrics(), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(run()))
