"""Pure helpers for substrate discovery orchestration."""

from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING

from orchestrator.path_registry import PathRegistry
from scan.related.cli_handler import extract_related_files

if TYPE_CHECKING:
    from containers import ArtifactIOService

VALID_PROJECT_MODES = ("greenfield", "brownfield", "hybrid")


def registry_for_artifacts(artifacts_dir: Path) -> PathRegistry:
    return PathRegistry(artifacts_dir.parent)


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


class SubstrateStateReader:
    """Substrate discovery state reading and writing.

    All cross-cutting services are received via constructor injection.
    """

    def __init__(self, artifact_io: ArtifactIOService) -> None:
        self._artifact_io = artifact_io

    def read_project_mode(self, artifacts_dir: Path) -> str | None:
        """Read project mode from scan-stage signals."""
        registry = registry_for_artifacts(artifacts_dir)
        json_path = registry.project_mode_json()
        txt_path = registry.project_mode_txt()

        if json_path.is_file():
            data = self._artifact_io.read_json(json_path)
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

    def write_status(
        self,
        artifacts_dir: Path,
        state: str,
        project_mode: str,
        total_sections: int,
        vacuum_sections: list[str],
        notes: str,
        threshold: int = 2,
    ) -> None:
        """Write ``artifacts/substrate/status.json``."""
        registry = registry_for_artifacts(artifacts_dir)
        status = {
            "state": state,
            "project_mode": project_mode,
            "total_sections": total_sections,
            "vacuum_sections": [int(section) for section in vacuum_sections],
            "threshold": threshold,
            "notes": notes,
        }
        self._artifact_io.write_json(registry.substrate_status(), status)


