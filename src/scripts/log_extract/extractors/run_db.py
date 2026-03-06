"""Extract timeline events and dispatch candidates from a run.db SQLite file.

Tables consumed: events, agents, tasks, gates, gate_members, messages.
"""

from __future__ import annotations

import sqlite3
import sys
from collections.abc import Iterator
from pathlib import Path

from log_extract.models import DispatchCandidate, TimelineEvent
from log_extract.utils import infer_section, parse_timestamp, summarize_text

_SOURCE = "run.db"


# ------------------------------------------------------------------
# Public API
# ------------------------------------------------------------------


def iter_events(
    db_path: Path,
    model_map: dict[str, tuple[str, str]],
) -> Iterator[TimelineEvent]:
    """Yield :class:`TimelineEvent` objects from every relevant table in *db_path*."""
    con = _connect(db_path)
    try:
        yield from _events_from_events_table(con)
        yield from _events_from_agents_table(con)
        yield from _events_from_tasks_table(con, model_map)
        yield from _events_from_gates_table(con)
        yield from _events_from_gate_members_table(con)
        yield from _events_from_messages_table(con)
    finally:
        con.close()


def iter_dispatch_candidates(
    db_path: Path,
    model_map: dict[str, tuple[str, str]],
) -> Iterator[DispatchCandidate]:
    """Yield :class:`DispatchCandidate` objects from the tasks table."""
    con = _connect(db_path)
    try:
        yield from _dispatches_from_tasks(con, model_map)
    finally:
        con.close()


# ------------------------------------------------------------------
# Internal: connection helper
# ------------------------------------------------------------------


def _connect(db_path: Path) -> sqlite3.Connection:
    con = sqlite3.connect(str(db_path))
    con.row_factory = sqlite3.Row
    return con


def _safe_ts(value: str | None) -> tuple[str, int] | None:
    """Parse a timestamp, returning *None* on failure or empty input."""
    if not value:
        return None
    try:
        return parse_timestamp(value)
    except (ValueError, TypeError) as exc:
        print(f"run_db: skipping malformed timestamp {value!r}: {exc}", file=sys.stderr)
        return None


# ------------------------------------------------------------------
# events table
# ------------------------------------------------------------------


def _events_from_events_table(con: sqlite3.Connection) -> Iterator[TimelineEvent]:
    try:
        cur = con.execute("SELECT id, ts, kind, tag, body, agent FROM events")
    except sqlite3.OperationalError:
        return

    for row in cur:
        parsed = _safe_ts(row["ts"])
        if parsed is None:
            continue

        ts_str, ts_ms = parsed
        tag = row["tag"] or ""
        body = row["body"] or ""
        agent = row["agent"] or ""
        detail = f"{tag} {body}".strip()

        raw_kind = (row["kind"] or "").strip()
        kind = _normalize_kind(raw_kind)
        if kind is None:
            print(
                f"run_db: skipping events row {row['id']} with unrecognised kind {raw_kind!r}",
                file=sys.stderr,
            )
            continue

        section = infer_section(tag, body, agent)

        yield TimelineEvent(
            ts=ts_str,
            ts_ms=ts_ms,
            source=_SOURCE,
            kind=kind,
            detail=detail,
            agent=agent,
            section=section,
            raw={"table": "events", "id": row["id"]},
        )


# ------------------------------------------------------------------
# agents table
# ------------------------------------------------------------------


def _events_from_agents_table(con: sqlite3.Connection) -> Iterator[TimelineEvent]:
    try:
        cur = con.execute("SELECT id, ts, name, pid, status FROM agents")
    except sqlite3.OperationalError:
        return

    for row in cur:
        parsed = _safe_ts(row["ts"])
        if parsed is None:
            continue

        ts_str, ts_ms = parsed
        name = row["name"] or ""
        status = row["status"] or ""
        detail = f"agent {name} pid={row['pid']} status={status}".strip()

        yield TimelineEvent(
            ts=ts_str,
            ts_ms=ts_ms,
            source=_SOURCE,
            kind="lifecycle",
            detail=detail,
            agent=name,
            section=infer_section(name),
            raw={"table": "agents", "id": row["id"]},
        )


# ------------------------------------------------------------------
# tasks table
# ------------------------------------------------------------------


def _events_from_tasks_table(
    con: sqlite3.Connection,
    model_map: dict[str, tuple[str, str]],
) -> Iterator[TimelineEvent]:
    try:
        cur = con.execute(
            "SELECT id, submitted_by, task_type, status, claimed_by, claimed_at,"
            " completed_at, agent_file, model, output_path, instance_id,"
            " flow_id, chain_id, trigger_gate_id, freshness_token FROM tasks"
        )
    except sqlite3.OperationalError:
        return

    for row in cur:
        task_type = row["task_type"] or ""
        model = row["model"] or ""
        agent_file = row["agent_file"] or ""
        claimed_by = row["claimed_by"] or ""

        agent_label = claimed_by or row["submitted_by"] or ""
        section = infer_section(task_type, agent_file, agent_label)

        backend, _ = model_map.get(model, ("", ""))

        # Claimed event
        claimed_at = row["claimed_at"]
        if claimed_at:
            parsed = _safe_ts(claimed_at)
            if parsed is not None:
                ts_str, ts_ms = parsed
                detail = f"task {row['id']} claimed: {task_type}"
                if claimed_by:
                    detail += f" by {claimed_by}"

                yield TimelineEvent(
                    ts=ts_str,
                    ts_ms=ts_ms,
                    source=_SOURCE,
                    kind="task",
                    detail=detail,
                    agent=agent_label,
                    model=model,
                    backend=backend,
                    section=section,
                    raw={"table": "tasks", "id": row["id"], "event": "claimed"},
                )

        # Completed event
        completed_at = row["completed_at"]
        if completed_at:
            parsed = _safe_ts(completed_at)
            if parsed is not None:
                ts_str, ts_ms = parsed
                status = row["status"] or "unknown"
                detail = f"task {row['id']} completed ({status}): {task_type}"

                yield TimelineEvent(
                    ts=ts_str,
                    ts_ms=ts_ms,
                    source=_SOURCE,
                    kind="task",
                    detail=detail,
                    agent=agent_label,
                    model=model,
                    backend=backend,
                    section=section,
                    raw={"table": "tasks", "id": row["id"], "event": "completed"},
                )


def _dispatches_from_tasks(
    con: sqlite3.Connection,
    model_map: dict[str, tuple[str, str]],
) -> Iterator[DispatchCandidate]:
    try:
        cur = con.execute(
            "SELECT id, submitted_by, task_type, status, claimed_by, claimed_at,"
            " completed_at, agent_file, model, output_path, instance_id,"
            " flow_id, chain_id, trigger_gate_id, freshness_token FROM tasks"
        )
    except sqlite3.OperationalError:
        return

    for row in cur:
        claimed_at = row["claimed_at"]
        model = row["model"] or ""
        if not claimed_at or not model:
            continue

        parsed = _safe_ts(claimed_at)
        if parsed is None:
            continue

        ts_str, ts_ms = parsed
        backend, source_family = model_map.get(model, ("", ""))
        task_type = row["task_type"] or ""
        agent_file = row["agent_file"] or ""
        claimed_by = row["claimed_by"] or ""
        agent_label = claimed_by or row["submitted_by"] or ""
        section = infer_section(task_type, agent_file, agent_label)

        yield DispatchCandidate(
            dispatch_id=f"rundb-task-{row['id']}",
            ts=ts_str,
            ts_ms=ts_ms,
            backend=backend,
            source_family=source_family,
            model=model,
            agent=agent_label,
            section=section,
            raw={"table": "tasks", "id": row["id"]},
        )


# ------------------------------------------------------------------
# gates table
# ------------------------------------------------------------------


def _events_from_gates_table(con: sqlite3.Connection) -> Iterator[TimelineEvent]:
    try:
        cur = con.execute(
            "SELECT gate_id, flow_id, status, expected_count,"
            " synthesis_task_type, fired_task_id, created_at, fired_at FROM gates"
        )
    except sqlite3.OperationalError:
        return

    for row in cur:
        gate_id = row["gate_id"] or ""
        flow_id = row["flow_id"] or ""

        # Created event
        created_at = row["created_at"]
        if created_at:
            parsed = _safe_ts(created_at)
            if parsed is not None:
                ts_str, ts_ms = parsed
                detail = f"gate {gate_id} created (flow={flow_id}, expected={row['expected_count']})"
                yield TimelineEvent(
                    ts=ts_str,
                    ts_ms=ts_ms,
                    source=_SOURCE,
                    kind="gate",
                    detail=detail,
                    section=infer_section(gate_id, flow_id),
                    raw={"table": "gates", "gate_id": gate_id, "event": "created"},
                )

        # Fired event
        fired_at = row["fired_at"]
        if fired_at:
            parsed = _safe_ts(fired_at)
            if parsed is not None:
                ts_str, ts_ms = parsed
                detail = f"gate {gate_id} fired (flow={flow_id})"
                yield TimelineEvent(
                    ts=ts_str,
                    ts_ms=ts_ms,
                    source=_SOURCE,
                    kind="gate",
                    detail=detail,
                    section=infer_section(gate_id, flow_id),
                    raw={"table": "gates", "gate_id": gate_id, "event": "fired"},
                )


# ------------------------------------------------------------------
# gate_members table
# ------------------------------------------------------------------


def _events_from_gate_members_table(con: sqlite3.Connection) -> Iterator[TimelineEvent]:
    try:
        cur = con.execute(
            "SELECT gate_id, chain_id, leaf_task_id, status, completed_at FROM gate_members"
        )
    except sqlite3.OperationalError:
        return

    for row in cur:
        completed_at = row["completed_at"]
        if not completed_at:
            continue

        parsed = _safe_ts(completed_at)
        if parsed is None:
            continue

        ts_str, ts_ms = parsed
        gate_id = row["gate_id"] or ""
        chain_id = row["chain_id"] or ""
        status = row["status"] or ""
        detail = f"gate_member {gate_id}/{chain_id} task={row['leaf_task_id']} {status}"

        yield TimelineEvent(
            ts=ts_str,
            ts_ms=ts_ms,
            source=_SOURCE,
            kind="gate",
            detail=detail,
            section=infer_section(gate_id, chain_id),
            raw={
                "table": "gate_members",
                "gate_id": gate_id,
                "chain_id": chain_id,
            },
        )


# ------------------------------------------------------------------
# messages table
# ------------------------------------------------------------------


def _events_from_messages_table(con: sqlite3.Connection) -> Iterator[TimelineEvent]:
    try:
        cur = con.execute(
            "SELECT id, ts, sender, target, body, claimed, claimed_by, claimed_at FROM messages"
        )
    except sqlite3.OperationalError:
        return

    for row in cur:
        parsed = _safe_ts(row["ts"])
        if parsed is None:
            continue

        ts_str, ts_ms = parsed
        sender = row["sender"] or ""
        target = row["target"] or ""
        body = summarize_text(row["body"] or "")
        detail = f"{sender} -> {target}: {body}"

        yield TimelineEvent(
            ts=ts_str,
            ts_ms=ts_ms,
            source=_SOURCE,
            kind="message",
            detail=detail,
            agent=sender,
            section=infer_section(sender, target, row["body"] or ""),
            raw={"table": "messages", "id": row["id"]},
        )


# ------------------------------------------------------------------
# Kind normalization
# ------------------------------------------------------------------

# The events table stores arbitrary kind strings; map them to our
# constrained Kind literal.  Unknown kinds are logged and skipped.
_KIND_MAP: dict[str, str] = {
    "lifecycle": "lifecycle",
    "summary": "summary",
    "signal": "signal",
    "message": "message",
    "dispatch": "dispatch",
    "response": "response",
    "tool_call": "tool_call",
    "tool_result": "tool_result",
    "task": "task",
    "gate": "gate",
    "artifact": "artifact",
    "session": "session",
}


def _normalize_kind(raw: str) -> str | None:
    return _KIND_MAP.get(raw)
