"""Module 5 POC – 15s dynamic captions + BGM ducking render test.

Usage:
    cd reels-pipeline
    pip install -r requirements.txt
    python test_module5.py

Generates fixtures automatically if missing, then renders a test reel to:
    projects/poc_output/test_reel.mp4
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

FIXTURES = ROOT / "fixtures"
OUTPUT = ROOT / "projects" / "poc_output" / "test_reel.mp4"


def main() -> int:
    print("=" * 60)
    print("Module 5 POC - Dynamic Captions + BGM Ducking")
    print("=" * 60)

    # ── Step 1: Ensure fixtures exist ──────────────────────────
    voice_path = FIXTURES / "sample_voice.mp3"
    bgm_path = FIXTURES / "sample_bgm.mp3"
    video_path = FIXTURES / "sample_bg.mp4"
    timestamps_path = FIXTURES / "sample_timestamps.json"

    if not all(p.exists() for p in [voice_path, bgm_path, video_path, timestamps_path]):
        print("\n[1/3] Generating sample fixtures...")
        from fixtures.generate_fixtures import generate_all

        generate_all()
    else:
        print("\n[1/3] Fixtures found - skipping generation")

    # ── Step 2: Load timestamps ────────────────────────────────
    print("\n[2/3] Loading word timestamps...")
    from modules.assembly import load_word_timestamps

    words = load_word_timestamps(timestamps_path)
    print(f"  -> {len(words)} words loaded")
    for w in words[:3]:
        print(f"     '{w.text}' @ {w.start:.2f}s - {w.end:.2f}s")
    print(f"     ... ({len(words) - 3} more)")

    # ── Step 3: Render ─────────────────────────────────────────
    print("\n[3/3] Rendering 15s reel with MoviePy 2.x...")
    from modules.assembly import assemble_reel
    from modules.utils import load_brand_config

    brand = load_brand_config()
    start = time.time()

    result = assemble_reel(
        background_video=video_path,
        voice_audio=voice_path,
        bgm_audio=bgm_path,
        word_timestamps=words,
        output_path=OUTPUT,
        brand_config=brand,
    )

    elapsed = time.time() - start
    size_mb = result.stat().st_size / (1024 * 1024)

    print("\n" + "=" * 60)
    print("POC COMPLETE")
    print(f"  Output : {result}")
    print(f"  Size   : {size_mb:.2f} MB")
    print(f"  Time   : {elapsed:.1f}s")
    print("=" * 60)
    print("\nOpen the file to verify:")
    print("  - Words appear one at a time (YouTube Shorts style)")
    print("  - BGM dips during speech, restores between words")
    print("  - Background video fills 9:16 frame")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
