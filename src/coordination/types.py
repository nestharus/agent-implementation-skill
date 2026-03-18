"""Typed tokens for coordination loop outcomes."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum

from coordination.problem_types import Problem


class CoordinationStatus(str, Enum):
    """Outcome of a coordination loop or global alignment recheck.

    Inherits from ``str`` so that ``==`` comparisons with plain strings
    continue to work (backward compatibility).
    """

    RESTART_PHASE1 = "restart_phase1"
    COMPLETE = "complete"
    EXHAUSTED = "exhausted"
    STALLED = "stalled"

    # Returned by run_global_alignment_recheck
    ALL_ALIGNED = "all_aligned"
    HAS_PROBLEMS = "has_problems"

    def __str__(self) -> str:  # noqa: D105
        return self.value


class CoordinationStrategy(str, Enum):
    """Strategy for executing a coordination problem group."""

    SEQUENTIAL = "sequential"
    PARALLEL = "parallel"
    SCAFFOLD_ASSIGN = "scaffold_assign"

    def __str__(self) -> str:  # noqa: D105
        return self.value


class NoteAction(str, Enum):
    """Action taken on a cross-section note acknowledgment."""

    ACCEPTED = "accepted"
    REJECTED = "rejected"
    DEFERRED = "deferred"

    def __str__(self) -> str:  # noqa: D105
        return self.value


# ---------------------------------------------------------------------------
# Coordination data structures
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class BridgeDirective:
    """Bridge instruction for a problem group.

    Replaces the loose ``{"needed": bool, "reason": str}`` dict that
    previously traveled through the coordination pipeline.
    """

    needed: bool = False
    reason: str = ""


@dataclass(frozen=True)
class RecurrenceReport:
    """Recurrence detection report for coordination rounds.

    Replaces the loose ``dict[str, Any]`` returned by
    ``detect_recurrence_patterns``.
    """

    recurring_sections: list[str]
    recurring_problem_count: int
    max_attempt: int
    problem_indices: list[int]

    def to_dict(self) -> dict:
        """Serialize to plain dict for JSON persistence."""
        return asdict(self)


@dataclass(frozen=True)
class ProblemGroup:
    """A group of causally related problems to fix together.

    Created by the coordination planner, which groups problems based
    on shared root causes, file overlaps, and fix-ordering constraints.
    Replaces the parallel ``list[list[Problem]]`` + ``list[str]``
    structures that previously carried group metadata separately.
    """

    problems: list[Problem]
    strategy: CoordinationStrategy = CoordinationStrategy.SEQUENTIAL
    reason: str = ""
    bridge: BridgeDirective = field(default_factory=BridgeDirective)
