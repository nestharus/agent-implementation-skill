"""OpenCode session log extractor.

Reads OpenCode's ``opencode.db`` SQLite database and yields
:class:`TimelineEvent` and :class:`SessionCandidate` objects.

OpenCode stores sessions, messages, and parts in three tables.
Message and part payloads live in a ``data`` column as JSON text.
"""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path
from typing import Iterator

from log_extract.models import SessionCandidate, TimelineEvent
from log_extract.utils import parse_timestamp, prompt_signature, summarize_text

_SOURCE = "opencode"
_BACKEND = "opencode"
_SOURCE_FAMILY = "opencode"


# ------------------------------------------------------------------
# Internal: connection helper
# ------------------------------------------------------------------


def _connect(db_path: Path) -> sqlite3.Connection | None:
    """Open *db_path* read-only.  Return ``None`` if the file is missing."""
    if not db_path.is_file():
        return None
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    return con


def _safe_parse_json(raw: str | None, context: str) -> dict | None:
    """Parse a JSON string, warning on failure.  Returns ``None`` on error."""
    if not raw:
        return None
    try:
        obj = json.loads(raw)
    except (json.JSONDecodeError, ValueError) as exc:
        print(f"WARNING: opencode: {context}: skipping malformed JSON: {exc}", file=sys.stderr)
        return None
    if not isinstance(obj, dict):
        return None
    return obj


def _ts_from_data_or_column(data: dict | None, column_value: str | None) -> tuple[str, int] | None:
    """Extract a timestamp from the data JSON's ``time.created`` (Unix ms)
    or fall back to the SQL column value."""
    if data is not None:
        time_block = data.get("time")
        if isinstance(time_block, dict):
            created = time_block.get("created")
            if created is not None:
                try:
                    return parse_timestamp(created)
                except (ValueError, TypeError):
                    pass

    if column_value:
        try:
            return parse_timestamp(column_value)
        except (ValueError, TypeError):
            pass

    return None


def _extract_text_from_parts(con: sqlite3.Connection, message_id: str) -> str:
    """Collect text content from parts belonging to *message_id*.

    Looks for parts whose ``data`` JSON has ``type`` = ``"text"`` and
    a ``text`` field.
    """
    try:
        cur = con.execute(
            "SELECT data FROM part WHERE message_id = ? ORDER BY time_created",
            (message_id,),
        )
    except sqlite3.OperationalError:
        return ""

    fragments: list[str] = []
    for row in cur:
        pdata = _safe_parse_json(row["data"], f"part of message {message_id}")
        if pdata is None:
            continue
        ptype = pdata.get("type", "")
        if ptype == "text":
            text = pdata.get("text", "")
            if text:
                fragments.append(text)
    return " ".join(fragments)


# ------------------------------------------------------------------
# Per-table event generators
# ------------------------------------------------------------------


def _iter_message_events(con: sqlite3.Connection) -> Iterator[TimelineEvent]:
    """Yield events from the ``message`` table joined with ``session``."""
    try:
        cur = con.execute(
            "SELECT m.id, m.session_id, m.time_created, m.data,"
            " s.directory"
            " FROM message m"
            " LEFT JOIN session s ON m.session_id = s.id"
            " ORDER BY m.time_created",
        )
    except sqlite3.OperationalError:
        return

    for row in cur:
        data = _safe_parse_json(row["data"], f"message {row['id']}")
        ts_pair = _ts_from_data_or_column(data, row["time_created"])
        if ts_pair is None:
            continue

        ts_str, ts_ms = ts_pair
        session_id = row["session_id"] or ""
        role = (data or {}).get("role", "")
        model_id = (data or {}).get("modelID", "")

        if role == "user":
            kind = "message"
            # Try to get text content from parts for user messages
            text = _extract_text_from_parts(con, row["id"])
            detail = summarize_text(text) if text else "user message"
        elif role == "assistant":
            kind = "response"
            detail = f"assistant response"
            if model_id:
                detail += f" ({model_id})"
        else:
            kind = "lifecycle"
            detail = f"message role={role}"

        yield TimelineEvent(
            ts=ts_str,
            ts_ms=ts_ms,
            source=_SOURCE,
            kind=kind,
            detail=detail,
            session_id=session_id,
            model=model_id,
            backend=_BACKEND,
            raw=data or {},
        )


def _iter_part_events(con: sqlite3.Connection) -> Iterator[TimelineEvent]:
    """Yield events from the ``part`` table joined with ``message``/``session``."""
    try:
        cur = con.execute(
            "SELECT p.id, p.message_id, p.session_id, p.time_created, p.data,"
            " m.data AS message_data"
            " FROM part p"
            " LEFT JOIN message m ON p.message_id = m.id"
            " ORDER BY p.time_created",
        )
    except sqlite3.OperationalError:
        return

    for row in cur:
        pdata = _safe_parse_json(row["data"], f"part {row['id']}")
        if pdata is None:
            continue

        # Timestamp: prefer part time_created, then message data time
        mdata = _safe_parse_json(row["message_data"], f"message of part {row['id']}")
        ts_pair = _ts_from_data_or_column(None, row["time_created"])
        if ts_pair is None:
            ts_pair = _ts_from_data_or_column(mdata, None)
        if ts_pair is None:
            continue

        ts_str, ts_ms = ts_pair
        session_id = row["session_id"] or ""
        ptype = pdata.get("type", "")
        model_id = (mdata or {}).get("modelID", "")

        # Classify the part type
        if ptype in ("tool-call", "tool_call") or ("tool" in ptype.lower() and "result" not in ptype.lower()):
            kind = "tool_call"
            tool_name = pdata.get("name", pdata.get("toolName", ""))
            detail = f"tool_call: {tool_name}" if tool_name else f"tool_call ({ptype})"
        elif ptype in ("tool-result", "tool_result"):
            kind = "tool_result"
            detail = f"tool_result ({ptype})"
        elif ptype == "step-finish":
            kind = "lifecycle"
            reason = pdata.get("reason", "")
            detail = f"step-finish: {reason}" if reason else "step-finish"
        else:
            kind = "lifecycle"
            detail = f"part: {ptype}" if ptype else "part"

        yield TimelineEvent(
            ts=ts_str,
            ts_ms=ts_ms,
            source=_SOURCE,
            kind=kind,
            detail=detail,
            session_id=session_id,
            model=model_id,
            backend=_BACKEND,
            raw=pdata,
        )


# ------------------------------------------------------------------
# Session candidate builder
# ------------------------------------------------------------------


def _build_session_candidates(con: sqlite3.Connection) -> Iterator[SessionCandidate]:
    """Yield a :class:`SessionCandidate` for each session row."""
    try:
        cur = con.execute(
            "SELECT id, directory, time_created FROM session ORDER BY time_created",
        )
    except sqlite3.OperationalError:
        return

    for row in cur:
        session_id = row["id"] or ""
        directory = row["directory"] or ""
        session_ts = row["time_created"]

        # Find earliest message timestamp for this session
        earliest_ts: str | None = None
        earliest_ms: int | None = None
        first_prompt: str = ""
        model: str = ""

        try:
            msg_cur = con.execute(
                "SELECT id, time_created, data FROM message"
                " WHERE session_id = ? ORDER BY time_created",
                (session_id,),
            )
        except sqlite3.OperationalError:
            msg_cur = iter(())  # type: ignore[assignment]

        for msg_row in msg_cur:
            mdata = _safe_parse_json(msg_row["data"], f"message {msg_row['id']}")
            ts_pair = _ts_from_data_or_column(mdata, msg_row["time_created"])
            if ts_pair is not None:
                ts_str, ts_ms = ts_pair
                if earliest_ms is None or ts_ms < earliest_ms:
                    earliest_ts = ts_str
                    earliest_ms = ts_ms

            if mdata is not None:
                # Track model from first assistant message
                if not model and mdata.get("role") == "assistant":
                    model = mdata.get("modelID", "")

                # Track first user prompt for signature
                if not first_prompt and mdata.get("role") == "user":
                    text = _extract_text_from_parts(con, msg_row["id"])
                    if text:
                        first_prompt = text

        # Fall back to session time_created if no message timestamps
        if earliest_ts is None and session_ts:
            try:
                ts_pair = parse_timestamp(session_ts)
                earliest_ts, earliest_ms = ts_pair
            except (ValueError, TypeError):
                pass

        if earliest_ts is None or earliest_ms is None:
            continue

        yield SessionCandidate(
            session_id=session_id,
            ts=earliest_ts,
            ts_ms=earliest_ms,
            backend=_BACKEND,
            source_family=_SOURCE_FAMILY,
            model=model,
            cwd=directory,
            prompt_signature=prompt_signature(first_prompt) if first_prompt else "",
        )


# ------------------------------------------------------------------
# Public API
# ------------------------------------------------------------------


def iter_events(opencode_homes: list[Path]) -> Iterator[TimelineEvent]:
    """Yield all timeline events from OpenCode session databases.

    Parameters
    ----------
    opencode_homes:
        Paths to OpenCode home directories, each expected to contain
        an ``opencode.db`` file.
    """
    for home in opencode_homes:
        db_path = home / "opencode.db"
        con = _connect(db_path)
        if con is None:
            continue
        try:
            yield from _iter_message_events(con)
            yield from _iter_part_events(con)
        finally:
            con.close()


def iter_session_candidates(opencode_homes: list[Path]) -> Iterator[SessionCandidate]:
    """Yield a :class:`SessionCandidate` for each session in OpenCode databases.

    Parameters
    ----------
    opencode_homes:
        Paths to OpenCode home directories.
    """
    for home in opencode_homes:
        db_path = home / "opencode.db"
        con = _connect(db_path)
        if con is None:
            continue
        try:
            yield from _build_session_candidates(con)
        finally:
            con.close()
