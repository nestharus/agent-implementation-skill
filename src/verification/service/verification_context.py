"""Write verification context JSON consumed by verification/testing agents.

Each verification or testing task needs a context file describing what to
check: section number, scope, file paths under inspection, consequence
note paths, and optional risk context.  The flow chain builder calls
``write_verification_context()`` once per queued task and stores the
result at the path returned by ``PathRegistry.verification_context()``.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from orchestrator.path_registry import PathRegistry

if TYPE_CHECKING:
    from containers import ArtifactIOService


def write_verification_context(
    artifact_io: ArtifactIOService,
    planspace: Path,
    section_number: str,
    task_type: str,
    scope: str,
    *,
    consequence_note_paths: list[str] | None = None,
    risk_context_path: str | None = None,
    codemap_refresh: bool = False,
    max_tests: int | None = None,
) -> Path:
    """Persist a verification context JSON and return its path.

    Parameters
    ----------
    artifact_io:
        DI-provided JSON I/O service.
    planspace:
        Root of the planspace directory tree.
    section_number:
        Section this verification task targets.
    task_type:
        Short task-type suffix (``structural``, ``integration``,
        ``behavioral``).
    scope:
        Scope qualifier read by the agent (e.g. ``imports_only``,
        ``full``, ``consequence_notes``, ``expanded``).
    consequence_note_paths:
        Paths to inbound consequence-note files, if any.
    risk_context_path:
        Path to the risk assessment JSON, if P3+ risk-aware testing.
    codemap_refresh:
        Whether the agent should trigger a codemap refresh before
        verification.
    max_tests:
        Test count cap for ``testing.behavioral`` tasks.
    """
    paths = PathRegistry(planspace)
    context: dict = {
        "section_number": section_number,
        "task_type": task_type,
        "scope": scope,
        "codemap_path": str(paths.codemap()),
        "section_spec_path": str(paths.section_spec(section_number)),
        "problem_frame_path": str(paths.problem_frame(section_number)),
        "proposal_state_path": str(paths.proposal_state(section_number)),
        "impl_modified_path": str(paths.impl_modified(section_number)),
    }

    if consequence_note_paths:
        context["consequence_note_paths"] = consequence_note_paths

    if risk_context_path:
        context["risk_context_path"] = risk_context_path

    if codemap_refresh:
        context["codemap_refresh"] = True

    if max_tests is not None:
        context["max_tests"] = max_tests

    dest = paths.verification_context(section_number, task_type)
    dest.parent.mkdir(parents=True, exist_ok=True)
    artifact_io.write_json(dest, context)
    return dest
