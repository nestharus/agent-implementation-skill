"""Coordination problem type hierarchy.

Discriminated union of frozen dataclasses. Each leaf has exactly its
fields — no optionals. The ``type`` field is fixed per subclass via
``init=False``, so callers cannot misspell it.

Cross-cutting aspect: ``NoteProblem`` captures the ``note_id`` field
shared by consequence_conflict, pending_negotiation, and
unaddressed_note problems.

Hierarchy::

    Problem (section, type, description, files)
    ├── BlockerProblem (+needs)
    ├── MisalignedProblem ()
    ├── NoteProblem (+note_id)
    │   ├── ConflictProblem ()
    │   ├── NegotiationProblem ()
    │   └── UnaddressedNoteProblem (+note_path)
    └── ScopeDeltaProblem (+delta_id, title, source, source_sections)
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field


@dataclass(frozen=True)
class Problem:
    """Base type for all coordination problems.

    Consumer sites type their parameters as ``Problem`` — they only
    need the 4 common fields.
    """

    section: str
    type: str
    description: str
    files: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        """Serialize to plain dict for JSON persistence."""
        return asdict(self)


@dataclass(frozen=True)
class BlockerProblem(Problem):
    """Section blocked — needs parent intervention."""

    type: str = field(default="needs_parent", init=False)
    needs: str = ""


@dataclass(frozen=True)
class MisalignedProblem(Problem):
    """Section is misaligned but not blocked."""

    type: str = field(default="misaligned", init=False)


# -- Note-tracked aspect (shared by 3 problem types) --


@dataclass(frozen=True)
class NoteProblem(Problem):
    """Intermediate: problem tracked by a consequence note ID."""

    note_id: str = ""


@dataclass(frozen=True)
class ConflictProblem(NoteProblem):
    """Consequence note was rejected — conflict needs resolution."""

    type: str = field(default="consequence_conflict", init=False)


@dataclass(frozen=True)
class NegotiationProblem(NoteProblem):
    """Consequence note was deferred — pending negotiation."""

    type: str = field(default="pending_negotiation", init=False)


@dataclass(frozen=True)
class UnaddressedNoteProblem(NoteProblem):
    """Consequence note has not been acknowledged."""

    type: str = field(default="unaddressed_note", init=False)
    note_path: str = ""


# -- Scope delta problems --


@dataclass(frozen=True)
class ScopeDeltaProblem(Problem):
    """Pending scope delta requires root reframing."""

    type: str = field(default="root_reframing", init=False)
    delta_id: str = ""
    title: str = ""
    source: str = ""
    source_sections: list[str] = field(default_factory=list)
