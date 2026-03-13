"""Risk artifact writers and hint loaders for implementation sections."""

from __future__ import annotations

from pathlib import Path

from containers import Services
from orchestrator.path_registry import PathRegistry
from orchestrator.repository.section_artifacts import write_section_input_artifact
from risk.types import (
    PostureProfile,
    RiskPlan,
    StepDecision,
)

_RISK_ITERATIONS_BASE = 5
_RISK_ITERATIONS_CAP = 9


def _unique_strings(values: list[str]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for value in values:
        item = str(value).strip()
        if not item or item in seen:
            continue
        seen.add(item)
        ordered.append(item)
    return ordered


def write_accepted_steps(
    planspace: Path,
    sec_num: str,
    risk_plan: RiskPlan,
) -> Path:
    paths = PathRegistry(planspace)
    accepted = [
        decision
        for decision in risk_plan.step_decisions
        if decision.decision == StepDecision.ACCEPT
        and decision.step_id in risk_plan.accepted_frontier
    ]
    accepted.sort(
        key=lambda decision: risk_plan.accepted_frontier.index(decision.step_id),
    )
    postures = [decision.posture for decision in accepted if decision.posture is not None]
    posture = max(postures, key=lambda p: p.rank) if postures else PostureProfile.P2_STANDARD
    dispatch_shapes = {
        decision.step_id: decision.dispatch_shape
        for decision in accepted
        if isinstance(decision.dispatch_shape, dict)
    }
    payload = {
        "accepted_steps": list(risk_plan.accepted_frontier),
        "posture": posture.value,
        "mitigations": _unique_strings(
            [
                mitigation
                for decision in accepted
                for mitigation in decision.mitigations
            ]
        ),
        "dispatch_shape": dispatch_shapes,
        "dispatch_shapes": dispatch_shapes,
    }
    return write_section_input_artifact(
        paths,
        sec_num,
        paths.risk_accepted_steps(sec_num).name,
        payload,
    )


def write_deferred_steps(
    planspace: Path,
    sec_num: str,
    risk_plan: RiskPlan,
) -> Path:
    paths = PathRegistry(planspace)
    deferred = [
        decision
        for decision in risk_plan.step_decisions
        if decision.decision == StepDecision.REJECT_DEFER
        and decision.step_id in risk_plan.deferred_steps
    ]
    payload = {
        "deferred_steps": list(risk_plan.deferred_steps),
        "wait_for": _unique_strings(
            [
                item
                for decision in deferred
                for item in decision.wait_for
            ]
        ),
        "reassessment_inputs": _unique_strings(
            list(risk_plan.expected_reassessment_inputs),
        ),
    }
    return write_section_input_artifact(
        paths,
        sec_num,
        paths.risk_deferred(sec_num).name,
        payload,
    )


def write_reopen_blocker(
    planspace: Path,
    sec_num: str,
    risk_plan: RiskPlan,
) -> Path:
    paths = PathRegistry(planspace)
    scope = f"section-{sec_num}"
    reopened = [
        decision
        for decision in risk_plan.step_decisions
        if decision.decision == StepDecision.REJECT_REOPEN
        and decision.step_id in risk_plan.reopen_steps
    ]
    reason = next(
        (decision.reason for decision in reopened if decision.reason),
        "cross-section incoherence requires reconciliation before local execution",
    )
    route_to = next(
        (decision.route_to for decision in reopened if decision.route_to),
        "coordination",
    )
    payload = {
        "state": "needs_parent",
        "blocker_type": "risk_reopen",
        "source": "roal",
        "section": sec_num,
        "scope": scope,
        "steps": list(risk_plan.reopen_steps),
        "route_to": route_to,
        "reason": reason,
        "detail": reason,
        "why_blocked": reason,
        "needs": "Resolve reopened ROAL steps before continuing local execution",
    }
    Services.artifact_io().write_json(paths.blocker_signal(sec_num), payload)
    return paths.blocker_signal(sec_num)


def write_risk_review_failure_blocker(
    planspace: Path,
    sec_num: str,
    reason: str,
) -> Path:
    paths = PathRegistry(planspace)
    payload = {
        "state": "needs_parent",
        "blocker_type": "risk_review_failure",
        "source": "roal",
        "section": sec_num,
        "scope": f"section-{sec_num}",
        "reason": reason,
        "detail": reason,
        "why_blocked": "ROAL review failed; fail-closed implementation skip engaged",
        "needs": "Repair risk review inputs or rerun ROAL successfully",
    }
    Services.artifact_io().write_json(paths.blocker_signal(sec_num), payload)
    return paths.blocker_signal(sec_num)


def blocking_risk_plan(sec_num: str) -> RiskPlan:
    scope = f"section-{sec_num}"
    return RiskPlan(
        plan_id=f"risk-plan-failure-{scope}",
        assessment_id=f"{scope}-risk-review-failure",
        package_id=f"pkg-implementation-{scope}",
        layer="implementation",
        step_decisions=[],
        accepted_frontier=[],
        deferred_steps=[],
        reopen_steps=[],
        expected_reassessment_inputs=[],
    )


def has_stale_freshness_token(
    planspace: Path,
    sec_num: str,
    triage_signal: object,
) -> bool:
    if not isinstance(triage_signal, dict):
        return False

    token = triage_signal.get("freshness_token", triage_signal.get("freshness"))
    if not isinstance(token, str) or not token.strip():
        return False

    current = Services.freshness().compute(planspace, sec_num)
    return token.strip() != current


def has_recent_loop_detected_signal(
    planspace: Path,
    sec_num: str,
    scope: str,
) -> bool:
    signals_dir = PathRegistry(planspace).signals_dir()
    if not signals_dir.exists():
        return False

    for path in sorted(signals_dir.glob("*.json")):
        payload = Services.artifact_io().read_json(path)
        if not isinstance(payload, dict):
            continue
        if str(payload.get("state", "")).strip().lower() != "loop_detected":
            continue

        if str(payload.get("section_number", "")).strip() == sec_num:
            return True
        if str(payload.get("section", "")).strip() in {sec_num, scope}:
            return True
        if str(payload.get("scope", "")).strip() == scope:
            return True
        if str(payload.get("target", "")).strip() in {sec_num, scope}:
            return True

    return False


def load_risk_hints(planspace: Path, sec_num: str) -> dict:
    triage_signal = Services.artifact_io().read_json(
        PathRegistry(planspace).intent_triage_signal(sec_num),
    )
    if not isinstance(triage_signal, dict):
        return {
            "signal": None,
            "triage_confidence": "low",
            "risk_mode_hint": "",
            "posture_floor": None,
            "max_iterations": 5,
        }

    triage_confidence = str(
        triage_signal.get("risk_confidence", triage_signal.get("confidence", "low")),
    )
    risk_mode_hint = str(triage_signal.get("risk_mode", ""))
    posture_floor = triage_signal.get("posture_floor")
    budget_hint = triage_signal.get("risk_budget_hint", 0)
    max_iterations = _RISK_ITERATIONS_BASE
    if isinstance(budget_hint, int):
        max_iterations = min(_RISK_ITERATIONS_BASE + max(budget_hint, 0), _RISK_ITERATIONS_CAP)

    return {
        "signal": triage_signal,
        "triage_confidence": triage_confidence,
        "risk_mode_hint": risk_mode_hint,
        "posture_floor": posture_floor,
        "max_iterations": max_iterations,
    }


def write_modified_file_manifest(
    planspace: Path,
    sec_num: str,
    modified_files: list[str],
) -> Path:
    paths = PathRegistry(planspace)
    return write_section_input_artifact(
        paths,
        sec_num,
        paths.modified_file_manifest(sec_num).name,
        {
            "modified_files": list(modified_files),
            "count": len(modified_files),
        },
    )
