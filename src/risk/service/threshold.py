"""Mechanical threshold validation for ROAL plans."""

from __future__ import annotations

from risk.types import AssessmentClass, DecisionClass, RiskPlan, StepAssessment, StepClass, StepDecision, StepMitigation


def _validate_accept_decision(
    decision: object, assessment_classes: dict, thresholds: dict,
    violations: list[str],
) -> None:
    """Validate an ACCEPT step decision against policy thresholds."""
    sid = decision.step_id
    if decision.posture is None:
        violations.append(f"accepted step {sid} is missing posture")
    if decision.residual_risk is None:
        violations.append(f"accepted step {sid} is missing residual_risk")
    assessment_class = assessment_classes.get(sid)
    threshold = thresholds.get(assessment_class.value) if assessment_class is not None else None
    if threshold is None:
        violations.append(f"accepted step {sid} is missing a policy threshold")
    elif decision.residual_risk is not None and decision.residual_risk > threshold:
        violations.append(
            f"accepted step {sid} residual risk "
            f"{decision.residual_risk} exceeds {assessment_class.value} threshold {threshold}"
        )
    if decision.route_to is not None:
        violations.append(f"accepted step {sid} must not set route_to")


def validate_risk_plan(plan: RiskPlan, parameters: dict) -> list[str]:
    """Validate that a risk plan meets policy requirements."""
    violations: list[str] = []
    thresholds = _resolve_thresholds(parameters)
    assessment_classes = _resolve_assessment_classes(parameters)

    if not plan.plan_id:
        violations.append("plan_id is required")
    if not plan.assessment_id:
        violations.append("assessment_id is required")
    if not plan.package_id:
        violations.append("package_id is required")

    seen_step_ids: set[str] = set()
    expected_accepted: list[str] = []
    expected_deferred: list[str] = []
    expected_reopen: list[str] = []

    for decision in plan.step_decisions:
        if decision.step_id in seen_step_ids:
            violations.append(f"duplicate step decision for {decision.step_id}")
            continue
        seen_step_ids.add(decision.step_id)

        if decision.decision == StepDecision.ACCEPT:
            expected_accepted.append(decision.step_id)
            _validate_accept_decision(decision, assessment_classes, thresholds, violations)
        elif decision.decision == StepDecision.REJECT_DEFER:
            expected_deferred.append(decision.step_id)
            if decision.route_to is not None:
                violations.append(
                    f"deferred step {decision.step_id} must not set route_to"
                )
        elif decision.decision == StepDecision.REJECT_REOPEN:
            expected_reopen.append(decision.step_id)

        if decision.dispatch_shape is not None and not _is_valid_dispatch_shape(
            decision.dispatch_shape
        ):
            violations.append(
                f"step {decision.step_id} has unsupported dispatch_shape"
            )

    if set(plan.accepted_frontier) != set(expected_accepted):
        violations.append("accepted_frontier does not match accept decisions")
    if set(plan.deferred_steps) != set(expected_deferred):
        violations.append("deferred_steps does not match reject_defer decisions")
    if set(plan.reopen_steps) != set(expected_reopen):
        violations.append("reopen_steps does not match reject_reopen decisions")

    overlap = (
        set(plan.accepted_frontier) & set(plan.deferred_steps)
        | set(plan.accepted_frontier) & set(plan.reopen_steps)
        | set(plan.deferred_steps) & set(plan.reopen_steps)
    )
    if overlap:
        violations.append(
            "step ids appear in multiple frontier sets: " + ", ".join(sorted(overlap))
        )

    return violations


_THRESHOLD_WAIT_FOR = "threshold-compliant-plan"


def _apply_threshold_override(
    decision: StepMitigation,
    assessment_class: object | None,
    threshold: int | None,
) -> StepMitigation:
    """Build a deferred decision when a step exceeds its threshold."""
    reason_parts = [decision.reason] if decision.reason else []
    if assessment_class is None or threshold is None:
        reason_parts.append(
            "fail-closed: missing assessment class or threshold "
            "during threshold enforcement"
        )
    else:
        reason_parts.append(
            f"fail-closed: residual risk {decision.residual_risk} exceeds "
            f"{assessment_class.value} threshold {threshold}"
        )
    wait_for = list(decision.wait_for)
    if _THRESHOLD_WAIT_FOR not in wait_for:
        wait_for.append(_THRESHOLD_WAIT_FOR)
    return StepMitigation(
        step_id=decision.step_id,
        decision=StepDecision.REJECT_DEFER,
        posture=decision.posture,
        mitigations=list(decision.mitigations),
        residual_risk=decision.residual_risk,
        reason="; ".join(part for part in reason_parts if part),
        wait_for=wait_for,
        route_to=None,
        dispatch_shape=None,
    )


def _rebuild_plan(plan: RiskPlan, decisions: list[StepMitigation]) -> RiskPlan:
    """Reconstruct a RiskPlan from updated decisions."""
    return RiskPlan(
        plan_id=plan.plan_id,
        assessment_id=plan.assessment_id,
        package_id=plan.package_id,
        layer=plan.layer,
        step_decisions=decisions,
        accepted_frontier=[
            d.step_id for d in decisions if d.decision == StepDecision.ACCEPT
        ],
        deferred_steps=[
            d.step_id for d in decisions if d.decision == StepDecision.REJECT_DEFER
        ],
        reopen_steps=[
            d.step_id for d in decisions if d.decision == StepDecision.REJECT_REOPEN
        ],
        expected_reassessment_inputs=list(plan.expected_reassessment_inputs),
    )


def enforce_thresholds(
    plan: RiskPlan,
    assessments: dict[str, StepAssessment],
    parameters: dict,
) -> RiskPlan:
    """Enforce thresholds mechanically."""
    thresholds = _resolve_thresholds(parameters)
    decisions: list[StepMitigation] = []

    for decision in plan.step_decisions:
        if decision.decision != StepDecision.ACCEPT:
            decisions.append(_clone_decision(decision))
            continue

        assessment = assessments.get(decision.step_id)
        assessment_class = assessment.assessment_class if assessment is not None else None
        threshold = thresholds.get(assessment_class.value) if assessment_class is not None else None

        if (
            assessment_class is None
            or threshold is None
            or decision.residual_risk is None
            or decision.residual_risk > threshold
        ):
            decisions.append(
                _apply_threshold_override(decision, assessment_class, threshold)
            )
            continue

        decisions.append(_clone_decision(decision))

    return _rebuild_plan(plan, decisions)


def load_default_parameters() -> dict:
    """Return default risk parameters."""
    class_thresholds = {
        "explore": 60,
        "stabilize": 60,
        "edit": 45,
        "coordinate": 35,
        "verify": 50,
        "local": 55,
        "component": 45,
        "cross_cutting": 35,
        "platform": 30,
        "irreversible": 20,
    }
    return {
        "posture_bands": {
            "P0": [0, 19],
            "P1": [20, 39],
            "P2": [40, 59],
            "P3": [60, 79],
            "P4": [80, 100],
        },
        "class_thresholds": class_thresholds,
        "cooldown_iterations": 2,
        "relaxation_required_successes": 3,
        "history_adjustment_bound": 10.0,
    }


def _resolve_thresholds(parameters: dict) -> dict[str, int]:
    raw = parameters.get("class_thresholds")
    if not isinstance(raw, dict):
        raw = parameters.get("step_thresholds")
    if not isinstance(raw, dict):
        raw = parameters.get("execution_thresholds")
    if not isinstance(raw, dict):
        raw = load_default_parameters()["class_thresholds"]
    return {
        str(key): int(value)
        for key, value in raw.items()
        if isinstance(value, int)
    }


def _parse_assessment_class(value: str) -> AssessmentClass | None:
    """Try to parse a string as StepClass or DecisionClass."""
    try:
        return StepClass(value)
    except ValueError:
        pass
    try:
        return DecisionClass(value)
    except ValueError:
        return None


def _resolve_assessment_classes(parameters: dict) -> dict[str, AssessmentClass]:
    raw = parameters.get("assessment_classes", parameters.get("step_classes", {}))
    if not isinstance(raw, dict):
        return {}
    resolved: dict[str, AssessmentClass] = {}
    for step_id, assessment_class in raw.items():
        if isinstance(assessment_class, (StepClass, DecisionClass)):
            resolved[str(step_id)] = assessment_class
        elif isinstance(assessment_class, str):
            parsed = _parse_assessment_class(assessment_class)
            if parsed is not None:
                resolved[str(step_id)] = parsed
    return resolved


_REQUIRED_STEP_KEYS = {"task_type", "concern_scope", "payload_path"}


def _is_valid_action(action: dict) -> bool:
    """Validate a single action within a v2 dispatch shape."""
    kind = action.get("kind")
    if kind in {"chain", "fanout"}:
        steps = action.get("steps") or action.get("tasks")
        if not isinstance(steps, list) or not steps:
            return False
        return all(
            isinstance(step, dict) and _REQUIRED_STEP_KEYS <= set(step)
            for step in steps
        )
    if kind == "gate":
        if not isinstance(action.get("mode"), str):
            return False
        if not isinstance(action.get("failure_policy"), str):
            return False
        synthesis = action.get("synthesis")
        return synthesis is None or isinstance(synthesis, dict)
    return False


def _is_valid_dispatch_shape(shape: object) -> bool:
    if not isinstance(shape, dict):
        return False
    if _REQUIRED_STEP_KEYS <= set(shape):
        return True

    if shape.get("version") != 2:
        return False
    actions = shape.get("actions")
    if not isinstance(actions, list) or not actions:
        return False

    return all(
        isinstance(action, dict) and _is_valid_action(action)
        for action in actions
    )


def _clone_decision(decision: StepMitigation) -> StepMitigation:
    return StepMitigation(
        step_id=decision.step_id,
        decision=decision.decision,
        posture=decision.posture,
        mitigations=list(decision.mitigations),
        residual_risk=decision.residual_risk,
        reason=decision.reason,
        wait_for=list(decision.wait_for),
        route_to=decision.route_to,
        dispatch_shape=decision.dispatch_shape,
    )
