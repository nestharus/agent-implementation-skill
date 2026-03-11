"""Tests for ingest_and_submit — queue-based task ingestion.

Covers:
- Legacy v1 single-task JSON submitted as single-step chains
- Legacy v1 JSONL submitted as chains
- v2 chain actions submitted via submit_chain
- v2 fanout actions submitted via submit_fanout
- Flow metadata propagation (flow_id, chain_id, origin_refs)
- Missing/empty/malformed signal files handled gracefully
- Signal file cleanup after successful ingestion
"""

from __future__ import annotations

import json
import sqlite3
import subprocess
from pathlib import Path

import pytest

from _paths import DB_SH

from flow.section_task_ingestion import ingest_and_submit


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _init_db(db_path: Path) -> None:
    """Initialize a fresh database via db.sh."""
    subprocess.run(
        ["bash", str(DB_SH), "init", str(db_path)],
        check=True,
        capture_output=True,
        text=True,
    )


def _query_all_tasks(db_path: Path) -> list[dict]:
    """Read all task rows as list of dicts, ordered by id."""
    conn = sqlite3.connect(str(db_path), timeout=5.0)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("SELECT * FROM tasks ORDER BY id")
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return rows


def _query_task(db_path: Path, task_id: int) -> dict:
    """Read a single task row as a dict."""
    conn = sqlite3.connect(str(db_path), timeout=5.0)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("SELECT * FROM tasks WHERE id = ?", (task_id,))
    row = cur.fetchone()
    conn.close()
    if row is None:
        raise ValueError(f"Task {task_id} not found")
    return dict(row)


def _query_gates(db_path: Path) -> list[dict]:
    """Read all gate rows."""
    conn = sqlite3.connect(str(db_path), timeout=5.0)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("SELECT * FROM gates ORDER BY gate_id")
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return rows


@pytest.fixture()
def db_path(tmp_path: Path) -> Path:
    """Create and initialize a test database."""
    p = tmp_path / "test.db"
    _init_db(p)
    return p


@pytest.fixture()
def planspace(tmp_path: Path) -> Path:
    """Create a planspace directory for flow context files."""
    ps = tmp_path / "planspace"
    ps.mkdir()
    (ps / "artifacts" / "flows").mkdir(parents=True)
    (ps / "artifacts" / "signals").mkdir(parents=True)
    return ps


# ---------------------------------------------------------------------------
# Missing / empty / malformed signal files
# ---------------------------------------------------------------------------

class TestSignalFileEdgeCases:
    """Edge cases for signal file handling."""

    def test_missing_file_returns_empty(
        self, planspace: Path, db_path: Path,
    ) -> None:
        sig = planspace / "artifacts" / "signals" / "nonexistent.json"
        result = ingest_and_submit(
            planspace, db_path, "test-agent", sig,
        )
        assert result == []

    def test_empty_file_returns_empty(
        self, planspace: Path, db_path: Path,
    ) -> None:
        sig = planspace / "artifacts" / "signals" / "empty.json"
        sig.write_text("")
        result = ingest_and_submit(
            planspace, db_path, "test-agent", sig,
        )
        assert result == []
        assert not sig.exists()  # cleaned up

    def test_malformed_json_returns_empty(
        self, planspace: Path, db_path: Path,
    ) -> None:
        sig = planspace / "artifacts" / "signals" / "broken.json"
        sig.write_text("{not valid json at all")
        result = ingest_and_submit(
            planspace, db_path, "test-agent", sig,
        )
        assert result == []
        # Original file should be renamed to .malformed.json
        assert (
            planspace / "artifacts" / "signals" / "broken.malformed.json"
        ).exists()


# ---------------------------------------------------------------------------
# Legacy v1 single-task JSON
# ---------------------------------------------------------------------------

class TestLegacyV1SingleTask:
    """Legacy single-task JSON submitted as single-step chain."""

    def test_single_task_submitted(
        self, planspace: Path, db_path: Path,
    ) -> None:
        sig = planspace / "artifacts" / "signals" / "task.json"
        sig.write_text(json.dumps({
            "task_type": "alignment_check",
            "concern_scope": "auth",
        }))
        ids = ingest_and_submit(
            planspace, db_path, "test-agent", sig,
        )
        assert len(ids) == 1
        task = _query_task(db_path, ids[0])
        assert task["task_type"] == "alignment_check"
        assert task["concern_scope"] == "auth"
        assert task["submitted_by"] == "test-agent"
        assert task["status"] == "pending"

    def test_signal_file_deleted_after_success(
        self, planspace: Path, db_path: Path,
    ) -> None:
        sig = planspace / "artifacts" / "signals" / "task.json"
        sig.write_text(json.dumps({
            "task_type": "alignment_check",
        }))
        ingest_and_submit(planspace, db_path, "test-agent", sig)
        assert not sig.exists()

    def test_flow_context_written(
        self, planspace: Path, db_path: Path,
    ) -> None:
        sig = planspace / "artifacts" / "signals" / "task.json"
        sig.write_text(json.dumps({
            "task_type": "alignment_check",
        }))
        ids = ingest_and_submit(
            planspace, db_path, "test-agent", sig,
        )
        ctx_path = (
            planspace / "artifacts" / "flows"
            / f"task-{ids[0]}-context.json"
        )
        assert ctx_path.exists()
        ctx = json.loads(ctx_path.read_text())
        assert ctx["task"]["task_id"] == ids[0]
        assert ctx["task"]["task_type"] == "alignment_check"

    def test_flow_metadata_propagated(
        self, planspace: Path, db_path: Path,
    ) -> None:
        """Caller-provided flow_id, chain_id, origin_refs are propagated."""
        sig = planspace / "artifacts" / "signals" / "task.json"
        sig.write_text(json.dumps({
            "task_type": "alignment_check",
        }))
        ids = ingest_and_submit(
            planspace, db_path, "test-agent", sig,
            flow_id="flow_custom",
            chain_id="chain_custom",
            origin_refs=["ref-1", "ref-2"],
        )
        task = _query_task(db_path, ids[0])
        assert task["flow_id"] == "flow_custom"
        assert task["chain_id"] == "chain_custom"

        # Check flow context for origin_refs
        ctx_path = (
            planspace / "artifacts" / "flows"
            / f"task-{ids[0]}-context.json"
        )
        ctx = json.loads(ctx_path.read_text())
        assert ctx["origin_refs"] == ["ref-1", "ref-2"]

    def test_optional_fields_preserved(
        self, planspace: Path, db_path: Path,
    ) -> None:
        """TaskSpec fields (priority, problem_id, payload_path) propagated."""
        sig = planspace / "artifacts" / "signals" / "task.json"
        sig.write_text(json.dumps({
            "task_type": "impact_analysis",
            "concern_scope": "payments",
            "payload_path": "/tmp/prompt.md",
            "priority": "high",
            "problem_id": "P-42",
        }))
        ids = ingest_and_submit(
            planspace, db_path, "test-agent", sig,
        )
        task = _query_task(db_path, ids[0])
        assert task["task_type"] == "impact_analysis"
        assert task["concern_scope"] == "payments"
        assert task["payload_path"] == "/tmp/prompt.md"
        assert task["priority"] == "high"
        assert task["problem_id"] == "P-42"


# ---------------------------------------------------------------------------
# Legacy v1 JSONL and JSON array
# ---------------------------------------------------------------------------

class TestLegacyV1Multi:
    """Legacy multi-task formats submitted as chains."""

    def test_jsonl_submitted(
        self, planspace: Path, db_path: Path,
    ) -> None:
        sig = planspace / "artifacts" / "signals" / "tasks.jsonl"
        lines = [
            json.dumps({"task_type": "alignment_check"}),
            json.dumps({"task_type": "impact_analysis"}),
        ]
        sig.write_text("\n".join(lines))
        ids = ingest_and_submit(
            planspace, db_path, "test-agent", sig,
        )
        assert len(ids) == 2
        tasks = _query_all_tasks(db_path)
        assert tasks[0]["task_type"] == "alignment_check"
        assert tasks[1]["task_type"] == "impact_analysis"

    def test_json_array_submitted(
        self, planspace: Path, db_path: Path,
    ) -> None:
        sig = planspace / "artifacts" / "signals" / "tasks.json"
        sig.write_text(json.dumps([
            {"task_type": "alignment_check"},
            {"task_type": "impact_analysis"},
        ]))
        ids = ingest_and_submit(
            planspace, db_path, "test-agent", sig,
        )
        assert len(ids) == 2

    def test_multi_task_chain_wiring(
        self, planspace: Path, db_path: Path,
    ) -> None:
        """Multiple legacy tasks form a chain with depends_on wiring."""
        sig = planspace / "artifacts" / "signals" / "tasks.json"
        sig.write_text(json.dumps([
            {"task_type": "alignment_check"},
            {"task_type": "impact_analysis"},
        ]))
        ids = ingest_and_submit(
            planspace, db_path, "test-agent", sig,
        )
        t0 = _query_task(db_path, ids[0])
        t1 = _query_task(db_path, ids[1])
        assert t0["depends_on"] is None
        assert t1["depends_on"] == str(ids[0])
        # Same chain_id
        assert t0["chain_id"] == t1["chain_id"]


# ---------------------------------------------------------------------------
# v2 chain declarations
# ---------------------------------------------------------------------------

class TestV2Chain:
    """v2 flow declarations with chain actions."""

    def test_v2_chain_submitted(
        self, planspace: Path, db_path: Path,
    ) -> None:
        sig = planspace / "artifacts" / "signals" / "flow.json"
        sig.write_text(json.dumps({
            "version": 2,
            "actions": [
                {
                    "kind": "chain",
                    "steps": [
                        {"task_type": "alignment_check", "payload_path": "artifacts/p1.md"},
                        {"task_type": "impact_analysis", "payload_path": "artifacts/p2.md"},
                    ],
                },
            ],
        }))
        ids = ingest_and_submit(
            planspace, db_path, "test-agent", sig,
        )
        assert len(ids) == 2
        t0 = _query_task(db_path, ids[0])
        t1 = _query_task(db_path, ids[1])
        assert t0["task_type"] == "alignment_check"
        assert t1["task_type"] == "impact_analysis"
        assert t1["depends_on"] == str(ids[0])

    def test_v2_chain_with_flow_metadata(
        self, planspace: Path, db_path: Path,
    ) -> None:
        sig = planspace / "artifacts" / "signals" / "flow.json"
        sig.write_text(json.dumps({
            "version": 2,
            "actions": [
                {
                    "kind": "chain",
                    "steps": [{"task_type": "alignment_check", "payload_path": "artifacts/p.md"}],
                },
            ],
        }))
        ids = ingest_and_submit(
            planspace, db_path, "test-agent", sig,
            flow_id="flow_v2",
            origin_refs=["from-proposal"],
        )
        task = _query_task(db_path, ids[0])
        assert task["flow_id"] == "flow_v2"


# ---------------------------------------------------------------------------
# v2 fanout declarations
# ---------------------------------------------------------------------------

class TestV2Fanout:
    """v2 flow declarations with fanout actions."""

    def test_v2_fanout_submitted(
        self, planspace: Path, db_path: Path,
    ) -> None:
        sig = planspace / "artifacts" / "signals" / "flow.json"
        sig.write_text(json.dumps({
            "version": 2,
            "actions": [
                {
                    "kind": "fanout",
                    "branches": [
                        {
                            "label": "branch-a",
                            "steps": [
                                {"task_type": "alignment_check", "payload_path": "artifacts/p1.md"},
                            ],
                        },
                        {
                            "label": "branch-b",
                            "steps": [
                                {"task_type": "impact_analysis", "payload_path": "artifacts/p2.md"},
                            ],
                        },
                    ],
                },
            ],
        }))
        # Fanout does not return task_ids directly (returns gate_id via
        # submit_fanout), so ingest_and_submit returns empty list for
        # fanout-only declarations.
        ids = ingest_and_submit(
            planspace, db_path, "test-agent", sig,
        )
        # Fanout tasks are in the DB, not in the returned list
        assert ids == []

        # But the tasks are in the DB
        tasks = _query_all_tasks(db_path)
        assert len(tasks) == 2
        types = {t["task_type"] for t in tasks}
        assert types == {"alignment_check", "impact_analysis"}

    def test_v2_fanout_with_gate(
        self, planspace: Path, db_path: Path,
    ) -> None:
        sig = planspace / "artifacts" / "signals" / "flow.json"
        sig.write_text(json.dumps({
            "version": 2,
            "actions": [
                {
                    "kind": "fanout",
                    "branches": [
                        {
                            "label": "a",
                            "steps": [
                                {"task_type": "alignment_check", "payload_path": "artifacts/p1.md"},
                            ],
                        },
                        {
                            "label": "b",
                            "steps": [
                                {"task_type": "impact_analysis", "payload_path": "artifacts/p2.md"},
                            ],
                        },
                    ],
                    "gate": {
                        "mode": "all",
                        "failure_policy": "include",
                    },
                },
            ],
        }))
        ingest_and_submit(
            planspace, db_path, "test-agent", sig,
        )

        gates = _query_gates(db_path)
        assert len(gates) == 1
        assert gates[0]["mode"] == "all"
        assert gates[0]["expected_count"] == 2


# ---------------------------------------------------------------------------
# v2 mixed declarations (chain + fanout)
# ---------------------------------------------------------------------------

class TestV2Mixed:
    """v2 declarations with both chain and fanout actions."""

    def test_chain_plus_fanout(
        self, planspace: Path, db_path: Path,
    ) -> None:
        sig = planspace / "artifacts" / "signals" / "flow.json"
        sig.write_text(json.dumps({
            "version": 2,
            "actions": [
                {
                    "kind": "chain",
                    "steps": [
                        {"task_type": "alignment_check", "payload_path": "artifacts/p1.md"},
                    ],
                },
                {
                    "kind": "fanout",
                    "branches": [
                        {
                            "label": "a",
                            "steps": [
                                {"task_type": "impact_analysis", "payload_path": "artifacts/p2.md"},
                            ],
                        },
                    ],
                },
            ],
        }))
        ids = ingest_and_submit(
            planspace, db_path, "test-agent", sig,
        )
        # Chain returns 1 task_id
        assert len(ids) == 1

        # Total tasks: 1 chain + 1 fanout branch
        tasks = _query_all_tasks(db_path)
        assert len(tasks) == 2


# ---------------------------------------------------------------------------
# v2 invalid declarations
# ---------------------------------------------------------------------------

class TestV2Invalid:
    """Invalid v2 declarations are rejected."""

    def test_invalid_v2_returns_empty(
        self, planspace: Path, db_path: Path,
    ) -> None:
        sig = planspace / "artifacts" / "signals" / "bad-flow.json"
        sig.write_text(json.dumps({
            "version": 2,
            "actions": [
                {
                    "kind": "chain",
                    "steps": [
                        {"task_type": "NONEXISTENT_TYPE"},
                    ],
                },
            ],
        }))
        ids = ingest_and_submit(
            planspace, db_path, "test-agent", sig,
        )
        assert ids == []
        # Should be renamed to .malformed.json
        assert (
            planspace / "artifacts" / "signals" / "bad-flow.malformed.json"
        ).exists()


# ---------------------------------------------------------------------------
# declared_by_task_id propagation
# ---------------------------------------------------------------------------

class TestDeclaredByTaskId:
    """declared_by_task_id is propagated to submitted tasks."""

    def test_declared_by_propagated(
        self, planspace: Path, db_path: Path,
    ) -> None:
        sig = planspace / "artifacts" / "signals" / "task.json"
        sig.write_text(json.dumps({
            "task_type": "alignment_check",
        }))
        ids = ingest_and_submit(
            planspace, db_path, "test-agent", sig,
            declared_by_task_id=42,
        )
        task = _query_task(db_path, ids[0])
        assert task["declared_by_task_id"] == 42


# ---------------------------------------------------------------------------
# DB path columns populated
# ---------------------------------------------------------------------------

class TestDBPaths:
    """flow_context_path, continuation_path, result_manifest_path populated."""

    def test_flow_paths_set(
        self, planspace: Path, db_path: Path,
    ) -> None:
        sig = planspace / "artifacts" / "signals" / "task.json"
        sig.write_text(json.dumps({
            "task_type": "alignment_check",
        }))
        ids = ingest_and_submit(
            planspace, db_path, "test-agent", sig,
        )
        tid = ids[0]
        task = _query_task(db_path, tid)
        assert task["flow_context_path"] == (
            f"artifacts/flows/task-{tid}-context.json"
        )
        assert task["continuation_path"] == (
            f"artifacts/flows/task-{tid}-continuation.json"
        )
        assert task["result_manifest_path"] == (
            f"artifacts/flows/task-{tid}-result.json"
        )
