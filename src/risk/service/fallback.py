"""Fallback risk plan builders for ROAL loop failures."""

from __future__ import annotations

from risk.types import (
    PostureProfile,
    RiskPackage,
    RiskPlan,
    StepDecision,
    StepMitigation,
)


def fallback_plan(
    package: RiskPackage,
    layer: str,
    *,
    assessment_id: str,
    reason: str,
) -> RiskPlan:
    decisions = [
        StepMitigation(
            step_id=step.step_id,
            decision=StepDecision.REJECT_REOPEN,
            posture=PostureProfile.P4_REOPEN,
            mitigations=[],
            residual_risk=100,
            reason=reason,
            route_to="parent",
        )
        for step in package.steps
    ]
    return RiskPlan(
        plan_id=f"risk-plan-{package.scope}-fallback",
        assessment_id=assessment_id,
        package_id=package.package_id,
        layer=layer,
        step_decisions=decisions,
        accepted_frontier=[],
        deferred_steps=[],
        reopen_steps=[decision.step_id for decision in decisions],
        expected_reassessment_inputs=[],
    )


def lightweight_fallback_plan(
    package: RiskPackage,
    layer: str,
    *,
    assessment_id: str,
    reason: str,
) -> RiskPlan:
    decisions = [
        StepMitigation(
            step_id=step.step_id,
            decision=StepDecision.REJECT_DEFER,
            posture=PostureProfile.P4_REOPEN,
            mitigations=[],
            residual_risk=100,
            reason=reason,
            wait_for=["full-risk-loop"],
        )
        for step in package.steps
    ]
    return RiskPlan(
        plan_id=f"risk-plan-{package.scope}-lightweight-fallback",
        assessment_id=assessment_id,
        package_id=package.package_id,
        layer=layer,
        step_decisions=decisions,
        accepted_frontier=[],
        deferred_steps=[decision.step_id for decision in decisions],
        reopen_steps=[],
        expected_reassessment_inputs=[],
    )
