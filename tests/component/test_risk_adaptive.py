"""Component tests for adaptive ROAL runtime behavior."""

from __future__ import annotations

import json
from pathlib import Path

from risk.repository.history import append_history_entry
from risk.engine.risk_assessor import run_risk_loop
from risk.repository.serialization import load_risk_artifact, serialize_assessment, serialize_plan
from risk.types import (
    PackageStep,
    PostureProfile,
    RiskAssessment,
    RiskHistoryEntry,
    RiskModifiers,
    RiskPackage,
    RiskType,
    RiskVector,
    StepAssessment,
    StepClass,
    StepDecision,
    StepMitigation,
    UnderstandingInventory,
)


def test_history_adjustment_modifies_assessment_risk_score(tmp_path: Path) -> None:
    package = _package()
    _write_artifacts(tmp_path)
    append_history_entry(
        tmp_path / "artifacts" / "risk" / "risk-history.jsonl",
        _history_entry(
            predicted_risk=10,
            actual_outcome="failure",
            posture=PostureProfile.P3_GUARDED,
        ),
    )

    def _dispatch(*_args, **kwargs) -> str:  # noqa: ANN002, ANN003
        if kwargs["agent_file"] == "risk-assessor.md":
            return json.dumps(serialize_assessment(_assessment(raw_risk=30)))
        return json.dumps(serialize_plan(_plan(residual_risk=30, posture=PostureProfile.P1_LIGHT)))

    run_risk_loop(tmp_path, "section-03", "implementation", package, _dispatch)

    assessment_payload = load_risk_artifact(
        tmp_path / "artifacts" / "risk" / "section-03-risk-assessment.json",
    )

    assert assessment_payload is not None
    assert assessment_payload["package_raw_risk"] > 30


def test_posture_hysteresis_prevents_large_jumps(tmp_path: Path) -> None:
    package = _package()
    _write_artifacts(tmp_path)
    append_history_entry(
        tmp_path / "artifacts" / "risk" / "risk-history.jsonl",
        _history_entry(
            actual_outcome="success",
            posture=PostureProfile.P0_DIRECT,
        ),
    )

    def _dispatch(*_args, **kwargs) -> str:  # noqa: ANN002, ANN003
        if kwargs["agent_file"] == "risk-assessor.md":
            return json.dumps(serialize_assessment(_assessment(raw_risk=85)))
        return json.dumps(serialize_plan(_plan(residual_risk=85, posture=PostureProfile.P4_REOPEN)))

    plan = run_risk_loop(tmp_path, "section-03", "implementation", package, _dispatch)

    assert plan.step_decisions[0].posture == PostureProfile.P1_LIGHT


def test_cooldown_prevents_immediate_relaxation(tmp_path: Path) -> None:
    package = _package()
    _write_artifacts(tmp_path)
    history_path = tmp_path / "artifacts" / "risk" / "risk-history.jsonl"
    append_history_entry(
        history_path,
        _history_entry(
            actual_outcome="failure",
            posture=PostureProfile.P3_GUARDED,
        ),
    )
    append_history_entry(
        history_path,
        _history_entry(
            actual_outcome="success",
            posture=PostureProfile.P3_GUARDED,
        ),
    )

    def _dispatch(*_args, **kwargs) -> str:  # noqa: ANN002, ANN003
        if kwargs["agent_file"] == "risk-assessor.md":
            return json.dumps(serialize_assessment(_assessment(raw_risk=35)))
        return json.dumps(serialize_plan(_plan(residual_risk=35, posture=PostureProfile.P1_LIGHT)))

    plan = run_risk_loop(tmp_path, "section-03", "implementation", package, _dispatch)

    assert plan.step_decisions[0].posture == PostureProfile.P3_GUARDED


def test_posture_floor_is_enforced(tmp_path: Path) -> None:
    package = _package()
    _write_artifacts(tmp_path)

    def _dispatch(*_args, **kwargs) -> str:  # noqa: ANN002, ANN003
        if kwargs["agent_file"] == "risk-assessor.md":
            return json.dumps(serialize_assessment(_assessment(raw_risk=25)))
        return json.dumps(serialize_plan(_plan(residual_risk=25, posture=PostureProfile.P1_LIGHT)))

    plan = run_risk_loop(
        tmp_path,
        "section-03",
        "implementation",
        package,
        _dispatch,
        posture_floor=PostureProfile.P2_STANDARD,
    )

    assert plan.step_decisions[0].posture == PostureProfile.P2_STANDARD


def _package() -> RiskPackage:
    return RiskPackage(
        package_id="pkg-implementation-section-03",
        layer="implementation",
        scope="section-03",
        origin_problem_id="problem-03",
        origin_source="proposal",
        steps=[
            PackageStep(
                step_id="edit-01",
                assessment_class=StepClass.EDIT,
                summary="Apply the approved change",
            ),
        ],
    )


def _assessment(*, raw_risk: int) -> RiskAssessment:
    return RiskAssessment(
        assessment_id="assessment-1",
        layer="implementation",
        package_id="pkg-implementation-section-03",
        assessment_scope="section-03",
        understanding_inventory=UnderstandingInventory(
            confirmed=["proposal excerpt reviewed"],
            assumed=[],
            missing=[],
            stale=[],
        ),
        package_raw_risk=raw_risk,
        assessment_confidence=0.8,
        dominant_risks=[RiskType.BRUTE_FORCE_REGRESSION],
        step_assessments=[
            StepAssessment(
                step_id="edit-01",
                assessment_class=StepClass.EDIT,
                summary="Apply the approved change",
                prerequisites=[],
                risk_vector=RiskVector(brute_force_regression=3),
                modifiers=RiskModifiers(blast_radius=1, confidence=0.8),
                raw_risk=raw_risk,
                dominant_risks=[RiskType.BRUTE_FORCE_REGRESSION],
            ),
        ],
        frontier_candidates=["edit-01"],
        reopen_recommendations=[],
        notes=[],
    )


def _plan(*, residual_risk: int, posture: PostureProfile):
    from risk.types import RiskPlan

    return RiskPlan(
        plan_id="plan-1",
        assessment_id="assessment-1",
        package_id="pkg-implementation-section-03",
        layer="implementation",
        step_decisions=[
            StepMitigation(
                step_id="edit-01",
                decision=StepDecision.ACCEPT,
                posture=posture,
                mitigations=["bounded edit"],
                residual_risk=residual_risk,
                reason="within threshold",
            ),
        ],
        accepted_frontier=["edit-01"],
        deferred_steps=[],
        reopen_steps=[],
        expected_reassessment_inputs=[],
    )


def _history_entry(
    *,
    predicted_risk: int = 35,
    actual_outcome: str,
    posture: PostureProfile,
) -> RiskHistoryEntry:
    return RiskHistoryEntry(
        package_id="pkg-old",
        step_id="edit-01",
        layer="implementation",
        assessment_class=StepClass.EDIT,
        posture=posture,
        predicted_risk=predicted_risk,
        actual_outcome=actual_outcome,
        dominant_risks=[RiskType.BRUTE_FORCE_REGRESSION],
        blast_radius_band=1,
    )


def _write_artifacts(tmp_path: Path) -> None:
    artifacts = tmp_path / "artifacts"
    sections = artifacts / "sections"
    proposals = artifacts / "proposals"
    readiness = artifacts / "readiness"
    risk = artifacts / "risk"

    sections.mkdir(parents=True, exist_ok=True)
    proposals.mkdir(parents=True, exist_ok=True)
    readiness.mkdir(parents=True, exist_ok=True)
    risk.mkdir(parents=True, exist_ok=True)

    (sections / "section-03.md").write_text("# Section 03\n", encoding="utf-8")
    (sections / "section-03-proposal-excerpt.md").write_text("Proposal excerpt\n", encoding="utf-8")
    (sections / "section-03-alignment-excerpt.md").write_text("Alignment excerpt\n", encoding="utf-8")
    (sections / "section-03-problem-frame.md").write_text("Problem frame\n", encoding="utf-8")
    (proposals / "section-03-microstrategy.md").write_text("- Apply the approved change\n", encoding="utf-8")
    (artifacts / "codemap.md").write_text("Codemap\n", encoding="utf-8")
    (artifacts / "tool-registry.json").write_text('{"tools":["pytest"]}', encoding="utf-8")
    (proposals / "section-03-proposal-state.json").write_text(
        '{"resolved_contracts":["src/app.py"],"resolved_anchors":["tests::smoke"],"shared_seam_candidates":[]}',
        encoding="utf-8",
    )
    (readiness / "section-03-execution-ready.json").write_text(
        '{"ready":true,"blockers":[]}',
        encoding="utf-8",
    )
