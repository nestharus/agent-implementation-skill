"""FreshnessService: canonical section freshness token computation."""

from __future__ import annotations

from pathlib import Path

from lib.core.hash_service import content_hash
from lib.core.path_registry import PathRegistry


def compute_section_freshness(planspace: Path, section_number: str) -> str:
    """Compute a canonical alignment fingerprint for a section."""
    hash_parts: list[bytes] = []
    registry = PathRegistry(planspace)
    sec = section_number

    for path in (
        registry.alignment_excerpt(sec),
        registry.proposal_excerpt(sec),
        registry.section_spec(sec),
        registry.proposal(sec),
    ):
        if path.exists():
            hash_parts.append(path.read_bytes())

    notes_dir = registry.notes_dir()
    if notes_dir.exists():
        for note in sorted(notes_dir.glob(f"from-*-to-{sec}.md")):
            hash_parts.append(note.read_bytes())

    tools_path = registry.tool_registry()
    if tools_path.exists():
        hash_parts.append(tools_path.read_bytes())

    decisions_path = registry.decisions_dir() / f"section-{sec}.md"
    if decisions_path.exists():
        hash_parts.append(decisions_path.read_bytes())

    microstrategy_path = registry.microstrategy(sec)
    if microstrategy_path.exists():
        hash_parts.append(microstrategy_path.read_bytes())

    todos_path = registry.todos(sec)
    if todos_path.exists():
        hash_parts.append(todos_path.read_bytes())

    codemap_path = registry.codemap()
    if codemap_path.exists():
        hash_parts.append(codemap_path.read_bytes())
    corrections_path = registry.corrections()
    if corrections_path.exists():
        hash_parts.append(corrections_path.read_bytes())

    for mode_file in (
        registry.project_mode_txt(),
        registry.project_mode_json(),
        registry.sections_dir() / f"section-{sec}-mode.txt",
    ):
        if mode_file.exists():
            hash_parts.append(mode_file.read_bytes())

    problem_frame = registry.problem_frame(sec)
    if problem_frame.exists():
        hash_parts.append(problem_frame.read_bytes())

    intent_global = registry.intent_global_dir()
    for intent_file in (
        intent_global / "philosophy.md",
        intent_global / "philosophy-source-manifest.json",
        intent_global / "philosophy-source-map.json",
    ):
        if intent_file.exists():
            hash_parts.append(intent_file.read_bytes())
    intent_sec_dir = registry.intent_section_dir(sec)
    for intent_file in (
        intent_sec_dir / "problem.md",
        intent_sec_dir / "problem-alignment.md",
        intent_sec_dir / "philosophy-excerpt.md",
    ):
        if intent_file.exists():
            hash_parts.append(intent_file.read_bytes())

    proposal_state_path = (
        registry.proposals_dir() / f"section-{sec}-proposal-state.json"
    )
    if proposal_state_path.exists():
        hash_parts.append(proposal_state_path.read_bytes())

    reconciliation_path = (
        registry.reconciliation_dir()
        / f"section-{sec}-reconciliation-result.json"
    )
    if reconciliation_path.exists():
        hash_parts.append(reconciliation_path.read_bytes())

    readiness_path = (
        registry.readiness_dir()
        / f"section-{sec}-execution-ready.json"
    )
    if readiness_path.exists():
        hash_parts.append(readiness_path.read_bytes())

    # Research artifacts steer proposal/implementation prompts and expansion.
    research_dossier = registry.research_dossier(sec)
    if research_dossier.exists():
        hash_parts.append(research_dossier.read_bytes())

    research_addendum = registry.research_addendum(sec)
    if research_addendum.exists():
        hash_parts.append(research_addendum.read_bytes())

    research_derived_surfaces = registry.research_derived_surfaces(sec)
    if research_derived_surfaces.exists():
        hash_parts.append(research_derived_surfaces.read_bytes())

    # Implementation feedback surfaces are part of the upward discovery signal.
    impl_feedback = registry.impl_feedback_surfaces(sec)
    if impl_feedback.exists():
        hash_parts.append(impl_feedback.read_bytes())

    research_status = (
        registry.research_section_dir(sec) / "research-status.json"
    )
    if research_status.exists():
        hash_parts.append(research_status.read_bytes())

    governance_packet = registry.governance_packet(sec)
    if governance_packet.exists():
        hash_parts.append(governance_packet.read_bytes())

    return content_hash(b"".join(hash_parts))[:16]
