"""Pure task-ingestion helpers for flow signal parsing."""

from __future__ import annotations

from pathlib import Path
import re
from typing import TYPE_CHECKING

from flow.types.schema import (
    ChainAction,
    FlowDeclaration,
    TaskSpec,
    parse_flow_signal,
    validate_flow_declaration,
)

if TYPE_CHECKING:
    from containers import ArtifactIOService, LogService

_SECTION_SCOPE_RE = re.compile(r"^section-(\d+)$")


def extract_legacy_tasks(decl: FlowDeclaration) -> list[dict]:
    """Extract flat task dicts from a legacy (v1) FlowDeclaration."""
    tasks: list[dict] = []
    for action in decl.actions:
        if isinstance(action, ChainAction):
            for step in action.steps:
                task: dict = {"task_type": step.task_type}
                if step.concern_scope:
                    task["concern_scope"] = step.concern_scope
                if step.payload_path:
                    task["payload_path"] = step.payload_path
                if step.priority and step.priority != "normal":
                    task["priority"] = step.priority
                if step.problem_id:
                    task["problem_id"] = step.problem_id
                tasks.append(task)
    return tasks


def find_first_section_scope(steps: list[TaskSpec]) -> str | None:
    """Return the first section number referenced by a chain step."""
    for step in steps:
        if not step.concern_scope:
            continue
        match = _SECTION_SCOPE_RE.match(step.concern_scope)
        if match:
            return match.group(1)
    return None


class FlowSignalParser:
    def __init__(self, logger: LogService, artifact_io: ArtifactIOService) -> None:
        self._logger = logger
        self._artifact_io = artifact_io

    def parse_signal_file(
        self,
        signal_path: Path,
    ) -> FlowDeclaration | None:
        """Parse a task-request signal file into a FlowDeclaration."""
        if not signal_path.exists():
            return None

        raw = signal_path.read_text(encoding="utf-8").strip()
        if not raw:
            signal_path.unlink(missing_ok=True)
            return None

        log = self._logger.log
        try:
            decl = parse_flow_signal(signal_path)
        except ValueError as exc:
            log(
                "  task_ingestion: WARNING - malformed signal in "
                f"{signal_path} ({exc}), renaming to .malformed.json",
            )
            self._artifact_io.rename_malformed(signal_path)
            return None

        if decl.version >= 2:
            errors = validate_flow_declaration(decl)
            if errors:
                log(
                    "  task_ingestion: WARNING - v2 flow declaration in "
                    f"{signal_path} has validation errors: {errors}",
                )
                self._artifact_io.rename_malformed(signal_path)
                return None

        signal_path.unlink(missing_ok=True)
        return decl

    def ingest_task_requests(
        self,
        signal_path: Path,
    ) -> list[dict]:
        """Read and parse a task-request signal file."""
        log = self._logger.log
        decl = self.parse_signal_file(signal_path)
        if decl is None:
            return []

        if decl.version >= 2:
            log(
                "  task_ingestion: WARNING - v2 flow actions should use "
                "ingest_and_submit, skipping",
            )
            return []

        entries = extract_legacy_tasks(decl)
        valid: list[dict] = []
        for entry in entries:
            if "task_type" not in entry:
                log(
                    "  task_ingestion: WARNING - skipping entry without "
                    f"task_type: {entry!r}",
                )
                continue
            valid.append(entry)

        return valid


# Backward-compat wrappers

def _get_parser() -> FlowSignalParser:
    from containers import Services
    return FlowSignalParser(
        logger=Services.logger(),
        artifact_io=Services.artifact_io(),
    )


def parse_signal_file(
    signal_path: Path,
) -> FlowDeclaration | None:
    return _get_parser().parse_signal_file(signal_path)


def ingest_task_requests(
    signal_path: Path,
) -> list[dict]:
    return _get_parser().ingest_task_requests(signal_path)
