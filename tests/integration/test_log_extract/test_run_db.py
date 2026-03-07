"""Tests for log_extract.extractors.run_db against an in-memory SQLite fixture."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import pytest

from log_extract.extractors.run_db import iter_dispatch_candidates, iter_events

# ------------------------------------------------------------------
# Fixture: tiny in-memory run.db
# ------------------------------------------------------------------

_SCHEMA = """
CREATE TABLE events (
    id INTEGER PRIMARY KEY,
    ts TEXT,
    kind TEXT,
    tag TEXT DEFAULT '',
    body TEXT DEFAULT '',
    agent TEXT DEFAULT ''
);
CREATE TABLE agents (
    id INTEGER PRIMARY KEY,
    ts TEXT,
    name TEXT,
    pid INTEGER,
    status TEXT DEFAULT 'active'
);
CREATE TABLE tasks (
    id INTEGER PRIMARY KEY,
    submitted_by TEXT,
    task_type TEXT,
    status TEXT DEFAULT 'pending',
    claimed_by TEXT,
    claimed_at TEXT,
    completed_at TEXT,
    agent_file TEXT,
    model TEXT,
    output_path TEXT,
    instance_id TEXT,
    flow_id TEXT,
    chain_id TEXT,
    trigger_gate_id TEXT,
    freshness_token TEXT
);
CREATE TABLE gates (
    gate_id TEXT PRIMARY KEY,
    flow_id TEXT,
    status TEXT DEFAULT 'open',
    expected_count INTEGER,
    synthesis_task_type TEXT,
    fired_task_id INTEGER,
    created_at TEXT,
    fired_at TEXT
);
CREATE TABLE gate_members (
    gate_id TEXT,
    chain_id TEXT,
    leaf_task_id INTEGER,
    status TEXT DEFAULT 'pending',
    completed_at TEXT
);
CREATE TABLE messages (
    id INTEGER PRIMARY KEY,
    ts TEXT,
    sender TEXT DEFAULT '',
    target TEXT,
    body TEXT,
    claimed INTEGER DEFAULT 0,
    claimed_by TEXT,
    claimed_at TEXT
);
"""

_SEED = """
-- 2 events
INSERT INTO events (id, ts, kind, tag, body, agent)
VALUES (1, '2026-03-01T10:00:00Z', 'lifecycle', 'startup', 'system initialised', 'orchestrator');
INSERT INTO events (id, ts, kind, tag, body, agent)
VALUES (2, '2026-03-01T10:01:00Z', 'signal', 'section-03', 'phase complete', 'worker-03');

-- 1 agent
INSERT INTO agents (id, ts, name, pid, status)
VALUES (1, '2026-03-01T09:59:00Z', 'worker-03', 12345, 'active');

-- 2 tasks: one claimed+completed, one pending (no timestamps)
INSERT INTO tasks (id, submitted_by, task_type, status, claimed_by, claimed_at, completed_at, agent_file, model)
VALUES (1, 'planner', 'section-05-implement', 'done', 'worker-05', '2026-03-01T10:05:00Z', '2026-03-01T10:15:00Z', 'agents/section_05.md', 'claude-opus');
INSERT INTO tasks (id, submitted_by, task_type, status, claimed_by, claimed_at, completed_at, agent_file, model)
VALUES (2, 'planner', 'section-07-review', 'pending', NULL, NULL, NULL, 'agents/section_07.md', NULL);

-- 1 gate with 1 member
INSERT INTO gates (gate_id, flow_id, status, expected_count, synthesis_task_type, fired_task_id, created_at, fired_at)
VALUES ('gate-section-05', 'flow-1', 'fired', 2, 'synthesize', 10, '2026-03-01T10:02:00Z', '2026-03-01T10:20:00Z');
INSERT INTO gate_members (gate_id, chain_id, leaf_task_id, status, completed_at)
VALUES ('gate-section-05', 'chain-a', 1, 'completed', '2026-03-01T10:18:00Z');

-- 1 message
INSERT INTO messages (id, ts, sender, target, body, claimed, claimed_by, claimed_at)
VALUES (1, '2026-03-01T10:03:00Z', 'planner', 'worker-05', 'please start section-05 implementation', 0, NULL, NULL);
"""

MODEL_MAP: dict[str, tuple[str, str]] = {
    "claude-opus": ("claude", "claude"),
    "gpt-5": ("codex", "codex"),
}


@pytest.fixture()
def db_path(tmp_path: Path) -> Path:
    """Create a tiny run.db in *tmp_path* and return its path."""
    path = tmp_path / "run.db"
    con = sqlite3.connect(str(path))
    con.executescript(_SCHEMA)
    con.executescript(_SEED)
    con.close()
    return path


# ------------------------------------------------------------------
# Tests: iter_events
# ------------------------------------------------------------------


class TestIterEvents:
    def test_total_event_count(self, db_path: Path) -> None:
        """We expect events from: events(2) + agents(1) + tasks(2 for task 1)
        + gates(2: created+fired) + gate_members(1) + messages(1) = 9 total."""
        events = list(iter_events(db_path, MODEL_MAP))
        assert len(events) == 9

    def test_events_table_rows(self, db_path: Path) -> None:
        events = [e for e in iter_events(db_path, MODEL_MAP) if e.raw.get("table") == "events"]
        assert len(events) == 2

        startup = next(e for e in events if "startup" in e.detail)
        assert startup.kind == "lifecycle"
        assert startup.source == "run.db"
        assert startup.agent == "orchestrator"
        assert "system initialised" in startup.detail

        signal = next(e for e in events if "phase complete" in e.detail)
        assert signal.kind == "signal"
        assert signal.section == "03"  # inferred from 'section-03' tag

    def test_agent_lifecycle_event(self, db_path: Path) -> None:
        events = [e for e in iter_events(db_path, MODEL_MAP) if e.raw.get("table") == "agents"]
        assert len(events) == 1
        ev = events[0]
        assert ev.kind == "lifecycle"
        assert "worker-03" in ev.detail
        assert "12345" in ev.detail
        assert ev.agent == "worker-03"
        assert ev.section == "03"

    def test_task_claimed_and_completed_events(self, db_path: Path) -> None:
        events = [e for e in iter_events(db_path, MODEL_MAP) if e.raw.get("table") == "tasks"]
        # task 1 has claimed_at + completed_at = 2 events; task 2 has neither = 0 events
        assert len(events) == 2

        claimed = next(e for e in events if e.raw.get("event") == "claimed")
        assert claimed.kind == "task"
        assert "claimed" in claimed.detail
        assert claimed.model == "claude-opus"
        assert claimed.backend == "claude"

        completed = next(e for e in events if e.raw.get("event") == "completed")
        assert completed.kind == "task"
        assert "completed" in completed.detail
        assert "done" in completed.detail

    def test_pending_task_produces_no_events(self, db_path: Path) -> None:
        """Task 2 has no claimed_at and no completed_at, so it should not emit events."""
        events = [e for e in iter_events(db_path, MODEL_MAP) if e.raw.get("table") == "tasks"]
        task_ids = {e.raw["id"] for e in events}
        assert 2 not in task_ids

    def test_gate_created_and_fired_events(self, db_path: Path) -> None:
        events = [e for e in iter_events(db_path, MODEL_MAP) if e.raw.get("table") == "gates"]
        assert len(events) == 2
        kinds = {e.raw["event"] for e in events}
        assert kinds == {"created", "fired"}

        created = next(e for e in events if e.raw["event"] == "created")
        assert created.kind == "gate"
        assert "gate-section-05" in created.detail

    def test_gate_member_completion_event(self, db_path: Path) -> None:
        events = [
            e for e in iter_events(db_path, MODEL_MAP) if e.raw.get("table") == "gate_members"
        ]
        assert len(events) == 1
        ev = events[0]
        assert ev.kind == "gate"
        assert "chain-a" in ev.detail

    def test_message_event(self, db_path: Path) -> None:
        events = [e for e in iter_events(db_path, MODEL_MAP) if e.raw.get("table") == "messages"]
        assert len(events) == 1
        ev = events[0]
        assert ev.kind == "message"
        assert "planner -> worker-05" in ev.detail
        assert ev.agent == "planner"

    def test_all_sources_are_run_db(self, db_path: Path) -> None:
        for ev in iter_events(db_path, MODEL_MAP):
            assert ev.source == "run.db"


# ------------------------------------------------------------------
# Tests: iter_dispatch_candidates
# ------------------------------------------------------------------


class TestIterDispatchCandidates:
    def test_claimed_task_becomes_dispatch(self, db_path: Path) -> None:
        dispatches = list(iter_dispatch_candidates(db_path, MODEL_MAP))
        assert len(dispatches) == 1

        d = dispatches[0]
        assert d.dispatch_id == "rundb-task-1"
        assert d.model == "claude-opus"
        assert d.backend == "claude"
        assert d.source_family == "claude"
        assert d.agent == "worker-05"

    def test_pending_task_not_dispatched(self, db_path: Path) -> None:
        dispatches = list(iter_dispatch_candidates(db_path, MODEL_MAP))
        dispatch_ids = {d.dispatch_id for d in dispatches}
        assert "rundb-task-2" not in dispatch_ids


# ------------------------------------------------------------------
# Tests: section inference on task fields
# ------------------------------------------------------------------


class TestSectionInference:
    def test_section_from_task_type(self, db_path: Path) -> None:
        """task_type='section-05-implement' should yield section='05'."""
        events = [
            e
            for e in iter_events(db_path, MODEL_MAP)
            if e.raw.get("table") == "tasks" and e.raw.get("id") == 1
        ]
        assert len(events) > 0
        for ev in events:
            assert ev.section == "05"

    def test_section_from_dispatch(self, db_path: Path) -> None:
        dispatches = list(iter_dispatch_candidates(db_path, MODEL_MAP))
        assert dispatches[0].section == "05"

    def test_section_from_event_tag(self, db_path: Path) -> None:
        """events row 2 has tag='section-03' so section should be '03'."""
        events = [
            e
            for e in iter_events(db_path, MODEL_MAP)
            if e.raw.get("table") == "events" and e.raw.get("id") == 2
        ]
        assert len(events) == 1
        assert events[0].section == "03"


# ------------------------------------------------------------------
# Tests: malformed data handling
# ------------------------------------------------------------------


class TestMalformedData:
    def test_bad_timestamp_skipped(self, tmp_path: Path) -> None:
        """Rows with unparseable timestamps should be silently skipped (stderr warning)."""
        path = tmp_path / "bad.db"
        con = sqlite3.connect(str(path))
        con.executescript(_SCHEMA)
        con.execute(
            "INSERT INTO events (id, ts, kind, tag, body, agent) "
            "VALUES (99, 'not-a-date', 'signal', 'x', 'y', 'z')"
        )
        con.commit()
        con.close()

        events = list(iter_events(path, MODEL_MAP))
        assert len(events) == 0

    def test_unknown_kind_skipped(self, tmp_path: Path) -> None:
        """Rows with kinds not in the Kind literal should be skipped."""
        path = tmp_path / "unknown_kind.db"
        con = sqlite3.connect(str(path))
        con.executescript(_SCHEMA)
        con.execute(
            "INSERT INTO events (id, ts, kind, tag, body, agent) "
            "VALUES (99, '2026-03-01T10:00:00Z', 'alien_kind', 'x', 'y', 'z')"
        )
        con.commit()
        con.close()

        events = list(iter_events(path, MODEL_MAP))
        assert len(events) == 0

    def test_missing_table_no_crash(self, tmp_path: Path) -> None:
        """An empty database (no tables) should just yield nothing, not crash."""
        path = tmp_path / "empty.db"
        con = sqlite3.connect(str(path))
        con.close()

        events = list(iter_events(path, MODEL_MAP))
        assert len(events) == 0
        dispatches = list(iter_dispatch_candidates(path, MODEL_MAP))
        assert len(dispatches) == 0
