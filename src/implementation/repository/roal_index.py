"""ROAL input-index CRUD operations for implementation sections."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from orchestrator.path_registry import PathRegistry

if TYPE_CHECKING:
    from containers import ArtifactIOService

IMPLEMENTATION_ROAL_KINDS = frozenset({
    "accepted_frontier",
    "deferred",
    "reopen",
})
_ROAL_INDEX_KINDS = frozenset({
    "accepted_frontier",
    "deferred",
    "reopen",
    "proposal_advisory",
})


def normalize_roal_entries(entries: list[dict]) -> list[dict]:
    normalized: list[dict] = []
    seen: set[tuple[str, str, str]] = set()
    for entry in entries:
        kind = str(entry.get("kind", "")).strip()
        path = str(entry.get("path", "")).strip()
        produced_by = str(entry.get("produced_by", "")).strip()
        if kind not in _ROAL_INDEX_KINDS or not path:
            continue
        key = (kind, path, produced_by)
        if key in seen:
            continue
        seen.add(key)
        item = {
            "kind": kind,
            "path": path,
        }
        if produced_by:
            item["produced_by"] = produced_by
        normalized.append(item)
    return normalized


class RoalIndex:
    """ROAL input-index CRUD operations for implementation sections.

    All cross-cutting services are received via constructor injection.
    """

    def __init__(
        self,
        artifact_io: ArtifactIOService,
    ) -> None:
        self._artifact_io = artifact_io

    def read_roal_input_index(
        self,
        planspace: Path,
        sec_num: str,
    ) -> list[dict]:
        paths = PathRegistry(planspace)
        index_path = paths.input_refs_dir(sec_num) / f"section-{sec_num}-roal-input-index.json"
        payload = self._artifact_io.read_json(index_path)
        if not isinstance(payload, list):
            return []
        return [entry for entry in payload if isinstance(entry, dict)]

    def write_roal_input_index(
        self,
        planspace: Path,
        sec_num: str,
        entries: list[dict],
    ) -> Path:
        """Write a typed ROAL input index for a section."""
        paths = PathRegistry(planspace)
        input_dir = paths.input_refs_dir(sec_num)
        index_path = input_dir / f"section-{sec_num}-roal-input-index.json"
        normalized_entries = normalize_roal_entries(entries)
        indexed_paths = {
            str(Path(entry["path"]).resolve())
            for entry in normalized_entries
        }

        if input_dir.exists():
            for ref_path in sorted(input_dir.glob("*.ref")):
                try:
                    referenced = ref_path.read_text(encoding="utf-8").strip()
                except OSError:
                    continue
                if not referenced:
                    continue
                target_path = Path(referenced)
                resolved = str(target_path.resolve())
                if (
                    target_path.parent == input_dir
                    and "-risk-" in target_path.name
                    and resolved not in indexed_paths
                ):
                    ref_path.unlink(missing_ok=True)
            for artifact_path in sorted(input_dir.iterdir()):
                if (
                    not artifact_path.is_file()
                    or artifact_path == index_path
                    or artifact_path.suffix == ".ref"
                ):
                    continue
                if (
                    artifact_path.parent == input_dir
                    and "-risk-" in artifact_path.name
                    and str(artifact_path.resolve()) not in indexed_paths
                ):
                    artifact_path.unlink(missing_ok=True)

        self._artifact_io.write_json(index_path, normalized_entries)
        return index_path

    def refresh_roal_input_index(
        self,
        planspace: Path,
        sec_num: str,
        *,
        replace_kinds: frozenset[str],
        new_entries: list[dict],
    ) -> Path:
        preserved = [
            entry
            for entry in self.read_roal_input_index(planspace, sec_num)
            if str(entry.get("kind", "")).strip() not in replace_kinds
        ]
        return self.write_roal_input_index(
            planspace,
            sec_num,
            preserved + new_entries,
        )
