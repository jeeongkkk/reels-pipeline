"""Stage [0] Reference Input – style direction for Human-Directed production.

MVP: store URLs, style notes, and follow-points. LLM style analysis comes in Phase B.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from modules.utils import ensure_dir, get_logger

logger = get_logger(__name__)

FOLLOW_POINT_OPTIONS = ("hook", "caption", "cut", "tone", "pacing")


@dataclass
class ReferenceInput:
    urls: list[str] = field(default_factory=list)
    style_notes: str = ""
    follow_points: list[str] = field(default_factory=lambda: ["hook", "caption", "tone"])
    local_files: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> ReferenceInput:
        if not data:
            return cls()
        points = [p for p in data.get("follow_points", []) if p in FOLLOW_POINT_OPTIONS]
        return cls(
            urls=[u.strip() for u in data.get("urls", []) if u and u.strip()],
            style_notes=(data.get("style_notes") or "").strip(),
            follow_points=points or ["hook", "caption", "tone"],
            local_files=[f for f in data.get("local_files", []) if f],
        )

    def summary_for_prompt(self) -> str:
        """Compact text injected into later modules (script / assembly)."""
        lines: list[str] = []
        if self.urls:
            lines.append("Reference URLs:")
            lines.extend(f"- {u}" for u in self.urls)
        if self.follow_points:
            lines.append(f"Follow: {', '.join(self.follow_points)}")
        if self.style_notes:
            lines.append(f"Style notes: {self.style_notes}")
        if self.local_files:
            lines.append("Local samples:")
            lines.extend(f"- {f}" for f in self.local_files)
        return "\n".join(lines) if lines else "No reference provided."


def save_reference(project_dir: Path, reference: ReferenceInput) -> Path:
    import json

    ensure_dir(project_dir)
    path = project_dir / "reference.json"
    path.write_text(json.dumps(reference.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info("Saved reference → %s", path)
    return path


def load_reference(project_dir: Path) -> ReferenceInput:
    import json

    path = project_dir / "reference.json"
    if not path.exists():
        return ReferenceInput()
    return ReferenceInput.from_dict(json.loads(path.read_text(encoding="utf-8")))
