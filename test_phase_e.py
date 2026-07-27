"""Phase E smoke test – caption + manual publish package.

Usage:
    python test_phase_e.py
    python test_phase_e.py path/to/project_dir
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))


async def run(project_dir: Path | None) -> int:
    from modules.publish import caption_from_script_json, publish_reel
    from modules.utils import ROOT_DIR

    if project_dir is None:
        projects = ROOT_DIR / "projects"
        candidates = sorted(
            [p for p in projects.iterdir() if p.is_dir() and (p / "final_reel.mp4").exists()],
            key=lambda p: p.name,
            reverse=True,
        )
        if not candidates:
            print("No final_reel.mp4 found. Run test_phase_d.py first.")
            return 1
        project_dir = candidates[0]

    video = project_dir / "final_reel.mp4"
    script_path = project_dir / "script.json"
    print("=" * 60)
    print("Phase E - Publish package")
    print("=" * 60)
    print(f"Project: {project_dir.name}")
    print(f"Video  : {video}")

    if script_path.exists():
        script = json.loads(script_path.read_text(encoding="utf-8"))
        caption = caption_from_script_json(script)
    else:
        caption = "B2B 마케팅 인사이트\n\n#B2B마케팅 #인스타릴스"

    print("\nCaption preview:\n")
    print(caption[:400])
    print("\n...")

    result = await publish_reel(video, caption, project_dir=project_dir, mode="manual")
    print("\nPublish result:")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("status", "").startswith(("ready", "uploaded", "fallback")) else 1


def main() -> int:
    arg = sys.argv[1] if len(sys.argv) > 1 else None
    project = Path(arg) if arg else None
    return asyncio.run(run(project))


if __name__ == "__main__":
    raise SystemExit(main())
