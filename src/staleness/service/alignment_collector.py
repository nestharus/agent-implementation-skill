"""Pure helpers for section alignment handling."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from orchestrator.path_registry import PathRegistry

if TYPE_CHECKING:
    from containers import LogService


class AlignmentCollector:
    """Collects modified files and extracts alignment problems.

    All cross-cutting services are received via constructor injection.
    """

    def __init__(self, logger: LogService) -> None:
        self._logger = logger

    def _resolve_relative(
        self, line: str, codespace_resolved: Path, codespace: Path,
    ) -> str | None:
        """Resolve a reported path to a safe relative path under *codespace*.

        Returns the relative path string, or ``None`` if the path escapes codespace.
        """
        pp = Path(line)
        if pp.is_absolute():
            try:
                return str(pp.resolve().relative_to(codespace_resolved))
            except ValueError:
                self._logger.log(
                    f"  WARNING: reported path outside codespace, skipping: {line}",
                )
                return None
        full = (codespace / pp).resolve()
        try:
            return str(full.relative_to(codespace_resolved))
        except ValueError:
            self._logger.log(
                f"  WARNING: reported path escapes codespace, skipping: {line}",
            )
            return None

    def collect_modified_files(
        self,
        planspace: Path,
        section: Any,
        codespace: Path,
    ) -> list[str]:
        """Collect modified file paths from the implementation report.

        Normalizes all paths to safe relative paths under ``codespace``.
        Absolute paths are converted to relative (if under codespace) or
        rejected. Paths containing ``..`` that escape codespace are rejected.
        """
        paths = PathRegistry(planspace)
        modified_report = paths.impl_modified(section.number)
        if not modified_report.exists():
            return []
        codespace_resolved = codespace.resolve()
        modified: set[str] = set()
        for line in modified_report.read_text(encoding="utf-8").strip().split("\n"):
            line = line.strip()
            if not line:
                continue
            rel = self._resolve_relative(line, codespace_resolved, codespace)
            if rel is not None:
                modified.add(rel)
        return list(modified)


def extract_problems(verdict: dict[str, object] | None) -> str | None:
    """Convert a parsed alignment verdict into a problem summary."""
    if verdict is None:
        return None
    if verdict.get("aligned", False):
        return None
    problems = verdict.get("problems")
    if isinstance(problems, list) and problems:
        return "\n".join(str(p) for p in problems)
    if isinstance(problems, str) and problems.strip():
        return problems.strip()
    return "Alignment judge reported misaligned (no details in verdict)"


# --- Thin backward-compat wrappers (used by containers.py service classes) ---


def _resolve_relative(
    line: str, codespace_resolved: Path, codespace: Path,
) -> str | None:
    from containers import Services
    return AlignmentCollector(logger=Services.logger())._resolve_relative(
        line, codespace_resolved, codespace,
    )


def collect_modified_files(
    planspace: Path,
    section: Any,
    codespace: Path,
) -> list[str]:
    from containers import Services
    return AlignmentCollector(logger=Services.logger()).collect_modified_files(
        planspace, section, codespace,
    )
