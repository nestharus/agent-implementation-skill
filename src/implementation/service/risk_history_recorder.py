"""Risk history recording for implementation sections."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from containers import ArtifactIOService

from orchestrator.path_registry import PathRegistry
from risk.repository.history import RiskHistory, append_history_entry, pattern_signature
from risk.service.package_builder import PackageBuilder
from risk.repository.serialization import RiskSerializer
from risk.types import (
    MAX_RESIDUAL_RISK,
    PostureProfile,
    RiskHistoryEntry,
    RiskPackage,
    RiskPlan,
    StepDecision,
)
from implementation.service.risk_artifacts import unique_strings


@dataclass(frozen=True)
class ImplementationOutcome:
    """Classified outcome of an implementation or risk decision."""

    outcome: str
    surprises: list[str] = field(default_factory=list)
    verification: str | None = None


def append_risk_review_failure_history(
    planspace: Path,
    package: RiskPackage | None,
    reason: str,
) -> None:
    if package is None:
        return

    paths = PathRegistry(planspace)
    for step in package.steps:
        append_history_entry(
            paths.risk_history(),
            RiskHistoryEntry(
                package_id=package.package_id,
                step_id=step.step_id,
                layer=package.layer,
                assessment_class=step.assessment_class,
                posture=PostureProfile.P4_REOPEN,
                predicted_risk=MAX_RESIDUAL_RISK,
                actual_outcome="risk_review_failure",
                surfaced_surprises=[reason],
                verification_outcome="failed",
                dominant_risks=[],
                blast_radius_band=0,
            ),
        )


def _determine_decision_outcome(
    decision,
    risk_plan: RiskPlan,
    accepted_outcome: ImplementationOutcome,
) -> ImplementationOutcome:
    """Map a step decision to its implementation outcome."""
    if decision.decision == StepDecision.ACCEPT:
        return ImplementationOutcome(
            accepted_outcome.outcome,
            list(accepted_outcome.surprises),
            accepted_outcome.verification,
        )
    if decision.decision == StepDecision.REJECT_DEFER:
        return ImplementationOutcome(
            "deferred",
            unique_strings(
                decision.wait_for + list(risk_plan.expected_reassessment_inputs),
            ),
        )
    return ImplementationOutcome(
        "reopened",
        [decision.reason] if decision.reason
        else ["ROAL reopened this step for higher-level routing"],
    )


def _record_over_guarding(
    decision,
    package_step,
    assessment_step,
    risk_plan: RiskPlan,
    prior_history: list[RiskHistoryEntry],
    accepted_verification: str | None,
    history_path: Path,
) -> None:
    """Record an over_guarded entry if prior rejections exist for this pattern."""
    current_signature = pattern_signature(
        package_step.assessment_class,
        assessment_step.dominant_risks,
        assessment_step.modifiers.blast_radius,
    )
    prior_rejections = [
        entry
        for entry in prior_history
        if pattern_signature(
            entry.assessment_class,
            entry.dominant_risks,
            entry.blast_radius_band,
        ) == current_signature
        and entry.actual_outcome.strip().lower() in {"deferred", "reopened"}
    ]
    if prior_rejections:
        append_history_entry(
            history_path,
            RiskHistoryEntry(
                package_id=risk_plan.package_id,
                step_id=decision.step_id,
                layer=risk_plan.layer,
                assessment_class=package_step.assessment_class,
                posture=decision.posture or PostureProfile.P2_STANDARD,
                predicted_risk=(
                    decision.residual_risk
                    if decision.residual_risk is not None
                    else assessment_step.raw_risk
                ),
                actual_outcome="over_guarded",
                surfaced_surprises=[
                    "similar deferred or reopened work later completed safely",
                ],
                verification_outcome=accepted_verification,
                dominant_risks=list(assessment_step.dominant_risks),
                blast_radius_band=assessment_step.modifiers.blast_radius,
            ),
        )


def _classify_implementation_outcome(
    modified: list[str], implementation_failed: bool,
) -> ImplementationOutcome:
    """Classify the implementation outcome for risk history recording."""
    if implementation_failed:
        return ImplementationOutcome(
            "failure", ["implementation failed after ROAL acceptance"], "failed",
        )
    if modified:
        return ImplementationOutcome("success", [], "passed")
    return ImplementationOutcome(
        "warning", ["implementation completed without file modifications"],
    )


def append_risk_history(
    planspace: Path,
    sec_num: str,
    risk_plan: RiskPlan,
    modified_files: list[str] | None,
    *,
    implementation_failed: bool = False,
    artifact_io: ArtifactIOService,
) -> None:
    scope = f"section-{sec_num}"
    paths = PathRegistry(planspace)
    package = PackageBuilder(artifact_io=artifact_io).read_package(paths, scope)
    assessment = RiskSerializer(artifact_io=artifact_io).load_risk_assessment(paths.risk_assessment(scope))
    prior_history = RiskHistory(artifact_io=artifact_io).read_history(paths.risk_history())

    package_steps = {
        step.step_id: step
        for step in (package.steps if package is not None else [])
    }
    assessment_steps = {
        step.step_id: step
        for step in (assessment.step_assessments if assessment is not None else [])
    }

    modified = list(modified_files or [])
    accepted = _classify_implementation_outcome(modified, implementation_failed)

    for decision in risk_plan.step_decisions:
        package_step = package_steps.get(decision.step_id)
        assessment_step = assessment_steps.get(decision.step_id)
        if package_step is None:
            continue

        result = _determine_decision_outcome(decision, risk_plan, accepted)
        append_history_entry(
            paths.risk_history(),
            RiskHistoryEntry(
                package_id=risk_plan.package_id,
                step_id=decision.step_id,
                layer=risk_plan.layer,
                assessment_class=package_step.assessment_class,
                posture=decision.posture or PostureProfile.P4_REOPEN,
                predicted_risk=(
                    decision.residual_risk
                    if decision.residual_risk is not None
                    else MAX_RESIDUAL_RISK
                ),
                actual_outcome=result.outcome,
                surfaced_surprises=list(result.surprises),
                verification_outcome=result.verification,
                dominant_risks=(
                    list(assessment_step.dominant_risks)
                    if assessment_step is not None
                    else []
                ),
                blast_radius_band=(
                    assessment_step.modifiers.blast_radius
                    if assessment_step is not None
                    else 0
                ),
            ),
        )
        if (
            decision.decision == StepDecision.ACCEPT
            and result.outcome in {"success", "warning"}
            and assessment_step is not None
        ):
            _record_over_guarding(
                decision, package_step, assessment_step,
                risk_plan, prior_history, accepted.verification,
                paths.risk_history(),
            )
