"""Project mode resolution helpers for the section loop."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from orchestrator.path_registry import PathRegistry
from orchestrator.types import PauseType

if TYPE_CHECKING:
    from containers import ArtifactIOService, LogService, PipelineControlService


@dataclass(frozen=True)
class ProjectMode:
    """Structured result from ``_read_project_mode_signal``."""

    mode: str
    evidence_files: list[str] = field(default_factory=list)
    reason: str = ""


class ProjectModeResolver:
    """Project mode resolution for the section loop.

    All cross-cutting services are received via constructor injection.
    """

    def __init__(
        self,
        artifact_io: ArtifactIOService,
        logger: LogService,
        pipeline_control: PipelineControlService,
    ) -> None:
        self._artifact_io = artifact_io
        self._logger = logger
        self._pipeline_control = pipeline_control

    def _read_project_mode_signal(
        self,
        mode_json_path: Path,
        mode_txt_path: Path,
        *,
        post_resume: bool,
    ) -> ProjectMode:
        if mode_json_path.exists():
            mode_data = self._artifact_io.read_json(mode_json_path)
            if mode_data is not None:
                return ProjectMode(
                    mode=str(mode_data.get("mode", "unknown")),
                    evidence_files=list(mode_data.get("constraints", [])),
                    reason="JSON signal (post-resume)" if post_resume else "JSON signal",
                )

            self._logger.log(
                "project-mode.json malformed"
                + (" after resume" if post_resume else "")
                + " — preserved as .malformed.json, trying text fallback",
            )
            if mode_txt_path.exists():
                return ProjectMode(
                    mode=mode_txt_path.read_text(encoding="utf-8").strip(),
                    reason=("text (post-resume)"
                            if post_resume else "text (JSON malformed)"),
                )
            return ProjectMode(
                mode="unknown",
                reason="default (post-resume)" if post_resume else "default",
            )

        if mode_txt_path.exists():
            return ProjectMode(
                mode=mode_txt_path.read_text(encoding="utf-8").strip(),
                reason="text (post-resume)" if post_resume else "text",
            )

        return ProjectMode(
            mode="unknown",
            reason="default (post-resume)" if post_resume else "default",
        )

    def resolve_project_mode(self, planspace: Path) -> tuple[str, list[str]]:
        """Resolve the current project mode, pausing fail-closed when needed."""
        paths = PathRegistry(planspace)
        mode_json_path = paths.project_mode_json()
        mode_txt_path = paths.project_mode_txt()

        pm = self._read_project_mode_signal(
            mode_json_path,
            mode_txt_path,
            post_resume=False,
        )

        if pm.reason == "default":
            if mode_json_path.exists():
                self._logger.log("No text fallback — pausing for parent (fail-closed)")
                self._pipeline_control.pause_for_parent(
                    planspace,
                    f"pause:{PauseType.NEED_DECISION}:project-mode-malformed — "
                    "JSON parse failed and no text fallback exists",
                )
            else:
                self._logger.log("No project-mode signal found — pausing for parent "
                    "(fail-closed)")
                self._pipeline_control.pause_for_parent(
                    planspace,
                    f"pause:{PauseType.NEED_DECISION}:project-mode-missing — "
                    "scan stage did not write project-mode signal",
                )

            pm = self._read_project_mode_signal(
                mode_json_path,
                mode_txt_path,
                post_resume=True,
            )

        self._logger.log(f"Project mode: {pm.mode} (from {pm.reason})")
        return pm.mode, pm.evidence_files

    def write_mode_contract(
        self,
        planspace: Path,
        mode: str,
        constraints: list[str],
    ) -> None:
        """Write the formalized project-mode contract artifact."""
        paths = PathRegistry(planspace)
        self._artifact_io.write_json(
            paths.mode_contract(),
            {
                "mode": mode,
                "constraints": constraints,
                "expected_outputs": [
                    "integration proposals",
                    "code changes",
                    "alignment checks",
                ],
            },
        )


