"""End-to-end integration test — Packet 11.

Builds a tiny synthetic run with all source types and verifies the full
pipeline produces a correctly sorted, correlated, and filterable timeline.
"""

import json
import os
import sqlite3
import tempfile
from pathlib import Path

from log_extract import cli, formatters, timeline
from log_extract.correlator import correlate
from log_extract.extractors import artifacts, claude, codex, gemini, opencode, run_db
from log_extract.utils import load_model_backend_map
from src.orchestrator.path_registry import PathRegistry


def _build_run_db(db_path: Path) -> None:
    """Create a minimal run.db with events and tasks."""
    con = sqlite3.connect(str(db_path))
    con.executescript("""
        CREATE TABLE events (
            id INTEGER PRIMARY KEY, ts TEXT, kind TEXT,
            tag TEXT DEFAULT '', body TEXT DEFAULT '', agent TEXT DEFAULT ''
        );
        CREATE TABLE agents (
            id INTEGER PRIMARY KEY, ts TEXT, name TEXT, pid INTEGER,
            status TEXT DEFAULT 'active'
        );
        CREATE TABLE tasks (
            id INTEGER PRIMARY KEY, submitted_by TEXT, task_type TEXT,
            status TEXT DEFAULT 'pending', claimed_by TEXT, claimed_at TEXT,
            completed_at TEXT, agent_file TEXT, model TEXT, output_path TEXT,
            instance_id TEXT, flow_id TEXT, chain_id TEXT,
            trigger_gate_id TEXT, freshness_token TEXT
        );
        CREATE TABLE gates (
            gate_id TEXT PRIMARY KEY, flow_id TEXT, status TEXT DEFAULT 'open',
            expected_count INTEGER, synthesis_task_type TEXT,
            fired_task_id INTEGER, created_at TEXT, fired_at TEXT
        );
        CREATE TABLE gate_members (
            gate_id TEXT, chain_id TEXT, leaf_task_id INTEGER,
            status TEXT DEFAULT 'pending', completed_at TEXT
        );
        CREATE TABLE messages (
            id INTEGER PRIMARY KEY, ts TEXT, sender TEXT DEFAULT '',
            target TEXT, body TEXT, claimed INTEGER DEFAULT 0,
            claimed_by TEXT, claimed_at TEXT
        );
    """)
    con.execute(
        "INSERT INTO events (ts, kind, tag, body, agent) VALUES (?, ?, ?, ?, ?)",
        ("2026-03-01T10:00:00.000Z", "lifecycle", "start", "pipeline started", "orchestrator"),
    )
    con.execute(
        "INSERT INTO events (ts, kind, tag, body, agent) VALUES (?, ?, ?, ?, ?)",
        ("2026-03-01T10:00:05.000Z", "summary", "done:03", "section 03 complete", "solver-03"),
    )
    con.execute(
        "INSERT INTO tasks VALUES (1, 'orch', 'section-03-implement', 'complete', "
        "'dispatcher', '2026-03-01T10:00:01.000Z', '2026-03-01T10:00:04.000Z', "
        "'solver.md', 'claude-opus', 'task-1-output.md', NULL, NULL, NULL, NULL, NULL)"
    )
    con.execute(
        "INSERT INTO agents (ts, name, pid, status) VALUES (?, ?, ?, ?)",
        ("2026-03-01T10:00:00.500Z", "solver-03", 12345, "active"),
    )
    con.commit()
    con.close()


def _build_claude_session(project_dir: Path) -> None:
    """Create a minimal Claude session JSONL."""
    session_file = project_dir / "test-session-123.jsonl"
    lines = [
        json.dumps({
            "type": "queue-operation", "operation": "enqueue",
            "timestamp": "2026-03-01T10:00:01.500Z",
            "sessionId": "test-session-123",
            "content": "Implement section 03",
        }),
        json.dumps({
            "type": "user",
            "timestamp": "2026-03-01T10:00:01.600Z",
            "sessionId": "test-session-123",
            "cwd": "/project",
            "message": {"role": "user", "content": "Implement section 03"},
        }),
        json.dumps({
            "type": "assistant",
            "timestamp": "2026-03-01T10:00:03.000Z",
            "sessionId": "test-session-123",
            "message": {"role": "assistant", "content": [
                {"type": "text", "text": "I'll implement section 03."},
            ]},
            "costUSD": 0.01,
        }),
    ]
    session_file.write_text("\n".join(lines) + "\n")


def _build_opencode_db(db_path: Path) -> None:
    """Create a minimal OpenCode DB."""
    con = sqlite3.connect(str(db_path))
    con.executescript("""
        CREATE TABLE session (
            id TEXT PRIMARY KEY, project_id TEXT, parent_id TEXT,
            slug TEXT, directory TEXT, title TEXT, version TEXT,
            time_created TEXT, time_updated TEXT
        );
        CREATE TABLE message (
            id TEXT PRIMARY KEY, session_id TEXT,
            time_created TEXT, time_updated TEXT, data TEXT
        );
        CREATE TABLE part (
            id TEXT PRIMARY KEY, message_id TEXT, session_id TEXT,
            time_created TEXT, time_updated TEXT, data TEXT
        );
    """)
    con.execute(
        "INSERT INTO session VALUES ('oc-sess-1', 'proj1', NULL, 'test', "
        "'/project', 'Test', '1.0', '2026-03-01T10:00:06.000Z', '2026-03-01T10:00:10.000Z')"
    )
    msg_data = json.dumps({
        "role": "user", "time": {"created": 1740826806000},
    })
    con.execute(
        "INSERT INTO message VALUES ('msg1', 'oc-sess-1', "
        "'2026-03-01T10:00:06.000Z', '2026-03-01T10:00:06.000Z', ?)",
        (msg_data,),
    )
    con.commit()
    con.close()


class TestEndToEnd:
    def setup_method(self):
        self.tmpdir = tempfile.mkdtemp()
        self.planspace = Path(self.tmpdir) / "planspace"
        self.planspace.mkdir()
        PathRegistry(self.planspace).ensure_artifacts_tree()

        # run.db
        _build_run_db(self.planspace / "run.db")

        # Artifacts
        artifacts_dir = self.planspace / "artifacts"
        (artifacts_dir / "test-output.md").write_text("output content")
        signals_dir = artifacts_dir / "signals"
        (signals_dir / "model-choice-03.json").write_text(
            json.dumps({"model": "claude-opus", "section": "03"})
        )
        meta = artifacts_dir / "test-output.meta.json"
        meta.write_text(json.dumps({"returncode": 0, "timed_out": False}))

        # Claude sessions
        self.claude_home = Path(self.tmpdir) / "claude"
        project_dir = self.claude_home / "projects" / "test-project"
        project_dir.mkdir(parents=True)
        _build_claude_session(project_dir)

        # OpenCode
        self.opencode_home = Path(self.tmpdir) / "opencode"
        self.opencode_home.mkdir()
        _build_opencode_db(self.opencode_home / "opencode.db")

        # Codex — empty (no sessions)
        self.codex_home = Path(self.tmpdir) / "codex"
        self.codex_home.mkdir()

        # Gemini — empty
        self.gemini_home = Path(self.tmpdir) / "gemini"
        self.gemini_home.mkdir()

    def test_full_pipeline_produces_sorted_output(self):
        model_map = {}
        db_path = self.planspace / "run.db"

        db_events = list(run_db.iter_events(db_path, model_map))
        dispatch_cands = list(run_db.iter_dispatch_candidates(db_path, model_map))
        art_events = list(artifacts.iter_events(self.planspace / "artifacts"))
        claude_events = list(claude.iter_events([self.claude_home]))
        claude_sessions = list(claude.iter_session_candidates([self.claude_home]))
        oc_events = list(opencode.iter_events([self.opencode_home]))
        oc_sessions = list(opencode.iter_session_candidates([self.opencode_home]))
        codex_events = list(codex.iter_events([self.codex_home]))
        gemini_events = list(gemini.iter_events([self.gemini_home]))

        # We have events from multiple sources
        assert len(db_events) > 0
        assert len(art_events) > 0
        assert len(claude_events) > 0

        # Correlate
        all_sessions = claude_sessions + oc_sessions
        links = correlate(dispatch_cands, all_sessions)

        # Merge
        merged = timeline.merge_and_sort([
            db_events, art_events, claude_events,
            codex_events, oc_events, gemini_events,
        ])

        # Decorate
        timeline.decorate(merged, links, dispatch_cands)
        merged = timeline.dedup(merged)

        # Verify sorted
        for i in range(1, len(merged)):
            assert merged[i].ts_ms >= merged[i - 1].ts_ms, (
                f"Not sorted at index {i}: {merged[i - 1].ts_ms} > {merged[i].ts_ms}"
            )

    def test_filters_work_on_integrated_stream(self):
        model_map = {}
        db_path = self.planspace / "run.db"

        db_events = list(run_db.iter_events(db_path, model_map))
        art_events = list(artifacts.iter_events(self.planspace / "artifacts"))
        claude_events = list(claude.iter_events([self.claude_home]))

        merged = timeline.merge_and_sort([db_events, art_events, claude_events])

        # Filter by source
        db_only = timeline.apply_filters(merged, sources={"run.db"})
        assert all(e.source == "run.db" for e in db_only)

        claude_only = timeline.apply_filters(merged, sources={"claude"})
        assert all(e.source == "claude" for e in claude_only)

        # Filter by kind
        signals = timeline.apply_filters(merged, kinds={"signal"})
        assert all(e.kind == "signal" for e in signals)

    def test_jsonl_output_is_valid(self):
        model_map = {}
        db_events = list(run_db.iter_events(self.planspace / "run.db", model_map))
        merged = timeline.merge_and_sort([db_events])
        lines = list(formatters.format_jsonl(merged))
        for line in lines:
            obj = json.loads(line)
            assert "ts" in obj
            assert "source" in obj
            assert "kind" in obj

    def test_csv_output_is_valid(self):
        model_map = {}
        db_events = list(run_db.iter_events(self.planspace / "run.db", model_map))
        merged = timeline.merge_and_sort([db_events])
        lines = list(formatters.format_csv(merged))
        assert len(lines) >= 2  # header + at least 1 data row
        assert "ts" in lines[0]

    def test_text_output_has_content(self):
        model_map = {}
        db_events = list(run_db.iter_events(self.planspace / "run.db", model_map))
        merged = timeline.merge_and_sort([db_events])
        lines = list(formatters.format_text(merged, use_color=False))
        assert len(lines) > 0
        assert "2026-03-01" in lines[0]

    def test_missing_sources_dont_fail(self):
        """Empty homes produce no events, pipeline still works."""
        model_map = {}
        empty = Path(self.tmpdir) / "empty"
        empty.mkdir()

        codex_events = list(codex.iter_events([empty]))
        gemini_events = list(gemini.iter_events([empty]))
        assert codex_events == []
        assert gemini_events == []
