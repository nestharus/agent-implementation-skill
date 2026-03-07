"""Pure helpers for substrate discovery orchestration."""

from __future__ import annotations

import re
from pathlib import Path

from lib.core.artifact_io import read_json, write_json
from lib.core.path_registry import PathRegistry
from scan.related_files import extract_related_files

VALID_PROJECT_MODES = ("greenfield", "brownfield", "hybrid")


def _registry_for_artifacts(artifacts_dir: Path) -> PathRegistry:
    return PathRegistry(artifacts_dir.parent)


def read_project_mode(artifacts_dir: Path) -> str | None:
    """Read project mode from scan-stage signals."""
    registry = _registry_for_artifacts(artifacts_dir)
    json_path = registry.project_mode_json()
    txt_path = registry.project_mode_txt()

    if json_path.is_file():
        data = read_json(json_path)
        if isinstance(data, dict):
            mode = data.get("mode", "").strip().lower()
            if mode in VALID_PROJECT_MODES:
                return mode
        else:
            print(
                "[SUBSTRATE][WARN] project-mode.json malformed "
                "-- preserved as .malformed.json, "
                "trying text fallback"
            )

    if txt_path.is_file():
        mode = txt_path.read_text(encoding="utf-8").strip().lower()
        if mode in VALID_PROJECT_MODES:
            return mode

    return None


def list_section_files(sections_dir: Path) -> list[Path]:
    """Return sorted list of ``section-N.md`` files."""
    files = [
        path
        for path in sections_dir.iterdir()
        if path.is_file() and re.match(r"section-\d+\.md$", path.name)
    ]
    return sorted(files)


def section_number(path: Path) -> str:
    """Extract section number string from a section filename."""
    match = re.match(r"section-(\d+)\.md$", path.name)
    if match:
        return match.group(1)
    return path.stem.replace("section-", "")


def count_existing_related(section_path: Path, codespace: Path) -> int:
    """Count how many related files in a section spec actually exist."""
    text = section_path.read_text(encoding="utf-8")
    related = extract_related_files(text)
    count = 0
    for rel_path in related:
        if (codespace / rel_path).exists():
            count += 1
    return count


def write_status(
    artifacts_dir: Path,
    state: str,
    project_mode: str,
    total_sections: int,
    vacuum_sections: list[str],
    notes: str,
    threshold: int = 2,
) -> None:
    """Write ``artifacts/substrate/status.json``."""
    status_dir = _registry_for_artifacts(artifacts_dir).substrate_dir()
    status_dir.mkdir(parents=True, exist_ok=True)
    status = {
        "state": state,
        "project_mode": project_mode,
        "total_sections": total_sections,
        "vacuum_sections": [int(section) for section in vacuum_sections],
        "threshold": threshold,
        "notes": notes,
    }
    write_json(status_dir / "status.json", status)
