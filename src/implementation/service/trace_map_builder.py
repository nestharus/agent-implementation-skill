"""Build trace maps linking problems → strategies → files for a section.

A trace map captures the provenance chain: which problems drove the
implementation, which TODOs were addressed, and which files changed.
"""

from __future__ import annotations

import re
from pathlib import Path

from containers import Services
from orchestrator.path_registry import PathRegistry
from proposal.repository.state import load_proposal_state


def build_trace_map(
    planspace: Path,
    codespace: Path,
    section_number: str,
    changed_files: list[str],
    related_files: list[str],
) -> dict:
    """Build and persist a trace map for the given section.

    Returns the trace map dict.
    """
    paths = PathRegistry(planspace)
    trace_map_path = paths.trace_map(section_number)
    trace_map_path.parent.mkdir(parents=True, exist_ok=True)

    ps = load_proposal_state(paths.proposal_state(section_number))
    trace_map = {
        "section": section_number,
        "problems": _extract_problems(paths, section_number),
        "strategies": [],
        "todo_ids": _extract_todo_ids(codespace, related_files),
        "files": list(changed_files),
        "governance": {
            "packet_path": str(paths.governance_packet(section_number)),
            "packet_hash": Services.hasher().file_hash(paths.governance_packet(section_number)),
            "problem_ids": [
                str(x) for x in ps.problem_ids
                if isinstance(x, str) and x.strip()
            ],
            "pattern_ids": [
                str(x) for x in ps.pattern_ids
                if isinstance(x, str) and x.strip()
            ],
            "profile_id": ps.profile_id or "",
        },
    }
    Services.artifact_io().write_json(trace_map_path, trace_map)
    Services.logger().log(f"Section {section_number}: trace-map written to {trace_map_path}")
    return trace_map


def _extract_problems(paths: PathRegistry, section_number: str) -> list[str]:
    """Extract problem statements from the section's problem frame."""
    problem_frame_path = paths.problem_frame(section_number)
    problems: list[str] = []
    if problem_frame_path.exists():
        text = problem_frame_path.read_text(encoding="utf-8")
        for line in text.split("\n"):
            stripped = line.strip()
            if stripped.startswith("- ") or stripped.startswith("* "):
                problems.append(stripped[2:])
    return problems


def _extract_todo_ids(
    codespace: Path, related_files: list[str],
) -> list[dict[str, str]]:
    """Extract TODO[id] markers from related source files."""
    todo_ids: list[dict[str, str]] = []
    for relative_path in related_files:
        full_path = codespace / relative_path
        if not full_path.exists():
            continue
        try:
            content = full_path.read_text(encoding="utf-8")
            for match in re.finditer(r"TODO\[([^\]]+)\]", content):
                todo_ids.append(
                    {"id": match.group(1), "file": relative_path}
                )
        except (OSError, UnicodeDecodeError):
            continue
    return todo_ids
