"""Task DB helpers: db.sh command wrapper and direct SQLite connection."""

from __future__ import annotations

import sqlite3
import subprocess
from contextlib import contextmanager
from collections.abc import Generator
from pathlib import Path

DB_SH = Path(__file__).resolve().parent.parent.parent / "scripts" / "db.sh"

_DB_COMMAND_TIMEOUT_SECONDS = 30
_SQLITE_CONNECT_TIMEOUT_SECONDS = 5.0
_SQLITE_BUSY_TIMEOUT_MS = 5000


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
    depends_on     TEXT,
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
    freshness_token      TEXT
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
"""


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
