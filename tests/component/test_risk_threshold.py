"""Component tests for ROAL threshold and structural validation."""

from __future__ import annotations

from risk.service.threshold import (
    enforce_thresholds,
    load_default_parameters,
    validate_risk_plan,
)
from risk.types import (
    PostureProfile,
    RiskModifiers,
    RiskPlan,
    RiskType,
    RiskVector,
    StepAssessment,
    StepClass,
    StepDecision,
    StepMitigation,
)


def test_validate_risk_plan_with_valid_plan_returns_no_violations() -> None:
    plan = RiskPlan(
        plan_id="plan-1",
        assessment_id="assessment-1",
        package_id="pkg-1",
        layer="implementation",
        step_decisions=[
            StepMitigation(
                step_id="explore-01",
                decision=StepDecision.ACCEPT,
                posture=PostureProfile.P1_LIGHT,
                residual_risk=25,
            ),
            StepMitigation(
                step_id="edit-02",
                decision=StepDecision.REJECT_DEFER,
                posture=PostureProfile.P2_STANDARD,
                residual_risk=58,
                wait_for=["fresh-readiness"],
            ),
        ],
        accepted_frontier=["explore-01"],
        deferred_steps=["edit-02"],
        reopen_steps=[],
    )

    violations = validate_risk_plan(
        plan,
        {
            **load_default_parameters(),
            "assessment_classes": {
                "explore-01": StepClass.EXPLORE,
                "edit-02": StepClass.EDIT,
            },
        },
    )

    assert violations == []


def test_validate_risk_plan_catches_structural_issues() -> None:
    """Structural validation still catches missing posture / residual_risk."""
    plan = RiskPlan(
        plan_id="plan-1",
        assessment_id="assessment-1",
        package_id="pkg-1",
        layer="implementation",
        step_decisions=[
            StepMitigation(
                step_id="edit-02",
                decision=StepDecision.ACCEPT,
                posture=None,
                residual_risk=None,
            )
        ],
        accepted_frontier=["edit-02"],
        deferred_steps=[],
        reopen_steps=[],
    )

    violations = validate_risk_plan(
        plan,
        {
            **load_default_parameters(),
            "assessment_classes": {"edit-02": StepClass.EDIT},
        },
    )

    assert any("missing posture" in v for v in violations)
    assert any("missing residual_risk" in v for v in violations)


def test_enforce_thresholds_is_noop_agent_decision_preserved() -> None:
    """Agent ACCEPT with high residual_risk is NOT overridden."""
    plan = RiskPlan(
        plan_id="plan-1",
        assessment_id="assessment-1",
        package_id="pkg-1",
        layer="implementation",
        step_decisions=[
            StepMitigation(
                step_id="edit-02",
                decision=StepDecision.ACCEPT,
                posture=PostureProfile.P2_STANDARD,
                residual_risk=70,
                reason="optimizer accepted the change",
            )
        ],
        accepted_frontier=["edit-02"],
        deferred_steps=[],
        reopen_steps=[],
    )
    assessments = {
        "edit-02": StepAssessment(
            step_id="edit-02",
            assessment_class=StepClass.EDIT,
            summary="Apply change",
            prerequisites=[],
            risk_vector=RiskVector(brute_force_regression=3),
            modifiers=RiskModifiers(confidence=0.8),
            raw_risk=70,
            dominant_risks=[RiskType.BRUTE_FORCE_REGRESSION],
        )
    }

    enforced = enforce_thresholds(plan, assessments, load_default_parameters())

    # Agent said ACCEPT — it stays ACCEPT even though residual_risk > edit threshold (45)
    assert enforced.step_decisions[0].decision == StepDecision.ACCEPT
    assert enforced.accepted_frontier == ["edit-02"]
    assert enforced.deferred_steps == []


def test_load_default_parameters_returns_expected_structure() -> None:
    parameters = load_default_parameters()

    assert parameters["posture_bands"]["P0"] == [0, 19]
    assert parameters["class_thresholds"]["edit"] == 45
    assert parameters["cooldown_iterations"] == 2
    assert parameters["relaxation_required_successes"] == 3
