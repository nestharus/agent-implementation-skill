"""Tests for fail-closed flow artifact handling (Proposal 1).

Covers:
- _read_flow_json(): 3-state reader (ok / missing / malformed)
- build_flow_context(): raises FlowCorruptionError on corrupt/missing files
- build_flow_context(): returns None when no flow_context_path declared
- reconcile_task_completion(): fails task on malformed continuation
- _read_origin_refs(): renames malformed context to .malformed.json
- task_dispatcher: fails task on FlowCorruptionError from build_flow_context
"""

from __future__ import annotations

import json
import sqlite3
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from _paths import DB_SH
from conftest import override_dispatcher_and_guard

from flow.types.schema import BranchSpec, GateSpec, TaskSpec
from flow.exceptions import FlowCorruptionError
from flow.service.flow_facade import (
    _read_flow_json,
    _read_origin_refs,
    build_flow_context,
    reconcile_task_completion,
    submit_chain,
    submit_fanout,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _init_db(db_path: Path) -> None:
    subprocess.run(
        ["bash", str(DB_SH), "init", str(db_path)],
        check=True, capture_output=True, text=True,
    )


def _query_task(db_path: Path, task_id: int) -> dict:
    conn = sqlite3.connect(str(db_path), timeout=5.0)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("SELECT * FROM tasks WHERE id = ?", (task_id,))
    row = cur.fetchone()
    conn.close()
    if row is None:
        raise ValueError(f"Task {task_id} not found")
    return dict(row)


def _query_all_tasks(db_path: Path) -> list[dict]:
    conn = sqlite3.connect(str(db_path), timeout=5.0)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("SELECT * FROM tasks ORDER BY id ASC")
    rows = cur.fetchall()
    conn.close()
    return [dict(r) for r in rows]


def _mark_task_running(db_path: Path, task_id: int) -> None:
    conn = sqlite3.connect(str(db_path), timeout=5.0)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute(
        "UPDATE tasks SET status='running', claimed_by='test' WHERE id=?",
        (task_id,),
    )
    conn.commit()
    conn.close()


def _mark_task_complete(db_path: Path, task_id: int) -> None:
    conn = sqlite3.connect(str(db_path), timeout=5.0)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute(
        "UPDATE tasks SET status='complete', completed_at=datetime('now') WHERE id=?",
        (task_id,),
    )
    conn.commit()
    conn.close()


def _query_gate_members(db_path: Path, gate_id: str) -> list[dict]:
    conn = sqlite3.connect(str(db_path), timeout=5.0)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute(
        "SELECT * FROM gate_members WHERE gate_id = ? ORDER BY chain_id",
        (gate_id,),
    )
    rows = cur.fetchall()
    conn.close()
    return [dict(r) for r in rows]


@pytest.fixture()
def db_path(tmp_path: Path) -> Path:
    p = tmp_path / "test.db"
    _init_db(p)
    return p


@pytest.fixture()
def planspace(tmp_path: Path) -> Path:
    ps = tmp_path / "planspace"
    ps.mkdir()
    (ps / "artifacts" / "flows").mkdir(parents=True)
    return ps


# ---------------------------------------------------------------------------
# _read_flow_json
# ---------------------------------------------------------------------------

class TestReadFlowJson:
    """Tests for the 3-state JSON reader."""

    def test_ok_on_valid_json(self, tmp_path: Path) -> None:
        f = tmp_path / "good.json"
        f.write_text('{"key": "value"}')
        status, data = _read_flow_json(f)
        assert status == "ok"
        assert data == {"key": "value"}

    def test_ok_on_valid_json_array(self, tmp_path: Path) -> None:
        f = tmp_path / "array.json"
        f.write_text('[1, 2, 3]')
        status, data = _read_flow_json(f)
        assert status == "ok"
        assert data == [1, 2, 3]

    def test_missing_on_nonexistent_file(self, tmp_path: Path) -> None:
        f = tmp_path / "nope.json"
        status, data = _read_flow_json(f)
        assert status == "missing"
        assert data is None

    def test_malformed_on_invalid_json(self, tmp_path: Path) -> None:
        f = tmp_path / "bad.json"
        f.write_text("{not valid json")
        status, data = _read_flow_json(f)
        assert status == "malformed"
        assert data is None

    def test_malformed_renames_to_dotmalformed(self, tmp_path: Path) -> None:
        f = tmp_path / "corrupt.json"
        f.write_text("{broken")
        _read_flow_json(f)
        assert not f.exists()
        assert (tmp_path / "corrupt.malformed.json").exists()

    def test_malformed_preserves_content(self, tmp_path: Path) -> None:
        """Renamed file preserves the original corrupt content."""
        f = tmp_path / "preserve.json"
        corrupt_content = "{this is not valid json at all"
        f.write_text(corrupt_content)
        _read_flow_json(f)
        renamed = tmp_path / "preserve.malformed.json"
        assert renamed.read_text() == corrupt_content

    def test_malformed_logs_warning(self, tmp_path: Path, capsys) -> None:
        f = tmp_path / "warn.json"
        f.write_text("{bad")
        _read_flow_json(f)
        captured = capsys.readouterr()
        assert "[FLOW][WARN]" in captured.out
        assert "Malformed JSON" in captured.out


# ---------------------------------------------------------------------------
# build_flow_context — fail-closed behavior
# ---------------------------------------------------------------------------

class TestBuildFlowContextFailClosed:
    """build_flow_context raises FlowCorruptionError on corrupt/missing."""

    def test_returns_none_without_flow_context_path(
        self, planspace: Path,
    ) -> None:
        """No flow_context_path declared -> None (not an error)."""
        result = build_flow_context(planspace, 1, flow_context_path=None)
        assert result is None

    def test_returns_none_for_empty_flow_context_path(
        self, planspace: Path,
    ) -> None:
        result = build_flow_context(planspace, 1, flow_context_path="")
        assert result is None

    def test_raises_on_missing_context_file(
        self, planspace: Path,
    ) -> None:
        with pytest.raises(FlowCorruptionError, match="missing"):
            build_flow_context(
                planspace, 1,
                flow_context_path="artifacts/flows/task-999-context.json",
            )

    def test_raises_on_malformed_context_file(
        self, planspace: Path,
    ) -> None:
        ctx_relpath = "artifacts/flows/task-99-context.json"
        ctx_file = planspace / ctx_relpath
        ctx_file.write_text("{broken json")

        with pytest.raises(FlowCorruptionError, match="corrupt"):
            build_flow_context(
                planspace, 99,
                flow_context_path=ctx_relpath,
            )

    def test_malformed_context_renamed_before_raise(
        self, planspace: Path,
    ) -> None:
        """Corrupt file is renamed BEFORE the error propagates."""
        ctx_relpath = "artifacts/flows/task-77-context.json"
        ctx_file = planspace / ctx_relpath
        ctx_file.write_text("not json!")

        with pytest.raises(FlowCorruptionError):
            build_flow_context(
                planspace, 77,
                flow_context_path=ctx_relpath,
            )

        assert not ctx_file.exists()
        assert ctx_file.with_suffix(".malformed.json").exists()

    def test_valid_context_still_works(
        self, db_path: Path, planspace: Path,
    ) -> None:
        """Valid flow context is returned normally (no regression)."""
        ids = submit_chain(
            db_path, "test-agent",
            [TaskSpec(task_type="staleness.alignment_check")],
            planspace=planspace,
        )
        tid = ids[0]
        task = _query_task(db_path, tid)

        result = build_flow_context(
            planspace, tid,
            flow_context_path=task["flow_context_path"],
        )
        assert result is not None
        assert result.task.task_id == tid


# ---------------------------------------------------------------------------
# reconcile_task_completion — malformed continuation
# ---------------------------------------------------------------------------

class TestReconcileMalformedContinuation:
    """Malformed continuation file triggers fail-closed behavior."""

    def test_malformed_continuation_renames_file(
        self, db_path: Path, planspace: Path,
    ) -> None:
        ids = submit_chain(
            db_path, "test-agent",
            [TaskSpec(task_type="staleness.alignment_check")],
            planspace=planspace,
        )
        tid = ids[0]
        _mark_task_running(db_path, tid)
        _mark_task_complete(db_path, tid)

        cont_path = planspace / f"artifacts/flows/task-{tid}-continuation.json"
        cont_path.write_text("{corrupt")

        reconcile_task_completion(
            db_path, planspace, tid,
            "complete", "artifacts/output.md",
        )

        assert not cont_path.exists()
        assert cont_path.with_suffix(".malformed.json").exists()

    def test_malformed_continuation_creates_no_new_tasks(
        self, db_path: Path, planspace: Path,
    ) -> None:
        ids = submit_chain(
            db_path, "test-agent",
            [TaskSpec(task_type="staleness.alignment_check")],
            planspace=planspace,
        )
        tid = ids[0]
        _mark_task_running(db_path, tid)
        _mark_task_complete(db_path, tid)

        cont_path = planspace / f"artifacts/flows/task-{tid}-continuation.json"
        cont_path.write_text("not json at all")

        reconcile_task_completion(
            db_path, planspace, tid,
            "complete", "artifacts/output.md",
        )

        all_tasks = _query_all_tasks(db_path)
        assert len(all_tasks) == 1

    def test_malformed_continuation_cancels_chain_descendants(
        self, db_path: Path, planspace: Path,
    ) -> None:
        """If a gated chain has a malformed continuation, descendants cancel."""
        # Create a chain with 2 steps under a gate
        branches = [
            BranchSpec(
                label="test-branch",
                steps=[
                    TaskSpec(task_type="staleness.alignment_check"),
                    TaskSpec(task_type="signals.impact_analysis"),
                ],
            ),
        ]
        gate_id = submit_fanout(
            db_path, "test-agent", branches,
            flow_id="flow_malcont",
            gate=GateSpec(mode="all", failure_policy="include"),
            planspace=planspace,
        )

        all_tasks = _query_all_tasks(db_path)
        first_task = all_tasks[0]
        second_task = all_tasks[1]

        _mark_task_running(db_path, first_task["id"])
        _mark_task_complete(db_path, first_task["id"])

        # Write malformed continuation for first task
        cont_path = (
            planspace
            / f"artifacts/flows/task-{first_task['id']}-continuation.json"
        )
        cont_path.write_text("{broken json")

        reconcile_task_completion(
            db_path, planspace, first_task["id"],
            "complete", "artifacts/output.md",
        )

        # Second task should be cancelled
        t2 = _query_task(db_path, second_task["id"])
        assert t2["status"] == "cancelled"

    def test_malformed_continuation_updates_gate_member(
        self, db_path: Path, planspace: Path,
    ) -> None:
        """Malformed continuation marks the gate member as failed."""
        branches = [
            BranchSpec(
                label="corrupted",
                steps=[TaskSpec(task_type="staleness.alignment_check")],
            ),
        ]
        gate_id = submit_fanout(
            db_path, "test-agent", branches,
            flow_id="flow_gate_corrupt",
            gate=GateSpec(mode="all", failure_policy="include"),
            planspace=planspace,
        )

        all_tasks = _query_all_tasks(db_path)
        task = all_tasks[0]

        _mark_task_running(db_path, task["id"])
        _mark_task_complete(db_path, task["id"])

        cont_path = (
            planspace
            / f"artifacts/flows/task-{task['id']}-continuation.json"
        )
        cont_path.write_text("{broken")

        reconcile_task_completion(
            db_path, planspace, task["id"],
            "complete", "artifacts/output.md",
        )

        members = _query_gate_members(db_path, gate_id)
        assert len(members) == 1
        assert members[0]["status"] == "failed"

    def test_malformed_continuation_logs_warning(
        self, db_path: Path, planspace: Path, capsys,
    ) -> None:
        ids = submit_chain(
            db_path, "test-agent",
            [TaskSpec(task_type="staleness.alignment_check")],
            planspace=planspace,
        )
        tid = ids[0]
        _mark_task_running(db_path, tid)
        _mark_task_complete(db_path, tid)

        cont_path = planspace / f"artifacts/flows/task-{tid}-continuation.json"
        cont_path.write_text("{bad")

        reconcile_task_completion(
            db_path, planspace, tid,
            "complete", "artifacts/output.md",
        )

        captured = capsys.readouterr()
        assert "[FLOW][WARN]" in captured.out
        assert "Malformed continuation" in captured.out


# ---------------------------------------------------------------------------
# _read_origin_refs — corruption preservation
# ---------------------------------------------------------------------------

class TestReadOriginRefsFailClosed:
    """_read_origin_refs renames malformed files and returns []."""

    def test_returns_refs_from_valid_context(
        self, db_path: Path, planspace: Path,
    ) -> None:
        ids = submit_chain(
            db_path, "test-agent",
            [TaskSpec(task_type="staleness.alignment_check")],
            origin_refs=["ref-1", "ref-2"],
            planspace=planspace,
        )
        result = _read_origin_refs(planspace, ids[0])
        assert result == ["ref-1", "ref-2"]

    def test_returns_empty_for_missing_file(
        self, planspace: Path,
    ) -> None:
        result = _read_origin_refs(planspace, 99999)
        assert result == []

    def test_renames_malformed_context(
        self, planspace: Path,
    ) -> None:
        ctx_file = planspace / "artifacts" / "flows" / "task-88-context.json"
        ctx_file.write_text("{corrupt json")

        result = _read_origin_refs(planspace, 88)
        assert result == []
        assert not ctx_file.exists()
        assert ctx_file.with_suffix(".malformed.json").exists()

    def test_malformed_logs_warning(
        self, planspace: Path, capsys,
    ) -> None:
        ctx_file = planspace / "artifacts" / "flows" / "task-77-context.json"
        ctx_file.write_text("{bad")

        _read_origin_refs(planspace, 77)
        captured = capsys.readouterr()
        assert "[FLOW][WARN]" in captured.out


# ---------------------------------------------------------------------------
# task_dispatcher — FlowCorruptionError handling
# ---------------------------------------------------------------------------

class TestDispatcherFlowCorruption:
    """Dispatcher fails task when flow context is corrupt."""

    def test_corrupt_flow_context_fails_task(
        self, db_path: Path, planspace: Path,
    ) -> None:
        """Task with malformed flow_context JSON -> task fails, no dispatch."""
        # Create a malformed flow context file.
        ctx_relpath = "artifacts/flows/task-1-context.json"
        ctx_file = planspace / ctx_relpath
        ctx_file.write_text("{not valid json")

        prompt = planspace / "artifacts" / "test-prompt.md"
        prompt.parent.mkdir(parents=True, exist_ok=True)
        prompt.write_text("# Test\n\nDo the thing.\n")

        task = {
            "id": "1",
            "type": "staleness.alignment_check",
            "by": "test-agent",
            "prio": "normal",
            "payload": str(prompt),
            "flow_context": ctx_relpath,
        }

        from flow.engine import task_dispatcher as task_dispatcher

        dispatch_called = False

        def fake_dispatch(*args, **kwargs):
            nonlocal dispatch_called
            dispatch_called = True
            return "done"

        with override_dispatcher_and_guard(fake_dispatch), \
             patch.object(task_dispatcher._task_registry, "resolve") as mock_resolve, \
             patch("flow.engine.task_dispatcher._db_claim_task"), \
             patch("flow.engine.task_dispatcher._db_fail_task") as mock_fail, \
             patch("flow.engine.task_dispatcher.notify_task_result") as mock_notify:
            mock_resolve.return_value = ("alignment-judge.md", "glm")

            task_dispatcher.dispatch_task(str(db_path), planspace, task)

            # dispatch_agent should NOT have been called
            assert not dispatch_called

            # fail-task should have been called
            assert mock_fail.call_count >= 1
            # _db_fail_task(db_path, task_id, error=err)
            assert "corrupt" in mock_fail.call_args.kwargs.get("error", "")

    def test_missing_flow_context_fails_task(
        self, db_path: Path, planspace: Path,
    ) -> None:
        """Task with declared flow_context but missing file -> task fails."""
        prompt = planspace / "artifacts" / "test-prompt.md"
        prompt.parent.mkdir(parents=True, exist_ok=True)
        prompt.write_text("# Test\n")

        task = {
            "id": "2",
            "type": "staleness.alignment_check",
            "by": "test-agent",
            "prio": "normal",
            "payload": str(prompt),
            "flow_context": "artifacts/flows/task-2-context.json",
        }

        from flow.engine import task_dispatcher as task_dispatcher

        dispatch_called = False

        def fake_dispatch(*args, **kwargs):
            nonlocal dispatch_called
            dispatch_called = True
            return ""

        with override_dispatcher_and_guard(fake_dispatch), \
             patch.object(task_dispatcher._task_registry, "resolve") as mock_resolve, \
             patch("flow.engine.task_dispatcher._db_claim_task"), \
             patch("flow.engine.task_dispatcher._db_fail_task") as mock_fail, \
             patch("flow.engine.task_dispatcher.notify_task_result"):
            mock_resolve.return_value = ("alignment-judge.md", "glm")

            task_dispatcher.dispatch_task(str(db_path), planspace, task)

            assert not dispatch_called

            assert mock_fail.call_count >= 1

    def test_valid_flow_context_dispatches_normally(
        self, db_path: Path, planspace: Path,
    ) -> None:
        """Valid flow context still dispatches the agent (no regression)."""
        ctx_relpath = "artifacts/flows/task-3-context.json"
        ctx_file = planspace / ctx_relpath
        ctx_file.parent.mkdir(parents=True, exist_ok=True)
        ctx_file.write_text(json.dumps({
            "task": {"task_id": 3, "task_type": "staleness.alignment_check"},
            "continuation_path": "artifacts/flows/task-3-continuation.json",
        }))

        prompt = planspace / "artifacts" / "test-prompt.md"
        prompt.write_text("# Test\n\nDo the thing.\n")

        task = {
            "id": "3",
            "type": "staleness.alignment_check",
            "by": "test-agent",
            "prio": "normal",
            "payload": str(prompt),
            "flow_context": ctx_relpath,
            "continuation": "artifacts/flows/task-3-continuation.json",
        }

        from flow.engine import task_dispatcher as task_dispatcher

        dispatch_called = False

        def fake_dispatch(*args, **kwargs):
            nonlocal dispatch_called
            dispatch_called = True
            return "done"

        with override_dispatcher_and_guard(fake_dispatch), \
             patch.object(task_dispatcher._task_registry, "resolve") as mock_resolve, \
             patch("flow.engine.task_dispatcher._db_claim_task"), \
             patch("flow.engine.task_dispatcher._db_complete_task"), \
             patch("flow.engine.task_dispatcher.notify_task_result"):
            mock_resolve.return_value = ("alignment-judge.md", "glm")

            task_dispatcher.dispatch_task(str(db_path), planspace, task)

            # dispatch_agent SHOULD have been called
            assert dispatch_called
