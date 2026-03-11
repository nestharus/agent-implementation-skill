"""Pure helpers for section alignment handling."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from orchestrator.path_registry import PathRegistry


def collect_modified_files(
    planspace: Path,
    section: Any,
    codespace: Path,
    *,
    logger: Callable[[str], None] | None = None,
) -> list[str]:
    """Collect modified file paths from the implementation report.

    Normalizes all paths to safe relative paths under ``codespace``.
    Absolute paths are converted to relative (if under codespace) or
    rejected. Paths containing ``..`` that escape codespace are rejected.
    """
    paths = PathRegistry(planspace)
    modified_report = paths.impl_modified(section.number)
    codespace_resolved = codespace.resolve()
    modified = set()
    if modified_report.exists():
        for line in modified_report.read_text(encoding="utf-8").strip().split("\n"):
            line = line.strip()
            if not line:
                continue
            pp = Path(line)
            if pp.is_absolute():
                try:
                    rel = pp.resolve().relative_to(codespace_resolved)
                except ValueError:
                    if logger is not None:
                        logger(
                            "  WARNING: reported path outside codespace, "
                            f"skipping: {line}",
                        )
                    continue
            else:
                full = (codespace / pp).resolve()
                try:
                    rel = full.relative_to(codespace_resolved)
                except ValueError:
                    if logger is not None:
                        logger(
                            "  WARNING: reported path escapes codespace, "
                            f"skipping: {line}",
                        )
                    continue
            modified.add(str(rel))
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
