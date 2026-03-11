from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Section:
    """A single section with its metadata and execution state."""

    number: str  # e.g., "01"
    path: Path
    global_proposal_path: Path = field(default_factory=Path)
    global_alignment_path: Path = field(default_factory=Path)
    related_files: list[str] = field(default_factory=list)
    solve_count: int = 0


@dataclass
class SectionResult:
    """Stores the outcome of a section's initial pass."""
    section_number: str
    aligned: bool = False
    problems: str | None = None
    modified_files: list[str] = field(default_factory=list)


@dataclass
class ProposalPassResult:
    """Structured result from a proposal-only pass through a section.

    Captures everything the proposal pass resolves — alignment status,
    readiness disposition, extracted blockers, reconciliation needs —
    so the orchestrator can inspect all sections before dispatching any
    implementation work.
    """

    section_number: str
    proposal_aligned: bool = False
    execution_ready: bool = False
    blockers: list[dict] = field(default_factory=list)
    needs_reconciliation: bool = False
    proposal_state_path: str = ""
