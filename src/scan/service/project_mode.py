"""Project mode resolution helpers for the section loop."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from orchestrator.path_registry import PathRegistry
from containers import Services


@dataclass(frozen=True)
class ProjectMode:
    """Structured result from ``_read_project_mode_signal``."""

    mode: str
    evidence_files: list[str] = field(default_factory=list)
    reason: str = ""


def _read_project_mode_signal(
    mode_json_path: Path,
    mode_txt_path: Path,
    *,
    post_resume: bool,
) -> ProjectMode:
    if mode_json_path.exists():
        mode_data = Services.artifact_io().read_json(mode_json_path)
        if mode_data is not None:
            return ProjectMode(
                mode=str(mode_data.get("mode", "unknown")),
                evidence_files=list(mode_data.get("constraints", [])),
                reason="JSON signal (post-resume)" if post_resume else "JSON signal",
            )

        Services.logger().log(
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


def resolve_project_mode(planspace: Path, parent: str) -> tuple[str, list[str]]:
    """Resolve the current project mode, pausing fail-closed when needed."""
    paths = PathRegistry(planspace)
    mode_json_path = paths.project_mode_json()
    mode_txt_path = paths.project_mode_txt()

    pm = _read_project_mode_signal(
        mode_json_path,
        mode_txt_path,
        post_resume=False,
    )

    if pm.reason == "default":
        if mode_json_path.exists():
            Services.logger().log("No text fallback — pausing for parent (fail-closed)")
            Services.pipeline_control().pause_for_parent(
                planspace,
                parent,
                "pause:needs_parent:project-mode-malformed — "
                "JSON parse failed and no text fallback exists",
            )
        else:
            Services.logger().log("No project-mode signal found — pausing for parent "
                "(fail-closed)")
            Services.pipeline_control().pause_for_parent(
                planspace,
                parent,
                "pause:needs_parent:project-mode-missing — "
                "scan stage did not write project-mode signal",
            )

        pm = _read_project_mode_signal(
            mode_json_path,
            mode_txt_path,
            post_resume=True,
        )

    Services.logger().log(f"Project mode: {pm.mode} (from {pm.reason})")
    return pm.mode, pm.evidence_files


def write_mode_contract(
    planspace: Path,
    mode: str,
    constraints: list[str],
) -> None:
    """Write the formalized project-mode contract artifact."""
    paths = PathRegistry(planspace)
    Services.artifact_io().write_json(
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
