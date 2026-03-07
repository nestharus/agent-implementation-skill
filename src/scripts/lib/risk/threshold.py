"""Mechanical threshold validation for ROAL plans."""

from __future__ import annotations

from lib.risk.types import RiskPlan, StepAssessment, StepClass, StepDecision, StepMitigation


def validate_risk_plan(plan: RiskPlan, parameters: dict) -> list[str]:
    """Validate that a risk plan meets policy requirements."""
    violations: list[str] = []
    thresholds = _resolve_thresholds(parameters)
    step_classes = _resolve_step_classes(parameters)

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
            if decision.posture is None:
                violations.append(f"accepted step {decision.step_id} is missing posture")
            if decision.residual_risk is None:
                violations.append(
                    f"accepted step {decision.step_id} is missing residual_risk"
                )
            step_class = step_classes.get(decision.step_id)
            threshold = thresholds.get(step_class.value) if step_class is not None else None
            if threshold is None:
                violations.append(
                    f"accepted step {decision.step_id} is missing a policy threshold"
                )
            elif decision.residual_risk is not None and decision.residual_risk > threshold:
                violations.append(
                    f"accepted step {decision.step_id} residual risk "
                    f"{decision.residual_risk} exceeds {step_class.value} threshold {threshold}"
                )
            if decision.route_to is not None:
                violations.append(
                    f"accepted step {decision.step_id} must not set route_to"
                )
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
        step_class = assessment.step_class if assessment is not None else None
        threshold = thresholds.get(step_class.value) if step_class is not None else None
        residual_risk = decision.residual_risk

        if (
            step_class is None
            or threshold is None
            or residual_risk is None
            or residual_risk > threshold
        ):
            reason_parts = [decision.reason] if decision.reason else []
            if step_class is None or threshold is None:
                reason_parts.append(
                    "fail-closed: missing step class or threshold during threshold enforcement"
                )
            else:
                reason_parts.append(
                    f"fail-closed: residual risk {residual_risk} exceeds "
                    f"{step_class.value} threshold {threshold}"
                )
            wait_for = list(decision.wait_for)
            if "threshold-compliant-plan" not in wait_for:
                wait_for.append("threshold-compliant-plan")
            decisions.append(
                StepMitigation(
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
            )
            continue

        decisions.append(_clone_decision(decision))

    accepted_frontier = [
        decision.step_id
        for decision in decisions
        if decision.decision == StepDecision.ACCEPT
    ]
    deferred_steps = [
        decision.step_id
        for decision in decisions
        if decision.decision == StepDecision.REJECT_DEFER
    ]
    reopen_steps = [
        decision.step_id
        for decision in decisions
        if decision.decision == StepDecision.REJECT_REOPEN
    ]
    return RiskPlan(
        plan_id=plan.plan_id,
        assessment_id=plan.assessment_id,
        package_id=plan.package_id,
        layer=plan.layer,
        step_decisions=decisions,
        accepted_frontier=accepted_frontier,
        deferred_steps=deferred_steps,
        reopen_steps=reopen_steps,
        expected_reassessment_inputs=list(plan.expected_reassessment_inputs),
    )


def load_default_parameters() -> dict:
    """Return default risk parameters."""
    step_thresholds = {
        "explore": 60,
        "stabilize": 60,
        "edit": 45,
        "coordinate": 35,
        "verify": 50,
    }
    return {
        "posture_bands": {
            "P0": [0, 19],
            "P1": [20, 39],
            "P2": [40, 59],
            "P3": [60, 79],
            "P4": [80, 100],
        },
        "step_thresholds": step_thresholds,
        "execution_thresholds": dict(step_thresholds),
        "cooldown_iterations": 2,
        "relaxation_required_successes": 3,
        "history_adjustment_bound": 10.0,
    }


def _resolve_thresholds(parameters: dict) -> dict[str, int]:
    raw = parameters.get("step_thresholds")
    if not isinstance(raw, dict):
        raw = parameters.get("execution_thresholds")
    if not isinstance(raw, dict):
        raw = load_default_parameters()["step_thresholds"]
    return {
        str(key): int(value)
        for key, value in raw.items()
        if isinstance(value, int)
    }


def _resolve_step_classes(parameters: dict) -> dict[str, StepClass]:
    raw = parameters.get("step_classes", {})
    if not isinstance(raw, dict):
        return {}
    resolved: dict[str, StepClass] = {}
    for step_id, step_class in raw.items():
        if isinstance(step_class, StepClass):
            resolved[str(step_id)] = step_class
        elif isinstance(step_class, str):
            try:
                resolved[str(step_id)] = StepClass(step_class)
            except ValueError:
                continue
    return resolved


def _is_valid_dispatch_shape(shape: object) -> bool:
    if not isinstance(shape, dict):
        return False
    if {"task_type", "concern_scope", "payload_path"} <= set(shape):
        return True

    if shape.get("version") != 2:
        return False
    actions = shape.get("actions")
    if not isinstance(actions, list) or not actions:
        return False

    for action in actions:
        if not isinstance(action, dict):
            return False
        kind = action.get("kind")
        if kind in {"chain", "fanout"}:
            steps = action.get("steps") or action.get("tasks")
            if not isinstance(steps, list) or not steps:
                return False
            for step in steps:
                if not isinstance(step, dict):
                    return False
                if not {"task_type", "concern_scope", "payload_path"} <= set(step):
                    return False
        elif kind == "gate":
            if not isinstance(action.get("mode"), str):
                return False
            if not isinstance(action.get("failure_policy"), str):
                return False
            synthesis = action.get("synthesis")
            if synthesis is not None and not isinstance(synthesis, dict):
                return False
        else:
            return False
    return True


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
