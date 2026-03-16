from __future__ import annotations

import json
import sqlite3
import subprocess
from pathlib import Path

from _paths import DB_SH
from flow.types.context import FlowEnvelope
from flow.types.schema import BranchSpec, GateSpec, TaskSpec
from src.orchestrator.path_registry import PathRegistry
from src.signals.repository.artifact_io import write_json
from containers import ArtifactIOService, HasherService
from src.research.engine.orchestrator import ResearchOrchestrator
from src.flow.engine.reconciler import (
    build_gate_aggregate_manifest,
    build_result_manifest,
)
from containers import Services


def submit_chain(env, steps, **kwargs):
    return Services.flow_ingestion().submit_chain(env, steps, **kwargs)


def submit_fanout(env, branches, **kwargs):
    return Services.flow_ingestion().submit_fanout(env, branches, **kwargs)


def reconcile_task_completion(db_path, planspace, task_id, status, output_path, **kwargs):
    from flow.engine.flow_submitter import FlowSubmitter
    from flow.engine.reconciler import Reconciler
    from flow.repository.flow_context_store import FlowContextStore
    from flow.repository.gate_repository import GateRepository
    from implementation.service.traceability_writer import TraceabilityWriter
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


def _init_db(db_path: Path) -> None:
    subprocess.run(
        ["bash", str(DB_SH), "init", str(db_path)],
        check=True,
        capture_output=True,
        text=True,
    )


def _query_task(db_path: Path, task_id: int) -> dict:
    conn = sqlite3.connect(str(db_path), timeout=5.0)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("SELECT * FROM tasks WHERE id=?", (task_id,))
    row = cur.fetchone()
    conn.close()
    return dict(row)


def _update_task_status(db_path: Path, task_id: int, status: str) -> None:
    conn = sqlite3.connect(str(db_path), timeout=5.0)
    conn.execute(
        "UPDATE tasks SET status=?, completed_at=datetime('now') WHERE id=?",
        (status, task_id),
    )
    conn.commit()
    conn.close()


def test_manifest_builders_keep_existing_shape() -> None:
    result = build_result_manifest(
        task_id=1,
        instance_id="inst_1",
        flow_id="flow_1",
        chain_id="chain_1",
        task_type="staleness.alignment_check",
        status="complete",
        output_path="out.md",
        error=None,
    )
    aggregate = build_gate_aggregate_manifest(
        gate_id="gate_1",
        flow_id="flow_1",
        mode="all",
        failure_policy="include",
        origin_refs=["ref-1"],
        members=[{"chain_id": "chain_1"}],
    )

    assert result["task_id"] == 1
    assert result["status"] == "complete"
    assert aggregate["gate_id"] == "gate_1"
    assert aggregate["members"] == [{"chain_id": "chain_1"}]


def test_reconcile_task_completion_writes_result_manifest(tmp_path) -> None:
    db_path = tmp_path / "test.db"
    planspace = tmp_path / "planspace"
    planspace.mkdir()
    PathRegistry(planspace).ensure_artifacts_tree()
    _init_db(db_path)

    [task_id] = submit_chain(
        FlowEnvelope(db_path=db_path, submitted_by="tester", planspace=planspace),
        [TaskSpec(task_type="staleness.alignment_check")],
    )
    _update_task_status(db_path, task_id, "complete")

    reconcile_task_completion(
        db_path,
        planspace,
        task_id,
        "complete",
        "artifacts/out.md",
    )

    task = _query_task(db_path, task_id)
    manifest = json.loads(
        (planspace / task["result_manifest_path"]).read_text(encoding="utf-8")
    )
    assert manifest["task_id"] == task_id
    assert manifest["status"] == "complete"


def test_reconcile_task_completion_extends_chain_from_continuation(tmp_path) -> None:
    db_path = tmp_path / "test.db"
    planspace = tmp_path / "planspace"
    planspace.mkdir()
    PathRegistry(planspace).ensure_artifacts_tree()
    _init_db(db_path)

    [task_id] = submit_chain(
        FlowEnvelope(db_path=db_path, submitted_by="tester", planspace=planspace),
        [TaskSpec(task_type="staleness.alignment_check")],
    )
    continuation_path = planspace / f"artifacts/flows/task-{task_id}-continuation.json"
    continuation_path.write_text(
        json.dumps(
            {
                "version": 2,
                "actions": [
                    {
                        "kind": "chain",
                        "steps": [{"task_type": "signals.impact_analysis"}],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    _update_task_status(db_path, task_id, "complete")

    reconcile_task_completion(db_path, planspace, task_id, "complete", None)

    conn = sqlite3.connect(str(db_path), timeout=5.0)
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT * FROM tasks ORDER BY id").fetchall()
    conn.close()
    assert len(rows) == 2
    assert rows[1]["depends_on"] == str(task_id)


def test_reconcile_task_completion_runs_research_plan_executor(
    tmp_path,
    monkeypatch,
) -> None:
    db_path = tmp_path / "test.db"
    planspace = tmp_path / "planspace"
    planspace.mkdir()
    PathRegistry(planspace).ensure_artifacts_tree()
    _init_db(db_path)

    [task_id] = submit_chain(
        FlowEnvelope(db_path=db_path, submitted_by="tester", planspace=planspace),
        [
            TaskSpec(
                task_type="research.plan",
                concern_scope="section-03",
                payload_path=str(planspace / "artifacts" / "research-plan-03-prompt.md"),
            )
        ],
    )
    _update_task_status(db_path, task_id, "complete")
    called: list[tuple[str, Path | None]] = []

    monkeypatch.setattr(
        "containers.ResearchOrchestratorService.execute_plan",
        lambda self, section_number, ps, codespace, plan_output_path: (
            called.append((section_number, codespace)) or True
        ),
    )

    reconcile_task_completion(
        db_path,
        planspace,
        task_id,
        "complete",
        str(planspace / "artifacts" / "task-1-output.md"),
        codespace=tmp_path / "codespace",
    )

    assert called == [("03", tmp_path / "codespace")]


def test_reconcile_task_completion_submits_research_verify_after_synthesis(
    tmp_path,
) -> None:
    db_path = tmp_path / "test.db"
    planspace = tmp_path / "planspace"
    planspace.mkdir()
    PathRegistry(planspace).ensure_artifacts_tree()
    _init_db(db_path)

    research_dir = (
        planspace
        / "artifacts"
        / "research"
        / "sections"
        / "section-03"
    )
    research_dir.mkdir(parents=True, exist_ok=True)
    write_json(
        research_dir / "research-plan.json",
        {
            "section": "03",
            "tickets": [{"ticket_id": "T-01"}],
            "flow": {"parallel_groups": [["T-01"]], "verify_claims": True},
        },
    )
    ResearchOrchestrator(
        hasher=HasherService(),
        artifact_io=ArtifactIOService(),
    ).write_research_status(
        "03",
        planspace,
        "tickets_submitted",
        trigger_hash="hash-03",
        cycle_id="cycle-03",
    )

    [task_id] = submit_chain(
        FlowEnvelope(db_path=db_path, submitted_by="tester", planspace=planspace),
        [
            TaskSpec(
                task_type="research.synthesis",
                concern_scope="section-03",
                payload_path=str(planspace / "artifacts" / "research-synthesis-03-prompt.md"),
            )
        ],
    )
    _update_task_status(db_path, task_id, "complete")

    reconcile_task_completion(
        db_path,
        planspace,
        task_id,
        "complete",
        str(planspace / "artifacts" / "task-synthesis-output.md"),
    )

    conn = sqlite3.connect(str(db_path), timeout=5.0)
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT * FROM tasks ORDER BY id").fetchall()
    conn.close()
    assert [row["task_type"] for row in rows] == [
        "research.synthesis",
        "research.verify",
    ]
    status = json.loads(
        (research_dir / "research-status.json").read_text(encoding="utf-8")
    )
    assert status["status"] == "verifying"


def test_reconcile_task_completion_records_post_impl_debt_signal(tmp_path) -> None:
    db_path = tmp_path / "test.db"
    planspace = tmp_path / "planspace"
    planspace.mkdir()
    PathRegistry(planspace).ensure_artifacts_tree()
    _init_db(db_path)

    prompt_path = planspace / "artifacts" / "post-impl-01-prompt.md"
    prompt_path.write_text("# prompt\n", encoding="utf-8")

    trace_path = planspace / "artifacts" / "trace" / "section-01.json"
    trace_path.write_text(
        json.dumps(
            {
                "section": "01",
                "governance": {
                    "packet_path": "",
                    "packet_hash": "",
                    "problem_ids": [],
                    "pattern_ids": [],
                    "profile_id": "",
                },
            }
        ),
        encoding="utf-8",
    )

    assessment_path = (
        planspace
        / "artifacts"
        / "governance"
        / "section-01-post-impl-assessment.json"
    )
    assessment_path.write_text(
        json.dumps(
            {
                "section": "01",
                "verdict": "accept_with_debt",
                "lenses": {},
                "debt_items": ["watch coupling"],
                "refactor_reasons": [],
                "problem_ids_addressed": ["PRB-0009"],
                "pattern_ids_followed": ["PAT-0003"],
                "profile_id": "PHI-global",
            }
        ),
        encoding="utf-8",
    )

    [task_id] = submit_chain(
        FlowEnvelope(db_path=db_path, submitted_by="tester", planspace=planspace),
        [
            TaskSpec(
                task_type="implementation.post_assessment",
                concern_scope="section-01",
                payload_path=str(prompt_path),
            )
        ],
    )
    _update_task_status(db_path, task_id, "complete")

    reconcile_task_completion(db_path, planspace, task_id, "complete", None)

    trace = json.loads(trace_path.read_text(encoding="utf-8"))
    debt_signal = json.loads(
        (
            planspace
            / "artifacts"
            / "signals"
            / "section-01-risk-register-signal.json"
        ).read_text(encoding="utf-8")
    )
    assert trace["governance"]["problem_ids"] == ["PRB-0009"]
    assert trace["governance"]["pattern_ids"] == ["PAT-0003"]
    assert trace["governance"]["profile_id"] == "PHI-global"
    assert debt_signal["debt_items"] == ["watch coupling"]


def test_reconcile_task_completion_emits_post_impl_refactor_blocker(tmp_path) -> None:
    db_path = tmp_path / "test.db"
    planspace = tmp_path / "planspace"
    planspace.mkdir()
    PathRegistry(planspace).ensure_artifacts_tree()
    _init_db(db_path)

    prompt_path = planspace / "artifacts" / "post-impl-02-prompt.md"
    prompt_path.parent.mkdir(parents=True, exist_ok=True)
    prompt_path.write_text("# prompt\n", encoding="utf-8")

    trace_path = planspace / "artifacts" / "trace" / "section-02.json"
    trace_path.parent.mkdir(parents=True, exist_ok=True)
    trace_path.write_text(
        json.dumps(
            {
                "section": "02",
                "governance": {
                    "packet_path": "",
                    "packet_hash": "",
                    "problem_ids": [],
                    "pattern_ids": [],
                    "profile_id": "",
                },
            }
        ),
        encoding="utf-8",
    )

    assessment_path = (
        planspace
        / "artifacts"
        / "governance"
        / "section-02-post-impl-assessment.json"
    )
    assessment_path.write_text(
        json.dumps(
            {
                "section": "02",
                "verdict": "refactor_required",
                "lenses": {},
                "debt_items": [],
                "refactor_reasons": ["pattern drift"],
                "problem_ids_addressed": ["PRB-0010"],
                "pattern_ids_followed": [],
                "profile_id": "PHI-global",
            }
        ),
        encoding="utf-8",
    )

    [task_id] = submit_chain(
        FlowEnvelope(db_path=db_path, submitted_by="tester", planspace=planspace),
        [
            TaskSpec(
                task_type="implementation.post_assessment",
                concern_scope="section-02",
                payload_path=str(prompt_path),
            )
        ],
    )
    _update_task_status(db_path, task_id, "complete")

    reconcile_task_completion(db_path, planspace, task_id, "complete", None)

    blocker = json.loads(
        (
            planspace
            / "artifacts"
            / "signals"
            / "section-02-post-impl-blocker.json"
        ).read_text(encoding="utf-8")
    )
    assert blocker["blocker_type"] == "post_impl_refactor_required"
    assert blocker["refactor_reasons"] == ["pattern drift"]


# ------------------------------------------------------------------
# Gate-fire reconciliation: task status consistency
# ------------------------------------------------------------------


def test_reconcile_member_task_statuses_patches_running_leaf(tmp_path) -> None:
    """_reconcile_member_task_statuses forces a stuck 'running' leaf to 'complete'."""
    from flow.repository.gate_repository import GateRepository

    db_path = tmp_path / "test.db"
    _init_db(db_path)

    # Insert a task and leave it in 'running' status.
    conn = sqlite3.connect(str(db_path), timeout=5.0)
    conn.execute(
        "INSERT INTO tasks(id, submitted_by, task_type, status) "
        "VALUES(58, 'tester', 'research.synthesis', 'running')",
    )
    conn.commit()

    members = [
        {"leaf_task_id": 58, "status": "complete", "chain_id": "c1"},
    ]
    patched = GateRepository._reconcile_member_task_statuses(conn, members)
    conn.close()

    assert patched == 1
    task = _query_task(db_path, 58)
    assert task["status"] == "complete"
    assert task["completed_at"] is not None


def test_reconcile_member_task_statuses_noop_when_already_terminal(tmp_path) -> None:
    """No patch when the leaf task is already in a terminal state."""
    from flow.repository.gate_repository import GateRepository

    db_path = tmp_path / "test.db"
    _init_db(db_path)

    conn = sqlite3.connect(str(db_path), timeout=5.0)
    conn.execute(
        "INSERT INTO tasks(id, submitted_by, task_type, status) "
        "VALUES(59, 'tester', 'research.synthesis', 'complete')",
    )
    conn.commit()

    members = [
        {"leaf_task_id": 59, "status": "complete", "chain_id": "c1"},
    ]
    patched = GateRepository._reconcile_member_task_statuses(conn, members)
    conn.close()

    assert patched == 0


def test_gate_fire_reconciles_stuck_running_task(tmp_path) -> None:
    """End-to-end: gate fires and patches a leaf task still stuck in 'running'."""
    db_path = tmp_path / "test.db"
    planspace = tmp_path / "planspace"
    planspace.mkdir()
    PathRegistry(planspace).ensure_artifacts_tree()
    _init_db(db_path)

    # Create a fanout with two branches and a synthesis gate.
    gate_id = submit_fanout(
        FlowEnvelope(
            db_path=db_path, submitted_by="tester",
            flow_id="flow_test", planspace=planspace,
        ),
        [
            BranchSpec(label="a", steps=[TaskSpec(task_type="research.ticket")]),
            BranchSpec(label="b", steps=[TaskSpec(task_type="research.ticket")]),
        ],
        gate=GateSpec(
            mode="all",
            failure_policy="include",
            synthesis=TaskSpec(
                task_type="research.synthesis",
                concern_scope="section-06",
            ),
        ),
    )
    assert gate_id is not None

    # Identify the two branch tasks.
    conn = sqlite3.connect(str(db_path), timeout=5.0)
    conn.row_factory = sqlite3.Row
    tasks = [
        dict(r)
        for r in conn.execute(
            "SELECT * FROM tasks ORDER BY id"
        ).fetchall()
    ]
    members = [
        dict(r)
        for r in conn.execute(
            "SELECT * FROM gate_members WHERE gate_id=? ORDER BY slot_label",
            (gate_id,),
        ).fetchall()
    ]
    conn.close()

    assert len(tasks) == 2
    assert len(members) == 2
    task_a_id = members[0]["leaf_task_id"]
    task_b_id = members[1]["leaf_task_id"]

    # Complete task A normally (sets tasks.status='complete').
    _update_task_status(db_path, task_a_id, "running")
    _update_task_status(db_path, task_a_id, "complete")
    reconcile_task_completion(
        db_path, planspace, task_a_id, "complete", "artifacts/a-out.md",
    )

    # Simulate the bug for task B: the gate member is marked complete,
    # but the task row is stuck in 'running' (dispatcher crashed before
    # calling complete_task).
    _update_task_status(db_path, task_b_id, "running")
    # Manually mark the gate member as complete without touching task status.
    conn = sqlite3.connect(str(db_path), timeout=5.0)
    conn.execute(
        "UPDATE gate_members SET status='complete', "
        "completed_at=datetime('now') WHERE gate_id=? AND chain_id=?",
        (gate_id, members[1]["chain_id"]),
    )
    conn.commit()
    conn.close()

    # Verify the task is still 'running' before the gate fires.
    assert _query_task(db_path, task_b_id)["status"] == "running"

    # Trigger check_and_fire_gate via the reconciler (mimics the path
    # that happens when the last member completes).
    from flow.repository.gate_repository import GateRepository
    gate_repo = GateRepository(ArtifactIOService())
    gate_repo.check_and_fire_gate(
        db_path, planspace, gate_id, "flow_test", [],
        build_gate_aggregate_manifest,
    )

    # After the gate fires, the stuck task should be reconciled.
    task_b = _query_task(db_path, task_b_id)
    assert task_b["status"] == "complete", (
        f"Expected task {task_b_id} to be 'complete' after gate fire, "
        f"got '{task_b['status']}'"
    )
    assert task_b["completed_at"] is not None

    # Verify the gate actually fired (synthesis task created).
    conn = sqlite3.connect(str(db_path), timeout=5.0)
    conn.row_factory = sqlite3.Row
    gate_row = dict(
        conn.execute(
            "SELECT * FROM gates WHERE gate_id=?", (gate_id,)
        ).fetchone()
    )
    all_tasks = conn.execute("SELECT * FROM tasks ORDER BY id").fetchall()
    conn.close()

    assert gate_row["status"] == "fired"
    # Original 2 branch tasks + 1 synthesis task
    assert len(all_tasks) == 3
    assert all_tasks[-1]["task_type"] == "research.synthesis"
