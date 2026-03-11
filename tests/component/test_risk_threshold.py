"""Component tests for ROAL threshold enforcement."""

from __future__ import annotations

from risk.threshold import (
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


def test_validate_risk_plan_catches_overrisk_accepted_steps() -> None:
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
                residual_risk=60,
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

    assert any("edit-02" in violation for violation in violations)


def test_enforce_thresholds_downgrades_over_threshold_steps() -> None:
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

    assert enforced.step_decisions[0].decision == StepDecision.REJECT_DEFER
    assert enforced.accepted_frontier == []
    assert enforced.deferred_steps == ["edit-02"]
    assert "threshold-compliant-plan" in enforced.step_decisions[0].wait_for


def test_load_default_parameters_returns_expected_structure() -> None:
    parameters = load_default_parameters()

    assert parameters["posture_bands"]["P0"] == [0, 19]
    assert parameters["class_thresholds"]["edit"] == 45
    assert parameters["cooldown_iterations"] == 2
    assert parameters["relaxation_required_successes"] == 3
    assert parameters["history_adjustment_bound"] == 10.0
