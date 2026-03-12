"""ROAL loop orchestration."""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from signals.repository.artifact_io import read_json
from dispatch.service.model_policy import load_model_policy, resolve
from orchestrator.path_registry import PathRegistry
from risk.repository.history import compute_history_adjustment, pattern_signature, read_history
from risk.repository.serialization import (
    serialize_assessment,
    serialize_plan,
    write_risk_artifact,
)
from risk.service.threshold import enforce_thresholds, load_default_parameters, validate_risk_plan
from risk.types import (
    PostureProfile,
    RiskAssessment,
    RiskHistoryEntry,
    RiskPackage,
    RiskPlan,
    StepAssessment,
    clamp_float,
    clamp_int,
)
from risk.service.package_builder import write_package
from risk.prompt.builders import build_risk_assessment_prompt, build_optimization_prompt
from risk.service.response_parser import parse_risk_assessment, parse_risk_plan
from risk.service.posture_hysteresis import apply_posture_hysteresis
from risk.service.fallback import fallback_plan, lightweight_fallback_plan
from dispatch.service.prompt_guard import write_validated_prompt
from taskrouter import agent_for


def run_risk_loop(
    planspace: Path,
    scope: str,
    layer: str,
    package: RiskPackage,
    dispatch_fn: Callable,
    max_iterations: int = 5,
    posture_floor: PostureProfile | str | None = None,
) -> RiskPlan:
    """Run the full ROAL loop for a package."""
    paths = PathRegistry(planspace)
    write_package(paths, package)
    parameters = _load_parameters(paths)
    history_entries = read_history(paths.risk_history())
    last_assessment: RiskAssessment | None = None
    last_plan: RiskPlan | None = None

    for iteration in range(1, max_iterations + 1):
        assessment_prompt = build_risk_assessment_prompt(package, planspace, scope)
        assessment_prompt_path = (
            paths.risk_dir() / f"{scope}-risk-assessment-prompt.md"
        )
        if not write_validated_prompt(assessment_prompt, assessment_prompt_path):
            fallback = fallback_plan(
                package,
                layer,
                assessment_id=f"{package.package_id}-assessment-fallback",
                reason="fail-closed: risk assessment prompt failed safety validation",
            )
            write_risk_artifact(paths.risk_plan(scope), serialize_plan(fallback))
            return fallback
        assessment_output_path = (
            paths.risk_dir() / f"{scope}-risk-assessment-output.md"
        )
        assessment_response = dispatch_fn(
            _risk_assessor_model(planspace),
            assessment_prompt_path,
            assessment_output_path,
            planspace,
            None,
            f"risk-assessor-{scope}",
            agent_file=agent_for("risk.assess"),
        )
        assessment = parse_risk_assessment(assessment_response)
        if assessment is None:
            fallback = fallback_plan(
                package,
                layer,
                assessment_id=f"{package.package_id}-assessment-fallback",
                reason="fail-closed: risk assessment could not be parsed",
            )
            write_risk_artifact(paths.risk_plan(scope), serialize_plan(fallback))
            return fallback

        _apply_history_adjustment(assessment, paths.risk_history(), history_entries, parameters)
        last_assessment = assessment
        write_risk_artifact(paths.risk_assessment(scope), serialize_assessment(assessment))

        optimization_prompt = build_optimization_prompt(
            assessment=assessment,
            package=package,
            parameters=parameters,
            planspace=planspace,
            scope=scope,
        )
        if iteration > 1 and last_plan is not None:
            optimization_prompt += (
                "\n\n## Previous Enforcement Outcome\n\n"
                "The previous optimizer response failed mechanical enforcement. "
                "Produce a strictly more conservative plan.\n"
            )
        optimization_prompt_path = paths.risk_dir() / f"{scope}-risk-plan-prompt.md"
        if not write_validated_prompt(optimization_prompt, optimization_prompt_path):
            fallback = fallback_plan(
                package,
                layer,
                assessment_id=assessment.assessment_id,
                reason="fail-closed: execution optimizer prompt failed safety validation",
            )
            write_risk_artifact(paths.risk_plan(scope), serialize_plan(fallback))
            return fallback
        optimization_output_path = paths.risk_dir() / f"{scope}-risk-plan-output.md"
        optimization_response = dispatch_fn(
            _execution_optimizer_model(planspace),
            optimization_prompt_path,
            optimization_output_path,
            planspace,
            None,
            f"execution-optimizer-{scope}",
            agent_file=agent_for("risk.optimize"),
        )
        plan = parse_risk_plan(optimization_response)
        if plan is None:
            fallback = fallback_plan(
                package,
                layer,
                assessment_id=assessment.assessment_id,
                reason="fail-closed: execution optimizer could not be parsed",
            )
            write_risk_artifact(paths.risk_plan(scope), serialize_plan(fallback))
            return fallback

        apply_posture_hysteresis(
            plan,
            assessment,
            history_entries,
            parameters,
            posture_floor=posture_floor,
        )
        assessments = {
            item.step_id: item
            for item in assessment.step_assessments
        }
        enriched_parameters = dict(parameters)
        enriched_parameters["assessment_classes"] = {
            step_id: step_assessment.assessment_class
            for step_id, step_assessment in assessments.items()
        }
        enforced_plan = enforce_thresholds(plan, assessments, enriched_parameters)
        violations = validate_risk_plan(enforced_plan, enriched_parameters)
        if not violations:
            write_risk_artifact(paths.risk_plan(scope), serialize_plan(enforced_plan))
            return enforced_plan

        last_plan = enforced_plan

    fallback_assessment_id = (
        last_assessment.assessment_id
        if last_assessment is not None
        else f"{package.package_id}-assessment-fallback"
    )
    final_fallback = fallback_plan(
        package,
        layer,
        assessment_id=fallback_assessment_id,
        reason="fail-closed: risk loop exhausted without a threshold-compliant plan",
    )
    write_risk_artifact(paths.risk_plan(scope), serialize_plan(final_fallback))
    return final_fallback


def run_lightweight_risk_check(
    planspace: Path,
    scope: str,
    layer: str,
    package: RiskPackage,
    dispatch_fn: Callable,
    posture_floor: PostureProfile | str | None = None,
) -> RiskPlan:
    """Run a lightweight risk check (single assessment, no full loop)."""
    paths = PathRegistry(planspace)
    write_package(paths, package)
    prompt = build_risk_assessment_prompt(package, planspace, scope)
    prompt_path = paths.risk_dir() / f"{scope}-light-risk-assessment-prompt.md"
    if not write_validated_prompt(prompt, prompt_path):
        fallback = fallback_plan(
            package,
            layer,
            assessment_id=f"{package.package_id}-assessment-fallback",
            reason="fail-closed: lightweight risk assessment prompt failed safety validation",
        )
        write_risk_artifact(paths.risk_plan(scope), serialize_plan(fallback))
        return fallback
    output_path = paths.risk_dir() / f"{scope}-light-risk-assessment-output.md"
    response = dispatch_fn(
        _risk_assessor_model(planspace),
        prompt_path,
        output_path,
        planspace,
        None,
        f"risk-assessor-light-{scope}",
        agent_file=agent_for("risk.assess"),
    )
    assessment = parse_risk_assessment(response)
    if assessment is None:
        fallback = fallback_plan(
            package,
            layer,
            assessment_id=f"{package.package_id}-assessment-fallback",
            reason="fail-closed: lightweight risk assessment could not be parsed",
        )
        write_risk_artifact(paths.risk_plan(scope), serialize_plan(fallback))
        return fallback

    parameters = _load_parameters(paths)
    history_entries = read_history(paths.risk_history())
    _apply_history_adjustment(assessment, paths.risk_history(), history_entries, parameters)
    write_risk_artifact(paths.risk_assessment(scope), serialize_assessment(assessment))
    optimization_prompt = build_optimization_prompt(
        assessment=assessment,
        package=package,
        parameters=parameters,
        planspace=planspace,
        scope=scope,
        lightweight=True,
    )
    optimization_prompt_path = paths.risk_dir() / f"{scope}-light-risk-plan-prompt.md"
    if not write_validated_prompt(optimization_prompt, optimization_prompt_path):
        fallback = lightweight_fallback_plan(
            package,
            layer,
            assessment_id=assessment.assessment_id,
            reason="fail-closed: lightweight execution optimizer prompt failed safety validation",
        )
        write_risk_artifact(paths.risk_plan(scope), serialize_plan(fallback))
        return fallback
    optimization_output_path = paths.risk_dir() / f"{scope}-light-risk-plan-output.md"
    try:
        optimization_response = dispatch_fn(
            _execution_optimizer_model(planspace),
            optimization_prompt_path,
            optimization_output_path,
            planspace,
            None,
            f"execution-optimizer-light-{scope}",
            agent_file=agent_for("risk.optimize"),
        )
    except Exception as exc:
        fallback = lightweight_fallback_plan(
            package,
            layer,
            assessment_id=assessment.assessment_id,
            reason=(
                "fail-closed: lightweight execution optimizer dispatch failed"
                f" ({exc})"
            ),
        )
        write_risk_artifact(paths.risk_plan(scope), serialize_plan(fallback))
        return fallback

    plan = parse_risk_plan(optimization_response)
    if plan is None:
        fallback = lightweight_fallback_plan(
            package,
            layer,
            assessment_id=assessment.assessment_id,
            reason="fail-closed: lightweight execution optimizer could not be parsed",
        )
        write_risk_artifact(paths.risk_plan(scope), serialize_plan(fallback))
        return fallback

    apply_posture_hysteresis(
        plan,
        assessment,
        history_entries,
        parameters,
        posture_floor=posture_floor,
    )
    assessments = {
        item.step_id: item
        for item in assessment.step_assessments
    }
    enriched_parameters = dict(parameters)
    enriched_parameters["assessment_classes"] = {
        step_id: step_assessment.assessment_class
        for step_id, step_assessment in assessments.items()
    }
    enforced_plan = enforce_thresholds(plan, assessments, enriched_parameters)
    violations = validate_risk_plan(enforced_plan, enriched_parameters)
    if violations:
        fallback = lightweight_fallback_plan(
            package,
            layer,
            assessment_id=assessment.assessment_id,
            reason="fail-closed: lightweight execution optimizer produced invalid plan",
        )
        write_risk_artifact(paths.risk_plan(scope), serialize_plan(fallback))
        return fallback

    write_risk_artifact(paths.risk_plan(scope), serialize_plan(enforced_plan))
    return enforced_plan


def _load_parameters(paths: PathRegistry) -> dict:
    parameters = load_default_parameters()
    raw = read_json(paths.risk_parameters())
    if not isinstance(raw, dict):
        return parameters

    posture_bands = raw.get("posture_bands")
    if isinstance(posture_bands, dict):
        parameters["posture_bands"] = posture_bands

    class_thresholds = raw.get("class_thresholds")
    if isinstance(class_thresholds, dict):
        parameters["class_thresholds"].update(
            {
                str(key): int(value)
                for key, value in class_thresholds.items()
                if isinstance(value, int)
            }
        )

    step_thresholds = raw.get("step_thresholds")
    if isinstance(step_thresholds, dict):
        parameters["step_thresholds"].update(
            {
                str(key): int(value)
                for key, value in step_thresholds.items()
                if isinstance(value, int)
            }
        )

    execution_thresholds = raw.get("execution_thresholds")
    if isinstance(execution_thresholds, dict):
        parameters["execution_thresholds"].update(
            {
                str(key): int(value)
                for key, value in execution_thresholds.items()
                if isinstance(value, int)
            }
        )

    for scalar_key in (
        "cooldown_iterations",
        "relaxation_required_successes",
        "history_adjustment_bound",
    ):
        if scalar_key in raw:
            parameters[scalar_key] = raw[scalar_key]

    parameters["execution_thresholds"] = dict(parameters["class_thresholds"])
    return parameters


def _apply_history_adjustment(
    assessment: RiskAssessment,
    history_path: Path,
    history_entries: list[RiskHistoryEntry],
    parameters: dict,
) -> None:
    primary_step = _primary_step_assessment(assessment)
    if primary_step is None:
        return

    signature = pattern_signature(
        primary_step.assessment_class,
        primary_step.dominant_risks,
        primary_step.modifiers.blast_radius,
    )
    matching_entries = [
        entry
        for entry in history_entries
        if _history_signature(entry) == signature
    ]
    adjustment = compute_history_adjustment(
        history_path,
        primary_step.assessment_class,
        primary_step.dominant_risks,
        primary_step.modifiers.blast_radius,
    )
    bound = _coerce_float(parameters.get("history_adjustment_bound"), 10.0)
    bounded_adjustment = clamp_float(adjustment, -bound, bound)
    assessment.package_raw_risk = clamp_int(
        assessment.package_raw_risk + int(round(bounded_adjustment)),
        0,
        100,
    )
    if matching_entries:
        assessment.notes.append(
            "history-adjustment "
            f"{signature} {bounded_adjustment:+.1f} from {len(matching_entries)} "
            "similar outcomes"
        )


def _primary_step_assessment(assessment: RiskAssessment) -> StepAssessment | None:
    if not assessment.step_assessments:
        return None
    return max(
        assessment.step_assessments,
        key=lambda item: (item.raw_risk, item.modifiers.blast_radius, len(item.dominant_risks)),
    )


def _history_signature(entry: RiskHistoryEntry) -> str:
    return pattern_signature(
        entry.assessment_class,
        entry.dominant_risks,
        entry.blast_radius_band,
    )


def _risk_assessor_model(planspace: Path) -> str:
    policy = load_model_policy(planspace)
    return resolve(policy, "risk_assessor")


def _execution_optimizer_model(planspace: Path) -> str:
    policy = load_model_policy(planspace)
    return resolve(policy, "execution_optimizer")


def _coerce_float(value: object, default: float) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    return default
