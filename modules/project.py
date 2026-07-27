"""Project session helpers – persist Stage [0] input and module outputs."""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from modules.modes import ProductionMode, parse_mode
from modules.reference import ReferenceInput, save_reference
from modules.utils import ROOT_DIR, ensure_dir, get_logger, get_settings

logger = get_logger(__name__)


def _slugify(text: str, max_len: int = 40) -> str:
    text = text.strip().lower()
    text = re.sub(r"[^\w\s-]", "", text, flags=re.UNICODE)
    text = re.sub(r"[\s_-]+", "-", text).strip("-")
    return (text[:max_len] or "untitled").rstrip("-")


@dataclass
class StrategicInput:
    topic: str
    target_audience: str = "B2B 마케터"
    tone_override: str = "brand default"
    production_mode: str = ProductionMode.VOICE_TTS.value
    reference: ReferenceInput = field(default_factory=ReferenceInput)
    created_at: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))

    def mode(self) -> ProductionMode:
        return parse_mode(self.production_mode)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["reference"] = self.reference.to_dict()
        data["production_mode"] = self.mode().value
        return data


def projects_root() -> Path:
    settings = get_settings()
    root = Path(settings.projects_dir)
    if not root.is_absolute():
        root = ROOT_DIR / root
    return ensure_dir(root)


def create_project(strategic: StrategicInput) -> Path:
    """Create a new project folder and save strategic_input + reference."""
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    name = f"{stamp}_{_slugify(strategic.topic)}"
    project_dir = ensure_dir(projects_root() / name)

    (project_dir / "strategic_input.json").write_text(
        json.dumps(strategic.to_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    save_reference(project_dir, strategic.reference)

    logger.info("Created project: %s", project_dir)
    return project_dir


def save_json(project_dir: Path, filename: str, data: Any) -> Path:
    ensure_dir(project_dir)
    path = project_dir / filename
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return path


def load_json(project_dir: Path, filename: str) -> Any | None:
    path = project_dir / filename
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def list_projects(limit: int = 20) -> list[Path]:
    root = projects_root()
    dirs = [p for p in root.iterdir() if p.is_dir() and (p / "strategic_input.json").exists()]
    dirs.sort(key=lambda p: p.name, reverse=True)
    return dirs[:limit]
