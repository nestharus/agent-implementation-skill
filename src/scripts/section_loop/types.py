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
