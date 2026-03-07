from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

from lib.core.artifact_io import write_json
from lib.pipelines.implementation_pass import _append_risk_history, _run_risk_review
from lib.risk.history import read_history
from lib.risk.loop import run_lightweight_risk_check
from lib.risk.package_builder import build_package_from_proposal
from lib.risk.serialization import serialize_assessment, serialize_plan
from lib.risk.types import (
    PostureProfile,
    RiskAssessment,
    RiskModifiers,
    RiskPackage,
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
    triage_confidence: str = "medium",
    microstrategy_lines: list[str] | None = None,
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
        f"# Section {sec_num}\n\nSection spec body.\n",
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
    lines = microstrategy_lines or [
        "Refresh understanding",
        "Implement approved change",
        "Verify results",
    ]
    (proposals / f"section-{sec_num}-microstrategy.md").write_text(
        "".join(f"- {line}\n" for line in lines),
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


def _make_section(
    planspace: Path,
    sec_num: str,
    *,
    related_files: list[str] | None = None,
    solve_count: int = 0,
) -> Section:
    return Section(
        number=sec_num,
        path=planspace / "artifacts" / "sections" / f"section-{sec_num}.md",
        related_files=related_files or ["src/main.py"],
        solve_count=solve_count,
    )


def _assessment_for(
    package: RiskPackage,
    *,
    raw_risks: list[int],
) -> RiskAssessment:
    step_assessments: list[StepAssessment] = []
    for step, raw_risk in zip(package.steps, raw_risks, strict=True):
        dominant_risks = (
            [RiskType.BRUTE_FORCE_REGRESSION]
            if raw_risk >= 60
            else [RiskType.CONTEXT_ROT]
        )
        vector = (
            RiskVector(brute_force_regression=4)
            if raw_risk >= 60
            else RiskVector(context_rot=1)
        )
        step_assessments.append(
            StepAssessment(
                step_id=step.step_id,
                step_class=step.step_class,
                summary=step.summary,
                prerequisites=list(step.prerequisites),
                risk_vector=vector,
                modifiers=RiskModifiers(
                    blast_radius=3 if raw_risk >= 60 else 1,
                    confidence=0.9,
                ),
                raw_risk=raw_risk,
                dominant_risks=dominant_risks,
            ),
        )

    return RiskAssessment(
        assessment_id=f"assessment-{package.scope}",
        layer=package.layer,
        package_id=package.package_id,
        assessment_scope=package.scope,
        understanding_inventory=UnderstandingInventory(
            confirmed=["proposal reviewed"],
            assumed=[],
            missing=[],
            stale=[],
        ),
        package_raw_risk=max(raw_risks),
        assessment_confidence=0.9,
        dominant_risks=list(step_assessments[-1].dominant_risks),
        step_assessments=step_assessments,
        frontier_candidates=[step.step_id for step in package.steps],
    )


def _plan_for(
    package: RiskPackage,
    assessment_id: str,
    decisions: list[StepMitigation],
) -> RiskPlan:
    return RiskPlan(
        plan_id=f"plan-{package.scope}",
        assessment_id=assessment_id,
        package_id=package.package_id,
        layer=package.layer,
        step_decisions=decisions,
        accepted_frontier=[
            decision.step_id
            for decision in decisions
            if decision.decision == StepDecision.ACCEPT
        ],
        deferred_steps=[
            decision.step_id
            for decision in decisions
            if decision.decision == StepDecision.REJECT_DEFER
        ],
        reopen_steps=[
            decision.step_id
            for decision in decisions
            if decision.decision == StepDecision.REJECT_REOPEN
        ],
    )


def test_full_risk_loop_single_step(
    planspace: Path,
    mock_dispatch: MagicMock,
) -> None:
    _write_risk_inputs(
        planspace,
        "01",
        triage_confidence="medium",
        microstrategy_lines=["Apply the approved change"],
    )
    section = _make_section(planspace, "01")
    package = build_package_from_proposal("section-01", planspace)
    assessment = _assessment_for(package, raw_risks=[20])
    plan = _plan_for(
        package,
        assessment.assessment_id,
        [
            StepMitigation(
                step_id=package.steps[0].step_id,
                decision=StepDecision.ACCEPT,
                posture=PostureProfile.P0_DIRECT,
                mitigations=["bounded single-file edit"],
                residual_risk=20,
                reason="below threshold",
            ),
        ],
    )
    mock_dispatch.side_effect = [
        json.dumps(serialize_assessment(assessment)),
        json.dumps(serialize_plan(plan)),
    ]

    result = _run_risk_review(planspace, "01", section, mock_dispatch)

    assert result is not None
    assert result.accepted_frontier == [package.steps[0].step_id]
    assert result.deferred_steps == []


def test_full_risk_loop_multi_step_with_defer(
    planspace: Path,
    mock_dispatch: MagicMock,
) -> None:
    _write_risk_inputs(
        planspace,
        "01",
        triage_confidence="medium",
        microstrategy_lines=["Refresh understanding", "Apply the approved change"],
    )
    section = _make_section(planspace, "01", related_files=["src/main.py", "src/utils.py"])
    package = build_package_from_proposal("section-01", planspace)
    assessment = _assessment_for(package, raw_risks=[25, 75])
    plan = _plan_for(
        package,
        assessment.assessment_id,
        [
            StepMitigation(
                step_id=package.steps[0].step_id,
                decision=StepDecision.ACCEPT,
                posture=PostureProfile.P1_LIGHT,
                mitigations=["refresh context first"],
                residual_risk=25,
                reason="frontier step is safe",
            ),
            StepMitigation(
                step_id=package.steps[1].step_id,
                decision=StepDecision.REJECT_DEFER,
                posture=PostureProfile.P4_REOPEN,
                mitigations=["defer until first step lands"],
                residual_risk=75,
                reason="second step remains high risk",
                wait_for=[package.steps[0].step_id],
            ),
        ],
    )
    mock_dispatch.side_effect = [
        json.dumps(serialize_assessment(assessment)),
        json.dumps(serialize_plan(plan)),
    ]

    result = _run_risk_review(planspace, "01", section, mock_dispatch)

    assert result is not None
    assert result.accepted_frontier == [package.steps[0].step_id]
    assert result.deferred_steps == [package.steps[1].step_id]


def test_risk_loop_fallback_on_parse_failure(
    planspace: Path,
    mock_dispatch: MagicMock,
) -> None:
    _write_risk_inputs(
        planspace,
        "01",
        triage_confidence="medium",
        microstrategy_lines=["Apply the approved change"],
    )
    section = _make_section(planspace, "01")
    package = build_package_from_proposal("section-01", planspace)
    mock_dispatch.return_value = "not valid json"

    result = _run_risk_review(planspace, "01", section, mock_dispatch)

    assert result is not None
    assert result.accepted_frontier == []
    assert result.reopen_steps == [package.steps[0].step_id]
    assert result.step_decisions[0].posture == PostureProfile.P4_REOPEN


def test_risk_loop_respects_threshold_enforcement(
    planspace: Path,
    mock_dispatch: MagicMock,
) -> None:
    _write_risk_inputs(
        planspace,
        "01",
        triage_confidence="medium",
        microstrategy_lines=["Apply the approved change"],
    )
    section = _make_section(planspace, "01")
    package = build_package_from_proposal("section-01", planspace)
    assessment = _assessment_for(package, raw_risks=[70])
    over_threshold_plan = _plan_for(
        package,
        assessment.assessment_id,
        [
            StepMitigation(
                step_id=package.steps[0].step_id,
                decision=StepDecision.ACCEPT,
                posture=PostureProfile.P3_GUARDED,
                mitigations=["tool agent still wants to proceed"],
                residual_risk=70,
                reason="optimizer accepted despite high residual risk",
            ),
        ],
    )
    mock_dispatch.side_effect = [
        json.dumps(serialize_assessment(assessment)),
        json.dumps(serialize_plan(over_threshold_plan)),
    ]

    result = _run_risk_review(planspace, "01", section, mock_dispatch)

    assert result is not None
    assert result.accepted_frontier == []
    assert result.deferred_steps == [package.steps[0].step_id]
    assert result.step_decisions[0].decision == StepDecision.REJECT_DEFER


def test_risk_history_accumulates(
    planspace: Path,
    mock_dispatch: MagicMock,
) -> None:
    for sec_num in ("01", "02"):
        _write_risk_inputs(
            planspace,
            sec_num,
            triage_confidence="medium",
            microstrategy_lines=["Apply the approved change"],
        )

    first_section = _make_section(planspace, "01")
    second_section = _make_section(planspace, "02")
    first_package = build_package_from_proposal("section-01", planspace)
    second_package = build_package_from_proposal("section-02", planspace)
    first_assessment = _assessment_for(first_package, raw_risks=[20])
    second_assessment = _assessment_for(second_package, raw_risks=[22])
    first_plan = _plan_for(
        first_package,
        first_assessment.assessment_id,
        [
            StepMitigation(
                step_id=first_package.steps[0].step_id,
                decision=StepDecision.ACCEPT,
                posture=PostureProfile.P1_LIGHT,
                mitigations=["safe change"],
                residual_risk=20,
                reason="below threshold",
            ),
        ],
    )
    second_plan = _plan_for(
        second_package,
        second_assessment.assessment_id,
        [
            StepMitigation(
                step_id=second_package.steps[0].step_id,
                decision=StepDecision.ACCEPT,
                posture=PostureProfile.P1_LIGHT,
                mitigations=["safe follow-up change"],
                residual_risk=22,
                reason="below threshold",
            ),
        ],
    )
    mock_dispatch.side_effect = [
        json.dumps(serialize_assessment(first_assessment)),
        json.dumps(serialize_plan(first_plan)),
        json.dumps(serialize_assessment(second_assessment)),
        json.dumps(serialize_plan(second_plan)),
    ]

    first_result = _run_risk_review(planspace, "01", first_section, mock_dispatch)
    second_result = _run_risk_review(planspace, "02", second_section, mock_dispatch)
    assert first_result is not None
    assert second_result is not None

    _append_risk_history(planspace, "01", first_result, ["src/main.py"])
    _append_risk_history(planspace, "02", second_result, ["src/utils.py"])
    history = read_history(planspace / "artifacts" / "risk" / "risk-history.jsonl")

    assert len(history) == 2
    assert {entry.package_id for entry in history} == {
        first_package.package_id,
        second_package.package_id,
    }


def test_lightweight_risk_check(
    planspace: Path,
    mock_dispatch: MagicMock,
) -> None:
    _write_risk_inputs(
        planspace,
        "01",
        triage_confidence="medium",
        microstrategy_lines=["Apply the approved change"],
    )
    package = build_package_from_proposal("section-01", planspace)
    assessment = _assessment_for(package, raw_risks=[20])
    mock_dispatch.return_value = json.dumps(serialize_assessment(assessment))

    plan = run_lightweight_risk_check(
        planspace,
        "section-01",
        "implementation",
        package,
        mock_dispatch,
    )

    assert plan.accepted_frontier == [package.steps[0].step_id]
    assert plan.deferred_steps == []
    assert [call.kwargs["agent_file"] for call in mock_dispatch.call_args_list] == [
        "risk-assessor.md",
    ]
