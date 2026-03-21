"""End-to-end integration tests for the full flow system.

Exercises the complete lifecycle: ingestion -> submission -> dispatch
simulation -> reconciliation -> gate firing -> synthesis. Uses real
SQLite, real filesystem artifacts, and mocked dispatch_agent only.

Scenarios:
1. Legacy single-task compatibility
2. Linear chain continuation (A -> B -> C)
3. Fanout + gate + synthesis
4. Gated chain extension delays gate fire
5. Failed branch cancellation
6. Failure policy: include vs block
7. Concurrent identical packages remain isolated
8. Nested synthesis emitting further work
"""

from __future__ import annotations

import json
import sqlite3
import subprocess
from pathlib import Path

import pytest

from _paths import DB_SH
from src.orchestrator.path_registry import PathRegistry

from flow.types.context import FlowEnvelope
from flow.types.schema import BranchSpec, GateSpec, TaskSpec
from containers import Services
from flow.engine.flow_submitter import FlowSubmitter
from flow.engine.reconciler import Reconciler
from flow.repository.flow_context_store import FlowContextStore
from flow.repository.gate_repository import GateRepository
from implementation.service.traceability_writer import TraceabilityWriter


def submit_chain(env, steps, **kwargs):
    return Services.flow_ingestion().submit_chain(env, steps, **kwargs)


def submit_fanout(env, branches, **kwargs):
    return Services.flow_ingestion().submit_fanout(env, branches, **kwargs)


def reconcile_task_completion(db_path, planspace, task_id, status, output_path, **kwargs):
    artifact_io = Services.artifact_io()
    flow_context_store = FlowContextStore(artifact_io)
    flow_submitter = FlowSubmitter(
        freshness=Services.freshness(),
        flow_context_store=flow_context_store,
    )
    gate_repository = GateRepository(artifact_io)
    reconciler = Reconciler(
        artifact_io=artifact_io,
        research=Services.research(),
        prompt_guard=Services.prompt_guard(),
        flow_submitter=flow_submitter,
        gate_repository=gate_repository,
        traceability_writer=TraceabilityWriter(
            artifact_io=artifact_io,
            hasher=Services.hasher(),
            logger=Services.logger(),
            section_alignment=Services.section_alignment(),
        ),
    )
    return reconciler.reconcile_task_completion(
        db_path, planspace, task_id, status, output_path, **kwargs,
    )


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
    """Read all task rows ordered by id."""
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


def _query_all_gates(db_path: Path) -> list[dict]:
    """Read all gate rows."""
    conn = sqlite3.connect(str(db_path), timeout=5.0)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("SELECT * FROM gates ORDER BY gate_id")
    rows = cur.fetchall()
    conn.close()
    return [dict(r) for r in rows]


def _dependency_ids(db_path: Path, task_id: int) -> list[int]:
    conn = sqlite3.connect(str(db_path), timeout=5.0)
    cur = conn.cursor()
    cur.execute(
        "SELECT depends_on_task_id FROM task_dependencies WHERE task_id=? ORDER BY depends_on_task_id",
        (task_id,),
    )
    rows = [int(row[0]) for row in cur.fetchall()]
    conn.close()
    return rows


def _mark_task_running(db_path: Path, task_id: int) -> None:
    """Claim a task for execution (simulates dispatcher claiming)."""
    conn = sqlite3.connect(str(db_path), timeout=5.0)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute(
        "UPDATE tasks SET status='running', claimed_by='test-dispatcher' WHERE id=?",
        (task_id,),
    )
    conn.commit()
    conn.close()


def _mark_task_complete_db(db_path: Path, task_id: int) -> None:
    """Mark a task as complete in DB (simulates db.sh complete-task)."""
    conn = sqlite3.connect(str(db_path), timeout=5.0)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute(
        "UPDATE tasks SET status='complete', completed_at=datetime('now') WHERE id=?",
        (task_id,),
    )
    conn.commit()
    conn.close()


def _mark_task_failed_db(
    db_path: Path, task_id: int, error: str = "test error",
) -> None:
    """Mark a task as failed in DB."""
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


def _simulate_agent_output(
    planspace: Path, task_id: int, output_text: str = "task output",
) -> str:
    """Simulate an agent writing output. Returns the output relpath."""
    output_relpath = f"artifacts/task-{task_id}-output.md"
    output_file = planspace / output_relpath
    output_file.parent.mkdir(parents=True, exist_ok=True)
    output_file.write_text(output_text)
    return output_relpath


def _complete_task(
    db_path: Path,
    planspace: Path,
    task_id: int,
    output_text: str = "task output",
) -> None:
    """Full completion cycle: claim, write output, mark complete, reconcile."""
    from flow.service.task_db_client import complete_task_with_result

    _mark_task_running(db_path, task_id)
    output_path = _simulate_agent_output(planspace, task_id, output_text)
    complete_task_with_result(db_path, task_id, output_path=output_path)
    reconcile_task_completion(
        db_path, planspace, task_id, "complete", output_path,
    )


def _fail_task(
    db_path: Path,
    planspace: Path,
    task_id: int,
    error: str = "agent crashed",
) -> None:
    """Full failure cycle: claim, mark failed, reconcile."""
    from flow.service.task_db_client import fail_task_with_result

    _mark_task_running(db_path, task_id)
    fail_task_with_result(db_path, task_id, error=error)
    reconcile_task_completion(
        db_path, planspace, task_id, "failed", None, error=error,
    )


def _find_next_runnable(db_path: Path) -> int | None:
    """Find the next runnable task (pending + deps met). Returns task_id or None."""
    conn = sqlite3.connect(str(db_path), timeout=5.0)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute(
        """SELECT id FROM tasks
           WHERE status='pending'
           ORDER BY
             CASE priority
               WHEN 'high'   THEN 0
               WHEN 'normal' THEN 1
               WHEN 'low'    THEN 2
               ELSE 3
             END,
             id ASC""",
    )
    for row in cur.fetchall():
        dep_row = conn.execute(
            "SELECT 1 FROM task_dependencies WHERE task_id=? AND satisfied=0 LIMIT 1",
            (row["id"],),
        ).fetchone()
        if dep_row:
            continue
        conn.close()
        return row["id"]
    conn.close()
    return None


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def db_path(tmp_path: Path) -> Path:
    """Create and initialize a test database."""
    p = tmp_path / "test.db"
    _init_db(p)
    return p


@pytest.fixture()
def planspace(tmp_path: Path) -> Path:
    """Create a planspace directory for flow artifacts."""
    ps = tmp_path / "planspace"
    ps.mkdir()
    PathRegistry(ps).ensure_artifacts_tree()
    return ps


# ===========================================================================
# Scenario 1: Legacy single-task compatibility
# ===========================================================================

class TestLegacySingleTaskCompatibility:
    """Submit a legacy v1 task, simulate dispatch + completion, verify result."""

    def test_legacy_task_full_lifecycle(
        self, db_path: Path, planspace: Path,
    ) -> None:
        """Legacy task enters queue, gets dispatched, completes, has result manifest."""
        # Submit via ingest_and_submit
        from containers import Services
        ingest_and_submit = Services.flow_ingestion().ingest_and_submit

        sig = planspace / "artifacts" / "signals" / "legacy-task.json"
        sig.write_text(json.dumps({
            "task_type": "staleness.alignment_check",
            "concern_scope": "authentication",
        }))
        ids = ingest_and_submit(planspace, "section-loop", sig, db_path=db_path)
        assert len(ids) == 1
        tid = ids[0]

        # Verify it is pending in the queue
        task = _query_task(db_path, tid)
        assert task["status"] == "pending"
        assert task["task_type"] == "staleness.alignment_check"

        # Simulate dispatch + completion
        _complete_task(db_path, planspace, tid)

        # Verify result manifest is written
        task_after = _query_task(db_path, tid)
        manifest_path = planspace / task_after["result_manifest_path"]
        assert manifest_path.exists()

        manifest = json.loads(manifest_path.read_text())
        assert manifest["status"] == "complete"
        assert manifest["task_type"] == "staleness.alignment_check"
        assert manifest["output_path"] is not None

        # Verify flow context file was written during submission
        ctx_path = planspace / task_after["flow_context_path"]
        assert ctx_path.exists()

    def test_legacy_task_signal_file_deleted(
        self, db_path: Path, planspace: Path,
    ) -> None:
        """Signal file is deleted after successful ingestion."""
        from containers import Services
        ingest_and_submit = Services.flow_ingestion().ingest_and_submit

        sig = planspace / "artifacts" / "signals" / "cleanup-test.json"
        sig.write_text(json.dumps({"task_type": "signals.impact_analysis"}))
        ingest_and_submit(planspace, "test", sig, db_path=db_path)
        assert not sig.exists()


# ===========================================================================
# Scenario 2: Linear chain continuation (A -> B -> C)
# ===========================================================================

class TestLinearChainContinuation:
    """Submit a chain, simulate completion with continuations, verify full lifecycle."""

    def test_three_step_chain_with_continuation(
        self, db_path: Path, planspace: Path,
    ) -> None:
        """Submit A, complete A with continuation adding B, complete B with
        continuation adding C. Verify all result manifests and chain wiring."""
        # Step 1: Submit initial chain with just step A
        ids = submit_chain(
            FlowEnvelope(db_path=db_path, submitted_by="test-agent", planspace=planspace),
            [TaskSpec(task_type="staleness.alignment_check")],
        )
        assert len(ids) == 1
        tid_a = ids[0]
        task_a = _query_task(db_path, tid_a)
        chain_id = task_a["chain_id"]
        flow_id = task_a["flow_id"]

        # Step 2: Complete A with a continuation that adds B
        _mark_task_running(db_path, tid_a)
        output_a = _simulate_agent_output(planspace, tid_a, "step A output")
        _mark_task_complete_db(db_path, tid_a)

        _write_continuation(planspace, tid_a, {
            "version": 2,
            "actions": [{
                "kind": "chain",
                "steps": [{"task_type": "signals.impact_analysis"}],
            }],
        })

        reconcile_task_completion(
            db_path, planspace, tid_a, "complete", output_a,
        )

        # Verify B was created with same chain_id and flow_id
        all_tasks = _query_all_tasks(db_path)
        assert len(all_tasks) == 2
        task_b = all_tasks[1]
        tid_b = task_b["id"]
        assert task_b["chain_id"] == chain_id
        assert task_b["flow_id"] == flow_id
        assert _dependency_ids(db_path, tid_b) == [tid_a]
        assert task_b["task_type"] == "signals.impact_analysis"

        # B should be runnable now (A is complete)
        runnable = _find_next_runnable(db_path)
        assert runnable == tid_b

        # Step 3: Complete B with a continuation that adds C
        _mark_task_running(db_path, tid_b)
        output_b = _simulate_agent_output(planspace, tid_b, "step B output")
        _mark_task_complete_db(db_path, tid_b)

        _write_continuation(planspace, tid_b, {
            "version": 2,
            "actions": [{
                "kind": "chain",
                "steps": [{"task_type": "coordination.fix"}],
            }],
        })

        reconcile_task_completion(
            db_path, planspace, tid_b, "complete", output_b,
        )

        # Verify C was created
        all_tasks = _query_all_tasks(db_path)
        assert len(all_tasks) == 3
        task_c = all_tasks[2]
        tid_c = task_c["id"]
        assert task_c["chain_id"] == chain_id
        assert task_c["flow_id"] == flow_id
        assert _dependency_ids(db_path, tid_c) == [tid_b]
        assert task_c["task_type"] == "coordination.fix"

        # Step 4: Complete C (no continuation)
        _complete_task(db_path, planspace, tid_c)

        # Verify all result manifests exist
        for tid in [tid_a, tid_b, tid_c]:
            t = _query_task(db_path, tid)
            manifest_path = planspace / t["result_manifest_path"]
            assert manifest_path.exists(), f"Missing manifest for task {tid}"
            manifest = json.loads(manifest_path.read_text())
            assert manifest["status"] == "complete"

    def test_chain_dependency_prevents_premature_dispatch(
        self, db_path: Path, planspace: Path,
    ) -> None:
        """Step B is not runnable until step A completes."""
        ids = submit_chain(
            FlowEnvelope(db_path=db_path, submitted_by="test-agent", planspace=planspace),
            [
                TaskSpec(task_type="staleness.alignment_check"),
                TaskSpec(task_type="signals.impact_analysis"),
            ],
        )

        # Only step A is runnable
        runnable = _find_next_runnable(db_path)
        assert runnable == ids[0]

        # After A completes, B becomes runnable
        _complete_task(db_path, planspace, ids[0])
        runnable = _find_next_runnable(db_path)
        assert runnable == ids[1]


# ===========================================================================
# Scenario 3: Fanout + gate + synthesis
# ===========================================================================

class TestFanoutGateSynthesis:
    """Submit a fanout with 3 branches and gate+synthesis, verify full lifecycle."""

    def test_fanout_gate_synthesis_full_lifecycle(
        self, db_path: Path, planspace: Path,
    ) -> None:
        """3 branches complete -> gate fires -> synthesis task created with
        trigger_gate_id set and flow context pointing to gate aggregate."""
        branches = [
            BranchSpec(
                label="branch-1",
                steps=[TaskSpec(task_type="staleness.alignment_check")],
            ),
            BranchSpec(
                label="branch-2",
                steps=[TaskSpec(task_type="signals.impact_analysis")],
            ),
            BranchSpec(
                label="branch-3",
                steps=[TaskSpec(task_type="coordination.fix")],
            ),
        ]
        gate_id = submit_fanout(
            FlowEnvelope(db_path=db_path, submitted_by="test-agent", flow_id="flow_fanout_syn", planspace=planspace),
            branches,
            gate=GateSpec(
                mode="all",
                failure_policy="include",
                synthesis=TaskSpec(
                    task_type="coordination.consequence_triage",
                    concern_scope="synthesis-scope",
                ),
            ),
        )
        assert gate_id is not None

        all_tasks = _query_all_tasks(db_path)
        assert len(all_tasks) == 3

        members = _query_gate_members(db_path, gate_id)
        assert len(members) == 3

        # Complete branch 1 and 2 -- gate should NOT fire yet
        for i in range(2):
            tid = all_tasks[i]["id"]
            _complete_task(db_path, planspace, tid)

        gate_mid = _query_gate(db_path, gate_id)
        assert gate_mid["status"] == "open"

        # Complete branch 3 -- gate should fire now
        _complete_task(db_path, planspace, all_tasks[2]["id"])

        gate_after = _query_gate(db_path, gate_id)
        assert gate_after["status"] == "fired"
        assert gate_after["fired_task_id"] is not None
        assert gate_after["aggregate_manifest_path"] is not None

        # Verify aggregate manifest has all 3 members
        agg = json.loads(
            (planspace / gate_after["aggregate_manifest_path"]).read_text()
        )
        assert agg["gate_id"] == gate_id
        assert len(agg["members"]) == 3
        assert all(m["status"] == "complete" for m in agg["members"])

        # Verify synthesis task
        syn_task = _query_task(db_path, gate_after["fired_task_id"])
        assert syn_task["task_type"] == "coordination.consequence_triage"
        assert syn_task["trigger_gate_id"] == gate_id
        assert syn_task["flow_id"] == "flow_fanout_syn"
        assert syn_task["concern_scope"] == "synthesis-scope"
        assert syn_task["status"] == "pending"

        # Verify synthesis task's flow context points to gate aggregate
        syn_ctx_path = planspace / syn_task["flow_context_path"]
        assert syn_ctx_path.exists()
        syn_ctx = json.loads(syn_ctx_path.read_text())
        assert syn_ctx["task"]["trigger_gate_id"] == gate_id

    def test_gate_does_not_fire_with_pending_members(
        self, db_path: Path, planspace: Path,
    ) -> None:
        """Gate stays open when some members are still pending."""
        branches = [
            BranchSpec(
                label="a",
                steps=[TaskSpec(task_type="staleness.alignment_check")],
            ),
            BranchSpec(
                label="b",
                steps=[TaskSpec(task_type="signals.impact_analysis")],
            ),
        ]
        gate_id = submit_fanout(
            FlowEnvelope(db_path=db_path, submitted_by="test-agent", flow_id="flow_partial", planspace=planspace),
            branches,
            gate=GateSpec(
                mode="all",
                synthesis=TaskSpec(task_type="coordination.fix"),
            ),
        )

        all_tasks = _query_all_tasks(db_path)
        # Complete only the first branch
        _complete_task(db_path, planspace, all_tasks[0]["id"])

        gate = _query_gate(db_path, gate_id)
        assert gate["status"] == "open"
        assert gate["fired_task_id"] is None

        # No synthesis task should exist yet
        tasks_after = _query_all_tasks(db_path)
        syn_tasks = [t for t in tasks_after if t.get("trigger_gate_id") == gate_id]
        assert len(syn_tasks) == 0


# ===========================================================================
# Scenario 4: Gated chain extension delays gate fire
# ===========================================================================

class TestGatedChainExtensionDelaysGateFire:
    """Chain continuation inside a gated branch delays the gate from firing."""

    def test_chain_extension_prevents_premature_gate_fire(
        self, db_path: Path, planspace: Path,
    ) -> None:
        """Branch 2's leaf writes a chain continuation. Gate must wait for
        the extended task to complete before firing."""
        branches = [
            BranchSpec(
                label="simple-branch",
                steps=[TaskSpec(task_type="staleness.alignment_check")],
            ),
            BranchSpec(
                label="extending-branch",
                steps=[TaskSpec(task_type="signals.impact_analysis")],
            ),
        ]
        gate_id = submit_fanout(
            FlowEnvelope(db_path=db_path, submitted_by="test-agent", flow_id="flow_extend_gate", planspace=planspace),
            branches,
            gate=GateSpec(mode="all", failure_policy="include"),
        )

        all_tasks = _query_all_tasks(db_path)
        assert len(all_tasks) == 2

        members = _query_gate_members(db_path, gate_id)
        simple_member = [m for m in members if m["slot_label"] == "simple-branch"][0]
        extending_member = [m for m in members if m["slot_label"] == "extending-branch"][0]

        simple_tid = simple_member["leaf_task_id"]
        extending_tid = extending_member["leaf_task_id"]

        # Complete simple-branch normally
        _complete_task(db_path, planspace, simple_tid)

        # Complete extending-branch with a continuation
        _mark_task_running(db_path, extending_tid)
        output_ext = _simulate_agent_output(planspace, extending_tid)
        _mark_task_complete_db(db_path, extending_tid)

        _write_continuation(planspace, extending_tid, {
            "version": 2,
            "actions": [{
                "kind": "chain",
                "steps": [{"task_type": "coordination.fix"}],
            }],
        })

        reconcile_task_completion(
            db_path, planspace, extending_tid, "complete", output_ext,
        )

        # Gate should NOT fire yet -- the extension is still pending
        gate_mid = _query_gate(db_path, gate_id)
        assert gate_mid["status"] == "open"

        # Verify gate member leaf was updated
        updated_members = _query_gate_members(db_path, gate_id)
        ext_member_after = [m for m in updated_members if m["slot_label"] == "extending-branch"][0]
        new_leaf_tid = ext_member_after["leaf_task_id"]
        assert new_leaf_tid != extending_tid  # leaf was updated

        # Complete the extended task
        _complete_task(db_path, planspace, new_leaf_tid)

        # NOW the gate should fire
        gate_after = _query_gate(db_path, gate_id)
        assert gate_after["status"] == "ready"  # no synthesis configured

        # Both members should be complete
        final_members = _query_gate_members(db_path, gate_id)
        for m in final_members:
            assert m["status"] == "complete"


# ===========================================================================
# Scenario 5: Failed branch cancellation
# ===========================================================================

class TestFailedBranchCancellation:
    """Failing a step in a chain cancels all pending descendants."""

    def test_fail_step_2_cancels_step_3(
        self, db_path: Path, planspace: Path,
    ) -> None:
        """3-step chain: complete step 1, fail step 2, verify step 3 fails closed."""
        ids = submit_chain(
            FlowEnvelope(db_path=db_path, submitted_by="test-agent", planspace=planspace),
            [
                TaskSpec(task_type="staleness.alignment_check"),
                TaskSpec(task_type="signals.impact_analysis"),
                TaskSpec(task_type="coordination.fix"),
            ],
        )
        tid_1, tid_2, tid_3 = ids

        # Complete step 1
        _complete_task(db_path, planspace, tid_1)

        # Verify step 2 is now runnable
        runnable = _find_next_runnable(db_path)
        assert runnable == tid_2

        # Fail step 2
        _fail_task(db_path, planspace, tid_2, error="step 2 broken")

        # Verify step 3 fails closed on the upstream dependency failure.
        task_3 = _query_task(db_path, tid_3)
        assert task_3["status"] == "failed"
        assert task_3["status_reason"] == "dependency_failed"
        assert task_3["error"] == f"dependency_failed:{tid_2}"

        # Verify step 2 result manifest shows failure
        task_2 = _query_task(db_path, tid_2)
        manifest_path = planspace / task_2["result_manifest_path"]
        assert manifest_path.exists()
        manifest = json.loads(manifest_path.read_text())
        assert manifest["status"] == "failed"
        assert manifest["error"] == "step 2 broken"

        # No more runnable tasks
        runnable = _find_next_runnable(db_path)
        assert runnable is None


# ===========================================================================
# Scenario 6: Failure policy: include vs block
# ===========================================================================

class TestFailurePolicyIncludeVsBlock:
    """Verify failure_policy controls whether gate fires or blocks on failure."""

    def test_include_policy_fires_with_failure(
        self, db_path: Path, planspace: Path,
    ) -> None:
        """failure_policy='include': gate fires even with failed branch.
        Synthesis task sees failure in aggregate manifest."""
        branches = [
            BranchSpec(
                label="will-fail",
                steps=[TaskSpec(task_type="staleness.alignment_check")],
            ),
            BranchSpec(
                label="will-succeed",
                steps=[TaskSpec(task_type="signals.impact_analysis")],
            ),
        ]
        gate_id = submit_fanout(
            FlowEnvelope(db_path=db_path, submitted_by="test-agent", flow_id="flow_include", planspace=planspace),
            branches,
            gate=GateSpec(
                mode="all",
                failure_policy="include",
                synthesis=TaskSpec(task_type="coordination.fix"),
            ),
        )

        members = _query_gate_members(db_path, gate_id)
        fail_member = [m for m in members if m["slot_label"] == "will-fail"][0]
        succ_member = [m for m in members if m["slot_label"] == "will-succeed"][0]

        # Fail one branch
        _fail_task(db_path, planspace, fail_member["leaf_task_id"])

        # Gate should still be open (other branch pending)
        gate_mid = _query_gate(db_path, gate_id)
        assert gate_mid["status"] == "open"

        # Complete the other branch
        _complete_task(db_path, planspace, succ_member["leaf_task_id"])

        # Gate should fire (include policy)
        gate_after = _query_gate(db_path, gate_id)
        assert gate_after["status"] == "fired"
        assert gate_after["fired_task_id"] is not None

        # Aggregate manifest should contain both statuses
        agg = json.loads(
            (planspace / gate_after["aggregate_manifest_path"]).read_text()
        )
        statuses = {m["status"] for m in agg["members"]}
        assert "failed" in statuses
        assert "complete" in statuses

        # Synthesis task should exist and be pending
        syn_task = _query_task(db_path, gate_after["fired_task_id"])
        assert syn_task["status"] == "pending"
        assert syn_task["trigger_gate_id"] == gate_id

    def test_block_policy_blocks_on_failure(
        self, db_path: Path, planspace: Path,
    ) -> None:
        """failure_policy='block': gate becomes blocked, no synthesis created."""
        branches = [
            BranchSpec(
                label="will-fail",
                steps=[TaskSpec(task_type="staleness.alignment_check")],
            ),
            BranchSpec(
                label="will-succeed",
                steps=[TaskSpec(task_type="signals.impact_analysis")],
            ),
        ]
        gate_id = submit_fanout(
            FlowEnvelope(db_path=db_path, submitted_by="test-agent", flow_id="flow_block", planspace=planspace),
            branches,
            gate=GateSpec(
                mode="all",
                failure_policy="block",
                synthesis=TaskSpec(task_type="coordination.fix"),
            ),
        )

        members = _query_gate_members(db_path, gate_id)
        fail_member = [m for m in members if m["slot_label"] == "will-fail"][0]
        succ_member = [m for m in members if m["slot_label"] == "will-succeed"][0]

        # Fail one branch
        _fail_task(db_path, planspace, fail_member["leaf_task_id"])

        # Complete the other branch
        _complete_task(db_path, planspace, succ_member["leaf_task_id"])

        # Gate should be blocked
        gate_after = _query_gate(db_path, gate_id)
        assert gate_after["status"] == "blocked"
        assert gate_after["fired_task_id"] is None

        # No synthesis task should exist
        all_tasks = _query_all_tasks(db_path)
        syn_tasks = [t for t in all_tasks if t.get("trigger_gate_id") == gate_id]
        assert len(syn_tasks) == 0


# ===========================================================================
# Scenario 7: Concurrent identical packages remain isolated
# ===========================================================================

class TestConcurrentPackageIsolation:
    """Two concurrent instances of the same package structure stay isolated."""

    def test_two_concurrent_flows_isolated(
        self, db_path: Path, planspace: Path,
    ) -> None:
        """Submit same fanout structure twice with different flow_ids.
        Complete tasks in interleaved order. Verify no cross-contamination."""
        # Create two identical fanout structures
        def _make_branches() -> list[BranchSpec]:
            return [
                BranchSpec(
                    label="branch-a",
                    steps=[TaskSpec(task_type="staleness.alignment_check")],
                ),
                BranchSpec(
                    label="branch-b",
                    steps=[TaskSpec(task_type="signals.impact_analysis")],
                ),
            ]

        gate_id_1 = submit_fanout(
            FlowEnvelope(db_path=db_path, submitted_by="agent-1", flow_id="flow_concurrent_1", planspace=planspace),
            _make_branches(),
            gate=GateSpec(
                mode="all",
                failure_policy="include",
                synthesis=TaskSpec(task_type="coordination.fix"),
            ),
        )

        gate_id_2 = submit_fanout(
            FlowEnvelope(db_path=db_path, submitted_by="agent-2", flow_id="flow_concurrent_2", planspace=planspace),
            _make_branches(),
            gate=GateSpec(
                mode="all",
                failure_policy="include",
                synthesis=TaskSpec(task_type="coordination.fix"),
            ),
        )

        assert gate_id_1 != gate_id_2

        # Get members for each gate
        members_1 = _query_gate_members(db_path, gate_id_1)
        members_2 = _query_gate_members(db_path, gate_id_2)

        # Verify distinct chain_ids between gates
        chains_1 = {m["chain_id"] for m in members_1}
        chains_2 = {m["chain_id"] for m in members_2}
        assert chains_1.isdisjoint(chains_2)

        # Interleaved completion: flow_1 branch-a, flow_2 branch-a, flow_1 branch-b, flow_2 branch-b
        _complete_task(db_path, planspace, members_1[0]["leaf_task_id"])

        # Gate 1 should still be open
        assert _query_gate(db_path, gate_id_1)["status"] == "open"
        # Gate 2 should still be open
        assert _query_gate(db_path, gate_id_2)["status"] == "open"

        _complete_task(db_path, planspace, members_2[0]["leaf_task_id"])

        # Both gates still open (each has 1 of 2 members done)
        assert _query_gate(db_path, gate_id_1)["status"] == "open"
        assert _query_gate(db_path, gate_id_2)["status"] == "open"

        _complete_task(db_path, planspace, members_1[1]["leaf_task_id"])

        # Gate 1 should fire now, gate 2 still open
        assert _query_gate(db_path, gate_id_1)["status"] == "fired"
        assert _query_gate(db_path, gate_id_2)["status"] == "open"

        _complete_task(db_path, planspace, members_2[1]["leaf_task_id"])

        # Both gates fired
        assert _query_gate(db_path, gate_id_1)["status"] == "fired"
        assert _query_gate(db_path, gate_id_2)["status"] == "fired"

        # Verify each gate's aggregate only contains its own members
        agg_1 = json.loads(
            (planspace / _query_gate(db_path, gate_id_1)["aggregate_manifest_path"]).read_text()
        )
        agg_2 = json.loads(
            (planspace / _query_gate(db_path, gate_id_2)["aggregate_manifest_path"]).read_text()
        )

        agg_1_chains = {m["chain_id"] for m in agg_1["members"]}
        agg_2_chains = {m["chain_id"] for m in agg_2["members"]}
        assert agg_1_chains == chains_1
        assert agg_2_chains == chains_2
        assert agg_1_chains.isdisjoint(agg_2_chains)

        # Verify each synthesis task has the right flow_id
        syn_1 = _query_task(db_path, _query_gate(db_path, gate_id_1)["fired_task_id"])
        syn_2 = _query_task(db_path, _query_gate(db_path, gate_id_2)["fired_task_id"])
        assert syn_1["flow_id"] == "flow_concurrent_1"
        assert syn_2["flow_id"] == "flow_concurrent_2"


# ===========================================================================
# Scenario 8: Nested synthesis emitting further work
# ===========================================================================

class TestNestedSynthesisEmittingWork:
    """Synthesis task can emit a continuation that creates more tasks."""

    def test_synthesis_continuation_creates_nested_chain(
        self, db_path: Path, planspace: Path,
    ) -> None:
        """Fanout -> gate fires -> synthesis created -> synthesis writes
        a continuation (another chain) -> continuation is processed."""
        branches = [
            BranchSpec(
                label="only",
                steps=[TaskSpec(task_type="staleness.alignment_check")],
            ),
        ]
        gate_id = submit_fanout(
            FlowEnvelope(db_path=db_path, submitted_by="test-agent", flow_id="flow_nested", planspace=planspace),
            branches,
            gate=GateSpec(
                mode="all",
                failure_policy="include",
                synthesis=TaskSpec(
                    task_type="signals.impact_analysis",
                    concern_scope="nested-scope",
                ),
            ),
        )

        # Complete the branch
        all_tasks = _query_all_tasks(db_path)
        branch_tid = all_tasks[0]["id"]
        _complete_task(db_path, planspace, branch_tid)

        # Gate should fire, synthesis task created
        gate = _query_gate(db_path, gate_id)
        assert gate["status"] == "fired"
        syn_tid = gate["fired_task_id"]
        assert syn_tid is not None

        syn_task = _query_task(db_path, syn_tid)
        assert syn_task["task_type"] == "signals.impact_analysis"
        assert syn_task["trigger_gate_id"] == gate_id

        # Now simulate the synthesis task completing with a continuation
        # that creates a new chain of 2 follow-up tasks
        _mark_task_running(db_path, syn_tid)
        syn_output = _simulate_agent_output(planspace, syn_tid, "synthesis result")
        _mark_task_complete_db(db_path, syn_tid)

        _write_continuation(planspace, syn_tid, {
            "version": 2,
            "actions": [{
                "kind": "chain",
                "steps": [
                    {"task_type": "coordination.consequence_triage"},
                    {"task_type": "coordination.fix"},
                ],
            }],
        })

        reconcile_task_completion(
            db_path, planspace, syn_tid, "complete", syn_output,
        )

        # Verify follow-up tasks were created
        all_tasks_final = _query_all_tasks(db_path)
        # Original branch (1) + synthesis (1) + follow-up chain (2) = 4
        assert len(all_tasks_final) == 4

        # The follow-up tasks should share the same flow_id
        follow_up = [
            t for t in all_tasks_final
            if t["id"] != branch_tid and t["id"] != syn_tid
        ]
        assert len(follow_up) == 2
        assert follow_up[0]["task_type"] == "coordination.consequence_triage"
        assert follow_up[1]["task_type"] == "coordination.fix"
        assert follow_up[0]["flow_id"] == "flow_nested"
        assert follow_up[1]["flow_id"] == "flow_nested"

        assert _dependency_ids(db_path, follow_up[0]["id"]) == [syn_tid]
        assert _dependency_ids(db_path, follow_up[1]["id"]) == [follow_up[0]["id"]]

        # Complete the follow-ups
        _complete_task(db_path, planspace, follow_up[0]["id"])
        _complete_task(db_path, planspace, follow_up[1]["id"])

        # All tasks should now have result manifests
        for t in _query_all_tasks(db_path):
            if t["result_manifest_path"]:
                mpath = planspace / t["result_manifest_path"]
                assert mpath.exists(), f"Missing manifest for task {t['id']}"

    def test_synthesis_continuation_creates_nested_fanout(
        self, db_path: Path, planspace: Path,
    ) -> None:
        """Synthesis task emits a fanout continuation -- proves nesting works
        for fanout structures, not just chains."""
        branches = [
            BranchSpec(
                label="single",
                steps=[TaskSpec(task_type="staleness.alignment_check")],
            ),
        ]
        gate_id = submit_fanout(
            FlowEnvelope(db_path=db_path, submitted_by="test-agent", flow_id="flow_nested_fanout", planspace=planspace),
            branches,
            gate=GateSpec(
                mode="all",
                failure_policy="include",
                synthesis=TaskSpec(task_type="signals.impact_analysis"),
            ),
        )

        # Complete branch -> gate fires -> synthesis created
        branch_tid = _query_all_tasks(db_path)[0]["id"]
        _complete_task(db_path, planspace, branch_tid)

        gate = _query_gate(db_path, gate_id)
        syn_tid = gate["fired_task_id"]

        # Synthesis writes a fanout continuation
        _mark_task_running(db_path, syn_tid)
        syn_output = _simulate_agent_output(planspace, syn_tid)
        _mark_task_complete_db(db_path, syn_tid)

        _write_continuation(planspace, syn_tid, {
            "version": 2,
            "actions": [{
                "kind": "fanout",
                "branches": [
                    {
                        "label": "nested-a",
                        "steps": [{"task_type": "coordination.consequence_triage"}],
                    },
                    {
                        "label": "nested-b",
                        "steps": [{"task_type": "coordination.fix"}],
                    },
                ],
                "gate": {
                    "mode": "all",
                    "failure_policy": "include",
                },
            }],
        })

        reconcile_task_completion(
            db_path, planspace, syn_tid, "complete", syn_output,
        )

        # Should have created 2 nested branch tasks
        all_tasks = _query_all_tasks(db_path)
        # Original branch (1) + synthesis (1) + 2 nested branches = 4
        assert len(all_tasks) == 4

        # A second gate should have been created for the nested fanout
        all_gates = _query_all_gates(db_path)
        assert len(all_gates) == 2  # original + nested

        nested_gate = [g for g in all_gates if g["gate_id"] != gate_id][0]
        assert nested_gate["flow_id"] == "flow_nested_fanout"
        assert nested_gate["expected_count"] == 2

        # Complete both nested branches
        nested_members = _query_gate_members(db_path, nested_gate["gate_id"])
        for m in nested_members:
            _complete_task(db_path, planspace, m["leaf_task_id"])

        # Nested gate should be ready (no synthesis on nested gate)
        nested_gate_after = _query_gate(db_path, nested_gate["gate_id"])
        assert nested_gate_after["status"] == "ready"
