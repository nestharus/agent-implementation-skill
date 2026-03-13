"""Extract timeline events and session candidates from Codex session logs.

Codex stores session transcripts as JSONL files under
``<codex_home>/sessions/YYYY/MM/DD/rollout-<timestamp>-<uuid>.jsonl``.
Each line is a JSON object with ``timestamp``, ``type``, and ``payload``
fields.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Iterator
from pathlib import Path

from log_extract.extractors.common import (
    events_from_home,
    safe_ts,
    session_candidates_from_home,
)
from log_extract.models import SessionCandidate, TimelineEvent
from log_extract.utils import prompt_signature, summarize_text

_SOURCE = "codex"
_BACKEND = "codex2"
_SOURCE_FAMILY = "codex"


# ------------------------------------------------------------------
# Public API
# ------------------------------------------------------------------


def iter_events(codex_homes: list[Path]) -> Iterator[TimelineEvent]:
    """Yield :class:`TimelineEvent` objects from every Codex session log found
    under the given *codex_homes* directories."""
    for home in codex_homes:
        yield from _events_from_home(home)


def iter_session_candidates(codex_homes: list[Path]) -> Iterator[SessionCandidate]:
    """Yield :class:`SessionCandidate` objects from Codex session logs.

    One candidate is emitted per JSONL file that contains a
    ``session_meta`` record.
    """
    for home in codex_homes:
        yield from _session_candidates_from_home(home)


# ------------------------------------------------------------------
# Internal: home-level iteration
# ------------------------------------------------------------------


def _iter_rollout_files(home: Path) -> Iterator[Path]:
    """Find all ``rollout-*.jsonl`` files under ``<home>/sessions/``."""
    sessions_dir = home / "sessions"
    if not sessions_dir.is_dir():
        return
    yield from sorted(sessions_dir.rglob("rollout-*.jsonl"))


def _events_from_home(home: Path) -> Iterator[TimelineEvent]:
    yield from events_from_home(
        home, _iter_rollout_files, _events_from_file, source_label="codex",
    )


def _session_candidates_from_home(home: Path) -> Iterator[SessionCandidate]:
    yield from session_candidates_from_home(
        home, _iter_rollout_files, _session_candidate_from_file, source_label="codex",
    )


# ------------------------------------------------------------------
# Internal: per-file parsing
# ------------------------------------------------------------------


def _iter_records(path: Path) -> Iterator[dict]:
    """Yield parsed JSON objects from a JSONL file, skipping bad lines."""
    try:
        fh = path.open("r", encoding="utf-8")
    except OSError as exc:
        print(f"codex: cannot read {path}: {exc}", file=sys.stderr)
        return
    with fh:
        for lineno, line in enumerate(fh, 1):
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError as exc:
                print(
                    f"codex: {path}:{lineno}: skipping truncated/malformed line: {exc}",
                    file=sys.stderr,
                )


def _safe_ts(record: dict) -> tuple[str, int] | None:
    """Extract and parse the ``timestamp`` field from a record."""
    return safe_ts(record.get("timestamp"), source_label="codex")


def _extract_first_text(content_blocks: list) -> str:
    """Return the first non-empty text from a list of content blocks."""
    for block in content_blocks:
        if isinstance(block, dict):
            text = block.get("text", "")
            if text:
                return text
    return ""


# ------------------------------------------------------------------
# Events from a single file
# ------------------------------------------------------------------


def _events_from_file(path: Path) -> Iterator[TimelineEvent]:
    for record in _iter_records(path):
        parsed = _safe_ts(record)
        if parsed is None:
            continue

        ts_str, ts_ms = parsed
        rec_type = record.get("type", "")
        payload = record.get("payload") or {}

        if rec_type == "session_meta":
            session_id = payload.get("id", "")
            yield TimelineEvent(
                ts=ts_str,
                ts_ms=ts_ms,
                source=_SOURCE,
                kind="session",
                detail=f"codex session started",
                session_id=session_id,
                backend=_BACKEND,
                raw={"file": str(path), "type": rec_type, "payload": payload},
            )

        elif rec_type == "response_item":
            yield from _events_from_response_item(ts_str, ts_ms, payload, path)

        elif rec_type == "event_msg":
            yield from _events_from_event_msg(ts_str, ts_ms, payload, path)


def _events_from_response_item(
    ts_str: str,
    ts_ms: int,
    payload: dict,
    path: Path,
) -> Iterator[TimelineEvent]:
    role = payload.get("role", "")
    content_blocks = payload.get("content") or []

    # Extract text from content blocks
    text_parts: list[str] = []
    for block in content_blocks:
        if isinstance(block, dict):
            text = block.get("text", "")
            if text:
                text_parts.append(text)

    detail = summarize_text(" ".join(text_parts)) if text_parts else ""

    if role == "assistant":
        kind = "response"
    else:
        # user, developer, or anything else
        kind = "message"

    yield TimelineEvent(
        ts=ts_str,
        ts_ms=ts_ms,
        source=_SOURCE,
        kind=kind,
        detail=detail,
        backend=_BACKEND,
        raw={"file": str(path), "type": "response_item", "role": role},
    )


def _events_from_event_msg(
    ts_str: str,
    ts_ms: int,
    payload: dict,
    path: Path,
) -> Iterator[TimelineEvent]:
    event_type = payload.get("type", "")
    if event_type not in ("task_started", "task_completed"):
        return

    detail = event_type.replace("_", " ")

    yield TimelineEvent(
        ts=ts_str,
        ts_ms=ts_ms,
        source=_SOURCE,
        kind="task",
        detail=detail,
        backend=_BACKEND,
        raw={"file": str(path), "type": "event_msg", "event_type": event_type},
    )


# ------------------------------------------------------------------
# Session candidate from a single file
# ------------------------------------------------------------------


def _session_candidate_from_file(path: Path) -> SessionCandidate | None:
    """Build a :class:`SessionCandidate` from a rollout file.

    Uses the ``session_meta`` record for session identity and the first
    user message for the prompt signature.
    """
    session_id = ""
    ts_str = ""
    ts_ms = 0
    cwd = ""
    first_user_text = ""
    meta_raw: dict = {}

    for record in _iter_records(path):
        rec_type = record.get("type", "")
        payload = record.get("payload") or {}

        if rec_type == "session_meta" and not session_id:
            session_id = payload.get("id", "")
            cwd = payload.get("cwd", "")
            parsed = _safe_ts(record)
            if parsed is not None:
                ts_str, ts_ms = parsed
            meta_raw = payload

        elif rec_type == "response_item" and not first_user_text:
            role = payload.get("role", "")
            if role in ("user", "developer"):
                first_user_text = _extract_first_text(payload.get("content") or [])

    if not session_id:
        return None

    sig = prompt_signature(first_user_text) if first_user_text else ""

    return SessionCandidate(
        session_id=session_id,
        ts=ts_str,
        ts_ms=ts_ms,
        backend=_BACKEND,
        source_family=_SOURCE_FAMILY,
        cwd=cwd,
        prompt_signature=sig,
        raw={"file": str(path), "payload": meta_raw},
    )
