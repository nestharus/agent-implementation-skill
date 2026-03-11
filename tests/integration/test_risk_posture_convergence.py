from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

from lib.core.artifact_io import write_json
from lib.risk.loop import run_risk_loop
from lib.risk.package_builder import build_package_from_proposal
from lib.risk.posture import select_posture
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


def _write_risk_inputs(planspace: Path, sec_num: str) -> None:
    artifacts = planspace / "artifacts"
    sections = artifacts / "sections"
    proposals = artifacts / "proposals"
    readiness = artifacts / "readiness"

    sections.mkdir(parents=True, exist_ok=True)
    proposals.mkdir(parents=True, exist_ok=True)
    readiness.mkdir(parents=True, exist_ok=True)

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
            "shared_seam_candidates": [],
            "execution_ready": True,
            "readiness_rationale": "ready",
            "problem_ids": [],
            "pattern_ids": [],
            "profile_id": "",
            "pattern_deviations": [],
            "governance_questions": [],
        },
    )
    write_json(
        readiness / f"section-{sec_num}-execution-ready.json",
        {"ready": True, "blockers": [], "rationale": "ready"},
    )
    write_json(artifacts / "tool-registry.json", {"tools": ["pytest"]})
    (artifacts / "codemap.md").write_text("Codemap\n", encoding="utf-8")


def test_posture_moves_one_step_at_a_time() -> None:
    posture = select_posture(
        raw_risk=35,
        current_posture=PostureProfile.P3_GUARDED,
        recent_outcomes=["success", "success", "success"],
        cooldown_remaining=0,
    )

    assert posture == PostureProfile.P2_STANDARD


def test_cooldown_prevents_immediate_relaxation() -> None:
    posture = select_posture(
        raw_risk=35,
        current_posture=PostureProfile.P3_GUARDED,
        recent_outcomes=["failure", "success", "success", "success"],
        cooldown_remaining=2,
    )

    assert posture == PostureProfile.P3_GUARDED


def test_asymmetric_evidence() -> None:
    tightened = select_posture(
        raw_risk=25,
        current_posture=PostureProfile.P1_LIGHT,
        recent_outcomes=["failure"],
    )
    held = select_posture(
        raw_risk=35,
        current_posture=PostureProfile.P3_GUARDED,
        recent_outcomes=["success", "success"],
        cooldown_remaining=0,
    )
    relaxed = select_posture(
        raw_risk=35,
        current_posture=PostureProfile.P3_GUARDED,
        recent_outcomes=["success", "success", "success"],
        cooldown_remaining=0,
    )

    assert tightened == PostureProfile.P2_STANDARD
    assert held == PostureProfile.P3_GUARDED
    assert relaxed == PostureProfile.P2_STANDARD


def test_convergence_when_risk_below_threshold(
    planspace: Path,
    mock_dispatch: MagicMock,
) -> None:
    _write_risk_inputs(planspace, "01")
    package = build_package_from_proposal("section-01", planspace)
    assessment = RiskAssessment(
        assessment_id="assessment-section-01",
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
        assessment_confidence=0.95,
        dominant_risks=[RiskType.CONTEXT_ROT],
        step_assessments=[
            StepAssessment(
                step_id=package.steps[0].step_id,
                assessment_class=package.steps[0].assessment_class,
                summary=package.steps[0].summary,
                prerequisites=[],
                risk_vector=RiskVector(context_rot=1),
                modifiers=RiskModifiers(confidence=0.95),
                raw_risk=20,
                dominant_risks=[RiskType.CONTEXT_ROT],
            ),
        ],
        frontier_candidates=[package.steps[0].step_id],
    )
    plan = RiskPlan(
        plan_id="plan-section-01",
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
                reason="already below threshold",
            ),
        ],
        accepted_frontier=[package.steps[0].step_id],
        deferred_steps=[],
        reopen_steps=[],
    )
    mock_dispatch.side_effect = [
        json.dumps(serialize_assessment(assessment)),
        json.dumps(serialize_plan(plan)),
    ]

    result = run_risk_loop(
        planspace,
        "section-01",
        "implementation",
        package,
        mock_dispatch,
    )

    assert result.accepted_frontier == [package.steps[0].step_id]
    assert [call.kwargs["agent_file"] for call in mock_dispatch.call_args_list] == [
        "risk-assessor.md",
        "execution-optimizer.md",
    ]
