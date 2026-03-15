from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from orchestrator.path_registry import PathRegistry

if TYPE_CHECKING:
    from containers import ArtifactIOService, LogService


class RecurrenceEmitter:
    """Emits recurrence signals for sections solved multiple times."""

    def __init__(
        self,
        artifact_io: ArtifactIOService,
        logger: LogService,
    ) -> None:
        self._artifact_io = artifact_io
        self._logger = logger

    def emit_recurrence_signal(
        self,
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
        recurrence_path = PathRegistry(planspace).recurrence_signal(section_number)
        self._artifact_io.write_json(recurrence_path, recurrence_signal)
        self._logger.log(
            f"Section {section_number}: recurrence signal written "
            f"(attempt {solve_count})"
        )
