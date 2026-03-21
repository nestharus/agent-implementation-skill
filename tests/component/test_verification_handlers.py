"""Component tests for verification/testing reconciler completion handlers."""

from __future__ import annotations

import json
import sqlite3
import subprocess
from pathlib import Path

from _paths import DB_SH
from flow.types.context import FlowEnvelope
from flow.types.schema import TaskSpec
from src.orchestrator.path_registry import PathRegistry
from src.signals.repository.artifact_io import write_json
from containers import ArtifactIOService, Services


def submit_chain(env, steps, **kwargs):
    return Services.flow_ingestion().submit_chain(env, steps, **kwargs)


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


def _update_task_status(db_path: Path, task_id: int, status: str) -> None:
    conn = sqlite3.connect(str(db_path), timeout=5.0)
    conn.execute(
        "UPDATE tasks SET status=?, completed_at=datetime('now') WHERE id=?",
        (status, task_id),
    )
    conn.commit()
    conn.close()


def _query_tasks(db_path: Path) -> list[dict]:
    conn = sqlite3.connect(str(db_path), timeout=5.0)
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT * FROM tasks ORDER BY id").fetchall()
    conn.close()
    return [dict(r) for r in rows]


# -- verification.structural completion: PAT-0001 malformed → inconclusive ---

def test_structural_malformed_records_inconclusive(tmp_path) -> None:
    db_path = tmp_path / "test.db"
    planspace = tmp_path / "planspace"
    planspace.mkdir()
    PathRegistry(planspace).ensure_artifacts_tree()
    _init_db(db_path)

    # Write malformed structural findings
    paths = PathRegistry(planspace)
    paths.verification_structural("03").write_text(
        "not valid json {{{",
        encoding="utf-8",
    )

    [task_id] = submit_chain(
        FlowEnvelope(db_path=db_path, submitted_by="tester", planspace=planspace),
        [TaskSpec(task_type="verification.structural", concern_scope="section-03")],
    )
    _update_task_status(db_path, task_id, "complete")

    reconcile_task_completion(db_path, planspace, task_id, "complete", None)

    status = json.loads(
        paths.verification_status("03").read_text(encoding="utf-8")
    )
    assert status["status"] == "inconclusive"
    assert status["source"] == "verification.structural"


# -- verification.structural: invalid schema → inconclusive -----------------

def test_structural_invalid_schema_records_inconclusive(tmp_path) -> None:
    db_path = tmp_path / "test.db"
    planspace = tmp_path / "planspace"
    planspace.mkdir()
    PathRegistry(planspace).ensure_artifacts_tree()
    _init_db(db_path)

    paths = PathRegistry(planspace)
    # findings list has entries missing required 'severity' key
    write_json(
        paths.verification_structural("04"),
        {"findings": [{"description": "something", "scope": "local"}]},
    )

    [task_id] = submit_chain(
        FlowEnvelope(db_path=db_path, submitted_by="tester", planspace=planspace),
        [TaskSpec(task_type="verification.structural", concern_scope="section-04")],
    )
    _update_task_status(db_path, task_id, "complete")

    reconcile_task_completion(db_path, planspace, task_id, "complete", None)

    status = json.loads(
        paths.verification_status("04").read_text(encoding="utf-8")
    )
    assert status["status"] == "inconclusive"


# -- verification.structural: error findings → queues integration task ------

def test_structural_error_findings_queues_integration(tmp_path) -> None:
    db_path = tmp_path / "test.db"
    planspace = tmp_path / "planspace"
    planspace.mkdir()
    PathRegistry(planspace).ensure_artifacts_tree()
    _init_db(db_path)

    paths = PathRegistry(planspace)
    write_json(
        paths.verification_structural("05"),
        {
            "findings": [
                {
                    "finding_id": "F-001",
                    "scope": "section_local",
                    "category": "import_resolution",
                    "sections": ["05"],
                    "file_paths": ["src/app.py"],
                    "description": "unresolved import",
                    "severity": "error",
                    "evidence_snippet": "import missing_mod",
                    "suggested_resolution": "add dependency",
                },
            ],
        },
    )

    [task_id] = submit_chain(
        FlowEnvelope(db_path=db_path, submitted_by="tester", planspace=planspace),
        [TaskSpec(task_type="verification.structural", concern_scope="section-05")],
    )
    _update_task_status(db_path, task_id, "complete")

    reconcile_task_completion(db_path, planspace, task_id, "complete", None)

    tasks = _query_tasks(db_path)
    task_types = [t["task_type"] for t in tasks]
    assert "verification.integration" in task_types

    status = json.loads(
        paths.verification_status("05").read_text(encoding="utf-8")
    )
    assert status["status"] == "findings_local"
    assert status["error_count"] == 1


# -- verification.structural: warnings only → pass, no integration task -----

def test_structural_warnings_only_passes(tmp_path) -> None:
    db_path = tmp_path / "test.db"
    planspace = tmp_path / "planspace"
    planspace.mkdir()
    PathRegistry(planspace).ensure_artifacts_tree()
    _init_db(db_path)

    paths = PathRegistry(planspace)
    write_json(
        paths.verification_structural("06"),
        {
            "findings": [
                {
                    "finding_id": "F-002",
                    "scope": "section_local",
                    "category": "naming",
                    "sections": ["06"],
                    "file_paths": ["src/util.py"],
                    "description": "inconsistent naming",
                    "severity": "warning",
                    "evidence_snippet": "def BadName()",
                    "suggested_resolution": "rename",
                },
            ],
        },
    )

    [task_id] = submit_chain(
        FlowEnvelope(db_path=db_path, submitted_by="tester", planspace=planspace),
        [TaskSpec(task_type="verification.structural", concern_scope="section-06")],
    )
    _update_task_status(db_path, task_id, "complete")

    reconcile_task_completion(db_path, planspace, task_id, "complete", None)

    tasks = _query_tasks(db_path)
    assert len(tasks) == 1  # no integration task queued

    status = json.loads(
        paths.verification_status("06").read_text(encoding="utf-8")
    )
    assert status["status"] == "pass"


# -- verification.integration: cross-section findings → blocker signal ------

def test_integration_cross_section_emits_blocker(tmp_path) -> None:
    db_path = tmp_path / "test.db"
    planspace = tmp_path / "planspace"
    planspace.mkdir()
    PathRegistry(planspace).ensure_artifacts_tree()
    _init_db(db_path)

    paths = PathRegistry(planspace)
    write_json(
        paths.verification_integration("07"),
        {
            "findings": [
                {
                    "finding_id": "F-010",
                    "scope": "cross_section",
                    "category": "interface_mismatch",
                    "sections": ["07", "08"],
                    "file_paths": ["src/api.py"],
                    "description": "return type mismatch across boundary",
                    "severity": "error",
                    "evidence_snippet": "int vs str",
                    "suggested_resolution": "align types",
                },
            ],
        },
    )

    [task_id] = submit_chain(
        FlowEnvelope(db_path=db_path, submitted_by="tester", planspace=planspace),
        [TaskSpec(task_type="verification.integration", concern_scope="section-07")],
    )
    _update_task_status(db_path, task_id, "complete")

    reconcile_task_completion(db_path, planspace, task_id, "complete", None)

    blocker = json.loads(
        paths.verification_blocker_signal("07").read_text(encoding="utf-8")
    )
    assert blocker["state"] == "need_decision"
    assert blocker["blocker_type"] == "verification_integration_failure"
    assert blocker["finding_count"] == 1


# -- testing.behavioral: failing tests → queues RCA and emits blocker -------

def test_behavioral_failures_queue_rca(tmp_path) -> None:
    db_path = tmp_path / "test.db"
    planspace = tmp_path / "planspace"
    planspace.mkdir()
    PathRegistry(planspace).ensure_artifacts_tree()
    _init_db(db_path)

    paths = PathRegistry(planspace)
    write_json(
        paths.testing_results("09"),
        {
            "results": [
                {"test_name": "test_login", "status": "passed"},
                {"test_name": "test_checkout", "status": "failed"},
            ],
        },
    )

    [task_id] = submit_chain(
        FlowEnvelope(db_path=db_path, submitted_by="tester", planspace=planspace),
        [TaskSpec(task_type="testing.behavioral", concern_scope="section-09")],
    )
    _update_task_status(db_path, task_id, "complete")

    reconcile_task_completion(db_path, planspace, task_id, "complete", None)

    tasks = _query_tasks(db_path)
    task_types = [t["task_type"] for t in tasks]
    assert "testing.rca" in task_types

    blocker = json.loads(
        paths.testing_blocker_signal("09").read_text(encoding="utf-8")
    )
    assert blocker["blocker_type"] == "test_behavioral_failure"
    assert blocker["failed_test_count"] == 1


# -- testing.behavioral: all pass → no RCA, no blocker ---------------------

def test_behavioral_all_pass_no_blocker(tmp_path) -> None:
    db_path = tmp_path / "test.db"
    planspace = tmp_path / "planspace"
    planspace.mkdir()
    PathRegistry(planspace).ensure_artifacts_tree()
    _init_db(db_path)

    paths = PathRegistry(planspace)
    write_json(
        paths.testing_results("10"),
        {
            "results": [
                {"test_name": "test_login", "status": "passed"},
                {"test_name": "test_signup", "status": "passed"},
            ],
        },
    )

    [task_id] = submit_chain(
        FlowEnvelope(db_path=db_path, submitted_by="tester", planspace=planspace),
        [TaskSpec(task_type="testing.behavioral", concern_scope="section-10")],
    )
    _update_task_status(db_path, task_id, "complete")

    reconcile_task_completion(db_path, planspace, task_id, "complete", None)

    tasks = _query_tasks(db_path)
    assert len(tasks) == 1  # no RCA queued
    assert not paths.testing_blocker_signal("10").exists()


# -- testing.behavioral: malformed → blocker (fail-closed) ------------------

def test_behavioral_malformed_emits_blocker(tmp_path) -> None:
    db_path = tmp_path / "test.db"
    planspace = tmp_path / "planspace"
    planspace.mkdir()
    PathRegistry(planspace).ensure_artifacts_tree()
    _init_db(db_path)

    paths = PathRegistry(planspace)
    paths.testing_results("11").write_text("garbage", encoding="utf-8")

    [task_id] = submit_chain(
        FlowEnvelope(db_path=db_path, submitted_by="tester", planspace=planspace),
        [TaskSpec(task_type="testing.behavioral", concern_scope="section-11")],
    )
    _update_task_status(db_path, task_id, "complete")

    reconcile_task_completion(db_path, planspace, task_id, "complete", None)

    blocker = json.loads(
        paths.testing_blocker_signal("11").read_text(encoding="utf-8")
    )
    assert blocker["blocker_type"] == "test_behavioral_failure"
    assert "malformed" in blocker["detail"]


# -- testing.rca: cross-section findings → blocker signal -------------------

def test_rca_cross_section_emits_blocker(tmp_path) -> None:
    db_path = tmp_path / "test.db"
    planspace = tmp_path / "planspace"
    planspace.mkdir()
    PathRegistry(planspace).ensure_artifacts_tree()
    _init_db(db_path)

    paths = PathRegistry(planspace)
    write_json(
        paths.testing_rca_findings("12"),
        {
            "findings": [
                {
                    "scope": "cross_section",
                    "description": "shared config dependency causes test failure",
                    "category": "dependency",
                    "file_paths": ["src/config.py"],
                },
            ],
        },
    )

    [task_id] = submit_chain(
        FlowEnvelope(db_path=db_path, submitted_by="tester", planspace=planspace),
        [TaskSpec(task_type="testing.rca", concern_scope="section-12")],
    )
    _update_task_status(db_path, task_id, "complete")

    reconcile_task_completion(db_path, planspace, task_id, "complete", None)

    blocker = json.loads(
        paths.verification_blocker_signal("12").read_text(encoding="utf-8")
    )
    assert blocker["state"] == "need_decision"
    assert blocker["source"] == "testing.rca"


# -- testing.rca: malformed → advisory, no crash ---------------------------

def test_rca_malformed_does_not_crash(tmp_path) -> None:
    db_path = tmp_path / "test.db"
    planspace = tmp_path / "planspace"
    planspace.mkdir()
    PathRegistry(planspace).ensure_artifacts_tree()
    _init_db(db_path)

    paths = PathRegistry(planspace)
    paths.testing_rca_findings("13").write_text("not json", encoding="utf-8")

    [task_id] = submit_chain(
        FlowEnvelope(db_path=db_path, submitted_by="tester", planspace=planspace),
        [TaskSpec(task_type="testing.rca", concern_scope="section-13")],
    )
    _update_task_status(db_path, task_id, "complete")

    # Should not raise
    reconcile_task_completion(db_path, planspace, task_id, "complete", None)


# -- Non-matching task types are no-ops for verification handlers -----------

def test_non_verification_task_is_noop(tmp_path) -> None:
    """Verify that a normal task type does not trigger verification handlers."""
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

    # Should not raise or produce verification artifacts
    reconcile_task_completion(db_path, planspace, task_id, "complete", None)
    paths = PathRegistry(planspace)
    # No verification status files should exist
    assert not list(paths.verification_dir().glob("*-verification-status.json"))
