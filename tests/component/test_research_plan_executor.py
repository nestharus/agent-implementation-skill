from __future__ import annotations

import json
import sqlite3
import subprocess
from pathlib import Path

import pytest

from _paths import DB_SH
from containers import ArtifactIOService, HasherService, Services
from src.orchestrator.path_registry import PathRegistry
from src.signals.repository.artifact_io import write_json
from src.research.engine.orchestrator import ResearchOrchestrator
from src.research.engine.research_plan_executor import ResearchPlanExecutor
from src.research.engine.research_branch_builder import ResearchBranchBuilder
from src.research.prompt.writers import ResearchPromptWriter


def _init_db(db_path: Path) -> None:
    subprocess.run(
        ["bash", str(DB_SH), "init", str(db_path)],
        check=True,
        capture_output=True,
        text=True,
    )


def _query_all(db_path: Path, sql: str) -> list[sqlite3.Row]:
    conn = sqlite3.connect(str(db_path), timeout=5.0)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(sql).fetchall()
    conn.close()
    return rows


def _write_common_artifacts(planspace: Path, section_number: str = "03") -> None:
    paths = PathRegistry(planspace)
    paths.research_section_dir(section_number).mkdir(parents=True, exist_ok=True)
    paths.section_spec(section_number).write_text("# Section\n", encoding="utf-8")
    paths.problem_frame(section_number).write_text("# Problem Frame\n", encoding="utf-8")
    paths.proposal_state(section_number).write_text("{}\n", encoding="utf-8")
    paths.codemap().write_text("# Codemap\n", encoding="utf-8")
    paths.corrections().write_text("{}\n", encoding="utf-8")
    paths.intent_surfaces_signal(section_number).write_text("{}\n", encoding="utf-8")


@pytest.fixture()
def orchestrator() -> ResearchOrchestrator:
    return ResearchOrchestrator(
        hasher=HasherService(),
        artifact_io=ArtifactIOService(),
    )


@pytest.fixture()
def executor() -> ResearchPlanExecutor:
    artifact_io = ArtifactIOService()
    prompt_guard = Services.prompt_guard()
    prompt_writer = ResearchPromptWriter(
        prompt_guard=prompt_guard,
        artifact_io=artifact_io,
    )
    return ResearchPlanExecutor(
        freshness=Services.freshness(),
        flow_ingestion=Services.flow_ingestion(),
        orchestrator=ResearchOrchestrator(
            hasher=HasherService(),
            artifact_io=artifact_io,
        ),
        branch_builder=ResearchBranchBuilder(
            prompt_guard=prompt_guard,
            artifact_io=artifact_io,
            prompt_writer=prompt_writer,
        ),
        prompt_writer=prompt_writer,
    )


def test_execute_research_plan_translates_semantic_plan_into_fanout(
    tmp_path: Path, orchestrator: ResearchOrchestrator, executor: ResearchPlanExecutor,
) -> None:
    planspace = tmp_path / "planspace"
    codespace = tmp_path / "codespace"
    db_path = planspace / "run.db"
    planspace.mkdir()
    PathRegistry(planspace).ensure_artifacts_tree()
    codespace.mkdir()
    _init_db(db_path)
    _write_common_artifacts(planspace)
    paths = PathRegistry(planspace)

    write_json(
        paths.research_plan("03"),
        {
            "section": "03",
            "tickets": [
                {"ticket_id": "T-01", "research_type": "web", "questions": ["q1"]},
                {"ticket_id": "T-02", "research_type": "code", "questions": ["q2"]},
                {"ticket_id": "T-03", "research_type": "both", "questions": ["q3"]},
            ],
            "flow": {
                "parallel_groups": [["T-01", "T-02", "T-03"]],
                "synthesis_inputs": ["T-01", "T-02", "T-03"],
                "verify_claims": True,
            },
            "not_researchable": [
                {
                    "question": "Choose the business policy",
                    "reason": "This is a product decision",
                    "route": "need_decision",
                }
            ],
        },
    )
    orchestrator.write_research_status(
        "03",
        planspace,
        "planned",
        trigger_hash="hash-03",
        cycle_id="cycle-03",
    )

    plan_output = planspace / "artifacts" / "task-99-output.md"
    plan_output.write_text("planner output\n", encoding="utf-8")

    assert executor.execute_research_plan("03", planspace, codespace, plan_output) is True

    tasks = _query_all(db_path, "SELECT * FROM tasks ORDER BY id")
    assert [task["task_type"] for task in tasks] == [
        "research.domain_ticket",
        "scan.explore",
        "research.domain_ticket",
        "research.domain_ticket",
        "scan.explore",
        "research.domain_ticket",
    ]

    gates = _query_all(db_path, "SELECT * FROM gates")
    assert len(gates) == 1
    assert gates[0]["expected_count"] == 3
    assert gates[0]["synthesis_task_type"] == "research.synthesis"

    status = json.loads(paths.research_status("03").read_text(encoding="utf-8"))
    assert status["status"] == "tickets_submitted"
    assert status["trigger_hash"] == "hash-03"

    signal = json.loads(
        (paths.signals_dir() / "section-03-research-blocker-0.json").read_text(
            encoding="utf-8"
        )
    )
    assert signal["state"] == "need_decision"


def test_execute_research_plan_fails_closed_when_plan_is_schema_mismatched(
    tmp_path: Path, orchestrator: ResearchOrchestrator, executor: ResearchPlanExecutor,
) -> None:
    planspace = tmp_path / "planspace"
    db_path = planspace / "run.db"
    planspace.mkdir()
    PathRegistry(planspace).ensure_artifacts_tree()
    _init_db(db_path)
    paths = PathRegistry(planspace)
    write_json(paths.research_plan("03"), {"section": "03", "tickets": []})
    orchestrator.write_research_status(
        "03",
        planspace,
        "planned",
        trigger_hash="hash-03",
        cycle_id="cycle-03",
    )

    assert executor.execute_research_plan("03", planspace, None, paths.research_plan("03")) is False
    status = json.loads(paths.research_status("03").read_text(encoding="utf-8"))
    assert status["status"] == "failed"
    assert paths.research_plan("03").with_suffix(".malformed.json").exists()
