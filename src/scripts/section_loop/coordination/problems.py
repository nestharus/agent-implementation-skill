"""Compatibility re-exports for coordination problem helpers."""

from lib.pipelines.coordination_problem_resolver import (
    _collect_outstanding_problems,
    _detect_recurrence_patterns,
    build_file_to_sections,
)

__all__ = [
    "_collect_outstanding_problems",
    "_detect_recurrence_patterns",
    "build_file_to_sections",
]
