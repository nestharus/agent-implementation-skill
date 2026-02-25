"""Coordination package — global problem coordinator for cross-section fixes.

Re-exports all public names so existing ``from section_loop.coordination import ...``
statements continue to work unchanged.
"""

from .execution import _dispatch_fix_group, write_coordinator_fix_prompt
from .planning import _parse_coordination_plan, write_coordination_plan_prompt
from .problems import (
    _collect_outstanding_problems,
    _detect_recurrence_patterns,
    build_file_to_sections,
)
from .runner import (
    MAX_COORDINATION_ROUNDS,
    MIN_COORDINATION_ROUNDS,
    run_global_coordination,
)

__all__ = [
    "MAX_COORDINATION_ROUNDS",
    "MIN_COORDINATION_ROUNDS",
    "build_file_to_sections",
    "_collect_outstanding_problems",
    "_detect_recurrence_patterns",
    "_parse_coordination_plan",
    "write_coordination_plan_prompt",
    "write_coordinator_fix_prompt",
    "_dispatch_fix_group",
    "run_global_coordination",
]
