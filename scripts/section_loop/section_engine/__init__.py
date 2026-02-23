"""section_engine package — decomposed from the monolithic section_engine.py.

Re-exports all public names so that ``from section_loop.section_engine import X``
continues to work unchanged.
"""

from .blockers import _append_open_problem, _update_blocker_rollup
from .reexplore import _reexplore_section, _write_alignment_surface
from .runner import run_section
from .todos import _check_needs_microstrategy, _extract_todos_from_files
from .traceability import _file_sha256, _verify_traceability, _write_traceability_index

__all__ = [
    "run_section",
    "_reexplore_section",
    "_extract_todos_from_files",
    "_check_needs_microstrategy",
    "_append_open_problem",
    "_update_blocker_rollup",
    "_write_alignment_surface",
    "_file_sha256",
    "_write_traceability_index",
    "_verify_traceability",
]
