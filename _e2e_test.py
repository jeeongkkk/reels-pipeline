"""End-to-end smoke test for card-news pipeline (2026-07)."""
from __future__ import annotations

import asyncio
import json
import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

TOPIC = "최근 정부지원사업 트렌드"
REPORT: dict = {"ok": [], "fail": [], "info": {}}


def _pass(name: str, detail: str = "") -> None:
    REPORT["ok"].append({"name": name, "detail": detail})
    msg = f"[PASS] {name}" + (f" - {detail}" if detail else "")
    print(msg, flush=True)


def _fail(name: str, detail: str) -> None:
    REPORT["fail"].append({"name": name, "detail": detail})
    print(f"[FAIL] {name} - {detail}", flush=True)


async def test_web_facts() -> list[str]:
    from modules.web_facts import fetch_live_web_facts

    bundle = await fetch_live_web_facts(TOPIC, limit=5, days=7)
    facts = bundle.prompt_facts(5)
    REPORT["info"]["web_provider"] = bundle.provider
    REPORT["info"]["web_days"] = bundle.days
    REPORT["info"]["web_n"] = len(facts)
    if not facts:
        _fail("web_facts", "no facts returned")
        return []
    spam = any(any(k in f for k in ("야동", "주소콘", "링크모음")) for f in facts)
    if spam:
        _fail("web_facts_spam", "spam content in facts")
    else:
        _pass("web_facts", f"provider={bundle.provider} n={len(facts)}")
    return facts


async def test_script(facts: list[str]):
    from modules.card_script import generate_card_script, _slide_lacks_fact_density

    script = await generate_card_script(TOPIC, facts, selected_hook=TOPIC)
    slides = script.slides
    REPORT["info"]["script_source"] = script.source
    REPORT["info"]["slide_n"] = len(slides)

    if len(slides) < 5:
        _fail("script_count", f"only {len(slides)} slides")
    else:
        _pass("script_count", f"n={len(slides)} source={script.source}")

    if not all(hasattr(s, "requires_real_entity") for s in slides):
        _fail("script_schema", "missing requires_real_entity")
    else:
        abstract = all(not s.requires_real_entity for s in slides)
        _pass(
            "script_requires_real_entity",
            f"all_abstract={abstract}",
        )

    weak = sum(1 for s in slides if _slide_lacks_fact_density(s))
    if weak >= 3:
        _fail("script_fact_density", f"{weak} weak slides")
    else:
        _pass("script_fact_density", f"weak={weak}")

    spam = any(
        any(k in f"{s.hook}{s.body}" for k in ("야동", "주소콘", "링크모음"))
        for s in slides
    )
    if spam:
        _fail("script_spam", "off-topic spam in slides")
    else:
        _pass("script_spam_clean")

    return script


async def test_waterfall_one(script) -> None:
    from modules.card_assets import get_background_image, TEMPLATES_DIR

    out = ROOT / "_e2e_photos"
    out.mkdir(exist_ok=True)
    slide = script.slides[0].to_dict()
    r = await get_background_image(
        search_query=slide.get("search_query") or "",
        image_prompt=slide.get("image_prompt") or "",
        output_dir=out,
        index=0,
        slide=slide,
    )
    tpl = TEMPLATES_DIR / "slide_bg_1.png"
    if r.source == "placeholder":
        _fail("waterfall_tier", f"fell to placeholder path={r.path}")
    else:
        _pass("waterfall_tier", f"source={r.source}")

    if not r.path.exists() or r.path.stat().st_size < 20_000:
        _fail("waterfall_file", f"bad file {r.path}")
    else:
        _pass("waterfall_file", f"{r.path.name} {r.path.stat().st_size}B")

    if not tpl.exists():
        _fail("waterfall_template", "templates/slide_bg_1.png missing")
    else:
        from PIL import Image

        with Image.open(tpl) as im:
            wh = im.size
        if wh != (1080, 1920):
            _fail("waterfall_size", f"got {wh}, want 1080x1920")
        else:
            _pass("waterfall_template_1080x1920", str(tpl))


async def test_full_pipeline(script_facts: list[str]) -> Path | None:
    from datetime import datetime

    from modules.card_pipeline import render_card_news_project
    from modules.utils import ROOT_DIR, ensure_dir

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    project_dir = ensure_dir(ROOT_DIR / "projects" / f"{stamp}_e2e-waterfall-test")

    def progress(pct: int, msg: str) -> None:
        print(f"  … {pct}% {msg}", flush=True)

    zip_path = await render_card_news_project(
        topic=TOPIC,
        selected_hook=TOPIC,
        research_facts=script_facts,
        project_dir=project_dir,
        on_progress=progress,
    )
    REPORT["info"]["project_dir"] = str(project_dir)
    REPORT["info"]["zip"] = str(zip_path)

    assets = json.loads((project_dir / "assets.json").read_text(encoding="utf-8"))
    sources = sorted({a.get("source") for a in assets})
    REPORT["info"]["bg_sources"] = sources
    placeholder_n = sum(1 for a in assets if a.get("source") == "placeholder")
    if placeholder_n == len(assets):
        _fail("pipeline_backgrounds", "all placeholders")
    else:
        _pass(
            "pipeline_backgrounds",
            f"n={len(assets)} sources={sources} placeholders={placeholder_n}",
        )

    script_json = json.loads((project_dir / "script.json").read_text(encoding="utf-8"))
    if len(script_json.get("slides") or []) < 5:
        _fail("pipeline_script", "too few slides in script.json")
    else:
        _pass("pipeline_script", f"slides={len(script_json['slides'])}")

    frames = list((project_dir / "card_frames").glob("*.png"))
    # ignore work dirs
    frames = [p for p in frames if p.parent.name == "card_frames"]
    if len(frames) < 5:
        # maybe nested
        frames = [
            p
            for p in (project_dir / "card_frames").rglob("*.png")
            if "_html_work" not in str(p)
        ]
    if len(frames) < 5:
        _fail("pipeline_pngs", f"only {len(frames)} png frames")
    else:
        from PIL import Image

        sample = frames[0]
        with Image.open(sample) as im:
            wh = im.size
        _pass("pipeline_pngs", f"n={len(frames)} sample={sample.name} size={wh}")

    if zip_path.exists() and zip_path.stat().st_size > 50_000:
        _pass("pipeline_zip", f"{zip_path.name} {zip_path.stat().st_size}B")
    else:
        _fail("pipeline_zip", f"missing/small zip {zip_path}")

    return project_dir


async def main() -> int:
    print("=== E2E card-news test ===", flush=True)
    try:
        facts = await test_web_facts()
        script = await test_script(facts)
        await test_waterfall_one(script)
        await test_full_pipeline(facts)
    except Exception as exc:  # noqa: BLE001
        _fail("uncaught", f"{exc}\n{traceback.format_exc()}")

    out = ROOT / "_e2e_report.json"
    out.write_text(json.dumps(REPORT, ensure_ascii=False, indent=2), encoding="utf-8")
    print("\n=== SUMMARY ===", flush=True)
    print(f"PASS {len(REPORT['ok'])}  FAIL {len(REPORT['fail'])}", flush=True)
    for f in REPORT["fail"]:
        print(f"  x {f['name']}: {f['detail'][:200]}", flush=True)
    print(f"report -> {out}", flush=True)
    return 1 if REPORT["fail"] else 0


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    raise SystemExit(asyncio.run(main()))
