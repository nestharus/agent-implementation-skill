"""Shared helpers for log extractors.

Functions here were extracted from gemini.py, codex.py, and run_db.py to
eliminate duplication.
"""

from __future__ import annotations

import sys
from collections.abc import Callable, Iterator
from pathlib import Path

from log_extract.models import SessionCandidate, TimelineEvent
from log_extract.utils import parse_timestamp


# ------------------------------------------------------------------
# Timestamp parsing
# ------------------------------------------------------------------


def safe_ts(
    value: object,
    *,
    source_label: str = "",
) -> tuple[str, int] | None:
    """Parse a single timestamp value, returning ``None`` on failure.

    Parameters
    ----------
    value:
        The raw timestamp (string, int, float, etc.).  ``None`` and
        other falsy values short-circuit to ``None``.
    source_label:
        Optional label (e.g. ``"codex"``, ``"run_db"``) included in
        the warning printed to stderr on malformed input.
    """
    if not value:
        return None
    try:
        return parse_timestamp(value)
    except (ValueError, TypeError) as exc:
        if source_label:
            print(
                f"{source_label}: skipping malformed timestamp {value!r}: {exc}",
                file=sys.stderr,
            )
        return None


def safe_ts_from_record(
    record: dict,
    keys: tuple[str, ...] = ("timestamp", "ts", "created_at", "create_time", "startTime"),
) -> tuple[str, int] | None:
    """Try several field names in *record* and return the first valid timestamp.

    Unlike :func:`safe_ts` this never prints a warning -- it silently
    moves on to the next candidate key.
    """
    for key in keys:
        raw = record.get(key)
        if raw is not None:
            try:
                return parse_timestamp(raw)
            except (ValueError, TypeError):
                continue
    return None


# ------------------------------------------------------------------
# Home-level iteration helpers
# ------------------------------------------------------------------


def events_from_home(
    home: Path,
    iter_files: Callable[[Path], Iterator[Path]],
    events_from_file: Callable[[Path], Iterator[TimelineEvent]],
    *,
    source_label: str = "",
) -> Iterator[TimelineEvent]:
    """Yield events by iterating files discovered under *home*.

    Parameters
    ----------
    home:
        Root directory to scan.
    iter_files:
        Callable that yields file paths under *home*.
    events_from_file:
        Callable that yields :class:`TimelineEvent` from one file.
    source_label:
        Label for error messages (e.g. ``"gemini"``).
    """
    for path in iter_files(home):
        try:
            yield from events_from_file(path)
        except Exception as exc:
            if source_label:
                print(f"{source_label}: error reading {path}: {exc}", file=sys.stderr)


def session_candidates_from_home(
    home: Path,
    iter_files: Callable[[Path], Iterator[Path]],
    candidate_from_file: Callable[[Path], SessionCandidate | None],
    *,
    source_label: str = "",
) -> Iterator[SessionCandidate]:
    """Yield session candidates by iterating files discovered under *home*.

    Parameters
    ----------
    home:
        Root directory to scan.
    iter_files:
        Callable that yields file paths under *home*.
    candidate_from_file:
        Callable that builds a :class:`SessionCandidate` from one file
        (returns ``None`` when the file cannot produce a valid candidate).
    source_label:
        Label for error messages (e.g. ``"gemini"``).
    """
    for path in iter_files(home):
        try:
            candidate = candidate_from_file(path)
            if candidate is not None:
                yield candidate
        except Exception as exc:
            if source_label:
                print(f"{source_label}: error reading {path}: {exc}", file=sys.stderr)
