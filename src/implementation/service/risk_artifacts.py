"""Risk artifact readers, writers, and hint loaders for implementation sections."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from orchestrator.path_registry import PathRegistry
from orchestrator.repository.section_artifacts import write_section_input_artifact
from risk.types import (
    PostureProfile,
    RiskPlan,
    StepDecision,
)
from signals.types import SIGNAL_LOOP_DETECTED, SIGNAL_NEEDS_PARENT

if TYPE_CHECKING:
    from containers import (
        ArtifactIOService,
        FreshnessService,
    )

_RISK_ITERATIONS_BASE = 5
_RISK_ITERATIONS_CAP = 9


def unique_strings(values: list[str]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for value in values:
        item = str(value).strip()
        if not item or item in seen:
            continue
        seen.add(item)
        ordered.append(item)
    return ordered


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


class RiskArtifacts:
    """Risk artifact readers, writers, and hint loaders for implementation sections.

    All cross-cutting services are received via constructor injection.
    """

    def __init__(
        self,
        artifact_io: ArtifactIOService,
        freshness: FreshnessService,
    ) -> None:
        self._artifact_io = artifact_io
        self._freshness = freshness

    def write_accepted_steps(
        self,
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
            "mitigations": unique_strings(
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
        self,
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
            "wait_for": unique_strings(
                [
                    item
                    for decision in deferred
                    for item in decision.wait_for
                ]
            ),
            "reassessment_inputs": unique_strings(
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
        self,
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
            "state": SIGNAL_NEEDS_PARENT,
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
        self._artifact_io.write_json(paths.blocker_signal(sec_num), payload)
        return paths.blocker_signal(sec_num)

    def write_risk_review_failure_blocker(
        self,
        planspace: Path,
        sec_num: str,
        reason: str,
    ) -> Path:
        paths = PathRegistry(planspace)
        payload = {
            "state": SIGNAL_NEEDS_PARENT,
            "blocker_type": "risk_review_failure",
            "source": "roal",
            "section": sec_num,
            "scope": f"section-{sec_num}",
            "reason": reason,
            "detail": reason,
            "why_blocked": "ROAL review failed; fail-closed implementation skip engaged",
            "needs": "Repair risk review inputs or rerun ROAL successfully",
        }
        self._artifact_io.write_json(paths.blocker_signal(sec_num), payload)
        return paths.blocker_signal(sec_num)

    def has_stale_freshness_token(
        self,
        planspace: Path,
        sec_num: str,
        triage_signal: object,
    ) -> bool:
        if not isinstance(triage_signal, dict):
            return False

        token = triage_signal.get("freshness_token", triage_signal.get("freshness"))
        if not isinstance(token, str) or not token.strip():
            return False

        current = self._freshness.compute(planspace, sec_num)
        return token.strip() != current

    def has_recent_loop_detected_signal(
        self,
        planspace: Path,
        sec_num: str,
        scope: str,
    ) -> bool:
        signals_dir = PathRegistry(planspace).signals_dir()
        if not signals_dir.exists():
            return False

        for path in sorted(signals_dir.glob("*.json")):
            payload = self._artifact_io.read_json(path)
            if not isinstance(payload, dict):
                continue
            if str(payload.get("state", "")).strip().lower() != SIGNAL_LOOP_DETECTED:
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

    def load_risk_hints(self, planspace: Path, sec_num: str) -> dict:
        triage_signal = self._artifact_io.read_json(
            PathRegistry(planspace).intent_triage_signal(sec_num),
        )
        if not isinstance(triage_signal, dict):
            return {
                "signal": None,
                "triage_confidence": "low",
                "risk_mode_hint": "",
                "posture_floor": None,
                "max_iterations": _RISK_ITERATIONS_BASE,
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
        self,
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


# ---------------------------------------------------------------------------
# Backward-compat free function wrappers
# ---------------------------------------------------------------------------


def write_accepted_steps(
    planspace: Path,
    sec_num: str,
    risk_plan: RiskPlan,
) -> Path:
    from containers import Services
    return RiskArtifacts(
        artifact_io=Services.artifact_io(),
        freshness=Services.freshness(),
    ).write_accepted_steps(planspace, sec_num, risk_plan)


def write_deferred_steps(
    planspace: Path,
    sec_num: str,
    risk_plan: RiskPlan,
) -> Path:
    from containers import Services
    return RiskArtifacts(
        artifact_io=Services.artifact_io(),
        freshness=Services.freshness(),
    ).write_deferred_steps(planspace, sec_num, risk_plan)


def write_reopen_blocker(
    planspace: Path,
    sec_num: str,
    risk_plan: RiskPlan,
) -> Path:
    from containers import Services
    return RiskArtifacts(
        artifact_io=Services.artifact_io(),
        freshness=Services.freshness(),
    ).write_reopen_blocker(planspace, sec_num, risk_plan)


def write_risk_review_failure_blocker(
    planspace: Path,
    sec_num: str,
    reason: str,
) -> Path:
    from containers import Services
    return RiskArtifacts(
        artifact_io=Services.artifact_io(),
        freshness=Services.freshness(),
    ).write_risk_review_failure_blocker(planspace, sec_num, reason)


def has_stale_freshness_token(
    planspace: Path,
    sec_num: str,
    triage_signal: object,
) -> bool:
    from containers import Services
    return RiskArtifacts(
        artifact_io=Services.artifact_io(),
        freshness=Services.freshness(),
    ).has_stale_freshness_token(planspace, sec_num, triage_signal)


def has_recent_loop_detected_signal(
    planspace: Path,
    sec_num: str,
    scope: str,
) -> bool:
    from containers import Services
    return RiskArtifacts(
        artifact_io=Services.artifact_io(),
        freshness=Services.freshness(),
    ).has_recent_loop_detected_signal(planspace, sec_num, scope)


def load_risk_hints(planspace: Path, sec_num: str) -> dict:
    from containers import Services
    return RiskArtifacts(
        artifact_io=Services.artifact_io(),
        freshness=Services.freshness(),
    ).load_risk_hints(planspace, sec_num)


def write_modified_file_manifest(
    planspace: Path,
    sec_num: str,
    modified_files: list[str],
) -> Path:
    from containers import Services
    return RiskArtifacts(
        artifact_io=Services.artifact_io(),
        freshness=Services.freshness(),
    ).write_modified_file_manifest(planspace, sec_num, modified_files)
