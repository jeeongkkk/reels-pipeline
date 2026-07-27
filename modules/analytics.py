"""Module 8: Analytics & Learning Loop.

Manual metrics entry now; Graph API insights can plug in later.
Learned hook weights are saved to config/learned_weights.json and used by Module 1.5.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from modules.utils import ROOT_DIR, ensure_dir, get_logger, load_brand_config

logger = get_logger(__name__)

ANALYTICS_DIR = ROOT_DIR / "projects" / "_analytics"
WEIGHTS_PATH = ROOT_DIR / "config" / "learned_weights.json"
HISTORY_PATH = ANALYTICS_DIR / "metrics_history.json"


@dataclass
class ReelMetrics:
    project_id: str
    views: int = 0
    likes: int = 0
    saves: int = 0
    shares: int = 0
    avg_watch_time: float = 0.0
    comments: int = 0
    follows: int = 0
    hook: str = ""
    mode: str = ""
    topic: str = ""
    recorded_at: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))

    def engagement_score(self) -> float:
        """Simple composite used for learning (0~100-ish scale)."""
        if self.views <= 0:
            return 0.0
        er = (self.likes + self.saves * 2 + self.shares * 3 + self.comments + self.follows * 5) / self.views
        watch = min(self.avg_watch_time / 15.0, 1.5)  # 15s baseline
        # views contribute log-ish boost
        import math

        view_boost = min(math.log10(self.views + 1) / 4.0, 1.0)
        return round((er * 80 + watch * 15 + view_boost * 20) * 10, 2)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["engagement_score"] = self.engagement_score()
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ReelMetrics:
        return cls(
            project_id=str(data.get("project_id", "")),
            views=int(data.get("views", 0) or 0),
            likes=int(data.get("likes", 0) or 0),
            saves=int(data.get("saves", 0) or 0),
            shares=int(data.get("shares", 0) or 0),
            avg_watch_time=float(data.get("avg_watch_time", 0) or 0),
            comments=int(data.get("comments", 0) or 0),
            follows=int(data.get("follows", 0) or 0),
            hook=str(data.get("hook", "") or ""),
            mode=str(data.get("mode", "") or ""),
            topic=str(data.get("topic", "") or ""),
            recorded_at=str(data.get("recorded_at") or datetime.now().isoformat(timespec="seconds")),
        )


def _load_history() -> list[dict[str, Any]]:
    if not HISTORY_PATH.exists():
        return []
    try:
        return json.loads(HISTORY_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []


def _save_history(rows: list[dict[str, Any]]) -> None:
    ensure_dir(ANALYTICS_DIR)
    HISTORY_PATH.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")


def load_learned_weights() -> dict[str, float]:
    brand = load_brand_config()
    base = dict(brand.get("hook_scoring", {}).get("weights", {}))
    if WEIGHTS_PATH.exists():
        try:
            learned = json.loads(WEIGHTS_PATH.read_text(encoding="utf-8"))
            weights = learned.get("weights") or learned
            if isinstance(weights, dict):
                base.update({k: float(v) for k, v in weights.items() if isinstance(v, (int, float))})
        except (json.JSONDecodeError, TypeError, ValueError):
            pass
    return base


def save_learned_weights(weights: dict[str, float], note: str = "") -> Path:
    ensure_dir(WEIGHTS_PATH.parent)
    payload = {
        "updated_at": datetime.now().isoformat(timespec="seconds"),
        "note": note,
        "weights": weights,
    }
    WEIGHTS_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return WEIGHTS_PATH


async def record_metrics(project_id: str, metrics: ReelMetrics) -> Path:
    """Append metrics to history and also save per-project file."""
    metrics.project_id = project_id
    row = metrics.to_dict()

    history = _load_history()
    # replace same project_id if re-recorded
    history = [h for h in history if h.get("project_id") != project_id]
    history.append(row)
    _save_history(history)

    project_dir = ROOT_DIR / "projects" / project_id
    if project_dir.exists():
        (project_dir / "metrics.json").write_text(
            json.dumps(row, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    logger.info(
        "record_metrics project=%s views=%d score=%.1f",
        project_id,
        metrics.views,
        metrics.engagement_score(),
    )
    await asyncio.sleep(0)
    return HISTORY_PATH


def list_metrics(limit: int = 50) -> list[ReelMetrics]:
    rows = _load_history()
    rows = sorted(rows, key=lambda r: r.get("recorded_at", ""), reverse=True)[:limit]
    return [ReelMetrics.from_dict(r) for r in rows]


def summarize_metrics(rows: list[ReelMetrics] | None = None) -> dict[str, Any]:
    rows = rows if rows is not None else list_metrics(200)
    if not rows:
        return {"count": 0, "avg_views": 0, "avg_engagement": 0, "best_project": None}

    avg_views = sum(r.views for r in rows) / len(rows)
    avg_eng = sum(r.engagement_score() for r in rows) / len(rows)
    best = max(rows, key=lambda r: r.engagement_score())
    by_mode: dict[str, list[ReelMetrics]] = {}
    for r in rows:
        by_mode.setdefault(r.mode or "unknown", []).append(r)

    mode_stats = {
        mode: {
            "count": len(items),
            "avg_views": round(sum(i.views for i in items) / len(items), 1),
            "avg_engagement": round(sum(i.engagement_score() for i in items) / len(items), 2),
        }
        for mode, items in by_mode.items()
    }

    return {
        "count": len(rows),
        "avg_views": round(avg_views, 1),
        "avg_engagement": round(avg_eng, 2),
        "best_project": best.project_id,
        "best_score": best.engagement_score(),
        "best_hook": best.hook,
        "by_mode": mode_stats,
    }


async def update_hook_weights(metrics_history: list[ReelMetrics] | None = None) -> dict[str, float]:
    """Nudge hook scoring weights using relative performance of past reels.

    High-engagement reels slightly boost emotional_trigger + novelty.
    Low-engagement reels boost clarity + audience_relevance (safer hooks).
    """
    rows = metrics_history if metrics_history is not None else list_metrics(200)
    weights = load_learned_weights()

    if len(rows) < 2:
        logger.info("Not enough metrics to learn (%d). Keeping current weights.", len(rows))
        await asyncio.sleep(0)
        return weights

    scores = [r.engagement_score() for r in rows]
    avg = sum(scores) / len(scores)
    high = [r for r in rows if r.engagement_score() >= avg]
    low = [r for r in rows if r.engagement_score() < avg]

    # Small adaptive nudges (keep signs of risk negative)
    delta = 0.02
    if len(high) >= len(low):
        weights["emotional_trigger"] = float(weights.get("emotional_trigger", 0.25)) + delta
        weights["novelty"] = float(weights.get("novelty", 0.15)) + delta
        weights["clarity"] = float(weights.get("clarity", 0.20)) - delta / 2
    else:
        weights["clarity"] = float(weights.get("clarity", 0.20)) + delta
        weights["audience_relevance"] = float(weights.get("audience_relevance", 0.30)) + delta
        weights["novelty"] = float(weights.get("novelty", 0.15)) - delta / 2

    # Normalize positive weights to sum ~0.9 (risk stays separate)
    positive_keys = ["audience_relevance", "emotional_trigger", "clarity", "novelty"]
    pos_sum = sum(max(weights.get(k, 0), 0.05) for k in positive_keys)
    target = 0.90
    for k in positive_keys:
        weights[k] = round(max(weights.get(k, 0.05), 0.05) / pos_sum * target, 4)
    weights["risk"] = round(min(weights.get("risk", -0.10), -0.05), 4)

    save_learned_weights(weights, note=f"updated from {len(rows)} metrics (avg_eng={avg:.1f})")
    logger.info("Updated hook weights: %s", weights)
    await asyncio.sleep(0)
    return weights


async def record_and_learn(metrics: ReelMetrics) -> dict[str, Any]:
    """Convenience: save metrics then recompute weights."""
    await record_metrics(metrics.project_id, metrics)
    weights = await update_hook_weights()
    return {"metrics": metrics.to_dict(), "weights": weights, "summary": summarize_metrics()}
