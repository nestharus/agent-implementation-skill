from __future__ import annotations

from pathlib import Path

from lib.core.artifact_io import write_json
from lib.core.path_registry import PathRegistry
from section_loop.communication import log


def emit_recurrence_signal(
    planspace: Path,
    section_number: str,
    solve_count: int,
) -> None:
    """Write the recurrence signal for sections solved multiple times."""
    recurrence_signal = {
        "section": section_number,
        "attempt": solve_count,
        "recurring": True,
        "escalate_to_coordinator": True,
    }
    recurrence_path = (
        PathRegistry(planspace).signals_dir()
        / f"section-{section_number}-recurrence.json"
    )
    write_json(recurrence_path, recurrence_signal)
    log(
        f"Section {section_number}: recurrence signal written "
        f"(attempt {solve_count})"
    )
