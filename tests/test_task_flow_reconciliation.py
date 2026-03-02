"""Tests for task_flow.py completion reconciliation.

Covers:
- Result manifest writing on task completion and failure
- Chain continuation: completed task with continuation file extends the chain
- Fanout continuation: completed task with fanout continuation creates branches + gate
- Failure cascading: failed task cancels pending chain descendants
- Gate member tracking: gated chain that extends updates leaf_task_id
- Gate firing: all members terminal -> gate aggregate manifest + synthesis task
- failure_policy="block": any failed member blocks the gate
- failure_policy="include": gate fires with failures included

All tests use real SQLite + real files + assert behavioral contracts.
"""

from __future__ import annotations

import json
import sqlite3
import subprocess
from pathlib import Path

import pytest

from flow_schema import BranchSpec, GateSpec, TaskSpec
from task_flow import (
    build_gate_aggregate_manifest,
    build_result_manifest,
    reconcile_task_completion,
    submit_chain,
    submit_fanout,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _find_project_root() -> Path:
    """Walk upward from this file to find the project root."""
    current = Path(__file__).resolve().parent
    for _ in range(5):
        if (current / "SKILL.md").exists():
            return current
        if (current / "scripts").is_dir() and (current / "agents").is_dir():
            return current
        parent = current.parent
        if parent == current:
            break
        current = parent
    return Path(__file__).resolve().parent.parent


_PROJECT_ROOT = _find_project_root()
_WORKFLOW_HOME = (
    _PROJECT_ROOT / "src"
    if (_PROJECT_ROOT / "src" / "scripts").exists()
    else _PROJECT_ROOT
)
_DB_SH = _WORKFLOW_HOME / "scripts" / "db.sh"


def _init_db(db_path: Path) -> None:
    """Initialize a fresh database via db.sh."""
    subprocess.run(
        ["bash", str(_DB_SH), "init", str(db_path)],
        check=True,
        capture_output=True,
        text=True,
    )


def _query_task(db_path: Path, task_id: int) -> dict:
    """Read a task row as a dict."""
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
    """Read all task rows as list of dicts."""
    conn = sqlite3.connect(str(db_path), timeout=5.0)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("SELECT * FROM tasks ORDER BY id ASC")
    rows = cur.fetchall()
    conn.close()
    return [dict(r) for r in rows]


def _query_gate(db_path: Path, gate_id: str) -> dict:
    """Read a gate row as a dict."""
    conn = sqlite3.connect(str(db_path), timeout=5.0)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("SELECT * FROM gates WHERE gate_id = ?", (gate_id,))
    row = cur.fetchone()
    conn.close()
    if row is None:
        raise ValueError(f"Gate {gate_id} not found")
    return dict(row)


def _query_gate_members(db_path: Path, gate_id: str) -> list[dict]:
    """Read all gate members for a gate as list of dicts."""
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


def _mark_task_running(db_path: Path, task_id: int) -> None:
    """Mark a task as running (claim it)."""
    conn = sqlite3.connect(str(db_path), timeout=5.0)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute(
        "UPDATE tasks SET status='running', claimed_by='test-dispatcher' WHERE id=?",
        (task_id,),
    )
    conn.commit()
    conn.close()


def _mark_task_complete(db_path: Path, task_id: int) -> None:
    """Mark a task as complete via DB (simulates db.sh complete-task)."""
    conn = sqlite3.connect(str(db_path), timeout=5.0)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute(
        "UPDATE tasks SET status='complete', completed_at=datetime('now') WHERE id=?",
        (task_id,),
    )
    conn.commit()
    conn.close()


def _mark_task_failed(db_path: Path, task_id: int, error: str = "test error") -> None:
    """Mark a task as failed via DB."""
    conn = sqlite3.connect(str(db_path), timeout=5.0)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute(
        "UPDATE tasks SET status='failed', error=?, completed_at=datetime('now') WHERE id=?",
        (error, task_id),
    )
    conn.commit()
    conn.close()


def _write_continuation(planspace: Path, task_id: int, content: dict) -> None:
    """Write a continuation file for a task."""
    cont_path = planspace / f"artifacts/flows/task-{task_id}-continuation.json"
    cont_path.parent.mkdir(parents=True, exist_ok=True)
    cont_path.write_text(json.dumps(content))


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
    return ps


# ---------------------------------------------------------------------------
# build_result_manifest / build_gate_aggregate_manifest
# ---------------------------------------------------------------------------

class TestBuildManifests:
    """Tests for manifest builder functions."""

    def test_result_manifest_shape(self) -> None:
        m = build_result_manifest(
            task_id=42,
            instance_id="inst_abc",
            flow_id="flow_def",
            chain_id="chain_ghi",
            task_type="alignment_check",
            status="complete",
            output_path="artifacts/task-42-output.md",
            error=None,
        )
        assert m["task_id"] == 42
        assert m["instance_id"] == "inst_abc"
        assert m["flow_id"] == "flow_def"
        assert m["chain_id"] == "chain_ghi"
        assert m["task_type"] == "alignment_check"
        assert m["status"] == "complete"
        assert m["output_path"] == "artifacts/task-42-output.md"
        assert m["error"] is None
        assert "completed_at" in m

    def test_result_manifest_with_error(self) -> None:
        m = build_result_manifest(
            task_id=7,
            instance_id="inst_x",
            flow_id="flow_y",
            chain_id="chain_z",
            task_type="impact_analysis",
            status="failed",
            output_path=None,
            error="something broke",
        )
        assert m["status"] == "failed"
        assert m["error"] == "something broke"
        assert m["output_path"] is None

    def test_gate_aggregate_manifest_shape(self) -> None:
        m = build_gate_aggregate_manifest(
            gate_id="gate_abc",
            flow_id="flow_def",
            mode="all",
            failure_policy="include",
            origin_refs=["ref-1"],
            members=[
                {
                    "chain_id": "chain_1",
                    "slot_label": "auth",
                    "status": "complete",
                    "result_manifest_path": "artifacts/flows/task-1-result.json",
                },
            ],
        )
        assert m["gate_id"] == "gate_abc"
        assert m["flow_id"] == "flow_def"
        assert m["mode"] == "all"
        assert m["failure_policy"] == "include"
        assert m["origin_refs"] == ["ref-1"]
        assert len(m["members"]) == 1
        assert m["members"][0]["chain_id"] == "chain_1"


# ---------------------------------------------------------------------------
# Result manifest writing
# ---------------------------------------------------------------------------

class TestResultManifestWriting:
    """Verify result manifests are written on completion/failure."""

    def test_result_manifest_written_on_complete(
        self, db_path: Path, planspace: Path,
    ) -> None:
        ids = submit_chain(
            db_path, "test-agent",
            [TaskSpec(task_type="alignment_check")],
            planspace=planspace,
        )
        tid = ids[0]
        _mark_task_running(db_path, tid)
        _mark_task_complete(db_path, tid)

        reconcile_task_completion(
            db_path, planspace, tid,
            "complete", "artifacts/task-output.md",
        )

        task = _query_task(db_path, tid)
        manifest_path = planspace / task["result_manifest_path"]
        assert manifest_path.exists()

        manifest = json.loads(manifest_path.read_text())
        assert manifest["task_id"] == tid
        assert manifest["status"] == "complete"
        assert manifest["output_path"] == "artifacts/task-output.md"
        assert manifest["error"] is None

    def test_result_manifest_written_on_failure(
        self, db_path: Path, planspace: Path,
    ) -> None:
        ids = submit_chain(
            db_path, "test-agent",
            [TaskSpec(task_type="alignment_check")],
            planspace=planspace,
        )
        tid = ids[0]
        _mark_task_running(db_path, tid)
        _mark_task_failed(db_path, tid)

        reconcile_task_completion(
            db_path, planspace, tid,
            "failed", None, error="agent crashed",
        )

        task = _query_task(db_path, tid)
        manifest_path = planspace / task["result_manifest_path"]
        assert manifest_path.exists()

        manifest = json.loads(manifest_path.read_text())
        assert manifest["status"] == "failed"
        assert manifest["error"] == "agent crashed"


# ---------------------------------------------------------------------------
# Chain continuation
# ---------------------------------------------------------------------------

class TestChainContinuation:
    """A completed task with a continuation file extends the chain."""

    def test_chain_continuation_queues_next_steps(
        self, db_path: Path, planspace: Path,
    ) -> None:
        """Continuation with chain action creates new tasks in same chain."""
        ids = submit_chain(
            db_path, "test-agent",
            [TaskSpec(task_type="alignment_check")],
            planspace=planspace,
        )
        tid = ids[0]
        task_before = _query_task(db_path, tid)
        chain_id = task_before["chain_id"]
        flow_id = task_before["flow_id"]

        _mark_task_running(db_path, tid)
        _mark_task_complete(db_path, tid)

        # Write continuation: extend with 2 more steps
        _write_continuation(planspace, tid, {
            "version": 2,
            "actions": [{
                "kind": "chain",
                "steps": [
                    {"task_type": "impact_analysis"},
                    {"task_type": "coordination_fix"},
                ],
            }],
        })

        reconcile_task_completion(
            db_path, planspace, tid,
            "complete", "artifacts/output.md",
        )

        # Should now have 3 tasks total
        all_tasks = _query_all_tasks(db_path)
        assert len(all_tasks) == 3

        # New tasks share the same chain_id and flow_id
        new_tasks = [t for t in all_tasks if t["id"] != tid]
        for t in new_tasks:
            assert t["chain_id"] == chain_id
            assert t["flow_id"] == flow_id

        # First new task depends on the original
        assert new_tasks[0]["depends_on"] == str(tid)
        # Second new task depends on the first new task
        assert new_tasks[1]["depends_on"] == str(new_tasks[0]["id"])

    def test_no_continuation_file_means_no_extension(
        self, db_path: Path, planspace: Path,
    ) -> None:
        """Without a continuation file, no new tasks are created."""
        ids = submit_chain(
            db_path, "test-agent",
            [TaskSpec(task_type="alignment_check")],
            planspace=planspace,
        )
        tid = ids[0]
        _mark_task_running(db_path, tid)
        _mark_task_complete(db_path, tid)

        reconcile_task_completion(
            db_path, planspace, tid,
            "complete", "artifacts/output.md",
        )

        all_tasks = _query_all_tasks(db_path)
        assert len(all_tasks) == 1


# ---------------------------------------------------------------------------
# Fanout continuation
# ---------------------------------------------------------------------------

class TestFanoutContinuation:
    """A completed task with a fanout continuation creates branches and gate."""

    def test_fanout_continuation_creates_branches(
        self, db_path: Path, planspace: Path,
    ) -> None:
        ids = submit_chain(
            db_path, "test-agent",
            [TaskSpec(task_type="alignment_check")],
            planspace=planspace,
        )
        tid = ids[0]
        task_before = _query_task(db_path, tid)
        flow_id = task_before["flow_id"]

        _mark_task_running(db_path, tid)
        _mark_task_complete(db_path, tid)

        # Write fanout continuation
        _write_continuation(planspace, tid, {
            "version": 2,
            "actions": [{
                "kind": "fanout",
                "branches": [
                    {
                        "label": "branch-a",
                        "steps": [{"task_type": "impact_analysis"}],
                    },
                    {
                        "label": "branch-b",
                        "steps": [{"task_type": "coordination_fix"}],
                    },
                ],
                "gate": {"mode": "all", "failure_policy": "include"},
            }],
        })

        reconcile_task_completion(
            db_path, planspace, tid,
            "complete", "artifacts/output.md",
        )

        # Should have 3 tasks: original + 2 branch tasks
        all_tasks = _query_all_tasks(db_path)
        assert len(all_tasks) == 3

        # Branch tasks share the same flow_id but different chain_ids
        branch_tasks = [t for t in all_tasks if t["id"] != tid]
        assert branch_tasks[0]["flow_id"] == flow_id
        assert branch_tasks[1]["flow_id"] == flow_id
        assert branch_tasks[0]["chain_id"] != branch_tasks[1]["chain_id"]


# ---------------------------------------------------------------------------
# Failure cascading
# ---------------------------------------------------------------------------

class TestFailureCascading:
    """Failed task cancels pending descendants in same chain."""

    def test_failed_task_cancels_pending_descendants(
        self, db_path: Path, planspace: Path,
    ) -> None:
        """3-step chain: fail step 1, steps 2+3 should be cancelled."""
        ids = submit_chain(
            db_path, "test-agent",
            [
                TaskSpec(task_type="alignment_check"),
                TaskSpec(task_type="impact_analysis"),
                TaskSpec(task_type="coordination_fix"),
            ],
            planspace=planspace,
        )

        # Mark first task as running then failed
        _mark_task_running(db_path, ids[0])
        _mark_task_failed(db_path, ids[0])

        reconcile_task_completion(
            db_path, planspace, ids[0],
            "failed", None, error="step 1 failed",
        )

        # Steps 2 and 3 should now be cancelled
        t1 = _query_task(db_path, ids[1])
        t2 = _query_task(db_path, ids[2])
        assert t1["status"] == "cancelled"
        assert t2["status"] == "cancelled"
        assert t1["error"] == "chain ancestor failed"

    def test_failed_gated_chain_marks_member_failed(
        self, db_path: Path, planspace: Path,
    ) -> None:
        """A failed task in a gated chain marks the gate member as failed."""
        branches = [
            BranchSpec(
                label="will-fail",
                steps=[TaskSpec(task_type="alignment_check")],
            ),
            BranchSpec(
                label="will-succeed",
                steps=[TaskSpec(task_type="impact_analysis")],
            ),
        ]
        gate_id = submit_fanout(
            db_path, "test-agent", branches,
            flow_id="flow_fail",
            gate=GateSpec(mode="all", failure_policy="include"),
            planspace=planspace,
        )

        all_tasks = _query_all_tasks(db_path)
        members_before = _query_gate_members(db_path, gate_id)

        # Find the task in the "will-fail" branch
        fail_member = [m for m in members_before if m["slot_label"] == "will-fail"][0]
        fail_task_id = fail_member["leaf_task_id"]

        _mark_task_running(db_path, fail_task_id)
        _mark_task_failed(db_path, fail_task_id)

        reconcile_task_completion(
            db_path, planspace, fail_task_id,
            "failed", None, error="branch failed",
        )

        # Gate member should be marked as failed
        members_after = _query_gate_members(db_path, gate_id)
        fail_member_after = [
            m for m in members_after if m["slot_label"] == "will-fail"
        ][0]
        assert fail_member_after["status"] == "failed"


# ---------------------------------------------------------------------------
# Gate firing
# ---------------------------------------------------------------------------

class TestGateFiring:
    """Gate fires when all members are terminal."""

    def test_gate_fires_when_all_complete(
        self, db_path: Path, planspace: Path,
    ) -> None:
        """Two branches complete -> gate fires, aggregate manifest written."""
        branches = [
            BranchSpec(
                label="a",
                steps=[TaskSpec(task_type="alignment_check")],
            ),
            BranchSpec(
                label="b",
                steps=[TaskSpec(task_type="impact_analysis")],
            ),
        ]
        gate_id = submit_fanout(
            db_path, "test-agent", branches,
            flow_id="flow_gate_fire",
            gate=GateSpec(mode="all", failure_policy="include"),
            planspace=planspace,
        )

        all_tasks = _query_all_tasks(db_path)
        assert len(all_tasks) == 2

        # Complete both tasks
        for t in all_tasks:
            _mark_task_running(db_path, t["id"])
            _mark_task_complete(db_path, t["id"])
            reconcile_task_completion(
                db_path, planspace, t["id"],
                "complete", f"artifacts/task-{t['id']}-output.md",
            )

        # Gate should be "ready" (no synthesis configured)
        gate = _query_gate(db_path, gate_id)
        assert gate["status"] == "ready"
        assert gate["aggregate_manifest_path"] is not None

        # Aggregate manifest should exist
        agg_file = planspace / gate["aggregate_manifest_path"]
        assert agg_file.exists()
        agg = json.loads(agg_file.read_text())
        assert agg["gate_id"] == gate_id
        assert len(agg["members"]) == 2
        assert all(m["status"] == "complete" for m in agg["members"])

    def test_gate_fires_synthesis_task(
        self, db_path: Path, planspace: Path,
    ) -> None:
        """Gate with synthesis config submits exactly one synthesis task."""
        branches = [
            BranchSpec(
                label="only",
                steps=[TaskSpec(task_type="alignment_check")],
            ),
        ]
        gate_id = submit_fanout(
            db_path, "test-agent", branches,
            flow_id="flow_syn",
            gate=GateSpec(
                mode="all",
                failure_policy="include",
                synthesis=TaskSpec(
                    task_type="impact_analysis",
                    problem_id="P-syn",
                    concern_scope="payments",
                ),
            ),
            planspace=planspace,
        )

        all_tasks = _query_all_tasks(db_path)
        assert len(all_tasks) == 1
        branch_task = all_tasks[0]

        # Complete the branch task
        _mark_task_running(db_path, branch_task["id"])
        _mark_task_complete(db_path, branch_task["id"])
        reconcile_task_completion(
            db_path, planspace, branch_task["id"],
            "complete", "artifacts/output.md",
        )

        # Gate should be "fired"
        gate = _query_gate(db_path, gate_id)
        assert gate["status"] == "fired"
        assert gate["fired_task_id"] is not None
        assert gate["fired_at"] is not None

        # Synthesis task should exist
        syn_task = _query_task(db_path, gate["fired_task_id"])
        assert syn_task["task_type"] == "impact_analysis"
        assert syn_task["problem_id"] == "P-syn"
        assert syn_task["concern_scope"] == "payments"
        assert syn_task["trigger_gate_id"] == gate_id
        assert syn_task["flow_id"] == "flow_syn"
        assert syn_task["status"] == "pending"

        # Only one synthesis task should be created
        all_tasks_after = _query_all_tasks(db_path)
        syn_tasks = [t for t in all_tasks_after if t["trigger_gate_id"] == gate_id]
        assert len(syn_tasks) == 1

    def test_gate_does_not_fire_early(
        self, db_path: Path, planspace: Path,
    ) -> None:
        """Gate does not fire until ALL members are terminal."""
        branches = [
            BranchSpec(
                label="a",
                steps=[TaskSpec(task_type="alignment_check")],
            ),
            BranchSpec(
                label="b",
                steps=[TaskSpec(task_type="impact_analysis")],
            ),
        ]
        gate_id = submit_fanout(
            db_path, "test-agent", branches,
            flow_id="flow_early",
            gate=GateSpec(mode="all", failure_policy="include"),
            planspace=planspace,
        )

        all_tasks = _query_all_tasks(db_path)
        # Only complete the first task
        _mark_task_running(db_path, all_tasks[0]["id"])
        _mark_task_complete(db_path, all_tasks[0]["id"])
        reconcile_task_completion(
            db_path, planspace, all_tasks[0]["id"],
            "complete", "artifacts/output.md",
        )

        # Gate should still be "open"
        gate = _query_gate(db_path, gate_id)
        assert gate["status"] == "open"


# ---------------------------------------------------------------------------
# failure_policy="block"
# ---------------------------------------------------------------------------

class TestFailurePolicyBlock:
    """failure_policy='block' prevents synthesis on any failure."""

    def test_block_policy_blocks_gate_on_failure(
        self, db_path: Path, planspace: Path,
    ) -> None:
        branches = [
            BranchSpec(
                label="will-fail",
                steps=[TaskSpec(task_type="alignment_check")],
            ),
            BranchSpec(
                label="will-succeed",
                steps=[TaskSpec(task_type="impact_analysis")],
            ),
        ]
        gate_id = submit_fanout(
            db_path, "test-agent", branches,
            flow_id="flow_block",
            gate=GateSpec(
                mode="all",
                failure_policy="block",
                synthesis=TaskSpec(task_type="coordination_fix"),
            ),
            planspace=planspace,
        )

        all_tasks = _query_all_tasks(db_path)
        members = _query_gate_members(db_path, gate_id)

        # Fail the first branch
        fail_member = [m for m in members if m["slot_label"] == "will-fail"][0]
        fail_tid = fail_member["leaf_task_id"]
        _mark_task_running(db_path, fail_tid)
        _mark_task_failed(db_path, fail_tid)
        reconcile_task_completion(
            db_path, planspace, fail_tid,
            "failed", None, error="branch failed",
        )

        # Complete the second branch
        succ_member = [m for m in members if m["slot_label"] == "will-succeed"][0]
        succ_tid = succ_member["leaf_task_id"]
        _mark_task_running(db_path, succ_tid)
        _mark_task_complete(db_path, succ_tid)
        reconcile_task_completion(
            db_path, planspace, succ_tid,
            "complete", "artifacts/output.md",
        )

        # Gate should be "blocked", not "fired"
        gate = _query_gate(db_path, gate_id)
        assert gate["status"] == "blocked"
        assert gate["fired_task_id"] is None

        # No synthesis task should have been created
        all_tasks_after = _query_all_tasks(db_path)
        syn_tasks = [t for t in all_tasks_after if t["trigger_gate_id"] == gate_id]
        assert len(syn_tasks) == 0

    def test_include_policy_fires_with_failures(
        self, db_path: Path, planspace: Path,
    ) -> None:
        """failure_policy='include' fires the gate even when some members fail."""
        branches = [
            BranchSpec(
                label="will-fail",
                steps=[TaskSpec(task_type="alignment_check")],
            ),
            BranchSpec(
                label="will-succeed",
                steps=[TaskSpec(task_type="impact_analysis")],
            ),
        ]
        gate_id = submit_fanout(
            db_path, "test-agent", branches,
            flow_id="flow_include",
            gate=GateSpec(
                mode="all",
                failure_policy="include",
                synthesis=TaskSpec(task_type="coordination_fix"),
            ),
            planspace=planspace,
        )

        members = _query_gate_members(db_path, gate_id)

        # Fail the first branch
        fail_member = [m for m in members if m["slot_label"] == "will-fail"][0]
        fail_tid = fail_member["leaf_task_id"]
        _mark_task_running(db_path, fail_tid)
        _mark_task_failed(db_path, fail_tid)
        reconcile_task_completion(
            db_path, planspace, fail_tid,
            "failed", None, error="branch failed",
        )

        # Complete the second branch
        succ_member = [m for m in members if m["slot_label"] == "will-succeed"][0]
        succ_tid = succ_member["leaf_task_id"]
        _mark_task_running(db_path, succ_tid)
        _mark_task_complete(db_path, succ_tid)
        reconcile_task_completion(
            db_path, planspace, succ_tid,
            "complete", "artifacts/output.md",
        )

        # Gate should be "fired" (not blocked)
        gate = _query_gate(db_path, gate_id)
        assert gate["status"] == "fired"
        assert gate["fired_task_id"] is not None

        # Aggregate manifest should show the failure
        agg = json.loads((planspace / gate["aggregate_manifest_path"]).read_text())
        statuses = {m["status"] for m in agg["members"]}
        assert "failed" in statuses
        assert "complete" in statuses


# ---------------------------------------------------------------------------
# Gate member leaf tracking
# ---------------------------------------------------------------------------

class TestGateMemberLeafTracking:
    """Gated chain that extends updates leaf_task_id so gate doesn't fire early."""

    def test_chain_extension_updates_leaf(
        self, db_path: Path, planspace: Path,
    ) -> None:
        """Extending a gated chain updates the gate member's leaf_task_id."""
        branches = [
            BranchSpec(
                label="extending",
                steps=[TaskSpec(task_type="alignment_check")],
            ),
        ]
        gate_id = submit_fanout(
            db_path, "test-agent", branches,
            flow_id="flow_leaf",
            gate=GateSpec(mode="all", failure_policy="include"),
            planspace=planspace,
        )

        all_tasks = _query_all_tasks(db_path)
        first_task = all_tasks[0]
        original_members = _query_gate_members(db_path, gate_id)
        original_leaf = original_members[0]["leaf_task_id"]
        assert original_leaf == first_task["id"]

        # Complete the first task with a continuation
        _mark_task_running(db_path, first_task["id"])
        _mark_task_complete(db_path, first_task["id"])

        _write_continuation(planspace, first_task["id"], {
            "version": 2,
            "actions": [{
                "kind": "chain",
                "steps": [{"task_type": "impact_analysis"}],
            }],
        })

        reconcile_task_completion(
            db_path, planspace, first_task["id"],
            "complete", "artifacts/output.md",
        )

        # Gate member's leaf_task_id should have been updated
        updated_members = _query_gate_members(db_path, gate_id)
        updated_leaf = updated_members[0]["leaf_task_id"]
        assert updated_leaf != original_leaf

        # Gate should NOT be ready (the new leaf task is still pending)
        gate = _query_gate(db_path, gate_id)
        assert gate["status"] == "open"

        # Now complete the extended task (the new leaf)
        new_task = _query_task(db_path, updated_leaf)
        assert new_task["task_type"] == "impact_analysis"
        _mark_task_running(db_path, updated_leaf)
        _mark_task_complete(db_path, updated_leaf)

        reconcile_task_completion(
            db_path, planspace, updated_leaf,
            "complete", "artifacts/output2.md",
        )

        # NOW the gate should be ready
        gate_after = _query_gate(db_path, gate_id)
        assert gate_after["status"] == "ready"

    def test_non_leaf_completion_does_not_finalize_gate_member(
        self, db_path: Path, planspace: Path,
    ) -> None:
        """Completing a non-leaf task in a gated chain does not mark the member complete."""
        branches = [
            BranchSpec(
                label="multi-step",
                steps=[
                    TaskSpec(task_type="alignment_check"),
                    TaskSpec(task_type="impact_analysis"),
                ],
            ),
        ]
        gate_id = submit_fanout(
            db_path, "test-agent", branches,
            flow_id="flow_nonleaf",
            gate=GateSpec(mode="all", failure_policy="include"),
            planspace=planspace,
        )

        all_tasks = _query_all_tasks(db_path)
        first_task = all_tasks[0]
        second_task = all_tasks[1]

        # The leaf should be the second task
        members = _query_gate_members(db_path, gate_id)
        assert members[0]["leaf_task_id"] == second_task["id"]

        # Complete the first task (not the leaf)
        _mark_task_running(db_path, first_task["id"])
        _mark_task_complete(db_path, first_task["id"])

        reconcile_task_completion(
            db_path, planspace, first_task["id"],
            "complete", "artifacts/output.md",
        )

        # Gate member should still be "pending"
        members_after = _query_gate_members(db_path, gate_id)
        assert members_after[0]["status"] == "pending"

        # Gate should still be "open"
        gate = _query_gate(db_path, gate_id)
        assert gate["status"] == "open"


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

class TestEdgeCases:
    """Edge cases for reconciliation."""

    def test_nonexistent_task_is_noop(
        self, db_path: Path, planspace: Path,
    ) -> None:
        """Reconciling a nonexistent task_id does nothing."""
        reconcile_task_completion(
            db_path, planspace, 99999,
            "complete", "artifacts/output.md",
        )
        # No error, no crash

    def test_task_without_flow_columns_is_safe(
        self, db_path: Path, planspace: Path,
    ) -> None:
        """A task without flow/chain IDs reconciles safely (writes manifest only)."""
        from task_router import submit_task as _submit

        tid = _submit(db_path, "test", "alignment_check")
        _mark_task_running(db_path, tid)
        _mark_task_complete(db_path, tid)

        # Should not crash even with no flow_id, chain_id, etc.
        reconcile_task_completion(
            db_path, planspace, tid,
            "complete", "artifacts/output.md",
        )

    def test_malformed_continuation_fails_closed(
        self, db_path: Path, planspace: Path,
    ) -> None:
        """A malformed continuation file fails the chain closed.

        The corrupt file is renamed to .malformed.json, pending
        descendants are cancelled, and no new tasks are created.
        """
        ids = submit_chain(
            db_path, "test-agent",
            [TaskSpec(task_type="alignment_check")],
            planspace=planspace,
        )
        tid = ids[0]
        _mark_task_running(db_path, tid)
        _mark_task_complete(db_path, tid)

        # Write invalid JSON as continuation
        cont_path = planspace / f"artifacts/flows/task-{tid}-continuation.json"
        cont_path.write_text("{invalid json broken")

        reconcile_task_completion(
            db_path, planspace, tid,
            "complete", "artifacts/output.md",
        )

        # Should have exactly 1 task (no new tasks created)
        all_tasks = _query_all_tasks(db_path)
        assert len(all_tasks) == 1

        # Corrupt file should be renamed
        assert not cont_path.exists()
        assert cont_path.with_suffix(".malformed.json").exists()
