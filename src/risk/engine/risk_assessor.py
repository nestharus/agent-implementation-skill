"""ROAL loop orchestration."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from orchestrator.path_registry import PathRegistry
from risk.repository.history import compute_history_adjustment, pattern_signature, read_history
from risk.repository.serialization import (
    serialize_assessment,
    serialize_plan,
    write_risk_artifact,
)
from risk.service.threshold import enforce_thresholds, load_default_parameters, validate_risk_plan
from risk.types import (
    HISTORY_ADJUSTMENT_BOUND,
    MAX_RESIDUAL_RISK,
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
from risk.prompt.writers import write_risk_assessment_prompt, write_optimization_prompt
from risk.service.response_parser import parse_risk_assessment, parse_risk_plan
from risk.service.posture_hysteresis import apply_posture_hysteresis, history_signature
from risk.service.fallback import fallback_plan, lightweight_fallback_plan

if TYPE_CHECKING:
    from containers import Services

_DEFAULT_RISK_ITERATIONS = 5


class RiskAssessor:
    """ROAL loop orchestration with constructor-injected dependencies."""

    def __init__(
        self,
        logger: Any,
        policies: Any,
        dispatcher: Any,
        task_router: Any,
        prompt_guard: Any,
        artifact_io: Any,
    ) -> None:
        self._logger = logger
        self._policies = policies
        self._dispatcher = dispatcher
        self._task_router = task_router
        self._prompt_guard = prompt_guard
        self._artifact_io = artifact_io

    def run_risk_loop(
        self,
        planspace: Path,
        scope: str,
        layer: str,
        package: RiskPackage,
        max_iterations: int = _DEFAULT_RISK_ITERATIONS,
        posture_floor: PostureProfile | str | None = None,
    ) -> RiskPlan:
        """Run the full ROAL loop for a package."""
        paths = PathRegistry(planspace)
        write_package(paths, package)
        parameters = self._load_parameters(paths)
        history_entries = read_history(paths.risk_history())
        last_assessment: RiskAssessment | None = None
        last_plan: RiskPlan | None = None

        for iteration in range(1, max_iterations + 1):
            assessment = self._validate_and_dispatch_assessment(
                planspace, scope, package,
            )
            if assessment is None:
                return _write_and_return_fallback(
                    paths, scope, package, layer,
                    assessment_id=f"{package.package_id}-assessment-fallback",
                    reason="fail-closed: risk assessment prompt failed safety validation or could not be parsed",
                )

            _apply_history_adjustment(assessment, paths.risk_history(), history_entries, parameters)
            last_assessment = assessment
            write_risk_artifact(paths.risk_assessment(scope), serialize_assessment(assessment))

            plan = self._validate_and_dispatch_optimization(
                planspace, scope,
                retry_hint=(iteration > 1 and last_plan is not None),
            )
            if plan is None:
                return _write_and_return_fallback(
                    paths, scope, package, layer,
                    assessment_id=assessment.assessment_id,
                    reason="fail-closed: execution optimizer prompt failed safety validation or could not be parsed",
                )

            apply_posture_hysteresis(
                plan, assessment, history_entries, parameters,
                posture_floor=posture_floor,
            )
            enforced_plan, violations = _enforce_and_validate(plan, assessment, parameters)
            if not violations:
                write_risk_artifact(paths.risk_plan(scope), serialize_plan(enforced_plan))
                return enforced_plan

            last_plan = enforced_plan

        fallback_assessment_id = (
            last_assessment.assessment_id
            if last_assessment is not None
            else f"{package.package_id}-assessment-fallback"
        )
        return _write_and_return_fallback(
            paths, scope, package, layer,
            assessment_id=fallback_assessment_id,
            reason="fail-closed: risk loop exhausted without a threshold-compliant plan",
        )

    def run_lightweight_risk_check(
        self,
        planspace: Path,
        scope: str,
        layer: str,
        package: RiskPackage,
        posture_floor: PostureProfile | str | None = None,
    ) -> RiskPlan:
        """Run a lightweight risk check (single assessment, no full loop)."""
        paths = PathRegistry(planspace)
        write_package(paths, package)

        assessment = self._validate_and_dispatch_assessment(
            planspace, scope, package, prefix="light",
        )
        if assessment is None:
            return _write_and_return_fallback(
                paths, scope, package, layer,
                assessment_id=f"{package.package_id}-assessment-fallback",
                reason="fail-closed: lightweight risk assessment prompt failed safety validation or could not be parsed",
            )

        parameters = self._load_parameters(paths)
        history_entries = read_history(paths.risk_history())
        _apply_history_adjustment(assessment, paths.risk_history(), history_entries, parameters)
        write_risk_artifact(paths.risk_assessment(scope), serialize_assessment(assessment))

        plan = self._validate_and_dispatch_lightweight_optimization(
            planspace, scope,
        )
        if plan is None:
            return _write_and_return_lightweight_fallback(
                paths, scope, package, layer,
                assessment_id=assessment.assessment_id,
                reason="fail-closed: lightweight execution optimizer failed",
            )

        apply_posture_hysteresis(
            plan, assessment, history_entries, parameters,
            posture_floor=posture_floor,
        )
        enforced_plan, violations = _enforce_and_validate(plan, assessment, parameters)
        if violations:
            return _write_and_return_lightweight_fallback(
                paths, scope, package, layer,
                assessment_id=assessment.assessment_id,
                reason="fail-closed: lightweight execution optimizer produced invalid plan",
            )

        write_risk_artifact(paths.risk_plan(scope), serialize_plan(enforced_plan))
        return enforced_plan

    # -----------------------------------------------------------------------
    # Extracted concerns: prompt building, agent dispatch, response parsing
    # -----------------------------------------------------------------------

    def _validate_and_dispatch_assessment(
        self,
        planspace: Path,
        scope: str,
        package: RiskPackage,
        *,
        prefix: str = "",
    ) -> RiskAssessment | None:
        """Build assessment prompt, validate, dispatch, and parse the response.

        Returns ``None`` when the prompt fails validation *or* the response cannot
        be parsed -- the caller decides what fallback to use.
        """
        paths = PathRegistry(planspace)
        tag = f"{prefix}-" if prefix else ""
        prompt = write_risk_assessment_prompt(package, planspace, scope)
        prompt_path = paths.risk_dir() / f"{scope}-{tag}risk-assessment-prompt.md"
        if not self._prompt_guard.write_validated(prompt, prompt_path):
            return None
        output_path = paths.risk_dir() / f"{scope}-{tag}risk-assessment-output.md"
        response = self._dispatcher.dispatch(
            self._risk_assessor_model(planspace),
            prompt_path,
            output_path,
            planspace,
            None,
            f"risk-assessor-{tag}{scope}",
            agent_file=self._task_router.agent_for("risk.assess"),
        )
        return parse_risk_assessment(response.output)

    def _validate_and_dispatch_optimization(
        self,
        planspace: Path,
        scope: str,
        *,
        retry_hint: bool = False,
    ) -> RiskPlan | None:
        """Build optimization prompt, validate, dispatch, and parse the response.

        Returns ``None`` when the prompt fails validation *or* the response cannot
        be parsed.
        """
        paths = PathRegistry(planspace)
        prompt = write_optimization_prompt(
            planspace=planspace,
            scope=scope,
        )
        if retry_hint:
            prompt += (
                "\n\n## Previous Enforcement Outcome\n\n"
                "The previous optimizer response failed mechanical enforcement. "
                "Produce a strictly more conservative plan.\n"
            )
        prompt_path = paths.risk_dir() / f"{scope}-risk-plan-prompt.md"
        if not self._prompt_guard.write_validated(prompt, prompt_path):
            return None
        output_path = paths.risk_dir() / f"{scope}-risk-plan-output.md"
        response = self._dispatcher.dispatch(
            self._execution_optimizer_model(planspace),
            prompt_path,
            output_path,
            planspace,
            None,
            f"execution-optimizer-{scope}",
            agent_file=self._task_router.agent_for("risk.optimize"),
        )
        return parse_risk_plan(response.output)

    def _validate_and_dispatch_lightweight_optimization(
        self,
        planspace: Path,
        scope: str,
    ) -> RiskPlan | None:
        """Lightweight variant of optimization dispatch (includes try/except)."""
        paths = PathRegistry(planspace)
        prompt = write_optimization_prompt(
            planspace=planspace,
            scope=scope,
            lightweight=True,
        )
        prompt_path = paths.risk_dir() / f"{scope}-light-risk-plan-prompt.md"
        if not self._prompt_guard.write_validated(prompt, prompt_path):
            return None
        output_path = paths.risk_dir() / f"{scope}-light-risk-plan-output.md"
        try:
            response = self._dispatcher.dispatch(
                self._execution_optimizer_model(planspace),
                prompt_path,
                output_path,
                planspace,
                None,
                f"execution-optimizer-light-{scope}",
                agent_file=self._task_router.agent_for("risk.optimize"),
            )
        except Exception as exc:  # noqa: BLE001 — fail-open: optimization is best-effort
            self._logger.log(
                f"Lightweight optimization dispatch failed ({exc}) — failing open",
            )
            return None
        return parse_risk_plan(response.output)

    def _load_parameters(self, paths: PathRegistry) -> dict:
        parameters = load_default_parameters()
        raw = self._artifact_io.read_json(paths.risk_parameters())
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

    def _risk_assessor_model(self, planspace: Path) -> str:
        policy = self._policies.load(planspace)
        return self._policies.resolve(policy, "risk_assessor")

    def _execution_optimizer_model(self, planspace: Path) -> str:
        policy = self._policies.load(planspace)
        return self._policies.resolve(policy, "execution_optimizer")


# ---------------------------------------------------------------------------
# Backward compat — called by containers.py RiskAssessmentService
# ---------------------------------------------------------------------------


def _build_assessor() -> RiskAssessor:
    from containers import Services
    return RiskAssessor(
        logger=Services.logger(),
        policies=Services.policies(),
        dispatcher=Services.dispatcher(),
        task_router=Services.task_router(),
        prompt_guard=Services.prompt_guard(),
        artifact_io=Services.artifact_io(),
    )


def run_risk_loop(
    planspace: Path,
    scope: str,
    layer: str,
    package: RiskPackage,
    max_iterations: int = _DEFAULT_RISK_ITERATIONS,
    posture_floor: PostureProfile | str | None = None,
) -> RiskPlan:
    """Run the full ROAL loop for a package."""
    return _build_assessor().run_risk_loop(
        planspace, scope, layer, package, max_iterations, posture_floor,
    )


def run_lightweight_risk_check(
    planspace: Path,
    scope: str,
    layer: str,
    package: RiskPackage,
    posture_floor: PostureProfile | str | None = None,
) -> RiskPlan:
    """Run a lightweight risk check (single assessment, no full loop)."""
    return _build_assessor().run_lightweight_risk_check(
        planspace, scope, layer, package, posture_floor,
    )


# ---------------------------------------------------------------------------
# Pure functions — kept as module-level (no Services dependency)
# ---------------------------------------------------------------------------


def _enforce_and_validate(
    plan: RiskPlan,
    assessment: RiskAssessment,
    parameters: dict,
) -> tuple[RiskPlan, list]:
    """Apply threshold enforcement and return (enforced_plan, violations)."""
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
    return enforced_plan, violations


def _write_and_return_fallback(
    paths: PathRegistry,
    scope: str,
    package: RiskPackage,
    layer: str,
    *,
    assessment_id: str,
    reason: str,
) -> RiskPlan:
    """Build a fallback plan, persist it, and return it."""
    fb = fallback_plan(package, layer, assessment_id=assessment_id, reason=reason)
    write_risk_artifact(paths.risk_plan(scope), serialize_plan(fb))
    return fb


def _write_and_return_lightweight_fallback(
    paths: PathRegistry,
    scope: str,
    package: RiskPackage,
    layer: str,
    *,
    assessment_id: str,
    reason: str,
) -> RiskPlan:
    """Build a lightweight fallback plan, persist it, and return it."""
    fb = lightweight_fallback_plan(
        package, layer, assessment_id=assessment_id, reason=reason,
    )
    write_risk_artifact(paths.risk_plan(scope), serialize_plan(fb))
    return fb


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
        if history_signature(entry) == signature
    ]
    adjustment = compute_history_adjustment(
        history_path,
        primary_step.assessment_class,
        primary_step.dominant_risks,
        primary_step.modifiers.blast_radius,
    )
    bound = _coerce_float(parameters.get("history_adjustment_bound"), HISTORY_ADJUSTMENT_BOUND)
    bounded_adjustment = clamp_float(adjustment, -bound, bound)
    assessment.package_raw_risk = clamp_int(
        assessment.package_raw_risk + int(round(bounded_adjustment)),
        0,
        MAX_RESIDUAL_RISK,
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


def _coerce_float(value: object, default: float) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    return default
