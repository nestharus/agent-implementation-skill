"""Claude Code session log extractor.

Scans Claude Code's JSONL session files under ``<claude_home>/projects/``
and yields :class:`TimelineEvent` and :class:`SessionCandidate` objects.

Claude Code stores one ``.jsonl`` file per session, with each line being
a JSON record typed by its ``"type"`` field.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Iterator

from log_extract.models import SessionCandidate, TimelineEvent
from log_extract.utils import parse_timestamp, prompt_signature, summarize_text

_BACKEND = "claude2"
_SOURCE = "claude"
_SOURCE_FAMILY = "claude"


# ------------------------------------------------------------------
# Internal helpers
# ------------------------------------------------------------------

def _extract_text_content(content: object) -> str:
    """Pull readable text from a message's ``content`` field.

    Content may be a plain string, or a list of content blocks (dicts).
    Text blocks have ``{"type": "text", "text": "..."}``.
    """
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                parts.append(block.get("text", ""))
            elif isinstance(block, str):
                parts.append(block)
        return " ".join(parts)
    return ""


def _iter_content_blocks(content: object) -> Iterator[dict]:
    """Yield individual content-block dicts from a message's content."""
    if isinstance(content, list):
        for block in content:
            if isinstance(block, dict):
                yield block


def _record_timestamp(record: dict) -> tuple[str, int] | None:
    """Try to extract a parsed timestamp from *record*."""
    # Explicit timestamp field (queue-operation records)
    ts_raw = record.get("timestamp")
    if ts_raw is not None:
        try:
            return parse_timestamp(ts_raw)
        except (ValueError, TypeError):
            pass

    # Some records embed a timestamp inside the message envelope
    msg = record.get("message")
    if isinstance(msg, dict):
        for key in ("timestamp", "ts"):
            val = msg.get(key)
            if val is not None:
                try:
                    return parse_timestamp(val)
                except (ValueError, TypeError):
                    pass

    return None


def _session_id_from(record: dict, file_stem: str) -> str:
    """Return the session id from the record or fall back to file stem."""
    return record.get("sessionId", "") or file_stem


# ------------------------------------------------------------------
# Per-record handlers
# ------------------------------------------------------------------

def _handle_queue_event(
    record: dict,
    ts_pair: tuple[str, int] | None,
    sid: str,
) -> TimelineEvent | None:
    """Handle a ``queue-operation`` record with ``operation == "enqueue"``.

    Returns a ``dispatch`` event, or ``None`` if the timestamp is missing.
    """
    if ts_pair is None:
        return None
    ts_str, ts_ms = ts_pair
    content_text = record.get("content", "")
    return TimelineEvent(
        ts=ts_str,
        ts_ms=ts_ms,
        source=_SOURCE,
        kind="dispatch",
        detail=summarize_text(content_text),
        session_id=sid,
        backend=_BACKEND,
        raw=record,
    )


def _handle_user_event(
    record: dict,
    ts_pair: tuple[str, int] | None,
    sid: str,
) -> TimelineEvent | None:
    """Handle a ``user`` record.

    Returns a ``message`` event, or ``None`` if the timestamp is missing.
    """
    msg = record.get("message", {})
    if isinstance(msg, dict):
        content = msg.get("content", "")
    else:
        content = ""
    text = _extract_text_content(content)

    # User records may lack a top-level timestamp; use the
    # record timestamp if available, otherwise skip the event
    # (we still track the record for session candidate metadata).
    if ts_pair is None:
        return None
    ts_str, ts_ms = ts_pair
    return TimelineEvent(
        ts=ts_str,
        ts_ms=ts_ms,
        source=_SOURCE,
        kind="message",
        detail=summarize_text(text),
        session_id=sid,
        backend=_BACKEND,
        raw=record,
    )


def _handle_assistant_event(
    record: dict,
    ts_pair: tuple[str, int] | None,
    sid: str,
) -> Iterator[TimelineEvent]:
    """Handle an ``assistant`` record.

    Yields a ``response`` event followed by ``tool_call`` / ``tool_result``
    events for any tool-use content blocks.
    """
    if ts_pair is None:
        return
    ts_str, ts_ms = ts_pair
    msg = record.get("message", {})
    if not isinstance(msg, dict):
        return
    content = msg.get("content", "")
    text = _extract_text_content(content)

    yield TimelineEvent(
        ts=ts_str,
        ts_ms=ts_ms,
        source=_SOURCE,
        kind="response",
        detail=summarize_text(text),
        session_id=sid,
        backend=_BACKEND,
        raw=record,
    )

    # Emit extra events for tool_use / tool_result blocks
    for block in _iter_content_blocks(content):
        btype = block.get("type", "")
        if btype == "tool_use":
            tool_name = block.get("name", "unknown")
            yield TimelineEvent(
                ts=ts_str,
                ts_ms=ts_ms,
                source=_SOURCE,
                kind="tool_call",
                detail=f"tool_use: {tool_name}",
                session_id=sid,
                backend=_BACKEND,
                raw=block,
            )
        elif btype == "tool_result":
            tool_id = block.get("tool_use_id", "")
            yield TimelineEvent(
                ts=ts_str,
                ts_ms=ts_ms,
                source=_SOURCE,
                kind="tool_result",
                detail=f"tool_result: {tool_id}",
                session_id=sid,
                backend=_BACKEND,
                raw=block,
            )


# ------------------------------------------------------------------
# Per-file line processing
# ------------------------------------------------------------------

def _iter_file_events(
    path: Path,
) -> Iterator[TimelineEvent]:
    """Stream events from a single JSONL session file."""
    file_stem = path.stem

    with path.open("r", encoding="utf-8", errors="replace") as fh:
        for line_no, raw_line in enumerate(fh, start=1):
            raw_line = raw_line.strip()
            if not raw_line:
                continue
            try:
                record = json.loads(raw_line)
            except json.JSONDecodeError as exc:
                print(
                    f"WARNING: {path}:{line_no}: skipping malformed JSON: {exc}",
                    file=sys.stderr,
                )
                continue

            if not isinstance(record, dict):
                continue

            ts_pair = _record_timestamp(record)
            rtype = record.get("type", "")
            sid = _session_id_from(record, file_stem)

            if rtype == "queue-operation" and record.get("operation") == "enqueue":
                event = _handle_queue_event(record, ts_pair, sid)
                if event is not None:
                    yield event
            elif rtype == "user":
                event = _handle_user_event(record, ts_pair, sid)
                if event is not None:
                    yield event
            elif rtype == "assistant":
                yield from _handle_assistant_event(record, ts_pair, sid)


def _extract_prompt_text(record: dict, rtype: str) -> str:
    """Extract prompt text from a queue-operation or user record."""
    if rtype == "queue-operation" and record.get("operation") == "enqueue":
        return record.get("content", "")
    if rtype == "user":
        msg = record.get("message", {})
        if isinstance(msg, dict):
            return _extract_text_content(msg.get("content", ""))
    return ""


def _build_session_candidate(path: Path) -> SessionCandidate | None:
    """Scan a JSONL file and produce a :class:`SessionCandidate`.

    Returns ``None`` if no valid timestamped line is found.
    """
    file_stem = path.stem
    earliest_ts: str | None = None
    earliest_ms: int | None = None
    cwd: str = ""
    first_prompt: str = ""
    session_id: str = ""

    with path.open("r", encoding="utf-8", errors="replace") as fh:
        for line_no, raw_line in enumerate(fh, start=1):
            raw_line = raw_line.strip()
            if not raw_line:
                continue
            try:
                record = json.loads(raw_line)
            except json.JSONDecodeError:
                print(
                    f"WARNING: {path}:{line_no}: skipping malformed JSON line",
                    file=sys.stderr,
                )
                continue

            if not isinstance(record, dict):
                continue

            # Track session id
            if not session_id:
                session_id = record.get("sessionId", "") or file_stem

            # Track earliest timestamp
            ts_pair = _record_timestamp(record)
            if ts_pair is not None:
                ts_str, ts_ms = ts_pair
                if earliest_ms is None or ts_ms < earliest_ms:
                    earliest_ts = ts_str
                    earliest_ms = ts_ms

            # Track cwd from user records
            rtype = record.get("type", "")
            if rtype == "user" and not cwd:
                cwd = record.get("cwd", "")

            # Track first prompt text
            if not first_prompt:
                first_prompt = _extract_prompt_text(record, rtype)

    if earliest_ts is None or earliest_ms is None:
        return None

    return SessionCandidate(
        session_id=session_id or file_stem,
        ts=earliest_ts,
        ts_ms=earliest_ms,
        backend=_BACKEND,
        source_family=_SOURCE_FAMILY,
        cwd=cwd,
        prompt_signature=prompt_signature(first_prompt) if first_prompt else "",
    )


# ------------------------------------------------------------------
# Public API
# ------------------------------------------------------------------

def _iter_session_files(claude_homes: list[Path]) -> Iterator[Path]:
    """Yield all ``.jsonl`` files under ``projects/`` in each claude home."""
    for home in claude_homes:
        projects_dir = home / "projects"
        if not projects_dir.is_dir():
            continue
        # Walk project hash directories
        for project_dir in sorted(projects_dir.iterdir()):
            if not project_dir.is_dir():
                continue
            for jsonl_file in sorted(project_dir.glob("*.jsonl")):
                yield jsonl_file


def iter_events(claude_homes: list[Path]) -> Iterator[TimelineEvent]:
    """Yield all timeline events from Claude Code session logs.

    Parameters
    ----------
    claude_homes:
        Paths to Claude Code home directories (each containing a
        ``projects/`` subdirectory with per-session ``.jsonl`` files).
    """
    for path in _iter_session_files(claude_homes):
        yield from _iter_file_events(path)


def iter_session_candidates(claude_homes: list[Path]) -> Iterator[SessionCandidate]:
    """Yield a :class:`SessionCandidate` for each session log file.

    Parameters
    ----------
    claude_homes:
        Paths to Claude Code home directories.
    """
    for path in _iter_session_files(claude_homes):
        candidate = _build_session_candidate(path)
        if candidate is not None:
            yield candidate
