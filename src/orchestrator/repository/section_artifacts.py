"""Shared helpers for writing section input artifacts with .ref sidecars."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from containers import ArtifactIOService

from orchestrator.path_registry import PathRegistry


class SectionArtifacts:
    def __init__(self, artifact_io: ArtifactIOService) -> None:
        self._artifact_io = artifact_io

    def write_section_input_artifact(
        self,
        paths: PathRegistry,
        sec_num: str,
        artifact_name: str,
        payload: dict,
    ) -> Path:
        """Write a JSON artifact into a section's input-refs directory.

        Creates both the JSON file and a companion ``.ref`` file that contains
        the resolved absolute path of the artifact, allowing downstream readers
        to locate it without scanning the directory.
        """
        input_dir = paths.input_refs_dir(sec_num)
        artifact_path = input_dir / artifact_name
        self._artifact_io.write_json(artifact_path, payload)
        ref_path = input_dir / f"{artifact_path.stem}.ref"
        ref_path.write_text(str(artifact_path.resolve()), encoding="utf-8")
        return artifact_path
