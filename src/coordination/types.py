"""Typed tokens for coordination loop outcomes."""

from __future__ import annotations

from enum import Enum


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
