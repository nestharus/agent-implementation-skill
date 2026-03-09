from __future__ import annotations

from pathlib import Path
from typing import Any

from lib.core.hash_service import content_hash
from lib.core.path_registry import PathRegistry


def section_inputs_hash(
    sec_num: str,
    planspace: Path,
    codespace: Path,
    sections_by_num: dict[str, Any],
) -> str:
    """Compute a hash of a section's alignment-relevant inputs."""
    del codespace

    hash_parts: list[bytes] = []
    paths = PathRegistry(planspace)

    for excerpt_path in (
        paths.proposal_excerpt(sec_num),
        paths.alignment_excerpt(sec_num),
    ):
        if excerpt_path.exists():
            hash_parts.append(excerpt_path.read_bytes())

    section = sections_by_num.get(sec_num)
    if section and section.related_files:
        hash_parts.append(
            "\n".join(sorted(section.related_files)).encode("utf-8"),
        )

    notes_dir = paths.notes_dir()
    if notes_dir.exists():
        for note in sorted(notes_dir.glob(f"from-*-to-{sec_num}.md")):
            hash_parts.append(note.read_bytes())

    tool_registry_path = paths.tool_registry()
    if tool_registry_path.exists():
        hash_parts.append(tool_registry_path.read_bytes())

    for input_path in (
        paths.section_spec(sec_num),
        paths.decisions_dir() / f"section-{sec_num}.md",
        paths.proposal(sec_num),
        paths.microstrategy(sec_num),
        paths.todos(sec_num),
        paths.codemap(),
        paths.corrections(),
        paths.project_mode_txt(),
        paths.project_mode_json(),
        paths.sections_dir() / f"section-{sec_num}-mode.txt",
        paths.problem_frame(sec_num),
        paths.proposals_dir() / f"section-{sec_num}-proposal-state.json",
        paths.reconciliation_dir()
        / f"section-{sec_num}-reconciliation-result.json",
        paths.readiness_dir() / f"section-{sec_num}-execution-ready.json",
        paths.research_dossier(sec_num),
        paths.research_addendum(sec_num),
        paths.research_derived_surfaces(sec_num),
        paths.impl_feedback_surfaces(sec_num),
        paths.research_section_dir(sec_num) / "research-status.json",
        paths.intent_global_dir() / "philosophy.md",
        paths.intent_global_dir() / "philosophy-source-manifest.json",
        paths.intent_global_dir() / "philosophy-source-map.json",
        paths.intent_section_dir(sec_num) / "problem.md",
        paths.intent_section_dir(sec_num) / "problem-alignment.md",
        paths.intent_section_dir(sec_num) / "philosophy-excerpt.md",
    ):
        if input_path.exists():
            hash_parts.append(input_path.read_bytes())

    for ms_path in sorted(paths.artifacts.glob(f"microstrategy-{sec_num}*.md")):
        hash_parts.append(ms_path.read_bytes())

    inputs_dir = paths.input_refs_dir(sec_num)
    if inputs_dir.exists():
        for ref_path in sorted(inputs_dir.glob("*.ref")):
            hash_parts.append(ref_path.read_bytes())
            try:
                referenced = Path(ref_path.read_text(encoding="utf-8").strip())
                if referenced.exists():
                    hash_parts.append(referenced.read_bytes())
            except (OSError, ValueError) as exc:
                hash_parts.append(f"REF_READ_ERROR:{ref_path}".encode("utf-8"))
                print(
                    f"[HASH][WARN] Failed to read ref {ref_path}: {exc}",
                )

    return content_hash(b"".join(hash_parts))


def coordination_recheck_hash(
    sec_num: str,
    planspace: Path,
    codespace: Path,
    sections_by_num: dict[str, Any],
    modified_files: list[str],
) -> str:
    """Canonical section-input hash plus coordinator-modified files."""
    base = section_inputs_hash(sec_num, planspace, codespace, sections_by_num)
    hash_parts = [base.encode("utf-8")]
    for mod_f in sorted(modified_files):
        mod_path = codespace / mod_f
        if mod_path.exists():
            hash_parts.append(mod_path.read_bytes())
    return content_hash(b"".join(hash_parts))
