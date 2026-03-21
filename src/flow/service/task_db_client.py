"""Task DB helpers: db.sh command wrapper and direct SQLite connection."""

from __future__ import annotations

import json
import sqlite3
import subprocess
from collections.abc import Generator, Iterable
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from orchestrator.path_registry import PathRegistry

DB_SH = Path(__file__).resolve().parent.parent.parent / "scripts" / "db.sh"

_DB_COMMAND_TIMEOUT_SECONDS = 30
_SQLITE_CONNECT_TIMEOUT_SECONDS = 5.0
_SQLITE_BUSY_TIMEOUT_MS = 5000
_ACTIVE_TASK_STATUSES = ("pending", "running", "blocked", "awaiting_input")
_ACTIVE_TASK_STATUSES_SQL = ", ".join("?" for _ in _ACTIVE_TASK_STATUSES)
_SUBSCRIPTION_VERIFICATION_MODES = (
    "subscriber_verifies",
    "producer_terminal",
    "validated_user_input",
)
_NON_TERMINAL_TASK_STATUSES = ("pending", "running", "blocked", "awaiting_input")
_NON_TERMINAL_TASK_STATUSES_SQL = ", ".join(
    "?" for _ in _NON_TERMINAL_TASK_STATUSES
)
_DEPENDENCY_WAIT_REASON = "waiting_for_dependencies"
_DEPENDENCY_FAILURE_REASON = "dependency_failed"
_DEFAULT_DEPENDENCY_STARVATION_SECONDS = 1800.0
_VALUE_AXIS_STATUSES = ("active", "aligned", "rejected", "superseded")


def db_cmd(db_path: str, command: str, *args: str) -> str:
    """Run a ``db.sh`` command, returning stripped stdout."""
    result = subprocess.run(  # noqa: S603, S607
        ["bash", str(DB_SH), command, db_path, *args],
        capture_output=True,
        text=True,
        timeout=_DB_COMMAND_TIMEOUT_SECONDS,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"db.sh {command} failed (rc={result.returncode}): "
            f"{result.stderr.strip()}"
        )
    return result.stdout.strip()


_INIT_SCHEMA = """\
CREATE TABLE IF NOT EXISTS id_seq (
  id INTEGER PRIMARY KEY AUTOINCREMENT
);

CREATE TABLE IF NOT EXISTS messages (
  id         INTEGER PRIMARY KEY,
  ts         TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f','now')),
  sender     TEXT    DEFAULT '',
  target     TEXT    NOT NULL,
  body       TEXT    NOT NULL,
  claimed    INTEGER NOT NULL DEFAULT 0,
  claimed_by TEXT,
  claimed_at TEXT
);

CREATE TABLE IF NOT EXISTS events (
  id    INTEGER PRIMARY KEY,
  ts    TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f','now')),
  kind  TEXT    NOT NULL,
  tag   TEXT    DEFAULT '',
  body  TEXT    DEFAULT '',
  agent TEXT    DEFAULT ''
);

CREATE TABLE IF NOT EXISTS agents (
  id     INTEGER PRIMARY KEY,
  ts     TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f','now')),
  name   TEXT    NOT NULL,
  pid    INTEGER,
  status TEXT    NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_messages_target_unclaimed
  ON messages(target) WHERE claimed = 0;
CREATE INDEX IF NOT EXISTS idx_messages_target_claimed_id
  ON messages(target, claimed, id);
CREATE INDEX IF NOT EXISTS idx_events_kind ON events(kind);
CREATE INDEX IF NOT EXISTS idx_events_kind_tag ON events(kind, tag);
CREATE INDEX IF NOT EXISTS idx_events_kind_id ON events(kind, id);
CREATE INDEX IF NOT EXISTS idx_agents_name ON agents(name);

CREATE TABLE IF NOT EXISTS tasks (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    submitted_by   TEXT    NOT NULL,
    task_type      TEXT    NOT NULL,
    problem_id     TEXT,
    concern_scope  TEXT,
    payload_path   TEXT,
    priority       TEXT    DEFAULT 'normal',
    status         TEXT    DEFAULT 'pending',
    claimed_by     TEXT,
    agent_file     TEXT,
    model          TEXT,
    output_path    TEXT,
    created_at     TEXT    DEFAULT (datetime('now')),
    claimed_at     TEXT,
    completed_at   TEXT,
    error          TEXT,
    instance_id          TEXT,
    flow_id              TEXT,
    chain_id             TEXT,
    declared_by_task_id  INTEGER,
    trigger_gate_id      TEXT,
    flow_context_path    TEXT,
    continuation_path    TEXT,
    result_manifest_path TEXT,
    freshness_token      TEXT,
    updated_at           TEXT,
    dedupe_key           TEXT,
    status_reason        TEXT,
    superseded_by_task_id INTEGER,
    result_envelope_path TEXT
);

CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);
CREATE INDEX IF NOT EXISTS idx_tasks_type   ON tasks(task_type);

CREATE TABLE IF NOT EXISTS gates (
    gate_id                TEXT PRIMARY KEY,
    flow_id                TEXT NOT NULL,
    created_by_task_id     INTEGER,
    parent_gate_id         TEXT,
    mode                   TEXT NOT NULL DEFAULT 'all',
    failure_policy         TEXT NOT NULL DEFAULT 'include',
    status                 TEXT NOT NULL DEFAULT 'open',
    expected_count         INTEGER NOT NULL,
    synthesis_task_type    TEXT,
    synthesis_problem_id   TEXT,
    synthesis_concern_scope TEXT,
    synthesis_payload_path TEXT,
    synthesis_priority     TEXT,
    aggregate_manifest_path TEXT,
    fired_task_id          INTEGER,
    created_at             TEXT DEFAULT (datetime('now')),
    fired_at               TEXT
);

CREATE TABLE IF NOT EXISTS gate_members (
    gate_id              TEXT NOT NULL,
    chain_id             TEXT NOT NULL,
    slot_label           TEXT,
    leaf_task_id         INTEGER NOT NULL,
    status               TEXT NOT NULL DEFAULT 'pending',
    result_manifest_path TEXT,
    completed_at         TEXT,
    PRIMARY KEY (gate_id, chain_id)
);

CREATE TABLE IF NOT EXISTS section_states (
    section_number   TEXT PRIMARY KEY,
    state            TEXT NOT NULL DEFAULT 'pending',
    updated_at       TEXT,
    error            TEXT,
    retry_count      INTEGER DEFAULT 0,
    blocked_reason   TEXT,
    context_json     TEXT,
    parent_section   TEXT DEFAULT NULL,
    depth            INTEGER DEFAULT 0,
    scope_grant      TEXT DEFAULT NULL,
    spawned_by_state TEXT DEFAULT NULL
);

CREATE INDEX IF NOT EXISTS idx_section_states_parent
  ON section_states(parent_section);
CREATE INDEX IF NOT EXISTS idx_section_states_depth
  ON section_states(depth);

CREATE TABLE IF NOT EXISTS section_transitions (
    id              INTEGER PRIMARY KEY,
    section_number  TEXT NOT NULL,
    from_state      TEXT NOT NULL,
    to_state        TEXT NOT NULL,
    event           TEXT NOT NULL,
    context_json    TEXT,
    attempt_number  INTEGER DEFAULT 1,
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_section_transitions_section
  ON section_transitions(section_number);
CREATE INDEX IF NOT EXISTS idx_section_transitions_section_to
  ON section_transitions(section_number, to_state);
CREATE INDEX IF NOT EXISTS idx_section_states_state
  ON section_states(state);

CREATE TABLE IF NOT EXISTS bootstrap_execution_log (
    id           INTEGER PRIMARY KEY,
    stage        TEXT    NOT NULL,
    status       TEXT    NOT NULL,
    started_at   TEXT,
    completed_at TEXT,
    error        TEXT
);
"""

_TASK_STORE_SCHEMA = """\
CREATE TABLE IF NOT EXISTS task_dependencies (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id INTEGER NOT NULL REFERENCES tasks(id),
    depends_on_task_id INTEGER NOT NULL REFERENCES tasks(id),
    satisfied INTEGER NOT NULL DEFAULT 0,
    satisfied_at TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(task_id, depends_on_task_id)
);

CREATE TABLE IF NOT EXISTS task_subscriptions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    subscriber_scope TEXT NOT NULL,
    task_id INTEGER NOT NULL REFERENCES tasks(id),
    callback_task_type TEXT,
    callback_payload_path TEXT,
    verification_mode TEXT NOT NULL DEFAULT 'subscriber_verifies'
        CHECK(verification_mode IN ('subscriber_verifies', 'producer_terminal', 'validated_user_input')),
    status TEXT NOT NULL DEFAULT 'active'
        CHECK(status IN ('active', 'consumed', 'failed')),
    last_error TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    notified_at TEXT,
    consumed_at TEXT
);

CREATE TABLE IF NOT EXISTS task_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id INTEGER NOT NULL REFERENCES tasks(id),
    event_type TEXT NOT NULL,
    detail TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS task_claims (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id INTEGER NOT NULL REFERENCES tasks(id),
    claim_scope TEXT NOT NULL,
    claim_kind TEXT NOT NULL DEFAULT 'result',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(task_id, claim_scope)
);

CREATE TABLE IF NOT EXISTS user_input_requests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id INTEGER NOT NULL UNIQUE REFERENCES tasks(id),
    requested_by TEXT NOT NULL,
    requested_for_scope TEXT,
    question TEXT NOT NULL,
    response_schema_json TEXT,
    response_json TEXT,
    status TEXT NOT NULL DEFAULT 'awaiting_input'
        CHECK(status IN ('awaiting_input', 'answered', 'cancelled')),
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    answered_at TEXT
);

CREATE TABLE IF NOT EXISTS value_axes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    section_scope TEXT NOT NULL,
    axis_name TEXT NOT NULL,
    source_task_id INTEGER REFERENCES tasks(id),
    discovered_at TEXT NOT NULL DEFAULT (datetime('now')),
    status TEXT NOT NULL DEFAULT 'active'
        CHECK(status IN ('active', 'aligned', 'rejected', 'superseded')),
    UNIQUE(section_scope, axis_name)
);

CREATE INDEX IF NOT EXISTS idx_tasks_dedupe_active ON tasks(dedupe_key)
    WHERE dedupe_key IS NOT NULL AND status IN ('pending', 'running', 'blocked', 'awaiting_input');
CREATE INDEX IF NOT EXISTS idx_tasks_updated ON tasks(updated_at);
CREATE INDEX IF NOT EXISTS idx_task_deps_task ON task_dependencies(task_id);
CREATE INDEX IF NOT EXISTS idx_task_deps_depends ON task_dependencies(depends_on_task_id);
CREATE INDEX IF NOT EXISTS idx_task_subs_task ON task_subscriptions(task_id);
CREATE INDEX IF NOT EXISTS idx_task_subs_scope ON task_subscriptions(subscriber_scope);
CREATE INDEX IF NOT EXISTS idx_task_events_task ON task_events(task_id);
CREATE INDEX IF NOT EXISTS idx_user_input_task ON user_input_requests(task_id);
CREATE INDEX IF NOT EXISTS idx_value_axes_scope_status ON value_axes(section_scope, status);

CREATE TRIGGER IF NOT EXISTS trg_tasks_default_updated_at
AFTER INSERT ON tasks
FOR EACH ROW
WHEN NEW.updated_at IS NULL
BEGIN
    UPDATE tasks
    SET updated_at = created_at
    WHERE id = NEW.id;
END;
"""


def _ensure_task_store_columns(conn: sqlite3.Connection) -> None:
    _ensure_table_columns(
        conn,
        "tasks",
        (
            ("updated_at", "TEXT"),
            ("dedupe_key", "TEXT"),
            ("status_reason", "TEXT"),
            ("superseded_by_task_id", "INTEGER REFERENCES tasks(id)"),
            ("result_envelope_path", "TEXT"),
        ),
    )
    conn.execute(
        "UPDATE tasks SET updated_at = COALESCE(updated_at, created_at) "
        "WHERE updated_at IS NULL"
    )
    if _table_exists(conn, "task_subscriptions"):
        _ensure_table_columns(
            conn,
            "task_subscriptions",
            (
                ("last_error", "TEXT"),
                ("notified_at", "TEXT"),
            ),
        )


def _table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table_name,),
    ).fetchone()
    return row is not None


def _ensure_table_columns(
    conn: sqlite3.Connection,
    table_name: str,
    additions: Iterable[tuple[str, str]],
) -> None:
    columns = {
        row[1]
        for row in conn.execute(f"PRAGMA table_info({table_name})")
    }
    for name, ddl in additions:
        if name not in columns:
            conn.execute(f"ALTER TABLE {table_name} ADD COLUMN {name} {ddl}")


def _normalize_optional(value: object) -> object | None:
    if value in (None, ""):
        return None
    return value


def _normalize_dedupe_key(
    dedupe_key: str | tuple[str, str] | None,
) -> str | None:
    if dedupe_key is None:
        return None
    if isinstance(dedupe_key, str):
        return dedupe_key
    task_type, flow_id = dedupe_key
    return json.dumps(
        {"task_type": task_type, "flow_id": flow_id},
        sort_keys=True,
        separators=(",", ":"),
    )


def _normalize_dependencies(
    depends_on_tasks: Iterable[str | int] | None,
) -> list[int]:
    if depends_on_tasks is None:
        return []
    return list(dict.fromkeys(int(task_id) for task_id in depends_on_tasks))


def _build_dispatch_task(values: tuple[object, ...]) -> dict[str, str]:
    result: dict[str, str] = {}
    for (_, key), val in zip(_NEXT_TASK_FIELDS, values):
        if val is not None and val != "":
            result[key] = str(val)
    return result


def _has_graph_dependencies(conn: sqlite3.Connection, task_id: int) -> bool:
    row = conn.execute(
        "SELECT 1 FROM task_dependencies WHERE task_id=? LIMIT 1",
        (task_id,),
    ).fetchone()
    return row is not None


def _graph_dependencies_satisfied(conn: sqlite3.Connection, task_id: int) -> bool:
    row = conn.execute(
        "SELECT 1 FROM task_dependencies WHERE task_id=? AND satisfied=0 LIMIT 1",
        (task_id,),
    ).fetchone()
    return row is None


def _task_is_runnable(
    conn: sqlite3.Connection,
    task_id: int,
) -> bool:
    if not _has_graph_dependencies(conn, task_id):
        return True
    return _graph_dependencies_satisfied(conn, task_id)


def _normalize_json_value(value: str | dict | list | None) -> Any:
    if value is None:
        return None
    if isinstance(value, str):
        return json.loads(value)
    return value


def _normalize_json_text(value: str | dict | list | None) -> str | None:
    if value is None:
        return None
    normalized = _normalize_json_value(value)
    return json.dumps(normalized, sort_keys=True, separators=(",", ":"))


def _schema_errors(
    value: Any,
    schema: dict[str, Any],
    *,
    path: str = "$",
) -> list[str]:
    errors: list[str] = []
    expected_type = schema.get("type")
    if expected_type is not None:
        type_ok = {
            "object": isinstance(value, dict),
            "array": isinstance(value, list),
            "string": isinstance(value, str),
            "number": isinstance(value, (int, float)) and not isinstance(value, bool),
            "integer": isinstance(value, int) and not isinstance(value, bool),
            "boolean": isinstance(value, bool),
            "null": value is None,
        }.get(str(expected_type), True)
        if not type_ok:
            return [f"{path}: expected {expected_type}"]

    if "enum" in schema and value not in schema["enum"]:
        errors.append(f"{path}: value not in enum")

    if isinstance(value, dict):
        required = schema.get("required", [])
        if isinstance(required, list):
            for key in required:
                if key not in value:
                    errors.append(f"{path}.{key}: missing required field")
        properties = schema.get("properties", {})
        if isinstance(properties, dict):
            for key, subschema in properties.items():
                if key not in value or not isinstance(subschema, dict):
                    continue
                errors.extend(
                    _schema_errors(value[key], subschema, path=f"{path}.{key}")
                )

    if isinstance(value, list):
        items = schema.get("items")
        if isinstance(items, dict):
            for index, item in enumerate(value):
                errors.extend(
                    _schema_errors(item, items, path=f"{path}[{index}]")
                )

    return errors


def _validate_response_schema(
    response_json: str | dict | list,
    response_schema_json: str | dict | None,
) -> list[str]:
    if response_schema_json in (None, "", {}):
        return []
    try:
        schema = _normalize_json_value(response_schema_json)
    except json.JSONDecodeError as exc:
        return [f"schema: invalid JSON ({exc})"]
    if schema in (None, {}):
        return []
    if not isinstance(schema, dict):
        return ["schema: expected an object"]
    try:
        response = _normalize_json_value(response_json)
    except json.JSONDecodeError as exc:
        return [f"response: invalid JSON ({exc})"]
    return _schema_errors(response, schema)


def _derive_user_input_response_path(payload_path: str | None) -> Path | None:
    if not payload_path:
        return None
    prompt_path = Path(payload_path)
    if not prompt_path.is_absolute():
        return None
    if prompt_path.name.endswith("-prompt.md"):
        return prompt_path.with_name(
            prompt_path.name.replace("-prompt.md", "-response.json")
        )
    return prompt_path.with_name(f"{prompt_path.stem}-response.json")


def _section_number_from_scope(section_scope: str | None) -> str | None:
    if not section_scope or not section_scope.startswith("section-"):
        return None
    return section_scope.removeprefix("section-")


def _validate_verification_mode(verification_mode: str) -> None:
    if verification_mode not in _SUBSCRIPTION_VERIFICATION_MODES:
        raise ValueError(
            "invalid verification_mode: "
            f"{verification_mode!r}; expected one of "
            f"{', '.join(_SUBSCRIPTION_VERIFICATION_MODES)}"
        )


def _insert_task_subscription(
    conn: sqlite3.Connection,
    task_id: int,
    subscriber_scope: str,
    *,
    callback_task_type: str | None = None,
    callback_payload_path: str | None = None,
    verification_mode: str = "subscriber_verifies",
) -> int:
    _validate_verification_mode(verification_mode)
    cur = conn.execute(
        """INSERT INTO task_subscriptions(
               subscriber_scope, task_id, callback_task_type,
               callback_payload_path, verification_mode
           ) VALUES(?, ?, ?, ?, ?)""",
        (
            subscriber_scope,
            task_id,
            callback_task_type,
            callback_payload_path,
            verification_mode,
        ),
    )
    return int(cur.lastrowid)


def _append_task_event(
    conn: sqlite3.Connection,
    task_id: int,
    event_type: str,
    detail: str | None = None,
) -> int:
    cur = conn.execute(
        "INSERT INTO task_events(task_id, event_type, detail) VALUES(?, ?, ?)",
        (task_id, event_type, detail),
    )
    return int(cur.lastrowid)


def _parse_db_datetime(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


def _dependency_rows_for_ids(
    conn: sqlite3.Connection,
    dependency_ids: list[int],
) -> dict[int, str]:
    if not dependency_ids:
        return {}
    rows = conn.execute(
        "SELECT id, status FROM tasks WHERE id IN ({})".format(
            ", ".join("?" for _ in dependency_ids),
        ),
        dependency_ids,
    ).fetchall()
    return {int(row[0]): str(row[1]) for row in rows}


def _dependency_state_for_new_task(
    conn: sqlite3.Connection,
    dependency_ids: list[int],
) -> tuple[str, str | None, str | None, bool]:
    if not dependency_ids:
        return ("pending", None, None, False)

    statuses = _dependency_rows_for_ids(conn, dependency_ids)
    missing = [dependency_id for dependency_id in dependency_ids if dependency_id not in statuses]
    failed = [
        dependency_id
        for dependency_id in dependency_ids
        if statuses.get(dependency_id) in {"failed", "cancelled"}
    ]
    if missing or failed:
        failed_ref = (failed or missing)[0]
        return (
            "failed",
            _DEPENDENCY_FAILURE_REASON,
            f"dependency_failed:{failed_ref}",
            True,
        )
    if all(statuses[dependency_id] == "complete" for dependency_id in dependency_ids):
        return ("pending", None, None, False)
    return ("blocked", _DEPENDENCY_WAIT_REASON, None, False)


def _write_dependency_rows(
    conn: sqlite3.Connection,
    task_id: int,
    dependency_ids: list[int],
) -> None:
    if not dependency_ids:
        return
    statuses = _dependency_rows_for_ids(conn, dependency_ids)
    conn.executemany(
        """INSERT OR IGNORE INTO task_dependencies(
               task_id, depends_on_task_id, satisfied, satisfied_at
           ) VALUES(?, ?, ?, ?)""",
        [
            (
                task_id,
                dependency_id,
                1 if statuses.get(dependency_id) == "complete" else 0,
                datetime.now(timezone.utc).isoformat()
                if statuses.get(dependency_id) == "complete"
                else None,
            )
            for dependency_id in dependency_ids
        ],
    )


def _request_task_in_txn(
    conn: sqlite3.Connection,
    task_spec,
    *,
    dedupe_key: str | tuple[str, str] | None = None,
    depends_on_tasks: Iterable[str | int] | None = None,
    subscriber_scope: str | None = None,
) -> int:
    normalized_dedupe_key = _normalize_dedupe_key(dedupe_key)
    dependency_ids = _normalize_dependencies(depends_on_tasks)
    existing_task_id: int | None = None
    if normalized_dedupe_key is not None:
        row = conn.execute(
            "SELECT id FROM tasks WHERE dedupe_key = ? "
            f"AND status IN ({_ACTIVE_TASK_STATUSES_SQL}) "
            "ORDER BY id ASC LIMIT 1",
            (normalized_dedupe_key, *_ACTIVE_TASK_STATUSES),
        ).fetchone()
        if row is not None:
            existing_task_id = int(row[0])

    if existing_task_id is None:
        status, status_reason, error, is_terminal = _dependency_state_for_new_task(
            conn,
            dependency_ids,
        )
        cur = conn.execute(
            """INSERT INTO tasks(
                   submitted_by, task_type, problem_id, concern_scope,
                   payload_path, priority, status, error, completed_at,
                   instance_id, flow_id, chain_id, declared_by_task_id,
                   trigger_gate_id, flow_context_path, continuation_path,
                   result_manifest_path, freshness_token, dedupe_key,
                   updated_at, status_reason
               )
               VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'), ?)""",
            (
                getattr(task_spec, "submitted_by"),
                getattr(task_spec, "task_type"),
                _normalize_optional(getattr(task_spec, "problem_id", None)),
                _normalize_optional(getattr(task_spec, "concern_scope", None)),
                _normalize_optional(getattr(task_spec, "payload_path", None)),
                getattr(task_spec, "priority", "normal"),
                status,
                error,
                datetime.now(timezone.utc).isoformat() if is_terminal else None,
                _normalize_optional(getattr(task_spec, "instance_id", None)),
                _normalize_optional(getattr(task_spec, "flow_id", None)),
                _normalize_optional(getattr(task_spec, "chain_id", None)),
                _normalize_optional(getattr(task_spec, "declared_by_task_id", None)),
                _normalize_optional(getattr(task_spec, "trigger_gate_id", None)),
                _normalize_optional(getattr(task_spec, "flow_context_path", None)),
                _normalize_optional(getattr(task_spec, "continuation_path", None)),
                _normalize_optional(getattr(task_spec, "result_manifest_path", None)),
                _normalize_optional(getattr(task_spec, "freshness_token", None)),
                normalized_dedupe_key,
                status_reason,
            ),
        )
        task_id = int(cur.lastrowid)
        _write_dependency_rows(conn, task_id, dependency_ids)
    else:
        task_id = existing_task_id

    if subscriber_scope is not None:
        _insert_task_subscription(conn, task_id, subscriber_scope)
    return task_id


def _mark_dependency_satisfied(
    conn: sqlite3.Connection,
    completed_task_id: int,
) -> None:
    rows = conn.execute(
        """SELECT id, task_id
           FROM task_dependencies
           WHERE depends_on_task_id=? AND satisfied=0
           ORDER BY id ASC""",
        (completed_task_id,),
    ).fetchall()
    for dependency_row_id, downstream_task_id in rows:
        conn.execute(
            "UPDATE task_dependencies SET satisfied=1, satisfied_at=datetime('now') WHERE id=?",
            (int(dependency_row_id),),
        )
        _append_task_event(
            conn,
            int(downstream_task_id),
            "dependency_satisfied",
            f"depends_on:{completed_task_id}",
        )
        waiting = conn.execute(
            "SELECT 1 FROM task_dependencies WHERE task_id=? AND satisfied=0 LIMIT 1",
            (int(downstream_task_id),),
        ).fetchone()
        if waiting is None:
            conn.execute(
                """UPDATE tasks
                   SET status='pending', status_reason=NULL, updated_at=datetime('now')
                   WHERE id=? AND status='blocked'""",
                (int(downstream_task_id),),
            )


def _cascade_dependency_failures(
    conn: sqlite3.Connection,
    failed_task_id: int,
) -> None:
    queue = [failed_task_id]
    seen: set[int] = set()
    while queue:
        current_failed_id = queue.pop(0)
        if current_failed_id in seen:
            continue
        seen.add(current_failed_id)
        rows = conn.execute(
            """SELECT DISTINCT task_id
               FROM task_dependencies
               WHERE depends_on_task_id=?
               ORDER BY task_id ASC""",
            (current_failed_id,),
        ).fetchall()
        for (downstream_task_id,) in rows:
            updated = conn.execute(
                f"""UPDATE tasks
                    SET status='failed',
                        error=?,
                        status_reason=?,
                        completed_at=COALESCE(completed_at, datetime('now')),
                        updated_at=datetime('now')
                    WHERE id=?
                      AND status IN ({_NON_TERMINAL_TASK_STATUSES_SQL})""",
                (
                    f"dependency_failed:{current_failed_id}",
                    _DEPENDENCY_FAILURE_REASON,
                    int(downstream_task_id),
                    *_NON_TERMINAL_TASK_STATUSES,
                ),
            )
            if updated.rowcount == 0:
                continue
            _append_task_event(
                conn,
                int(downstream_task_id),
                "failed",
                f"dependency_failed:{current_failed_id}",
            )
            queue.append(int(downstream_task_id))


def _resolve_subscriptions_in_txn(
    conn: sqlite3.Connection,
    task_id: int,
    planspace: Path,
) -> None:
    task_row = conn.execute(
        "SELECT result_envelope_path FROM tasks WHERE id=?",
        (task_id,),
    ).fetchone()
    result_envelope_path = (
        str(PathRegistry(planspace).task_result_envelope(task_id))
        if task_row is None or not task_row[0]
        else str(task_row[0])
    )
    subscriptions = conn.execute(
        "SELECT id, subscriber_scope, callback_task_type FROM task_subscriptions "
        "WHERE task_id=? AND status='active' ORDER BY id ASC",
        (task_id,),
    ).fetchall()
    for subscription_id, subscriber_scope, callback_task_type in subscriptions:
        if callback_task_type:
            try:
                callback_task_id = _request_task_in_txn(
                    conn,
                    SimpleNamespace(
                        task_type=str(callback_task_type),
                        submitted_by=f"task-subscription:{task_id}",
                        concern_scope=str(subscriber_scope),
                        payload_path=result_envelope_path,
                        priority="normal",
                    ),
                )
            except Exception as exc:  # noqa: BLE001
                conn.execute(
                    """UPDATE task_subscriptions
                       SET status='failed', last_error=?, notified_at=datetime('now')
                       WHERE id=?""",
                    (str(exc), int(subscription_id)),
                )
                _append_task_event(
                    conn,
                    task_id,
                    "subscription_notification_failed",
                    f"{subscription_id}:{exc}",
                )
                continue
            conn.execute(
                """UPDATE task_subscriptions
                   SET status='consumed',
                       last_error=NULL,
                       notified_at=datetime('now'),
                       consumed_at=datetime('now')
                   WHERE id=?""",
                (int(subscription_id),),
            )
            _append_task_event(
                conn,
                task_id,
                "subscription_notified",
                f"{subscription_id}:{callback_task_id}",
            )
            continue

        conn.execute(
            """UPDATE task_subscriptions
               SET status='consumed',
                   last_error=NULL,
                   notified_at=datetime('now'),
                   consumed_at=datetime('now')
               WHERE id=?""",
            (int(subscription_id),),
        )
        _append_task_event(conn, task_id, "subscription_notified", str(subscription_id))


def _resolve_validated_user_input_subscriptions_in_txn(
    conn: sqlite3.Connection,
    task_id: int,
    response_artifact_path: str | None,
) -> None:
    subscriptions = conn.execute(
        """SELECT id, subscriber_scope, callback_task_type
           FROM task_subscriptions
           WHERE task_id=?
             AND status='active'
             AND verification_mode='validated_user_input'
           ORDER BY id ASC""",
        (task_id,),
    ).fetchall()
    for subscription_id, subscriber_scope, callback_task_type in subscriptions:
        if callback_task_type:
            try:
                callback_task_id = _request_task_in_txn(
                    conn,
                    SimpleNamespace(
                        task_type=str(callback_task_type),
                        submitted_by=f"user-input-subscription:{task_id}",
                        concern_scope=str(subscriber_scope),
                        payload_path=response_artifact_path,
                        priority="normal",
                    ),
                )
            except Exception as exc:  # noqa: BLE001
                conn.execute(
                    """UPDATE task_subscriptions
                       SET status='failed', last_error=?, notified_at=datetime('now')
                       WHERE id=?""",
                    (str(exc), int(subscription_id)),
                )
                _append_task_event(
                    conn,
                    task_id,
                    "subscription_notification_failed",
                    f"{subscription_id}:{exc}",
                )
                continue
            detail = f"{subscription_id}:{callback_task_id}"
        else:
            detail = str(subscription_id)

        conn.execute(
            """UPDATE task_subscriptions
               SET status='consumed',
                   last_error=NULL,
                   notified_at=datetime('now'),
                   consumed_at=datetime('now')
               WHERE id=?""",
            (int(subscription_id),),
        )
        _append_task_event(conn, task_id, "subscription_notified", detail)


def _request_user_input_in_txn(
    conn: sqlite3.Connection,
    task_id: int,
    question_text: str,
    response_schema_json: str | dict | None = None,
) -> None:
    task_row = conn.execute(
        "SELECT submitted_by, concern_scope, status FROM tasks WHERE id=?",
        (task_id,),
    ).fetchone()
    if task_row is None:
        raise RuntimeError(f"task not found for user input request: {task_id}")

    task_status = str(task_row[2])
    if task_status in {"complete", "failed", "cancelled"}:
        raise RuntimeError(
            f"task not eligible for user input request: {task_id} ({task_status})"
        )

    conn.execute(
        """INSERT INTO user_input_requests(
               task_id, requested_by, requested_for_scope, question,
               response_schema_json, response_json, status, answered_at
           ) VALUES(?, ?, ?, ?, ?, NULL, 'awaiting_input', NULL)
           ON CONFLICT(task_id) DO UPDATE SET
               requested_by=excluded.requested_by,
               requested_for_scope=excluded.requested_for_scope,
               question=excluded.question,
               response_schema_json=excluded.response_schema_json,
               response_json=NULL,
               status='awaiting_input',
               answered_at=NULL""",
        (
            int(task_id),
            str(task_row[0]),
            str(task_row[1]) if task_row[1] is not None else None,
            question_text,
            _normalize_json_text(response_schema_json),
        ),
    )
    updated = conn.execute(
        f"""UPDATE tasks
            SET status='awaiting_input',
                claimed_by=NULL,
                claimed_at=NULL,
                status_reason='awaiting_user_input',
                updated_at=datetime('now')
            WHERE id=?
              AND status IN ({_NON_TERMINAL_TASK_STATUSES_SQL})""",
        (int(task_id), *_NON_TERMINAL_TASK_STATUSES),
    )
    if updated.rowcount == 0:
        raise RuntimeError(f"task not movable to awaiting_input: {task_id}")
    _append_task_event(conn, int(task_id), "awaiting_input", question_text)


def _record_value_axis_in_txn(
    conn: sqlite3.Connection,
    section_scope: str,
    axis_name: str,
    *,
    source_task_id: int | None = None,
) -> tuple[int, bool]:
    cur = conn.execute(
        """INSERT OR IGNORE INTO value_axes(
               section_scope, axis_name, source_task_id
           ) VALUES(?, ?, ?)""",
        (section_scope, axis_name, source_task_id),
    )
    row = conn.execute(
        """SELECT id
           FROM value_axes
           WHERE section_scope=? AND axis_name=?""",
        (section_scope, axis_name),
    ).fetchone()
    if row is None:
        raise RuntimeError(
            f"failed to record value axis for {section_scope}: {axis_name}"
        )
    return int(row[0]), cur.rowcount > 0


def _get_value_axes_in_txn(
    conn: sqlite3.Connection,
    section_scope: str,
    *,
    status: str = "active",
) -> list[dict[str, Any]]:
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """SELECT *
           FROM value_axes
           WHERE section_scope=? AND status=?
           ORDER BY id ASC""",
        (section_scope, status),
    ).fetchall()
    return [dict(row) for row in rows]


def _write_value_axes_artifact(
    conn: sqlite3.Connection,
    planspace: Path,
    section_scope: str,
    *,
    triggered_axes: list[str] | None = None,
) -> None:
    section_number = _section_number_from_scope(section_scope)
    if section_number is None:
        return
    axes = _get_value_axes_in_txn(conn, section_scope, status="active")
    artifact_path = PathRegistry(planspace).value_axes_artifact(section_number)
    artifact_path.parent.mkdir(parents=True, exist_ok=True)
    artifact_path.write_text(
        json.dumps(
            {
                "section_scope": section_scope,
                "triggered_axes": triggered_axes or [],
                "active_axes": axes,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )


def _detect_value_expansion_in_txn(
    conn: sqlite3.Connection,
    section_scope: str,
) -> list[dict[str, Any]]:
    axes = _get_value_axes_in_txn(conn, section_scope, status="active")
    if not axes:
        return []
    alignment_row = conn.execute(
        f"""SELECT 1
            FROM tasks
            WHERE concern_scope=?
              AND task_type='section.assess'
              AND status IN ({_ACTIVE_TASK_STATUSES_SQL})
            LIMIT 1""",
        (section_scope, *_ACTIVE_TASK_STATUSES),
    ).fetchone()
    if alignment_row is not None:
        return []
    return axes


def init_db(db_path: str | Path) -> None:
    """Initialize the coordination database schema (idempotent).

    Creates all required tables and indexes using the same schema
    as ``db.sh init``.
    """
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=_SQLITE_CONNECT_TIMEOUT_SECONDS)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(f"PRAGMA busy_timeout={_SQLITE_BUSY_TIMEOUT_MS}")
    conn.executescript(_INIT_SCHEMA)
    conn.executescript(_TASK_STORE_SCHEMA)
    _ensure_task_store_columns(conn)
    conn.commit()
    conn.close()


@contextmanager
def task_db(db_path: str | Path) -> Generator[sqlite3.Connection]:
    """Open a WAL-mode SQLite connection with standard pragmas.

    Usage::

        with task_db(db_path) as conn:
            conn.execute("SELECT ...")
    """
    conn = sqlite3.connect(str(db_path), timeout=_SQLITE_CONNECT_TIMEOUT_SECONDS)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(f"PRAGMA busy_timeout={_SQLITE_BUSY_TIMEOUT_MS}")
    try:
        yield conn
    finally:
        conn.close()


# ------------------------------------------------------------------
# Pure-Python task operations (replace db.sh subprocess calls)
# ------------------------------------------------------------------

def has_active_task(db_path: str | Path, concern_scope: str, task_type: str) -> bool:
    """Check if a pending or running task exists for this scope+type."""
    with task_db(db_path) as conn:
        row = conn.execute(
            "SELECT 1 FROM tasks WHERE concern_scope = ? AND task_type = ? "
            f"AND status IN ({_ACTIVE_TASK_STATUSES_SQL}) LIMIT 1",
            (concern_scope, task_type, *_ACTIVE_TASK_STATUSES),
        ).fetchone()
    return row is not None


def reset_stuck_running_tasks(db_path: str | Path) -> int:
    """Reset tasks stuck in 'running' status back to 'pending'.

    On startup, tasks left in 'running' from a previous crashed process
    cannot make progress.  Returns the number of tasks reset.
    """
    with task_db(db_path) as conn:
        cur = conn.execute(
            "UPDATE tasks SET status='pending', claimed_by=NULL, "
            "claimed_at=NULL WHERE status='running'"
        )
        conn.commit()
        return cur.rowcount


def purge_stale_tasks(db_path: str | Path) -> int:
    """Mark all pending/running tasks as failed on process restart.

    Called on fresh (non-resume) runs to ensure leftover tasks from a
    previous process do not execute with outdated context.  Returns the
    number of tasks purged.
    """
    with task_db(db_path) as conn:
        cur = conn.execute(
            "UPDATE tasks SET status='failed', error='stale: process restart', "
            "completed_at=datetime('now') "
            f"WHERE status IN ({_NON_TERMINAL_TASK_STATUSES_SQL})",
            _NON_TERMINAL_TASK_STATUSES,
        )
        conn.commit()
        return cur.rowcount

_NEXT_TASK_FIELDS = [
    ("id", "id"), ("task_type", "type"), ("submitted_by", "by"),
    ("priority", "prio"), ("problem_id", "problem"),
    ("concern_scope", "scope"), ("payload_path", "payload"),
    ("instance_id", "instance"),
    ("flow_id", "flow"), ("chain_id", "chain"),
    ("declared_by_task_id", "declared_by_task"),
    ("trigger_gate_id", "trigger_gate"),
    ("flow_context_path", "flow_context"),
    ("continuation_path", "continuation"),
    ("freshness_token", "freshness"),
]


def get_task(db_path: str | Path, task_id: str | int) -> dict[str, str] | None:
    """Return a task row by ID using dispatcher-compatible field aliases."""
    with task_db(db_path) as conn:
        row = conn.execute(
            "SELECT id, task_type, problem_id, concern_scope, payload_path, "
            "priority, submitted_by, instance_id, flow_id, "
            "chain_id, declared_by_task_id, trigger_gate_id, "
            "flow_context_path, continuation_path, freshness_token, "
            "status, output_path, error, result_manifest_path "
            "FROM tasks WHERE id=?",
            (int(task_id),),
        ).fetchone()
        if row is None:
            return None

    (
        tid, ttype, pid, scope, payload, prio, by,
        inst, flow, chain, declared_by, trig_gate, flow_ctx,
        cont, freshness, status, output_path, error, result_manifest,
    ) = row
    values = (
        tid, ttype, by, prio, pid, scope, payload,
        inst, flow, chain, declared_by, trig_gate, flow_ctx,
        cont, freshness,
    )
    result = _build_dispatch_task(values)
    if status is not None and status != "":
        result["status"] = str(status)
    if output_path is not None and output_path != "":
        result["output"] = str(output_path)
    if error is not None and error != "":
        result["error"] = str(error)
    if result_manifest is not None and result_manifest != "":
        result["result_manifest"] = str(result_manifest)
    return result


def request_task(
    db_path: str | Path,
    task_spec,
    *,
    dedupe_key: str | tuple[str, str] | None = None,
    depends_on_tasks: Iterable[str | int] | None = None,
    subscriber_scope: str | None = None,
) -> int:
    """Create a task reservation with optional dedupe, deps, and subscription."""
    effective_dependencies = depends_on_tasks
    if effective_dependencies is None:
        effective_dependencies = getattr(task_spec, "depends_on_tasks", None)
    with task_db(db_path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        task_id = _request_task_in_txn(
            conn,
            task_spec,
            dedupe_key=dedupe_key,
            depends_on_tasks=effective_dependencies,
            subscriber_scope=subscriber_scope,
        )
        conn.commit()
        return task_id


def claim_runnable_task(
    db_path: str | Path,
    dispatcher_id: str,
) -> dict[str, str] | None:
    """Atomically claim the next pending task whose dependencies are satisfied."""
    with task_db(db_path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            """UPDATE tasks
               SET status='pending', status_reason=NULL, updated_at=datetime('now')
               WHERE status='blocked'
                 AND EXISTS (
                   SELECT 1 FROM task_dependencies deps WHERE deps.task_id = tasks.id
                 )
                 AND NOT EXISTS (
                   SELECT 1 FROM task_dependencies deps
                   WHERE deps.task_id = tasks.id AND deps.satisfied = 0
                 )"""
        )
        cur = conn.execute(
            "SELECT id, task_type, problem_id, concern_scope, payload_path, "
            "priority, submitted_by, instance_id, flow_id, "
            "chain_id, declared_by_task_id, trigger_gate_id, "
            "flow_context_path, continuation_path, freshness_token "
            "FROM tasks WHERE status='pending' ORDER BY "
            "CASE priority WHEN 'high' THEN 0 WHEN 'normal' THEN 1 "
            "WHEN 'low' THEN 2 ELSE 3 END, id ASC",
        )
        for row in cur:
            (tid, ttype, pid, scope, payload, prio, by,
             inst, flow, chain, declared_by, trig_gate, flow_ctx,
             cont, freshness) = row
            if not _task_is_runnable(conn, int(tid)):
                continue
            claimed = conn.execute(
                "UPDATE tasks SET status='running', claimed_by=?, "
                "claimed_at=datetime('now'), updated_at=datetime('now') "
                "WHERE id=? AND status='pending'",
                (dispatcher_id, tid),
            )
            if claimed.rowcount == 0:
                continue
            conn.commit()
            values = (tid, ttype, by, prio, pid, scope, payload,
                      inst, flow, chain, declared_by, trig_gate,
                      flow_ctx, cont, freshness)
            return _build_dispatch_task(values)
        conn.commit()
    return None


def complete_task_with_result(
    db_path: str | Path,
    task_id: str | int,
    *,
    output_path: str | None = None,
    result_envelope_path: str | None = None,
    planspace: Path | None = None,
    result_envelope=None,
) -> None:
    """Mark a running task complete and satisfy downstream dependencies."""
    tid = int(task_id)
    effective_planspace = planspace or Path(db_path).resolve().parent
    with task_db(db_path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        cur = conn.execute(
            "UPDATE tasks SET status='complete', output_path=?, "
            "result_envelope_path=?, status_reason=NULL, "
            "completed_at=datetime('now'), updated_at=datetime('now') "
            "WHERE id=? AND status='running'",
            (output_path, result_envelope_path, tid),
        )
        if cur.rowcount == 0:
            raise RuntimeError(
                f"task not completable (not running or not found): {task_id}"
            )
        _append_task_event(conn, tid, "completed", output_path or result_envelope_path)
        conn.commit()
    from flow.engine.subscription_resolver import SubscriptionResolver
    from signals.repository import artifact_io as artifact_io_module

    resolver = SubscriptionResolver(artifact_io_module)
    resolver.resolve(
        db_path,
        tid,
        effective_planspace,
        result_envelope,
    )


def fail_task_with_result(
    db_path: str | Path,
    task_id: str | int,
    *,
    error: str | None = None,
    result_envelope_path: str | None = None,
    output_path: str | None = None,
) -> None:
    """Mark a running task failed and fail closed on downstream dependencies."""
    tid = int(task_id)
    with task_db(db_path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        cur = conn.execute(
            "UPDATE tasks SET status='failed', error=?, output_path=?, "
            "result_envelope_path=?, status_reason=NULL, completed_at=datetime('now'), "
            "updated_at=datetime('now') WHERE id=? AND status='running'",
            (error, output_path, result_envelope_path, tid),
        )
        if cur.rowcount == 0:
            raise RuntimeError(
                f"task not failable (not running or not found): {task_id}"
            )
        _append_task_event(conn, tid, "failed", error or result_envelope_path)
        _cascade_dependency_failures(conn, tid)
        conn.commit()


def query_tasks(
    db_path: str | Path,
    *,
    status: str | None = None,
    concern_scope: str | None = None,
    task_type: str | None = None,
    dedupe_key: str | None = None,
) -> list[dict]:
    """Query task rows with optional filters."""
    with task_db(db_path) as conn:
        conn.row_factory = sqlite3.Row
        clauses: list[str] = []
        params: list[object] = []
        if status is not None:
            clauses.append("status = ?")
            params.append(status)
        if concern_scope is not None:
            clauses.append("concern_scope = ?")
            params.append(concern_scope)
        if task_type is not None:
            clauses.append("task_type = ?")
            params.append(task_type)
        if dedupe_key is not None:
            clauses.append("dedupe_key = ?")
            params.append(dedupe_key)

        sql = "SELECT * FROM tasks"
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY id ASC"
        rows = conn.execute(sql, params).fetchall()
    return [dict(row) for row in rows]


def subscribe_to_task(
    db_path: str | Path,
    task_id: str | int,
    subscriber_scope: str,
    *,
    callback_task_type: str | None = None,
    callback_payload_path: str | None = None,
    verification_mode: str = "subscriber_verifies",
) -> int:
    """Create a task subscription row and return its ID."""
    with task_db(db_path) as conn:
        subscription_id = _insert_task_subscription(
            conn,
            int(task_id),
            subscriber_scope,
            callback_task_type=callback_task_type,
            callback_payload_path=callback_payload_path,
            verification_mode=verification_mode,
        )
        conn.commit()
        return subscription_id


def request_user_input(
    db_path: str | Path,
    task_id: str | int,
    question_text: str,
    response_schema_json: str | dict | None = None,
) -> None:
    """Create a user-input request row and block the task awaiting answer."""
    with task_db(db_path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        _request_user_input_in_txn(
            conn,
            int(task_id),
            question_text,
            response_schema_json=response_schema_json,
        )
        conn.commit()


def answer_user_input(
    db_path: str | Path,
    task_id: str | int,
    response_json: str | dict | list,
) -> bool:
    """Validate and store a user response, unblocking the task on success."""
    tid = int(task_id)
    with task_db(db_path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            """SELECT response_schema_json, status
               FROM user_input_requests
               WHERE task_id=?""",
            (tid,),
        ).fetchone()
        if row is None:
            raise RuntimeError(f"user input request not found for task: {task_id}")
        errors = _validate_response_schema(response_json, row[0])
        if errors:
            _append_task_event(
                conn,
                tid,
                "user_input_validation_failed",
                "; ".join(errors),
            )
            conn.commit()
            return False

        normalized_response = _normalize_json_value(response_json)
        response_text = json.dumps(
            normalized_response,
            sort_keys=True,
            separators=(",", ":"),
        )
        conn.execute(
            """UPDATE user_input_requests
               SET status='answered',
                   response_json=?,
                   answered_at=datetime('now')
               WHERE task_id=?""",
            (response_text, tid),
        )
        updated = conn.execute(
            """UPDATE tasks
               SET status='pending',
                   claimed_by=NULL,
                   claimed_at=NULL,
                   status_reason=NULL,
                   updated_at=datetime('now')
               WHERE id=? AND status='awaiting_input'""",
            (tid,),
        )
        if updated.rowcount == 0:
            raise RuntimeError(
                f"task not awaiting_input when answering user input: {task_id}"
            )

        task_row = conn.execute(
            "SELECT payload_path FROM tasks WHERE id=?",
            (tid,),
        ).fetchone()
        response_artifact_path = None
        if task_row is not None:
            artifact_path = _derive_user_input_response_path(
                str(task_row[0]) if task_row[0] is not None else None
            )
            if artifact_path is not None:
                artifact_path.parent.mkdir(parents=True, exist_ok=True)
                artifact_path.write_text(
                    json.dumps(normalized_response, indent=2, sort_keys=True) + "\n",
                    encoding="utf-8",
                )
                response_artifact_path = str(artifact_path)

        _append_task_event(
            conn,
            tid,
            "user_input_answered",
            response_artifact_path or "answered",
        )
        _resolve_validated_user_input_subscriptions_in_txn(
            conn,
            tid,
            response_artifact_path,
        )
        conn.commit()
        return True


def get_active_subscriptions(
    db_path: str | Path,
    task_id: str | int,
) -> list[dict]:
    """Return active subscriptions for the given task."""
    with task_db(db_path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM task_subscriptions "
            "WHERE task_id=? AND status='active' ORDER BY id ASC",
            (int(task_id),),
        ).fetchall()
    return [dict(row) for row in rows]


def record_value_axis(
    db_path: str | Path,
    section_scope: str,
    axis_name: str,
    source_task_id: int | None = None,
) -> int:
    """Persist a value axis, deduplicated by section scope and axis name."""
    with task_db(db_path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        axis_id, _ = _record_value_axis_in_txn(
            conn,
            section_scope,
            axis_name,
            source_task_id=source_task_id,
        )
        conn.commit()
        return axis_id


def get_value_axes(
    db_path: str | Path,
    section_scope: str,
    *,
    status: str = "active",
) -> list[dict[str, Any]]:
    """Return persisted value axes for a section scope filtered by status."""
    if status not in _VALUE_AXIS_STATUSES:
        raise ValueError(f"invalid value axis status: {status}")
    with task_db(db_path) as conn:
        return _get_value_axes_in_txn(conn, section_scope, status=status)


def update_value_axis_status(
    db_path: str | Path,
    axis_id: int,
    status: str,
) -> None:
    """Update a persisted value axis status."""
    if status not in _VALUE_AXIS_STATUSES:
        raise ValueError(f"invalid value axis status: {status}")
    with task_db(db_path) as conn:
        updated = conn.execute(
            "UPDATE value_axes SET status=? WHERE id=?",
            (status, int(axis_id)),
        )
        if updated.rowcount == 0:
            raise RuntimeError(f"value axis not found: {axis_id}")
        conn.commit()


def detect_value_expansion(
    db_path: str | Path,
    section_scope: str,
) -> list[dict[str, Any]]:
    """Return active value axes that do not yet have an active assess task."""
    with task_db(db_path) as conn:
        return _detect_value_expansion_in_txn(conn, section_scope)


def resolve_subscriptions(
    db_path: str | Path,
    task_id: str | int,
    planspace: Path,
) -> None:
    """Resolve active subscriptions for a completed task."""
    from flow.engine.subscription_resolver import SubscriptionResolver
    from signals.repository import artifact_io as artifact_io_module

    resolver = SubscriptionResolver(artifact_io_module)
    resolver.resolve(db_path, int(task_id), planspace, result_envelope=None)


def detect_dependency_starvation(
    db_path: str | Path,
    *,
    threshold_seconds: float = _DEFAULT_DEPENDENCY_STARVATION_SECONDS,
) -> list[int]:
    """Return blocked task IDs whose dependencies have made no progress."""
    with task_db(db_path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """SELECT id, created_at, updated_at
               FROM tasks
               WHERE status='blocked'
               ORDER BY id ASC"""
        ).fetchall()
        starved: list[int] = []
        now = datetime.now(timezone.utc)
        for row in rows:
            task_id = int(row["id"])
            task_timestamp = row["updated_at"] or row["created_at"]
            if not task_timestamp:
                continue
            task_age = now - _parse_db_datetime(str(task_timestamp))
            if task_age.total_seconds() < threshold_seconds:
                continue
            dep_rows = conn.execute(
                """SELECT t.id, t.created_at, t.updated_at
                   FROM task_dependencies deps
                   JOIN tasks t ON t.id = deps.depends_on_task_id
                   WHERE deps.task_id=? AND deps.satisfied=0""",
                (task_id,),
            ).fetchall()
            if not dep_rows:
                continue
            latest_progress = max(
                _parse_db_datetime(str(dep_row["updated_at"] or dep_row["created_at"]))
                for dep_row in dep_rows
                if dep_row["updated_at"] or dep_row["created_at"]
            )
            if (now - latest_progress).total_seconds() < threshold_seconds:
                continue
            starved.append(task_id)
            _append_task_event(
                conn,
                task_id,
                "dependency_starvation",
                f"threshold_seconds:{int(threshold_seconds)}",
            )
        conn.commit()
        return starved


def log_task_event(
    db_path: str | Path,
    task_id: str | int,
    event_type: str,
    detail: str | None = None,
) -> int:
    """Append a row to the task event log and return the event ID."""
    with task_db(db_path) as conn:
        cur = conn.execute(
            "INSERT INTO task_events(task_id, event_type, detail) VALUES(?, ?, ?)",
            (int(task_id), event_type, detail),
        )
        conn.commit()
        return int(cur.lastrowid)


def update_task_flow_paths(
    db_path: str | Path,
    task_id: int,
    flow_context_path: str,
    continuation_path: str,
    result_manifest_path: str,
) -> None:
    """Update a task's flow-related paths after submission."""
    with task_db(db_path) as conn:
        conn.execute(
            """UPDATE tasks
               SET flow_context_path=?, continuation_path=?,
                   result_manifest_path=?
               WHERE id=?""",
            (flow_context_path, continuation_path, result_manifest_path, task_id),
        )
        conn.commit()


def update_task_routing(
    db_path: str | Path, task_id: str | int, agent_file: str, model: str,
) -> None:
    """Update the task row with the resolved agent file and model."""
    with task_db(db_path) as conn:
        conn.execute(
            "UPDATE tasks SET agent_file=?, model=? WHERE id=?",
            (agent_file, model, int(task_id)),
        )
        conn.commit()


def count_tasks(db_path: str | Path) -> int:
    """Return the total number of tasks in the queue (any status)."""
    with task_db(db_path) as conn:
        row = conn.execute("SELECT COUNT(*) FROM tasks").fetchone()
        return row[0] if row else 0


def count_tasks_by_type(db_path: str | Path, task_type: str) -> int:
    """Return the count of tasks matching a given task_type (any status)."""
    with task_db(db_path) as conn:
        row = conn.execute(
            "SELECT COUNT(*) FROM tasks WHERE task_type = ?",
            (task_type,),
        ).fetchone()
        return row[0] if row else 0


def count_pending_tasks(db_path: str | Path, flow_id: str | None = None) -> int:
    """Return the number of non-terminal tasks.

    If *flow_id* is provided, only counts tasks belonging to that flow.
    """
    with task_db(db_path) as conn:
        if flow_id:
            row = conn.execute(
                "SELECT COUNT(*) FROM tasks "
                f"WHERE status IN ({_NON_TERMINAL_TASK_STATUSES_SQL}) AND flow_id = ?",
                (*_NON_TERMINAL_TASK_STATUSES, flow_id),
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT COUNT(*) FROM tasks "
                f"WHERE status IN ({_NON_TERMINAL_TASK_STATUSES_SQL})",
                _NON_TERMINAL_TASK_STATUSES,
            ).fetchone()
        return row[0] if row else 0


def load_task(db_path: str | Path, task_id: int) -> dict | None:
    """Load a task row by ID. Returns a dict or None if not found."""
    with task_db(db_path) as conn:
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute("SELECT * FROM tasks WHERE id = ?", (task_id,))
        row = cur.fetchone()
    return dict(row) if row is not None else None


def send_message(
    db_path: str | Path, target: str, body: str, *, sender: str = "",
) -> None:
    """Send a mailbox message (equivalent to ``db.sh send``)."""
    with task_db(db_path) as conn:
        cur = conn.execute("INSERT INTO id_seq DEFAULT VALUES")
        nid = cur.lastrowid
        conn.execute(
            "INSERT INTO messages(id, sender, target, body) VALUES(?, ?, ?, ?)",
            (nid, sender, target, body),
        )
        conn.commit()


def log_event(
    db_path: str | Path, kind: str, tag: str = "",
    body: str = "", *, agent: str = "",
) -> None:
    """Record a lifecycle event (equivalent to ``db.sh log``)."""
    with task_db(db_path) as conn:
        cur = conn.execute("INSERT INTO id_seq DEFAULT VALUES")
        nid = cur.lastrowid
        conn.execute(
            "INSERT INTO events(id, kind, tag, body, agent) VALUES(?, ?, ?, ?, ?)",
            (nid, kind, tag, body, agent),
        )
        conn.commit()


def log_bootstrap_stage(
    db_path: str | Path,
    stage: str,
    status: str,
    error: str | None = None,
) -> None:
    """Record a bootstrap execution stage transition.

    Inserts a row into the ``bootstrap_execution_log`` table.
    Valid *status* values: ``pending``, ``running``, ``completed``, ``failed``.
    """
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
    started_at = now if status == "running" else None
    completed_at = now if status in ("completed", "failed") else None
    with task_db(db_path) as conn:
        conn.execute(
            "INSERT INTO bootstrap_execution_log"
            "(stage, status, started_at, completed_at, error) "
            "VALUES(?, ?, ?, ?, ?)",
            (stage, status, started_at, completed_at, error),
        )
        conn.commit()
