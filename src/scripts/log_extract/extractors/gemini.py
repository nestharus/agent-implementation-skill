"""Best-effort extractor for Gemini CLI session logs.

Gemini CLI stores history under ``<gemini_home>/history/<project>/...``
as JSON or JSONL files.  In practice Gemini is mostly used in headless
``--yolo`` mode with ``prompt_mode = "arg"``, which does NOT persist
session logs, so the history directory is often empty.  This extractor
handles that gracefully -- missing or empty directories produce zero
events and zero errors.

All errors are emitted as warnings to stderr; this extractor must never
block system success.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Iterator
from pathlib import Path

from log_extract.extractors.common import (
    events_from_home,
    safe_ts_from_record,
    session_candidates_from_home,
)
from log_extract.models import SessionCandidate, TimelineEvent
from log_extract.utils import prompt_signature, summarize_text

_SOURCE = "gemini"
_BACKEND = "gemini"
_SOURCE_FAMILY = "gemini"


# ------------------------------------------------------------------
# Public API
# ------------------------------------------------------------------


def iter_events(gemini_homes: list[Path]) -> Iterator[TimelineEvent]:
    """Yield :class:`TimelineEvent` objects from Gemini CLI history.

    Parameters
    ----------
    gemini_homes:
        Paths to Gemini CLI home directories, each expected to contain
        a ``history/`` subdirectory.  Missing or empty directories are
        silently skipped.
    """
    for home in gemini_homes:
        try:
            yield from _events_from_home(home)
        except Exception as exc:  # noqa: BLE001 — best-effort home scanning
            print(f"gemini: unexpected error scanning {home}: {exc}", file=sys.stderr)


def iter_session_candidates(gemini_homes: list[Path]) -> Iterator[SessionCandidate]:
    """Yield :class:`SessionCandidate` objects from Gemini CLI history.

    Parameters
    ----------
    gemini_homes:
        Paths to Gemini CLI home directories.
    """
    for home in gemini_homes:
        try:
            yield from _session_candidates_from_home(home)
        except Exception as exc:  # noqa: BLE001 — best-effort home scanning
            print(f"gemini: unexpected error scanning {home}: {exc}", file=sys.stderr)


# ------------------------------------------------------------------
# Internal: home-level iteration
# ------------------------------------------------------------------


def _iter_history_files(home: Path) -> Iterator[Path]:
    """Find JSON and JSONL files under ``<home>/history/``."""
    history_dir = home / "history"
    if not history_dir.is_dir():
        return
    for path in sorted(history_dir.rglob("*")):
        if path.is_file() and path.suffix in (".json", ".jsonl"):
            yield path


def _events_from_home(home: Path) -> Iterator[TimelineEvent]:
    yield from events_from_home(
        home, _iter_history_files, _events_from_file, source_label="gemini",
    )


def _session_candidates_from_home(home: Path) -> Iterator[SessionCandidate]:
    yield from session_candidates_from_home(
        home, _iter_history_files, _session_candidate_from_file, source_label="gemini",
    )


# ------------------------------------------------------------------
# Internal: record parsing
# ------------------------------------------------------------------


def _load_records(path: Path) -> Iterator[dict]:
    """Yield parsed JSON records from a JSON or JSONL file.

    A ``.json`` file may contain a single object or an array of objects.
    A ``.jsonl`` file is treated line-by-line.  Malformed content is
    warned and skipped.
    """
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        print(f"gemini: cannot read {path}: {exc}", file=sys.stderr)
        return

    if not text.strip():
        return

    if path.suffix == ".jsonl":
        yield from _parse_jsonl(path, text)
    else:
        yield from _parse_json(path, text)


def _parse_jsonl(path: Path, text: str) -> Iterator[dict]:
    for lineno, line in enumerate(text.splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError as exc:
            print(
                f"WARNING: gemini: {path}:{lineno}: skipping malformed line: {exc}",
                file=sys.stderr,
            )
            continue
        if isinstance(obj, dict):
            yield obj


def _parse_json(path: Path, text: str) -> Iterator[dict]:
    try:
        obj = json.loads(text)
    except json.JSONDecodeError as exc:
        print(
            f"WARNING: gemini: {path}: skipping malformed JSON: {exc}",
            file=sys.stderr,
        )
        return

    if isinstance(obj, dict):
        yield obj
    elif isinstance(obj, list):
        for item in obj:
            if isinstance(item, dict):
                yield item


def _safe_ts(record: dict) -> tuple[str, int] | None:
    """Try to extract a parsed timestamp from common field names."""
    return safe_ts_from_record(record)


def _extract_role(record: dict) -> str:
    """Best-effort role extraction from a record."""
    return str(record.get("role", record.get("author", ""))).lower()


def _join_parts(items: list) -> str:
    """Join a list of string/dict parts into a single text string."""
    parts: list[str] = []
    for item in items:
        if isinstance(item, str):
            parts.append(item)
        elif isinstance(item, dict):
            t = item.get("text", "")
            if t:
                parts.append(t)
    return " ".join(parts) if parts else ""


def _extract_text(record: dict) -> str:
    """Best-effort text extraction from a record."""
    for key in ("text", "content", "message", "body"):
        val = record.get(key)
        if isinstance(val, str) and val.strip():
            return val
        if isinstance(val, list):
            joined = _join_parts(val)
            if joined:
                return joined
    parts_field = record.get("parts")
    if isinstance(parts_field, list):
        return _join_parts(parts_field)
    return ""


def _extract_id(record: dict, path: Path) -> str:
    """Best-effort session/record id extraction."""
    for key in ("id", "session_id", "sessionId", "name"):
        val = record.get(key)
        if val and isinstance(val, str):
            return val
    return ""


# ------------------------------------------------------------------
# Events from a single file
# ------------------------------------------------------------------


def _events_from_file(path: Path) -> Iterator[TimelineEvent]:
    for record in _load_records(path):
        ts_pair = _safe_ts(record)
        if ts_pair is None:
            continue

        ts_str, ts_ms = ts_pair
        role = _extract_role(record)
        text = _extract_text(record)
        detail = summarize_text(text) if text else ""
        record_id = _extract_id(record, path)

        if role in ("model", "assistant", "bot"):
            kind = "response"
        elif role in ("user", "human"):
            kind = "message"
        else:
            # Generic record with a timestamp -- treat as session info
            kind = "session" if record_id else "message"

        yield TimelineEvent(
            ts=ts_str,
            ts_ms=ts_ms,
            source=_SOURCE,
            kind=kind,
            detail=detail,
            session_id=record_id,
            backend=_BACKEND,
            raw={"file": str(path), **{k: v for k, v in record.items() if k != "raw"}},
        )


# ------------------------------------------------------------------
# Session candidate from a single file
# ------------------------------------------------------------------


def _session_candidate_from_file(path: Path) -> SessionCandidate | None:
    """Build a :class:`SessionCandidate` from a history file.

    Returns ``None`` if no usable identity or timestamp is found.
    """
    earliest_ts: str | None = None
    earliest_ms: int | None = None
    session_id = ""
    first_user_text = ""

    for record in _load_records(path):
        # Track session id
        if not session_id:
            session_id = _extract_id(record, path)

        # Track earliest timestamp
        ts_pair = _safe_ts(record)
        if ts_pair is not None:
            ts_str, ts_ms = ts_pair
            if earliest_ms is None or ts_ms < earliest_ms:
                earliest_ts = ts_str
                earliest_ms = ts_ms

        # Track first user text for prompt signature
        if not first_user_text:
            role = _extract_role(record)
            if role in ("user", "human"):
                first_user_text = _extract_text(record)

    # Need at minimum an id and a timestamp
    if not session_id or earliest_ts is None or earliest_ms is None:
        return None

    sig = prompt_signature(first_user_text) if first_user_text else ""

    return SessionCandidate(
        session_id=session_id,
        ts=earliest_ts,
        ts_ms=earliest_ms,
        backend=_BACKEND,
        source_family=_SOURCE_FAMILY,
        prompt_signature=sig,
        raw={"file": str(path)},
    )
