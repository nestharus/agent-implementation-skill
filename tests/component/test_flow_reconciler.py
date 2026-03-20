from __future__ import annotations

import json
import sqlite3
import subprocess
from pathlib import Path

from _paths import DB_SH
from flow.types.context import FlowEnvelope
from flow.types.schema import BranchSpec, GateSpec, TaskSpec
from src.flow.service.task_db_client import init_db as init_task_db
from src.orchestrator.path_registry import PathRegistry
from src.orchestrator.engine.section_state_machine import (
    SectionState,
    get_section_state,
    set_section_state,
)
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


def test_reconcile_task_completion_routes_vertical_misalignment_into_state_machine(
    tmp_path,
) -> None:
    db_path = tmp_path / "test.db"
    planspace = tmp_path / "planspace"
    planspace.mkdir()
    PathRegistry(planspace).ensure_artifacts_tree()
    _init_db(db_path)

    set_section_state(
        db_path,
        "01",
        SectionState.ASSESSING,
        parent_section="00",
        scope_grant="Only delegated auth changes.",
    )

    [task_id] = submit_chain(
        FlowEnvelope(db_path=db_path, submitted_by="tester", planspace=planspace),
        [TaskSpec(task_type="section.assess", concern_scope="section-01")],
    )
    _update_task_status(db_path, task_id, "complete")

    output_path = planspace / "artifacts" / "task-1-output.md"
    output_path.write_text(
        '{"frame_ok": true, "aligned": false, '
        '"problems": ["Violates parent scope grant"], '
        '"vertical_misalignment": true}\n',
        encoding="utf-8",
    )

    reconcile_task_completion(
        db_path,
        planspace,
        task_id,
        "complete",
        "artifacts/task-1-output.md",
    )

    assert get_section_state(db_path, "01") == SectionState.PROPOSING


def test_reconcile_task_completion_routes_accepted_risk_outcome_into_state_machine(
    tmp_path,
) -> None:
    db_path = tmp_path / "test.db"
    planspace = tmp_path / "planspace"
    planspace.mkdir()
    paths = PathRegistry(planspace)
    paths.ensure_artifacts_tree()
    _init_db(db_path)

    set_section_state(
        db_path,
        "01",
        SectionState.RISK_EVAL,
        parent_section="00",
        scope_grant="Only delegated auth changes.",
    )

    write_json(
        paths.risk_plan("section-01"),
        {
            "accepted_frontier": ["Implement change"],
            "deferred_steps": [],
            "reopen_steps": [],
        },
    )

    [task_id] = submit_chain(
        FlowEnvelope(db_path=db_path, submitted_by="tester", planspace=planspace),
        [TaskSpec(task_type="section.risk_eval", concern_scope="section-01")],
    )
    _update_task_status(db_path, task_id, "complete")

    reconcile_task_completion(
        db_path,
        planspace,
        task_id,
        "complete",
        None,
    )

    assert get_section_state(db_path, "01") == SectionState.MICROSTRATEGY


def test_reconcile_task_completion_routes_deferred_risk_outcome_into_state_machine(
    tmp_path,
) -> None:
    db_path = tmp_path / "test.db"
    planspace = tmp_path / "planspace"
    planspace.mkdir()
    paths = PathRegistry(planspace)
    paths.ensure_artifacts_tree()
    _init_db(db_path)

    set_section_state(
        db_path,
        "01",
        SectionState.RISK_EVAL,
        parent_section="00",
        scope_grant="Only delegated auth changes.",
    )

    write_json(
        paths.risk_plan("section-01"),
        {
            "accepted_frontier": [],
            "deferred_steps": ["Need more research"],
            "reopen_steps": [],
        },
    )

    [task_id] = submit_chain(
        FlowEnvelope(db_path=db_path, submitted_by="tester", planspace=planspace),
        [TaskSpec(task_type="section.risk_eval", concern_scope="section-01")],
    )
    _update_task_status(db_path, task_id, "complete")

    reconcile_task_completion(
        db_path,
        planspace,
        task_id,
        "complete",
        None,
    )

    assert get_section_state(db_path, "01") == SectionState.BLOCKED


def test_reconcile_task_completion_routes_reopened_risk_outcome_into_state_machine(
    tmp_path,
) -> None:
    db_path = tmp_path / "test.db"
    planspace = tmp_path / "planspace"
    planspace.mkdir()
    paths = PathRegistry(planspace)
    paths.ensure_artifacts_tree()
    _init_db(db_path)

    set_section_state(
        db_path,
        "01",
        SectionState.RISK_EVAL,
        parent_section="00",
        scope_grant="Only delegated auth changes.",
    )

    write_json(
        paths.risk_plan("section-01"),
        {
            "accepted_frontier": [],
            "deferred_steps": [],
            "reopen_steps": ["Reopen proposal"],
        },
    )

    [task_id] = submit_chain(
        FlowEnvelope(db_path=db_path, submitted_by="tester", planspace=planspace),
        [TaskSpec(task_type="section.risk_eval", concern_scope="section-01")],
    )
    _update_task_status(db_path, task_id, "complete")

    reconcile_task_completion(
        db_path,
        planspace,
        task_id,
        "complete",
        None,
    )

    assert get_section_state(db_path, "01") == SectionState.BLOCKED


def test_reconcile_task_completion_fail_closes_missing_risk_plan_into_blocked(
    tmp_path,
) -> None:
    db_path = tmp_path / "test.db"
    planspace = tmp_path / "planspace"
    planspace.mkdir()
    PathRegistry(planspace).ensure_artifacts_tree()
    _init_db(db_path)

    set_section_state(
        db_path,
        "01",
        SectionState.RISK_EVAL,
        parent_section="00",
        scope_grant="Only delegated auth changes.",
    )

    [task_id] = submit_chain(
        FlowEnvelope(db_path=db_path, submitted_by="tester", planspace=planspace),
        [TaskSpec(task_type="section.risk_eval", concern_scope="section-01")],
    )
    _update_task_status(db_path, task_id, "complete")

    reconcile_task_completion(
        db_path,
        planspace,
        task_id,
        "complete",
        None,
    )

    assert get_section_state(db_path, "01") == SectionState.BLOCKED


def test_reconcile_task_completion_fail_closes_malformed_risk_plan_into_blocked(
    tmp_path,
) -> None:
    db_path = tmp_path / "test.db"
    planspace = tmp_path / "planspace"
    planspace.mkdir()
    paths = PathRegistry(planspace)
    paths.ensure_artifacts_tree()
    _init_db(db_path)

    set_section_state(
        db_path,
        "01",
        SectionState.RISK_EVAL,
        parent_section="00",
        scope_grant="Only delegated auth changes.",
    )

    paths.risk_plan("section-01").write_text("{not valid json}\n", encoding="utf-8")

    [task_id] = submit_chain(
        FlowEnvelope(db_path=db_path, submitted_by="tester", planspace=planspace),
        [TaskSpec(task_type="section.risk_eval", concern_scope="section-01")],
    )
    _update_task_status(db_path, task_id, "complete")

    reconcile_task_completion(
        db_path,
        planspace,
        task_id,
        "complete",
        None,
    )

    assert get_section_state(db_path, "01") == SectionState.BLOCKED


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
# Section pipeline task chain: section.propose completion
# ------------------------------------------------------------------


def _write_proposal_state(planspace: Path, section_number: str, *, ready: bool = True) -> None:
    """Write a minimal proposal-state artifact for readiness resolution."""
    paths = PathRegistry(planspace)
    state = {
        "resolved_anchors": [],
        "unresolved_anchors": [],
        "resolved_contracts": [],
        "unresolved_contracts": [],
        "research_questions": [],
        "blocking_research_questions": [] if ready else ["What is the API?"],
        "user_root_questions": [],
        "new_section_candidates": [],
        "shared_seam_candidates": [],
        "execution_ready": ready,
        "readiness_rationale": "all clear" if ready else "has blockers",
        "problem_ids": [],
        "pattern_ids": [],
        "profile_id": "",
        "pattern_deviations": [],
        "governance_questions": [],
    }
    write_json(paths.proposal_state(section_number), state)


def test_section_propose_complete_submits_impl_chain_when_ready(tmp_path) -> None:
    """When section.propose completes and the section is execution-ready,
    the reconciler submits section.implement -> section.verify chain."""
    db_path = tmp_path / "test.db"
    planspace = tmp_path / "planspace"
    planspace.mkdir()
    PathRegistry(planspace).ensure_artifacts_tree()
    _init_db(db_path)

    # Write a ready proposal-state artifact
    _write_proposal_state(planspace, "04", ready=True)

    prompt_path = planspace / "artifacts" / "proposal-04-prompt.md"
    prompt_path.write_text("# proposal prompt\n", encoding="utf-8")

    [task_id] = submit_chain(
        FlowEnvelope(db_path=db_path, submitted_by="tester", planspace=planspace),
        [
            TaskSpec(
                task_type="section.propose",
                concern_scope="section-04",
                payload_path=str(prompt_path),
            ),
        ],
    )
    _update_task_status(db_path, task_id, "complete")

    reconcile_task_completion(db_path, planspace, task_id, "complete", None)

    # The reconciler should have submitted section.implement and section.verify
    conn = sqlite3.connect(str(db_path), timeout=5.0)
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT * FROM tasks ORDER BY id").fetchall()
    conn.close()

    task_types = [row["task_type"] for row in rows]
    assert "section.implement" in task_types, (
        f"Expected section.implement in {task_types}"
    )
    assert "section.verify" in task_types, (
        f"Expected section.verify in {task_types}"
    )

    # Verify chain ordering: implement before verify
    impl_id = next(r["id"] for r in rows if r["task_type"] == "section.implement")
    verify_row = next(r for r in rows if r["task_type"] == "section.verify")
    assert str(verify_row["depends_on"]) == str(impl_id), (
        f"section.verify should depend on section.implement (task {impl_id})"
    )

    # Verify the readiness artifact was written
    paths = PathRegistry(planspace)
    readiness_path = paths.execution_ready("04")
    assert readiness_path.exists()
    readiness_data = json.loads(readiness_path.read_text(encoding="utf-8"))
    assert readiness_data["ready"] is True


def test_section_propose_complete_no_chain_when_blocked(tmp_path) -> None:
    """When section.propose completes but the section is NOT execution-ready,
    no follow-on chain is submitted."""
    db_path = tmp_path / "test.db"
    planspace = tmp_path / "planspace"
    planspace.mkdir()
    PathRegistry(planspace).ensure_artifacts_tree()
    _init_db(db_path)

    # Write a blocked proposal-state artifact
    _write_proposal_state(planspace, "05", ready=False)

    prompt_path = planspace / "artifacts" / "proposal-05-prompt.md"
    prompt_path.write_text("# proposal prompt\n", encoding="utf-8")

    [task_id] = submit_chain(
        FlowEnvelope(db_path=db_path, submitted_by="tester", planspace=planspace),
        [
            TaskSpec(
                task_type="section.propose",
                concern_scope="section-05",
                payload_path=str(prompt_path),
            ),
        ],
    )
    _update_task_status(db_path, task_id, "complete")

    reconcile_task_completion(db_path, planspace, task_id, "complete", None)

    # Only the original task should be in the DB — no follow-on chain
    conn = sqlite3.connect(str(db_path), timeout=5.0)
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT * FROM tasks ORDER BY id").fetchall()
    conn.close()

    task_types = [row["task_type"] for row in rows]
    assert task_types == ["section.propose"], (
        f"Expected only section.propose, got {task_types}"
    )


def test_section_propose_complete_no_proposal_state_means_blocked(tmp_path) -> None:
    """When section.propose completes but no proposal-state exists,
    the section defaults to blocked (fail-closed)."""
    db_path = tmp_path / "test.db"
    planspace = tmp_path / "planspace"
    planspace.mkdir()
    PathRegistry(planspace).ensure_artifacts_tree()
    _init_db(db_path)

    # Deliberately do NOT write proposal-state — missing artifact = fail-closed

    prompt_path = planspace / "artifacts" / "proposal-06-prompt.md"
    prompt_path.write_text("# proposal prompt\n", encoding="utf-8")

    [task_id] = submit_chain(
        FlowEnvelope(db_path=db_path, submitted_by="tester", planspace=planspace),
        [
            TaskSpec(
                task_type="section.propose",
                concern_scope="section-06",
                payload_path=str(prompt_path),
            ),
        ],
    )
    _update_task_status(db_path, task_id, "complete")

    reconcile_task_completion(db_path, planspace, task_id, "complete", None)

    # No follow-on chain — missing proposal state is fail-closed
    conn = sqlite3.connect(str(db_path), timeout=5.0)
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT * FROM tasks ORDER BY id").fetchall()
    conn.close()

    task_types = [row["task_type"] for row in rows]
    assert task_types == ["section.propose"], (
        f"Expected only section.propose, got {task_types}"
    )


def test_section_decompose_complete_registers_children_and_advances_parent(tmp_path) -> None:
    db_path = tmp_path / "test.db"
    planspace = tmp_path / "planspace"
    planspace.mkdir()
    paths = PathRegistry(planspace)
    paths.ensure_artifacts_tree()
    _init_db(db_path)
    init_task_db(db_path)

    set_section_state(db_path, "07", SectionState.DECOMPOSING, depth=1)
    paths.section_spec("07").write_text("# Section 07\n", encoding="utf-8")
    paths.section_spec("07.1").write_text("# Section 07.1\n", encoding="utf-8")
    paths.section_spec("07.2").write_text("# Section 07.2\n", encoding="utf-8")

    output_path = planspace / "artifacts" / "section-07-decompose-output.json"
    output_path.write_text(json.dumps({
        "children": [
            {
                "section_number": "07.1",
                "spec_path": "artifacts/sections/section-07.1.md",
                "scope_grant": "Own the event contract and validation boundary.",
            },
            {
                "section_number": "07.2",
                "spec_path": "artifacts/sections/section-07.2.md",
                "scope_grant": "Own persistence and lifecycle integration.",
            },
        ],
    }), encoding="utf-8")

    [task_id] = submit_chain(
        FlowEnvelope(db_path=db_path, submitted_by="tester", planspace=planspace),
        [
            TaskSpec(
                task_type="section.decompose_children",
                concern_scope="section-07",
                payload_path=str(paths.section_spec("07")),
            ),
        ],
    )
    _update_task_status(db_path, task_id, "complete")

    reconcile_task_completion(
        db_path,
        planspace,
        task_id,
        "complete",
        str(output_path),
    )

    assert get_section_state(db_path, "07") == SectionState.AWAITING_CHILDREN

    conn = sqlite3.connect(str(db_path), timeout=5.0)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT section_number, parent_section, depth, scope_grant, spawned_by_state "
        "FROM section_states WHERE parent_section = ? ORDER BY section_number",
        ("07",),
    ).fetchall()
    conn.close()

    assert [row["section_number"] for row in rows] == ["07.1", "07.2"]
    assert all(row["parent_section"] == "07" for row in rows)
    assert all(row["depth"] == 2 for row in rows)
    assert rows[0]["scope_grant"] == "Own the event contract and validation boundary."
    assert rows[1]["scope_grant"] == "Own persistence and lifecycle integration."
    assert all(row["spawned_by_state"] == "decomposing" for row in rows)


# ------------------------------------------------------------------
# Section pipeline task chain: section.implement completion
# ------------------------------------------------------------------


def test_section_implement_complete_writes_signal(tmp_path) -> None:
    """When section.implement completes, the reconciler writes an
    impl-complete signal artifact."""
    db_path = tmp_path / "test.db"
    planspace = tmp_path / "planspace"
    planspace.mkdir()
    PathRegistry(planspace).ensure_artifacts_tree()
    _init_db(db_path)

    prompt_path = planspace / "artifacts" / "impl-03-prompt.md"
    prompt_path.write_text("# impl prompt\n", encoding="utf-8")

    [task_id] = submit_chain(
        FlowEnvelope(db_path=db_path, submitted_by="tester", planspace=planspace),
        [
            TaskSpec(
                task_type="section.implement",
                concern_scope="section-03",
                payload_path=str(prompt_path),
            ),
        ],
    )
    _update_task_status(db_path, task_id, "complete")

    reconcile_task_completion(db_path, planspace, task_id, "complete", None)

    # Verify the impl-complete signal was written
    paths = PathRegistry(planspace)
    signal_path = paths.signals_dir() / "section-03-impl-complete.json"
    assert signal_path.exists(), (
        f"Expected impl-complete signal at {signal_path}"
    )
    signal = json.loads(signal_path.read_text(encoding="utf-8"))
    assert signal["section"] == "03"
    assert signal["status"] == "complete"


def test_section_propose_complete_preserves_payload_in_followon(tmp_path) -> None:
    """The payload_path from the propose task is carried through to
    the implementation and verify tasks."""
    db_path = tmp_path / "test.db"
    planspace = tmp_path / "planspace"
    planspace.mkdir()
    PathRegistry(planspace).ensure_artifacts_tree()
    _init_db(db_path)

    _write_proposal_state(planspace, "07", ready=True)

    prompt_path = planspace / "artifacts" / "section-07-prompt.md"
    prompt_path.write_text("# prompt\n", encoding="utf-8")

    [task_id] = submit_chain(
        FlowEnvelope(db_path=db_path, submitted_by="tester", planspace=planspace),
        [
            TaskSpec(
                task_type="section.propose",
                concern_scope="section-07",
                payload_path=str(prompt_path),
            ),
        ],
    )
    _update_task_status(db_path, task_id, "complete")

    reconcile_task_completion(db_path, planspace, task_id, "complete", None)

    conn = sqlite3.connect(str(db_path), timeout=5.0)
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT * FROM tasks ORDER BY id").fetchall()
    conn.close()

    impl_row = next(r for r in rows if r["task_type"] == "section.implement")
    verify_row = next(r for r in rows if r["task_type"] == "section.verify")

    assert impl_row["payload_path"] == str(prompt_path)
    assert verify_row["payload_path"] == str(prompt_path)
    assert impl_row["concern_scope"] == "section-07"
    assert verify_row["concern_scope"] == "section-07"


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


# ------------------------------------------------------------------
# Bootstrap follow-on chain: classify_entry -> extract_problems + extract_values
# ------------------------------------------------------------------


def test_bootstrap_classify_entry_spawns_extract_followons(tmp_path) -> None:
    """Completing bootstrap.classify_entry triggers two follow-on tasks:
    bootstrap.extract_problems and bootstrap.extract_values.

    Also verifies the dedup guard (RC3): reconciling the same task a
    second time does not create duplicate follow-ons.
    """
    from flow.service.task_db_client import init_db

    db_path = tmp_path / "test.db"
    planspace = tmp_path / "planspace"
    planspace.mkdir()
    PathRegistry(planspace).ensure_artifacts_tree()
    init_db(db_path)

    payload_path = planspace / "artifacts" / "spec.md"
    payload_path.write_text("# spec\n", encoding="utf-8")

    [task_id] = submit_chain(
        FlowEnvelope(db_path=db_path, submitted_by="tester", planspace=planspace),
        [
            TaskSpec(
                task_type="bootstrap.classify_entry",
                concern_scope="bootstrap",
                payload_path=str(payload_path),
            ),
        ],
    )
    _update_task_status(db_path, task_id, "running")
    _update_task_status(db_path, task_id, "complete")

    reconcile_task_completion(db_path, planspace, task_id, "complete", None)

    # Assert two new pending tasks: extract_problems and extract_values
    conn = sqlite3.connect(str(db_path), timeout=5.0)
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT * FROM tasks ORDER BY id").fetchall()
    conn.close()

    followon_types = sorted(
        row["task_type"] for row in rows if row["id"] != task_id
    )
    assert followon_types == [
        "bootstrap.extract_problems",
        "bootstrap.extract_values",
    ], f"Expected extract_problems + extract_values, got {followon_types}"

    # Both should be pending, have payload_path set, and concern_scope=bootstrap
    for row in rows:
        if row["id"] == task_id:
            continue
        assert row["status"] == "pending", (
            f"Follow-on {row['task_type']} should be pending, got {row['status']}"
        )
        assert row["payload_path"] == str(payload_path), (
            f"Follow-on {row['task_type']} missing payload_path"
        )
        assert row["concern_scope"] == "bootstrap", (
            f"Follow-on {row['task_type']} concern_scope should be 'bootstrap'"
        )

    # Dedup guard (RC3): reconciling again should NOT create duplicates
    reconcile_task_completion(db_path, planspace, task_id, "complete", None)

    conn = sqlite3.connect(str(db_path), timeout=5.0)
    conn.row_factory = sqlite3.Row
    rows_after = conn.execute("SELECT * FROM tasks ORDER BY id").fetchall()
    conn.close()

    followon_types_after = sorted(
        row["task_type"] for row in rows_after if row["id"] != task_id
    )
    assert followon_types_after == followon_types, (
        f"Dedup guard failed: expected {followon_types}, got {followon_types_after}"
    )


# ------------------------------------------------------------------
# User interaction chain: confirm_understanding gate + interpret_response
# ------------------------------------------------------------------


def test_confirm_understanding_with_signal_submits_interpret_response(tmp_path) -> None:
    """When confirm_understanding completes and a NEED_DECISION signal exists,
    the reconciler submits bootstrap.interpret_response (not assess_reliability)."""
    from flow.service.task_db_client import init_db

    db_path = tmp_path / "test.db"
    planspace = tmp_path / "planspace"
    planspace.mkdir()
    PathRegistry(planspace).ensure_artifacts_tree()
    init_db(db_path)

    payload_path = planspace / "artifacts" / "spec.md"
    payload_path.write_text("# spec\n", encoding="utf-8")

    # Write the NEED_DECISION signal file
    signal_dir = planspace / "artifacts" / "signals"
    signal_dir.mkdir(parents=True, exist_ok=True)
    signal_path = signal_dir / "confirm-understanding-signal.json"
    write_json(signal_path, {
        "state": "NEED_DECISION",
        "detail": "Exploration findings require user confirmation",
    })

    [task_id] = submit_chain(
        FlowEnvelope(db_path=db_path, submitted_by="tester", planspace=planspace),
        [
            TaskSpec(
                task_type="bootstrap.confirm_understanding",
                concern_scope="bootstrap",
                payload_path=str(payload_path),
            ),
        ],
    )
    _update_task_status(db_path, task_id, "running")
    _update_task_status(db_path, task_id, "complete")

    reconcile_task_completion(db_path, planspace, task_id, "complete", None)

    conn = sqlite3.connect(str(db_path), timeout=5.0)
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT * FROM tasks ORDER BY id").fetchall()
    conn.close()

    followon_types = [
        row["task_type"] for row in rows if row["id"] != task_id
    ]
    assert followon_types == ["bootstrap.interpret_response"], (
        f"Expected interpret_response follow-on, got {followon_types}"
    )


def test_confirm_understanding_without_signal_submits_assess_reliability(tmp_path) -> None:
    """When confirm_understanding completes and no NEED_DECISION signal exists,
    the reconciler submits bootstrap.assess_reliability directly."""
    from flow.service.task_db_client import init_db

    db_path = tmp_path / "test.db"
    planspace = tmp_path / "planspace"
    planspace.mkdir()
    PathRegistry(planspace).ensure_artifacts_tree()
    init_db(db_path)

    payload_path = planspace / "artifacts" / "spec.md"
    payload_path.write_text("# spec\n", encoding="utf-8")

    # No signal file written — agent absorbed all findings

    [task_id] = submit_chain(
        FlowEnvelope(db_path=db_path, submitted_by="tester", planspace=planspace),
        [
            TaskSpec(
                task_type="bootstrap.confirm_understanding",
                concern_scope="bootstrap",
                payload_path=str(payload_path),
            ),
        ],
    )
    _update_task_status(db_path, task_id, "running")
    _update_task_status(db_path, task_id, "complete")

    reconcile_task_completion(db_path, planspace, task_id, "complete", None)

    conn = sqlite3.connect(str(db_path), timeout=5.0)
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT * FROM tasks ORDER BY id").fetchall()
    conn.close()

    followon_types = [
        row["task_type"] for row in rows if row["id"] != task_id
    ]
    assert followon_types == ["bootstrap.assess_reliability"], (
        f"Expected assess_reliability follow-on, got {followon_types}"
    )


def test_interpret_response_with_valid_response_submits_assess_reliability(tmp_path) -> None:
    """When interpret_response completes and user-response.json is valid,
    the reconciler submits bootstrap.assess_reliability."""
    from flow.service.task_db_client import init_db

    db_path = tmp_path / "test.db"
    planspace = tmp_path / "planspace"
    planspace.mkdir()
    PathRegistry(planspace).ensure_artifacts_tree()
    init_db(db_path)

    payload_path = planspace / "artifacts" / "spec.md"
    payload_path.write_text("# spec\n", encoding="utf-8")

    # Write a valid user-response.json
    global_dir = planspace / "artifacts" / "global"
    global_dir.mkdir(parents=True, exist_ok=True)
    write_json(global_dir / "user-response.json", {
        "confirmed_problems": [{"problem_id": "PRB-001"}],
        "corrected_problems": [],
        "new_problems": [],
        "confirmed_values": [],
        "corrected_values": [],
        "new_context": "",
    })

    [task_id] = submit_chain(
        FlowEnvelope(db_path=db_path, submitted_by="tester", planspace=planspace),
        [
            TaskSpec(
                task_type="bootstrap.interpret_response",
                concern_scope="bootstrap",
                payload_path=str(payload_path),
            ),
        ],
    )
    _update_task_status(db_path, task_id, "running")
    _update_task_status(db_path, task_id, "complete")

    reconcile_task_completion(db_path, planspace, task_id, "complete", None)

    conn = sqlite3.connect(str(db_path), timeout=5.0)
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT * FROM tasks ORDER BY id").fetchall()
    conn.close()

    followon_types = [
        row["task_type"] for row in rows if row["id"] != task_id
    ]
    assert followon_types == ["bootstrap.assess_reliability"], (
        f"Expected assess_reliability follow-on, got {followon_types}"
    )


def test_interpret_response_with_malformed_response_fails_closed(tmp_path) -> None:
    """When interpret_response completes but user-response.json is malformed,
    the reconciler does NOT submit assess_reliability (fail-closed) and
    preserves the malformed file per PAT-0001."""
    from flow.service.task_db_client import init_db

    db_path = tmp_path / "test.db"
    planspace = tmp_path / "planspace"
    planspace.mkdir()
    PathRegistry(planspace).ensure_artifacts_tree()
    init_db(db_path)

    payload_path = planspace / "artifacts" / "spec.md"
    payload_path.write_text("# spec\n", encoding="utf-8")

    # Write a malformed user-response.json (missing required keys)
    global_dir = planspace / "artifacts" / "global"
    global_dir.mkdir(parents=True, exist_ok=True)
    response_path = global_dir / "user-response.json"
    write_json(response_path, {"incomplete": True})

    [task_id] = submit_chain(
        FlowEnvelope(db_path=db_path, submitted_by="tester", planspace=planspace),
        [
            TaskSpec(
                task_type="bootstrap.interpret_response",
                concern_scope="bootstrap",
                payload_path=str(payload_path),
            ),
        ],
    )
    _update_task_status(db_path, task_id, "running")
    _update_task_status(db_path, task_id, "complete")

    reconcile_task_completion(db_path, planspace, task_id, "complete", None)

    # No follow-on task should be submitted
    conn = sqlite3.connect(str(db_path), timeout=5.0)
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT * FROM tasks ORDER BY id").fetchall()
    conn.close()

    followon_types = [
        row["task_type"] for row in rows if row["id"] != task_id
    ]
    assert followon_types == [], (
        f"Expected no follow-on tasks (fail-closed), got {followon_types}"
    )

    # The original file should have been renamed to .malformed.json
    assert not response_path.exists(), (
        "Malformed user-response.json should have been renamed"
    )
    malformed_path = global_dir / "user-response.malformed.json"
    assert malformed_path.exists(), (
        "Malformed file should be preserved at user-response.malformed.json"
    )


def test_interpret_response_with_missing_response_fails_closed(tmp_path) -> None:
    """When interpret_response completes but user-response.json does not exist,
    the reconciler does NOT submit assess_reliability (fail-closed)."""
    from flow.service.task_db_client import init_db

    db_path = tmp_path / "test.db"
    planspace = tmp_path / "planspace"
    planspace.mkdir()
    PathRegistry(planspace).ensure_artifacts_tree()
    init_db(db_path)

    payload_path = planspace / "artifacts" / "spec.md"
    payload_path.write_text("# spec\n", encoding="utf-8")

    # No user-response.json written at all

    [task_id] = submit_chain(
        FlowEnvelope(db_path=db_path, submitted_by="tester", planspace=planspace),
        [
            TaskSpec(
                task_type="bootstrap.interpret_response",
                concern_scope="bootstrap",
                payload_path=str(payload_path),
            ),
        ],
    )
    _update_task_status(db_path, task_id, "running")
    _update_task_status(db_path, task_id, "complete")

    reconcile_task_completion(db_path, planspace, task_id, "complete", None)

    conn = sqlite3.connect(str(db_path), timeout=5.0)
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT * FROM tasks ORDER BY id").fetchall()
    conn.close()

    followon_types = [
        row["task_type"] for row in rows if row["id"] != task_id
    ]
    assert followon_types == [], (
        f"Expected no follow-on tasks (fail-closed), got {followon_types}"
    )


# ------------------------------------------------------------------
# Hierarchical codemap fallback guard (Piece 4i)
# ------------------------------------------------------------------


def test_codemap_synthesize_complete_with_empty_codemap_falls_back(tmp_path) -> None:
    """When scan.codemap_synthesize completes but the codemap file is
    empty/missing, the reconciler still submits bootstrap.explore_sections
    and records a fallback entry in bootstrap_execution_log."""
    from flow.service.task_db_client import init_db

    db_path = tmp_path / "test.db"
    planspace = tmp_path / "planspace"
    planspace.mkdir()
    PathRegistry(planspace).ensure_artifacts_tree()
    init_db(db_path)

    payload_path = planspace / "artifacts" / "spec.md"
    payload_path.write_text("# spec\n", encoding="utf-8")

    # Do NOT create a codemap file -- simulates synthesis that produced nothing.

    [task_id] = submit_chain(
        FlowEnvelope(db_path=db_path, submitted_by="tester", planspace=planspace),
        [
            TaskSpec(
                task_type="scan.codemap_synthesize",
                concern_scope="bootstrap",
                payload_path=str(payload_path),
            ),
        ],
    )
    _update_task_status(db_path, task_id, "running")
    _update_task_status(db_path, task_id, "complete")

    reconcile_task_completion(db_path, planspace, task_id, "complete", None)

    # The reconciler should still submit bootstrap.explore_sections
    conn = sqlite3.connect(str(db_path), timeout=5.0)
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT * FROM tasks ORDER BY id").fetchall()
    conn.close()

    followon_types = [
        row["task_type"] for row in rows if row["id"] != task_id
    ]
    assert followon_types == ["bootstrap.explore_sections"], (
        f"Expected explore_sections follow-on despite empty codemap, "
        f"got {followon_types}"
    )

    # Verify fallback was logged in bootstrap_execution_log
    conn = sqlite3.connect(str(db_path), timeout=5.0)
    conn.row_factory = sqlite3.Row
    log_rows = conn.execute(
        "SELECT * FROM bootstrap_execution_log WHERE stage LIKE '%fallback%'"
    ).fetchall()
    conn.close()

    assert len(log_rows) >= 1, (
        "Expected a fallback entry in bootstrap_execution_log"
    )
    assert log_rows[0]["stage"] == "hierarchical_codemap_fallback"
    assert log_rows[0]["error"] is not None


def test_codemap_synthesize_complete_with_valid_codemap_proceeds(tmp_path) -> None:
    """When scan.codemap_synthesize completes and the codemap exists with
    content, the reconciler submits bootstrap.explore_sections and records
    a success entry (not a fallback) in bootstrap_execution_log."""
    from flow.service.task_db_client import init_db

    db_path = tmp_path / "test.db"
    planspace = tmp_path / "planspace"
    planspace.mkdir()
    PathRegistry(planspace).ensure_artifacts_tree()
    init_db(db_path)

    payload_path = planspace / "artifacts" / "spec.md"
    payload_path.write_text("# spec\n", encoding="utf-8")

    # Create a valid codemap file
    codemap_path = PathRegistry(planspace).codemap()
    codemap_path.parent.mkdir(parents=True, exist_ok=True)
    codemap_path.write_text("# Codemap\n\n## Modules\n- src/api\n", encoding="utf-8")

    [task_id] = submit_chain(
        FlowEnvelope(db_path=db_path, submitted_by="tester", planspace=planspace),
        [
            TaskSpec(
                task_type="scan.codemap_synthesize",
                concern_scope="bootstrap",
                payload_path=str(payload_path),
            ),
        ],
    )
    _update_task_status(db_path, task_id, "running")
    _update_task_status(db_path, task_id, "complete")

    reconcile_task_completion(db_path, planspace, task_id, "complete", None)

    # The reconciler should submit bootstrap.explore_sections
    conn = sqlite3.connect(str(db_path), timeout=5.0)
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT * FROM tasks ORDER BY id").fetchall()
    conn.close()

    followon_types = [
        row["task_type"] for row in rows if row["id"] != task_id
    ]
    assert followon_types == ["bootstrap.explore_sections"], (
        f"Expected explore_sections follow-on, got {followon_types}"
    )

    # Verify success was logged (not fallback)
    conn = sqlite3.connect(str(db_path), timeout=5.0)
    conn.row_factory = sqlite3.Row
    log_rows = conn.execute(
        "SELECT * FROM bootstrap_execution_log"
    ).fetchall()
    conn.close()

    assert len(log_rows) >= 1, (
        "Expected a log entry in bootstrap_execution_log"
    )
    assert log_rows[0]["stage"] == "hierarchical_codemap"
    assert log_rows[0]["status"] == "completed"
    assert log_rows[0]["error"] is None


def test_codemap_synthesize_failed_falls_back_to_explore_sections(tmp_path) -> None:
    """When scan.codemap_synthesize fails, the reconciler submits
    bootstrap.explore_sections as a fallback and records the failure
    in bootstrap_execution_log."""
    from flow.service.task_db_client import init_db

    db_path = tmp_path / "test.db"
    planspace = tmp_path / "planspace"
    planspace.mkdir()
    PathRegistry(planspace).ensure_artifacts_tree()
    init_db(db_path)

    payload_path = planspace / "artifacts" / "spec.md"
    payload_path.write_text("# spec\n", encoding="utf-8")

    [task_id] = submit_chain(
        FlowEnvelope(db_path=db_path, submitted_by="tester", planspace=planspace),
        [
            TaskSpec(
                task_type="scan.codemap_synthesize",
                concern_scope="bootstrap",
                payload_path=str(payload_path),
            ),
        ],
    )
    _update_task_status(db_path, task_id, "running")
    _update_task_status(db_path, task_id, "failed")

    reconcile_task_completion(db_path, planspace, task_id, "failed", None)

    # Despite the failure, the reconciler should submit explore_sections
    conn = sqlite3.connect(str(db_path), timeout=5.0)
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT * FROM tasks ORDER BY id").fetchall()
    conn.close()

    followon_types = [
        row["task_type"] for row in rows if row["id"] != task_id
    ]
    assert "bootstrap.explore_sections" in followon_types, (
        f"Expected explore_sections follow-on despite synthesis failure, "
        f"got {followon_types}"
    )

    # Verify fallback failure was logged
    conn = sqlite3.connect(str(db_path), timeout=5.0)
    conn.row_factory = sqlite3.Row
    log_rows = conn.execute(
        "SELECT * FROM bootstrap_execution_log WHERE stage LIKE '%fallback%'"
    ).fetchall()
    conn.close()

    assert len(log_rows) >= 1, (
        "Expected a fallback entry in bootstrap_execution_log"
    )
    assert log_rows[0]["stage"] == "hierarchical_codemap_fallback"
    assert log_rows[0]["status"] == "failed"
    assert "failed" in log_rows[0]["error"]
