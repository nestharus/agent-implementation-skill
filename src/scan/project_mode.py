"""Project mode resolution helpers for the section loop."""

from __future__ import annotations

from pathlib import Path

from signals.artifact_io import read_json, write_json
from orchestrator.path_registry import PathRegistry
from signals.section_loop_communication import log
from orchestrator.pipeline_control import pause_for_parent


def _read_project_mode_signal(
    mode_json_path: Path,
    mode_txt_path: Path,
    *,
    post_resume: bool,
) -> tuple[str, list[str], str]:
    if mode_json_path.exists():
        mode_data = read_json(mode_json_path)
        if mode_data is not None:
            return (
                str(mode_data.get("mode", "unknown")),
                list(mode_data.get("constraints", [])),
                "JSON signal (post-resume)" if post_resume else "JSON signal",
            )

        log(
            "project-mode.json malformed"
            + (" after resume" if post_resume else "")
            + " — preserved as .malformed.json, trying text fallback",
        )
        if mode_txt_path.exists():
            return (
                mode_txt_path.read_text(encoding="utf-8").strip(),
                [],
                ("text (post-resume)"
                 if post_resume else "text (JSON malformed)"),
            )
        return "unknown", [], (
            "default (post-resume)" if post_resume else "default"
        )

    if mode_txt_path.exists():
        return (
            mode_txt_path.read_text(encoding="utf-8").strip(),
            [],
            "text (post-resume)" if post_resume else "text",
        )

    return "unknown", [], "default (post-resume)" if post_resume else "default"


def resolve_project_mode(planspace: Path, parent: str) -> tuple[str, list[str]]:
    """Resolve the current project mode, pausing fail-closed when needed."""
    paths = PathRegistry(planspace)
    mode_json_path = paths.project_mode_json()
    mode_txt_path = paths.project_mode_txt()

    project_mode, mode_constraints, mode_source = _read_project_mode_signal(
        mode_json_path,
        mode_txt_path,
        post_resume=False,
    )

    if mode_source == "default":
        if mode_json_path.exists():
            log("No text fallback — pausing for parent (fail-closed)")
            pause_for_parent(
                planspace,
                parent,
                "pause:needs_parent:project-mode-malformed — "
                "JSON parse failed and no text fallback exists",
            )
        else:
            log("No project-mode signal found — pausing for parent "
                "(fail-closed)")
            pause_for_parent(
                planspace,
                parent,
                "pause:needs_parent:project-mode-missing — "
                "scan stage did not write project-mode signal",
            )

        project_mode, mode_constraints, mode_source = _read_project_mode_signal(
            mode_json_path,
            mode_txt_path,
            post_resume=True,
        )

    log(f"Project mode: {project_mode} (from {mode_source})")
    return project_mode, mode_constraints


def write_mode_contract(
    planspace: Path,
    mode: str,
    constraints: list[str],
) -> None:
    """Write the formalized project-mode contract artifact."""
    paths = PathRegistry(planspace)
    write_json(
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
