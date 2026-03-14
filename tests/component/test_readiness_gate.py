from __future__ import annotations

import json
from pathlib import Path

import pytest
from dependency_injector import providers

from containers import FreshnessService, Services
from src.orchestrator.path_registry import PathRegistry
from src.proposal.engine.readiness_gate import (
    publish_discoveries,
    resolve_and_route,
    route_blockers,
)
from src.proposal.repository.state import ProposalState
from src.proposal.service.readiness_resolver import ReadinessResult
from src.orchestrator.types import Section

def _section(planspace: Path) -> Section:
    section = Section(
        number="03",
        path=planspace / "artifacts" / "sections" / "section-03.md",
    )
    section.path.write_text("# Section 03\n", encoding="utf-8")
    return section

def test_publish_discoveries_writes_scope_delta_and_research_artifact(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    planspace = tmp_path / "planspace"
    planspace.mkdir()
    PathRegistry(planspace).ensure_artifacts_tree()
    appended: list[str] = []
    monkeypatch.setattr(
        "src.proposal.engine.readiness_gate.append_open_problem",
        lambda _planspace, _section, detail, _source: appended.append(detail),
    )

    publish_discoveries(
        "03",
        ProposalState(
            new_section_candidates=["Create retry worker"],
            research_questions=["Need API quota behavior"],
        ),
        planspace,
    )

    scope_files = list((planspace / "artifacts" / "scope-deltas").glob("*.json"))
    assert len(scope_files) == 1
    assert appended == ["Need API quota behavior"]
    rq_path = planspace / "artifacts" / "open-problems" / "section-03-research-questions.json"
    assert json.loads(rq_path.read_text(encoding="utf-8"))["research_questions"] == [
        "Need API quota behavior"
    ]

def test_route_blockers_writes_signals_and_queues_reconciliation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    planspace = tmp_path / "planspace"
    planspace.mkdir()
    PathRegistry(planspace).ensure_artifacts_tree()
    queued: list[tuple[list[str], list[str]]] = []

    monkeypatch.setattr(
        "src.proposal.engine.readiness_gate.queue_reconciliation_request",
        lambda _artifacts, _section, contracts, anchors: queued.append(
            (contracts, anchors)
        ),
    )
    monkeypatch.setattr(
        "src.proposal.engine.readiness_gate.update_blocker_rollup",
        lambda *_args, **_kwargs: None,
    )

    route_blockers(
        "03",
        ProposalState(
            user_root_questions=["Choose retry policy"],
            shared_seam_candidates=["shared client cache"],
            unresolved_contracts=["CacheProtocol"],
            unresolved_anchors=["client.cache"],
        ),
        planspace,
    )

    assert (
        planspace / "artifacts" / "signals" / "section-03-proposal-q0-signal.json"
    ).exists()
    assert (
        planspace / "artifacts" / "signals" / "substrate-trigger-03-00.json"
    ).exists()
    assert (
        planspace / "artifacts" / "signals" / "section-03-seam-0-signal.json"
    ).exists()
    assert queued == [(["CacheProtocol"], ["client.cache"])]

def test_route_blockers_dispatches_research_plan_on_first_encounter(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    planspace = tmp_path / "planspace"
    planspace.mkdir()
    PathRegistry(planspace).ensure_artifacts_tree()
    submitted: list[dict] = []
    prompt_calls: list[dict] = []
    status_writes: list[tuple[str, Path, str]] = []

    prompt_path = planspace / "artifacts" / "research-plan-03-prompt.md"
    prompt_path.write_text("# Prompt\n", encoding="utf-8")

    monkeypatch.setattr(
        "src.proposal.engine.readiness_gate.update_blocker_rollup",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        "containers.ResearchOrchestratorService.compute_trigger_hash",
        lambda self, questions: "hash-03",
    )
    monkeypatch.setattr(
        "containers.ResearchOrchestratorService.is_complete_for_trigger",
        lambda *_args, **_kwargs: False,
    )
    monkeypatch.setattr(
        "src.proposal.engine.readiness_gate.write_research_plan_prompt",
        lambda section_number, ps, codespace, trigger_path: (
            prompt_calls.append(
                {
                    "section_number": section_number,
                    "planspace": ps,
                    "codespace": codespace,
                    "trigger_path": trigger_path,
                }
            )
            or prompt_path
        ),
    )
    class _StubFreshness(FreshnessService):
        def compute(self, planspace, section_number):
            return "fresh-03"
    Services.freshness.override(providers.Object(_StubFreshness()))
    monkeypatch.setattr(
        "containers.ResearchOrchestratorService.write_status",
        lambda self, section_number, ps, status, **kwargs: status_writes.append(
            (section_number, ps, status, kwargs)
        ),
    )
    monkeypatch.setattr(
        "src.proposal.engine.readiness_gate.submit_task",
        lambda db_path, task: (
            submitted.append(
                {
                    "db_path": db_path,
                    "submitted_by": task.submitted_by,
                    "task_type": task.task_type,
                    "concern_scope": task.concern_scope,
                    "payload_path": task.payload_path,
                    "problem_id": task.problem_id,
                    "freshness_token": task.freshness_token,
                }
            )
            or 41
        ),
    )

    try:
        route_blockers(
            "03",
            ProposalState(
                blocking_research_questions=[
                    "Should the retry ledger be persisted centrally?"
                ],
            ),
            planspace,
        )

        trigger_path = (
            planspace
            / "artifacts"
            / "research"
            / "sections"
            / "section-03"
            / "research-trigger.json"
        )
        assert trigger_path.exists()
        trigger = json.loads(trigger_path.read_text(encoding="utf-8"))
        assert trigger == {
            "section": "03",
            "trigger_source": "proposal-state:blocking_research_questions",
            "questions": ["Should the retry ledger be persisted centrally?"],
            "trigger_hash": "hash-03",
            "cycle_id": "research-03-hash-03",
        }
        assert prompt_calls == [
            {
                "section_number": "03",
                "planspace": planspace,
                "codespace": None,
                "trigger_path": trigger_path,
            }
        ]
        assert submitted == [
            {
                "db_path": planspace / "run.db",
                "submitted_by": "readiness-03",
                "task_type": "research.plan",
                "concern_scope": "section-03",
                "payload_path": str(prompt_path),
                "problem_id": "research-03",
                "freshness_token": "fresh-03",
            }
        ]
        assert status_writes == [
            (
                "03",
                planspace,
                "planned",
                {"trigger_hash": "hash-03", "cycle_id": "research-03-hash-03"},
            )
        ]
        assert not (
            planspace
            / "artifacts"
            / "signals"
            / "section-03-blocking-research-0-signal.json"
        ).exists()
    finally:
        Services.freshness.reset_override()

def test_route_blockers_falls_back_to_needs_parent_after_research_complete(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    planspace = tmp_path / "planspace"
    submitted: list[dict] = []

    monkeypatch.setattr(
        "src.proposal.engine.readiness_gate.update_blocker_rollup",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        "containers.ResearchOrchestratorService.compute_trigger_hash",
        lambda self, questions: "hash-03",
    )
    monkeypatch.setattr(
        "containers.ResearchOrchestratorService.is_complete_for_trigger",
        lambda *_args, **_kwargs: True,
    )
    monkeypatch.setattr(
        "src.proposal.engine.readiness_gate.submit_task",
        lambda db_path, task: (
            submitted.append(task)
            or 42
        ),
    )

    route_blockers(
        "03",
        ProposalState(
            blocking_research_questions=[
                "Should the retry ledger be persisted centrally?"
            ],
        ),
        planspace,
    )

    signal_path = (
        planspace
        / "artifacts"
        / "signals"
        / "section-03-blocking-research-0-signal.json"
    )
    assert signal_path.exists()
    assert json.loads(signal_path.read_text(encoding="utf-8")) == {
        "state": "needs_parent",
        "section": "03",
        "detail": "Should the retry ledger be persisted centrally?",
        "needs": "Parent/coordination answer — research could not resolve",
        "why_blocked": (
            "Research completed but blocking question remains unresolved"
        ),
        "source": "proposal-state:blocking_research_questions",
    }
    assert submitted == []
    assert not (
        planspace
        / "artifacts"
        / "research"
        / "sections"
        / "section-03"
        / "research-trigger.json"
    ).exists()

def test_route_blockers_falls_back_to_needs_parent_when_prompt_blocked(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    planspace = tmp_path / "planspace"
    submitted: list[dict] = []

    monkeypatch.setattr(
        "src.proposal.engine.readiness_gate.update_blocker_rollup",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        "containers.ResearchOrchestratorService.compute_trigger_hash",
        lambda self, questions: "hash-03",
    )
    monkeypatch.setattr(
        "containers.ResearchOrchestratorService.is_complete_for_trigger",
        lambda *_args, **_kwargs: False,
    )
    monkeypatch.setattr(
        "src.proposal.engine.readiness_gate.write_research_plan_prompt",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        "src.proposal.engine.readiness_gate.submit_task",
        lambda db_path, task: (
            submitted.append(task)
            or 44
        ),
    )
    status_writes: list[tuple[str, Path, str, dict]] = []
    monkeypatch.setattr(
        "containers.ResearchOrchestratorService.write_status",
        lambda self, section_number, ps, status, **kwargs: status_writes.append(
            (section_number, ps, status, kwargs)
        ),
    )

    route_blockers(
        "03",
        ProposalState(
            blocking_research_questions=[
                "Should the retry ledger be persisted centrally?"
            ],
        ),
        planspace,
    )

    signal_path = (
        planspace
        / "artifacts"
        / "signals"
        / "section-03-blocking-research-0-signal.json"
    )
    assert signal_path.exists()
    assert json.loads(signal_path.read_text(encoding="utf-8")) == {
        "state": "needs_parent",
        "section": "03",
        "detail": "Should the retry ledger be persisted centrally?",
        "needs": "Parent/coordination answer to this blocking research question",
        "why_blocked": (
            "Research prompt generation failed validation and "
            "cannot be dispatched safely"
        ),
        "source": "proposal-state:blocking_research_questions",
    }
    assert submitted == []
    assert status_writes == [
        (
            "03",
            planspace,
            "failed",
            {
                "detail": "research plan prompt blocked by validation",
                "trigger_hash": "hash-03",
                "cycle_id": "research-03-hash-03",
            },
        )
    ]

def test_route_blockers_ignores_empty_blocking_research_questions(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    planspace = tmp_path / "planspace"
    submitted: list[dict] = []

    monkeypatch.setattr(
        "src.proposal.engine.readiness_gate.update_blocker_rollup",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        "src.proposal.engine.readiness_gate.submit_task",
        lambda db_path, task: (
            submitted.append(task)
            or 43
        ),
    )

    route_blockers(
        "03",
        ProposalState(blocking_research_questions=[]),
        planspace,
    )

    assert submitted == []
    assert not (
        planspace
        / "artifacts"
        / "research"
        / "sections"
        / "section-03"
        / "research-trigger.json"
    ).exists()
    assert not (
        planspace
        / "artifacts"
        / "signals"
        / "section-03-blocking-research-0-signal.json"
    ).exists()

def test_resolve_and_route_returns_blocked_proposal_pass_result(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    noop_communicator) -> None:
    planspace = tmp_path / "planspace"
    planspace.mkdir()
    PathRegistry(planspace).ensure_artifacts_tree()
    artifacts = planspace / "artifacts"
    section = _section(planspace)
    proposal_state_path = artifacts / "proposals" / "section-03-proposal-state.json"
    proposal_state_path.write_text(
        json.dumps(
            {
                "resolved_anchors": [],
                "unresolved_contracts": ["CacheProtocol"],
                "resolved_contracts": [],
                "unresolved_anchors": [],
                "user_root_questions": ["Choose retry policy"],
                "shared_seam_candidates": [],
                "new_section_candidates": [],
                "research_questions": [],
                "blocking_research_questions": [],
                "execution_ready": False,
                "readiness_rationale": "blocked",
                "problem_ids": [],
                "pattern_ids": [],
                "profile_id": "",
                "pattern_deviations": [],
                "governance_questions": [],
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(
        "src.proposal.engine.readiness_gate.resolve_readiness",
        lambda *_args, **_kwargs: ReadinessResult(
            ready=False,
            blockers=[{"type": "user_root_questions", "description": "Choose retry policy"}],
            rationale="blocked",
        ),
    )
    monkeypatch.setattr(
        "src.proposal.engine.readiness_gate.append_open_problem",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        "src.proposal.engine.readiness_gate.queue_reconciliation_request",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        "src.proposal.engine.readiness_gate.update_blocker_rollup",
        lambda *_args, **_kwargs: None,
    )

    result = resolve_and_route(section, planspace, "proposal")

    assert result.ready is False
    assert result.blockers == [
        {"type": "user_root_questions", "description": "Choose retry policy"}
    ]
    assert result.proposal_pass_result is not None
    assert result.proposal_pass_result.execution_ready is False
    assert result.proposal_pass_result.needs_reconciliation is True
