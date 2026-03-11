from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

from lib.core.artifact_io import write_json
from lib.pipelines.implementation_pass import _run_risk_review
from lib.risk.package_builder import build_package_from_proposal
from lib.risk.serialization import serialize_assessment, serialize_plan
from lib.risk.types import (
    PostureProfile,
    RiskAssessment,
    RiskModifiers,
    RiskPlan,
    RiskType,
    RiskVector,
    StepAssessment,
    StepDecision,
    StepMitigation,
    UnderstandingInventory,
)
from section_loop.types import Section


def _write_risk_inputs(
    planspace: Path,
    sec_num: str,
    *,
    triage_confidence: str = "high",
    shared_seams: list[str] | None = None,
    freshness_token: str | None = None,
) -> None:
    artifacts = planspace / "artifacts"
    sections = artifacts / "sections"
    proposals = artifacts / "proposals"
    readiness = artifacts / "readiness"
    signals = artifacts / "signals"

    sections.mkdir(parents=True, exist_ok=True)
    proposals.mkdir(parents=True, exist_ok=True)
    readiness.mkdir(parents=True, exist_ok=True)
    signals.mkdir(parents=True, exist_ok=True)

    (sections / f"section-{sec_num}.md").write_text(
        f"# Section {sec_num}\n\nBody\n",
        encoding="utf-8",
    )
    (sections / f"section-{sec_num}-proposal-excerpt.md").write_text(
        "Proposal excerpt\n",
        encoding="utf-8",
    )
    (sections / f"section-{sec_num}-alignment-excerpt.md").write_text(
        "Alignment excerpt\n",
        encoding="utf-8",
    )
    (sections / f"section-{sec_num}-problem-frame.md").write_text(
        "Problem frame\n",
        encoding="utf-8",
    )
    (proposals / f"section-{sec_num}-microstrategy.md").write_text(
        "- Apply the approved change\n",
        encoding="utf-8",
    )
    write_json(
        proposals / f"section-{sec_num}-proposal-state.json",
        {
            "resolved_anchors": ["tests::smoke"],
            "unresolved_anchors": [],
            "resolved_contracts": ["src/main.py"],
            "unresolved_contracts": [],
            "research_questions": [],
            "blocking_research_questions": [],
            "user_root_questions": [],
            "new_section_candidates": [],
            "shared_seam_candidates": shared_seams or [],
            "execution_ready": True,
            "readiness_rationale": "ready",
            "problem_ids": [],
            "pattern_ids": [],
            "profile_id": "",
            "pattern_deviations": [],
            "governance_questions": [],
            "constraint_ids": [],
            "governance_candidate_refs": [],
            "design_decision_refs": [],
        },
    )
    write_json(
        readiness / f"section-{sec_num}-execution-ready.json",
        {"ready": True, "blockers": [], "rationale": "ready"},
    )
    signal_payload: dict[str, str] = {
        "intent_mode": "lightweight",
        "confidence": triage_confidence,
    }
    if freshness_token is not None:
        signal_payload["freshness_token"] = freshness_token
    write_json(signals / f"intent-triage-{sec_num}.json", signal_payload)
    write_json(artifacts / "tool-registry.json", {"tools": ["pytest"]})
    (artifacts / "codemap.md").write_text("Codemap\n", encoding="utf-8")


def _section(planspace: Path, sec_num: str) -> Section:
    return Section(
        number=sec_num,
        path=planspace / "artifacts" / "sections" / f"section-{sec_num}.md",
        related_files=["src/main.py"],
    )


def _full_review_payloads(planspace: Path, sec_num: str) -> tuple[str, str]:
    package = build_package_from_proposal(f"section-{sec_num}", planspace)
    assessment = RiskAssessment(
        assessment_id=f"assessment-section-{sec_num}",
        layer="implementation",
        package_id=package.package_id,
        assessment_scope=package.scope,
        understanding_inventory=UnderstandingInventory(
            confirmed=["proposal reviewed"],
            assumed=[],
            missing=[],
            stale=[],
        ),
        package_raw_risk=20,
        assessment_confidence=0.9,
        dominant_risks=[RiskType.CONTEXT_ROT],
        step_assessments=[
            StepAssessment(
                step_id=package.steps[0].step_id,
                assessment_class=package.steps[0].assessment_class,
                summary=package.steps[0].summary,
                prerequisites=[],
                risk_vector=RiskVector(context_rot=1),
                modifiers=RiskModifiers(confidence=0.9),
                raw_risk=20,
                dominant_risks=[RiskType.CONTEXT_ROT],
            ),
        ],
        frontier_candidates=[package.steps[0].step_id],
    )
    plan = RiskPlan(
        plan_id=f"plan-section-{sec_num}",
        assessment_id=assessment.assessment_id,
        package_id=package.package_id,
        layer="implementation",
        step_decisions=[
            StepMitigation(
                step_id=package.steps[0].step_id,
                decision=StepDecision.ACCEPT,
                posture=PostureProfile.P0_DIRECT,
                mitigations=["bounded edit"],
                residual_risk=20,
                reason="below threshold",
            ),
        ],
        accepted_frontier=[package.steps[0].step_id],
        deferred_steps=[],
        reopen_steps=[],
    )
    return (
        json.dumps(serialize_assessment(assessment)),
        json.dumps(serialize_plan(plan)),
    )


def test_trivial_single_file_edit_gets_lightweight_roal(
    planspace: Path,
    mock_dispatch: MagicMock,
) -> None:
    """Trivial work gets lightweight ROAL (no skip mode)."""
    _write_risk_inputs(planspace, "01", triage_confidence="high")
    mock_dispatch.side_effect = list(_full_review_payloads(planspace, "01"))

    result = _run_risk_review(planspace, "01", _section(planspace, "01"), mock_dispatch)

    assert result is not None
    assert mock_dispatch.call_count > 0


def test_multi_section_triggers_full(
    planspace: Path,
    mock_dispatch: MagicMock,
) -> None:
    _write_risk_inputs(
        planspace,
        "01",
        triage_confidence="high",
        shared_seams=["section-02"],
    )
    mock_dispatch.side_effect = list(_full_review_payloads(planspace, "01"))

    result = _run_risk_review(planspace, "01", _section(planspace, "01"), mock_dispatch)

    assert result is not None
    assert result.accepted_frontier == ["edit-01"]
    assert [call.kwargs["agent_file"] for call in mock_dispatch.call_args_list] == [
        "risk-assessor.md",
        "execution-optimizer.md",
    ]


def test_stale_inputs_trigger_full(
    planspace: Path,
    mock_dispatch: MagicMock,
) -> None:
    _write_risk_inputs(
        planspace,
        "01",
        triage_confidence="high",
        freshness_token="stale-token",
    )
    mock_dispatch.side_effect = list(_full_review_payloads(planspace, "01"))

    result = _run_risk_review(planspace, "01", _section(planspace, "01"), mock_dispatch)

    assert result is not None
    assert result.accepted_frontier == ["edit-01"]
    assert mock_dispatch.call_count == 2


def test_monitor_signals_trigger_full(
    planspace: Path,
    mock_dispatch: MagicMock,
) -> None:
    _write_risk_inputs(planspace, "01", triage_confidence="high")
    write_json(
        planspace / "artifacts" / "signals" / "loop-detected-01.json",
        {
            "state": "loop_detected",
            "section_number": "01",
            "detail": "repeating the same edit",
        },
    )
    mock_dispatch.side_effect = list(_full_review_payloads(planspace, "01"))

    result = _run_risk_review(planspace, "01", _section(planspace, "01"), mock_dispatch)

    assert result is not None
    assert result.accepted_frontier == ["edit-01"]
    assert mock_dispatch.call_count == 2
