"""Phase B smoke test – script (+ optional TTS) for both production modes.

Usage:
    cd reels-pipeline
    python test_phase_b.py
    python test_phase_b.py voice_tts "B2B 마케팅 릴스"
    python test_phase_b.py music_caption "B2B 마케팅 릴스"
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))


async def run(mode: str, topic: str) -> int:
    from modules.modes import MODE_LABELS, parse_mode
    from modules.project import StrategicInput, create_project, save_json
    from modules.reference import ReferenceInput
    from modules.research import research_topic
    from modules.script import generate_script
    from modules.tts import run_tts_for_mode

    prod_mode = parse_mode(mode)
    print("=" * 60)
    print("Phase B - Script / TTS")
    print("=" * 60)
    print(f"Topic : {topic}")
    print(f"Mode  : {prod_mode.value} ({MODE_LABELS.get(prod_mode)})")

    reference = ReferenceInput(
        urls=["https://www.instagram.com/reel/example/"],
        style_notes="문장 단위 자막, 하단, 첫 1초 강한 훅",
        follow_points=["hook", "caption", "tone"],
    )
    strategic = StrategicInput(
        topic=topic,
        production_mode=prod_mode.value,
        reference=reference,
    )
    project_dir = create_project(strategic)
    print(f"Project: {project_dir.name}")

    print("\n[1] Research...")
    research = await research_topic(topic, reference=reference)
    save_json(project_dir, "research.json", research.to_dict())
    hook = research.hooks[0] if research.hooks else topic
    print(f"  hook seed: {hook}")

    print("\n[2] Script...")
    script = await generate_script(
        topic=topic,
        research_facts=research.facts,
        selected_hook=hook,
        mode=prod_mode,
        reference=reference,
    )
    save_json(project_dir, "script.json", script.to_dict())
    print(f"  source : {script.source}")
    print(f"  hook   : {script.hook}")
    if script.caption_lines:
        print(f"  captions: {len(script.caption_lines)}")
        for line in script.caption_lines:
            print(f"    - {line}")
    else:
        print(f"  tts_chars: {len(script.full_text)}")
        print(f"  preview: {script.full_text[:120]}...")

    print("\n[3] TTS...")
    voice_path = project_dir / "voice.mp3"
    tts_out = await run_tts_for_mode(prod_mode, script.full_text, voice_path)
    if tts_out is None:
        print("  skipped (music_caption)")
    else:
        print(f"  saved: {tts_out} ({tts_out.stat().st_size} bytes)")

    print("\n" + "=" * 60)
    print("Phase B COMPLETE")
    print(f"Saved under: {project_dir}")
    print("=" * 60)
    return 0


def main() -> int:
    args = sys.argv[1:]
    mode = "voice_tts"
    topic = "B2B 마케팅 릴스"
    if args:
        mode = args[0]
    if len(args) > 1:
        topic = " ".join(args[1:])
    return asyncio.run(run(mode, topic))


if __name__ == "__main__":
    raise SystemExit(main())
