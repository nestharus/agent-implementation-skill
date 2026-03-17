"""Tests for pipeline resume-from-existing-state logic.

Covers:
- ProposalPhase._load_completed_proposals() and run_proposal_pass() skipping
  completed sections on resume.
- PipelineOrchestrator._run_loop() using existing cycle state instead of
  clearing and re-running the proposal pass.
- reset_stuck_running_tasks() resetting tasks stuck in 'running' status.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from containers import Services
from implementation.service.section_reexplorer import SectionReexplorer
from orchestrator.engine.section_pipeline import SectionPipeline
from orchestrator.path_registry import PathRegistry
from orchestrator.repository.cycle_state import CycleState
from orchestrator.types import ProposalPassResult, Section
from proposal.engine.proposal_phase import run_proposal_pass
from flow.service.task_db_client import (
    init_db,
    reset_stuck_running_tasks,
    submit_task,
    claim_task,
    next_task,
)
from flow.types.routing import Task


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _planspace(tmp_path: Path) -> Path:
    planspace = tmp_path / "planspace"
    planspace.mkdir()
    PathRegistry(planspace).ensure_artifacts_tree()
    return planspace


def _write_proposal_state(planspace: Path, sec_num: str) -> None:
    """Write a valid proposal-state JSON for the given section."""
    paths = PathRegistry(planspace)
    state = {
        "resolved_anchors": ["anchor-a"],
        "unresolved_anchors": [],
        "resolved_contracts": ["contract-a"],
        "unresolved_contracts": [],
        "research_questions": [],
        "blocking_research_questions": [],
        "user_root_questions": [],
        "new_section_candidates": [],
        "shared_seam_candidates": [],
        "execution_ready": True,
        "readiness_rationale": "all resolved",
        "problem_ids": [],
        "pattern_ids": [],
        "profile_id": "",
        "pattern_deviations": [],
        "governance_questions": [],
    }
    state_path = paths.proposal_state(sec_num)
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps(state), encoding="utf-8")


def _write_execution_ready(planspace: Path, sec_num: str, *, ready: bool = True) -> None:
    """Write an execution-ready JSON artifact for the given section."""
    paths = PathRegistry(planspace)
    data = {
        "ready": ready,
        "blockers": [],
        "rationale": "all resolved" if ready else "not ready",
    }
    ready_path = paths.execution_ready(sec_num)
    ready_path.parent.mkdir(parents=True, exist_ok=True)
    ready_path.write_text(json.dumps(data), encoding="utf-8")


# ---------------------------------------------------------------------------
# 1. run_proposal_pass() skips completed sections on resume
# ---------------------------------------------------------------------------

def test_proposal_pass_skips_completed_sections(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    noop_pipeline_control,
    noop_change_tracker,
    capturing_communicator,
) -> None:
    """Sections with both proposal-state and execution-ready on disk are
    skipped entirely — run_section is never called for them."""
    planspace = _planspace(tmp_path)

    # Section 01: complete on disk (should be skipped)
    _write_proposal_state(planspace, "01")
    _write_execution_ready(planspace, "01", ready=True)

    # Section 02: no on-disk state (should run through pipeline)
    sec1_path = planspace / "artifacts" / "sections" / "section-01.md"
    sec1_path.parent.mkdir(parents=True, exist_ok=True)
    sec1_path.write_text("# Section 01\n", encoding="utf-8")
    sec2_path = planspace / "artifacts" / "sections" / "section-02.md"
    sec2_path.write_text("# Section 02\n", encoding="utf-8")

    section1 = Section(number="01", path=sec1_path, related_files=["src/a.py"])
    section2 = Section(number="02", path=sec2_path, related_files=["src/b.py"])

    dispatched_sections: list[str] = []

    def _run_section(self, planspace, codespace, section, *, all_sections=None, pass_mode="full"):
        dispatched_sections.append(section.number)
        return ProposalPassResult(
            section_number=section.number,
            execution_ready=True,
        )

    monkeypatch.setattr(SectionPipeline, "run_section", _run_section)
    monkeypatch.setattr("containers.LogService.log_lifecycle", lambda *args, **kwargs: None)

    results = run_proposal_pass(
        [section1, section2],
        {"01": section1, "02": section2},
        planspace,
        tmp_path / "codespace",
    )

    # Section 01 was NOT dispatched to the pipeline
    assert "01" not in dispatched_sections
    # Section 02 WAS dispatched
    assert "02" in dispatched_sections
    # Both sections appear in results
    assert results["01"].execution_ready is True
    assert results["02"].execution_ready is True


def test_proposal_pass_skips_blocked_sections_on_resume(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    noop_pipeline_control,
    noop_change_tracker,
    capturing_communicator,
) -> None:
    """Sections with execution-ready=false ARE skipped on resume (already evaluated)."""
    planspace = _planspace(tmp_path)

    _write_proposal_state(planspace, "01")
    _write_execution_ready(planspace, "01", ready=False)

    sec_path = planspace / "artifacts" / "sections" / "section-01.md"
    sec_path.parent.mkdir(parents=True, exist_ok=True)
    sec_path.write_text("# Section 01\n", encoding="utf-8")
    section = Section(number="01", path=sec_path, related_files=["src/a.py"])

    dispatched_sections: list[str] = []

    def _run_section(self, planspace, codespace, section, *, all_sections=None, pass_mode="full"):
        dispatched_sections.append(section.number)
        return ProposalPassResult(section_number="01", execution_ready=True)

    monkeypatch.setattr(SectionPipeline, "run_section", _run_section)
    monkeypatch.setattr("containers.LogService.log_lifecycle", lambda *args, **kwargs: None)

    results = run_proposal_pass(
        [section],
        {"01": section},
        planspace,
        tmp_path / "codespace",
    )

    # Section 01 was NOT dispatched — already evaluated (even though blocked)
    assert "01" not in dispatched_sections
    assert results["01"].execution_ready is False


def test_proposal_pass_does_not_skip_missing_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    noop_pipeline_control,
    noop_change_tracker,
    capturing_communicator,
) -> None:
    """Sections without proposal-state on disk are NOT skipped."""
    planspace = _planspace(tmp_path)

    # Only write execution_ready, not proposal_state
    _write_execution_ready(planspace, "01", ready=True)

    sec_path = planspace / "artifacts" / "sections" / "section-01.md"
    sec_path.parent.mkdir(parents=True, exist_ok=True)
    sec_path.write_text("# Section 01\n", encoding="utf-8")
    section = Section(number="01", path=sec_path, related_files=["src/a.py"])

    dispatched_sections: list[str] = []

    def _run_section(self, planspace, codespace, section, *, all_sections=None, pass_mode="full"):
        dispatched_sections.append(section.number)
        return ProposalPassResult(section_number="01", execution_ready=True)

    monkeypatch.setattr(SectionPipeline, "run_section", _run_section)
    monkeypatch.setattr("containers.LogService.log_lifecycle", lambda *args, **kwargs: None)

    results = run_proposal_pass(
        [section],
        {"01": section},
        planspace,
        tmp_path / "codespace",
    )

    assert "01" in dispatched_sections


# ---------------------------------------------------------------------------
# 2. _run_loop() uses existing cycle state
# ---------------------------------------------------------------------------

def test_cycle_state_loads_existing_proposal_results(tmp_path: Path) -> None:
    """CycleState loads existing proposal results from disk on construction."""
    planspace = _planspace(tmp_path)
    paths = PathRegistry(planspace)

    # Write proposal results to disk
    existing_results = {
        "01": {
            "section_number": "01",
            "proposal_aligned": True,
            "execution_ready": True,
            "blockers": [],
            "needs_reconciliation": False,
            "proposal_state_path": str(paths.proposal_state("01")),
        },
    }
    paths.proposal_results().write_text(
        json.dumps(existing_results), encoding="utf-8",
    )

    cycle = CycleState(
        artifact_io=Services.artifact_io(),
        proposal_path=paths.proposal_results(),
        section_path=paths.section_results(),
    )

    # Loaded from disk
    assert "01" in cycle.proposal_results
    assert cycle.proposal_results["01"].execution_ready is True
    assert cycle.proposal_results["01"].section_number == "01"


def test_cycle_state_empty_when_no_file(tmp_path: Path) -> None:
    """CycleState starts empty when no prior results exist on disk."""
    planspace = _planspace(tmp_path)
    paths = PathRegistry(planspace)

    cycle = CycleState(
        artifact_io=Services.artifact_io(),
        proposal_path=paths.proposal_results(),
        section_path=paths.section_results(),
    )

    assert cycle.proposal_results == {}
    assert cycle.section_results == {}


# ---------------------------------------------------------------------------
# 3. reset_stuck_running_tasks
# ---------------------------------------------------------------------------

def test_reset_stuck_running_tasks_resets_to_pending(tmp_path: Path) -> None:
    """Tasks stuck in 'running' status are reset to 'pending' on startup."""
    db_path = tmp_path / "run.db"
    init_db(db_path)

    # Submit and claim a task (moves it to 'running')
    task = Task(
        task_type="test.task",
        submitted_by="test",
        payload_path="/tmp/payload.md",
    )
    task_id = submit_task(str(db_path), task)
    claim_task(str(db_path), "dispatcher", task_id)

    # Submit a second task (stays 'pending')
    task2 = Task(
        task_type="test.task2",
        submitted_by="test",
        payload_path="/tmp/payload2.md",
    )
    submit_task(str(db_path), task2)

    # Reset stuck tasks
    count = reset_stuck_running_tasks(str(db_path))

    assert count == 1

    # The previously-running task should now be pending and available
    result = next_task(str(db_path))
    assert result is not None
    assert result["id"] == str(task_id)


def test_reset_stuck_running_tasks_noop_when_none(tmp_path: Path) -> None:
    """Returns 0 when no tasks are stuck in 'running'."""
    db_path = tmp_path / "run.db"
    init_db(db_path)

    count = reset_stuck_running_tasks(str(db_path))
    assert count == 0
