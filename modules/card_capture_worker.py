"""Subprocess worker: render card slides with Playwright (Windows-safe).

Streamlit's asyncio loop on Windows cannot spawn Playwright's browser
subprocess (NotImplementedError). Running capture in a fresh process
with ProactorEventLoop avoids that.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path


def _ensure_windows_proactor() -> None:
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())


def main(argv: list[str] | None = None) -> int:
    _ensure_windows_proactor()
    args = argv if argv is not None else sys.argv[1:]
    if not args:
        print("Usage: python -m modules.card_capture_worker <job.json>", file=sys.stderr)
        return 2

    job_path = Path(args[0])
    job = json.loads(job_path.read_text(encoding="utf-8"))

    from modules.card_render import _render_all_sync

    slides = job["slides"]
    backgrounds = [Path(p) for p in job["backgrounds"]]
    output_dir = Path(job["output_dir"])
    brand_color = job.get("brand_color", "#FFD700")
    logo = job.get("logo", "authority")
    highlight_mode = job.get("highlight_mode", "color")

    paths = _render_all_sync(
        slides,
        backgrounds,
        output_dir,
        brand_color,
        logo,
        highlight_mode,
    )
    result_path = output_dir / "_capture_result.json"
    result_path.write_text(
        json.dumps([str(p) for p in paths], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(result_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
