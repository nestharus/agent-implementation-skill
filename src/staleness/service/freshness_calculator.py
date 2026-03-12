"""FreshnessService: canonical section freshness token computation."""

from __future__ import annotations

from pathlib import Path

from staleness.helpers.content_hasher import content_hash
from orchestrator.path_registry import PathRegistry


def compute_section_freshness(planspace: Path, section_number: str) -> str:
    """Compute a canonical alignment fingerprint for a section."""
    hash_parts: list[bytes] = []
    registry = PathRegistry(planspace)
    sec = section_number

    def _add(path: Path) -> None:
        if path.exists():
            hash_parts.append(path.read_bytes())

    for path in (
        registry.alignment_excerpt(sec),
        registry.proposal_excerpt(sec),
        registry.section_spec(sec),
        registry.proposal(sec),
    ):
        _add(path)

    notes_dir = registry.notes_dir()
    if notes_dir.exists():
        for note in sorted(notes_dir.glob(f"from-*-to-{sec}.md")):
            hash_parts.append(note.read_bytes())

    _add(registry.tool_registry())

    decisions_path = registry.decisions_dir() / f"section-{sec}.md"
    _add(decisions_path)

    _add(registry.microstrategy(sec))
    _add(registry.todos(sec))

    _add(registry.codemap())
    _add(registry.corrections())

    for mode_file in (
        registry.project_mode_txt(),
        registry.project_mode_json(),
        registry.section_mode_txt(sec),
    ):
        _add(mode_file)

    _add(registry.problem_frame(sec))

    intent_global = registry.intent_global_dir()
    for intent_file in (
        intent_global / "philosophy.md",
        intent_global / "philosophy-source-manifest.json",
        intent_global / "philosophy-source-map.json",
    ):
        _add(intent_file)
    intent_sec_dir = registry.intent_section_dir(sec)
    for intent_file in (
        intent_sec_dir / "problem.md",
        intent_sec_dir / "problem-alignment.md",
        intent_sec_dir / "philosophy-excerpt.md",
    ):
        _add(intent_file)

    proposal_state_path = (
        registry.proposals_dir() / f"section-{sec}-proposal-state.json"
    )
    _add(proposal_state_path)

    reconciliation_path = (
        registry.reconciliation_dir()
        / f"section-{sec}-reconciliation-result.json"
    )
    _add(reconciliation_path)

    readiness_path = (
        registry.readiness_dir()
        / f"section-{sec}-execution-ready.json"
    )
    _add(readiness_path)

    # Research artifacts steer proposal/implementation prompts and expansion.
    _add(registry.research_dossier(sec))
    _add(registry.research_addendum(sec))
    _add(registry.research_derived_surfaces(sec))

    # Implementation feedback surfaces are part of the upward discovery signal.
    _add(registry.impl_feedback_surfaces(sec))

    research_status = (
        registry.research_section_dir(sec) / "research-status.json"
    )
    _add(research_status)

    _add(registry.governance_packet(sec))

    return content_hash(b"".join(hash_parts))[:16]
