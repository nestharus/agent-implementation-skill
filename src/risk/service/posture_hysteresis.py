"""Posture hysteresis logic for ROAL risk decisions."""

from __future__ import annotations

from risk.repository.history import pattern_signature
from risk.service.posture import apply_one_step_rule, can_relax_posture, count_trailing_successes, select_posture
from risk.service.quantifier import risk_to_posture
from risk.types import (
    PostureProfile,
    RiskAssessment,
    RiskHistoryEntry,
    RiskPlan,
    StepAssessment,
    StepMitigation,
)


def apply_posture_hysteresis(
    plan: RiskPlan,
    assessment: RiskAssessment,
    history_entries: list[RiskHistoryEntry],
    parameters: dict,
    *,
    posture_floor: PostureProfile | str | None,
) -> None:
    assessment_steps = {
        step.step_id: step
        for step in assessment.step_assessments
    }
    floor = _coerce_posture(posture_floor)
    cooldown_iterations = _coerce_int(
        parameters.get("cooldown_iterations"),
        default=2,
    )

    for decision in plan.step_decisions:
        if decision.posture is None:
            continue

        step_assessment = assessment_steps.get(decision.step_id)
        raw_risk = _decision_raw_risk(decision, step_assessment, assessment.package_raw_risk)
        previous_posture: PostureProfile | None = None
        recent_outcomes: list[str] = []
        cooldown_remaining = 0

        if step_assessment is not None:
            matching_history = _matching_history(history_entries, step_assessment)
            if matching_history:
                previous_posture = matching_history[-1].posture
                recent_outcomes = [
                    _normalize_history_outcome(entry.actual_outcome)
                    for entry in matching_history
                ]
                cooldown_remaining = _cooldown_remaining(
                    recent_outcomes,
                    cooldown_iterations,
                )

        hysteresis_posture = (
            select_posture(
                raw_risk,
                previous_posture,
                recent_outcomes,
                cooldown_remaining,
            )
            if previous_posture is not None
            else risk_to_posture(raw_risk)
        )
        target = _stricter_posture(decision.posture, hysteresis_posture)

        if previous_posture is not None:
            consecutive_successes = count_trailing_successes(recent_outcomes)
            if (
                target.rank < previous_posture.rank
                and not can_relax_posture(
                    previous_posture,
                    consecutive_successes,
                    cooldown_remaining,
                )
            ):
                target = previous_posture
            target = apply_one_step_rule(previous_posture, target)

        if floor is not None:
            target = _stricter_posture(target, floor)

        decision.posture = target


def _matching_history(
    history_entries: list[RiskHistoryEntry],
    step_assessment: StepAssessment,
) -> list[RiskHistoryEntry]:
    signature = pattern_signature(
        step_assessment.assessment_class,
        step_assessment.dominant_risks,
        step_assessment.modifiers.blast_radius,
    )
    return [
        entry
        for entry in history_entries
        if _history_signature(entry) == signature
    ]


def _history_signature(entry: RiskHistoryEntry) -> str:
    return pattern_signature(
        entry.assessment_class,
        entry.dominant_risks,
        entry.blast_radius_band,
    )


def _decision_raw_risk(
    decision: StepMitigation,
    step_assessment: StepAssessment | None,
    package_raw_risk: int,
) -> int:
    if decision.residual_risk is not None:
        return decision.residual_risk
    if step_assessment is not None:
        return step_assessment.raw_risk
    return package_raw_risk


def _normalize_history_outcome(outcome: str) -> str:
    normalized = outcome.strip().lower()
    if normalized in {"reopened", "reopen"}:
        return "reopen"
    if normalized in {"risk_review_failure", "failed"}:
        return "failure"
    if normalized == "over_guarded":
        return "success"
    return normalized


def _cooldown_remaining(outcomes: list[str], cooldown_iterations: int) -> int:
    last_failure_index = -1
    for index, outcome in enumerate(outcomes):
        if outcome in {"failure", "error", "reopen", "blocked"}:
            last_failure_index = index
    if last_failure_index < 0:
        return 0
    iterations_since_failure = len(outcomes) - last_failure_index - 1
    return max(cooldown_iterations - iterations_since_failure, 0)


def _coerce_posture(value: PostureProfile | str | None) -> PostureProfile | None:
    if isinstance(value, PostureProfile):
        return value
    if isinstance(value, str) and value.strip():
        try:
            return PostureProfile(value.strip())
        except ValueError:
            return None
    return None


def _stricter_posture(
    left: PostureProfile,
    right: PostureProfile,
) -> PostureProfile:
    if left.rank >= right.rank:
        return left
    return right


def _coerce_int(value: object, *, default: int) -> int:
    return int(value) if isinstance(value, int) else default
