"""Implementation-pass orchestration helpers for the section loop."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Callable

from lib.core.artifact_io import read_json, write_json
from lib.services.alignment_change_tracker import (
    check_and_clear,
    check_pending as alignment_changed_pending,
)
from lib.core.path_registry import PathRegistry
from lib.repositories.note_repository import read_incoming_notes
from lib.repositories.proposal_state_repository import load_proposal_state
from lib.risk.engagement import determine_engagement
from lib.risk.history import append_history_entry, pattern_signature, read_history
from lib.risk.loop import run_lightweight_risk_check, run_risk_loop
from lib.risk.package_builder import build_package_from_proposal, read_package, refresh_package
from lib.risk.serialization import deserialize_assessment, read_risk_artifact
from lib.risk.types import (
    PostureProfile,
    RiskHistoryEntry,
    RiskMode,
    RiskPackage,
    RiskPlan,
    StepDecision,
)
from lib.services.freshness_service import compute_section_freshness
from lib.services.readiness_resolver import resolve_readiness
from section_loop.communication import AGENT_NAME, DB_SH, log, mailbox_send
from section_loop.dispatch import dispatch_agent
from section_loop.pipeline_control import (
    _section_inputs_hash,
    handle_pending_messages,
)
from section_loop.section_engine import run_section
from section_loop.types import ProposalPassResult, Section, SectionResult


class ImplementationPassExit(Exception):
    """Raised when the implementation pass should stop the outer run."""


class ImplementationPassRestart(Exception):
    """Raised when Phase 1 should restart after an alignment change."""


def _posture_rank(posture: PostureProfile) -> int:
    ranks = {
        PostureProfile.P0_DIRECT: 0,
        PostureProfile.P1_LIGHT: 1,
        PostureProfile.P2_STANDARD: 2,
        PostureProfile.P3_GUARDED: 3,
        PostureProfile.P4_REOPEN: 4,
    }
    return ranks[posture]


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


def _write_section_input_artifact(
    paths: PathRegistry,
    sec_num: str,
    artifact_name: str,
    payload: dict,
) -> Path:
    input_dir = paths.input_refs_dir(sec_num)
    artifact_path = input_dir / artifact_name
    write_json(artifact_path, payload)
    ref_path = input_dir / f"{artifact_path.stem}.ref"
    ref_path.write_text(str(artifact_path.resolve()), encoding="utf-8")
    return artifact_path


def _write_accepted_steps(
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
    posture = max(postures, key=_posture_rank) if postures else PostureProfile.P2_STANDARD
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
    return _write_section_input_artifact(
        paths,
        sec_num,
        f"section-{sec_num}-risk-accepted-steps.json",
        payload,
    )


def _write_deferred_steps(
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
    return _write_section_input_artifact(
        paths,
        sec_num,
        f"section-{sec_num}-risk-deferred.json",
        payload,
    )


def _write_reopen_blocker(
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
    write_json(paths.blocker_signal(sec_num), payload)
    return paths.blocker_signal(sec_num)


def _write_risk_review_failure_blocker(
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
    write_json(paths.blocker_signal(sec_num), payload)
    return paths.blocker_signal(sec_num)


def _blocking_risk_plan(sec_num: str) -> RiskPlan:
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


def _check_and_clear_alignment_changed(planspace: Path) -> bool:
    return check_and_clear(planspace, db_sh=DB_SH, agent_name=AGENT_NAME)


def _has_stale_freshness_token(
    planspace: Path,
    sec_num: str,
    triage_signal: object,
) -> bool:
    if not isinstance(triage_signal, dict):
        return False

    token = triage_signal.get("freshness_token", triage_signal.get("freshness"))
    if not isinstance(token, str) or not token.strip():
        return False

    current = compute_section_freshness(planspace, sec_num)
    return token.strip() != current


def _has_recent_loop_detected_signal(
    planspace: Path,
    sec_num: str,
    scope: str,
) -> bool:
    signals_dir = PathRegistry(planspace).signals_dir()
    if not signals_dir.exists():
        return False

    for path in sorted(signals_dir.glob("*.json")):
        payload = read_json(path)
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


def _load_risk_hints(planspace: Path, sec_num: str) -> dict:
    triage_signal = read_json(
        PathRegistry(planspace).signals_dir() / f"intent-triage-{sec_num}.json",
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
    max_iterations = 5
    if isinstance(budget_hint, int):
        max_iterations = min(5 + max(budget_hint, 0), 9)

    return {
        "signal": triage_signal,
        "triage_confidence": triage_confidence,
        "risk_mode_hint": risk_mode_hint,
        "posture_floor": posture_floor,
        "max_iterations": max_iterations,
    }


def _write_modified_file_manifest(
    planspace: Path,
    sec_num: str,
    modified_files: list[str],
) -> Path:
    paths = PathRegistry(planspace)
    return _write_section_input_artifact(
        paths,
        sec_num,
        f"section-{sec_num}-modified-file-manifest.json",
        {
            "modified_files": list(modified_files),
            "count": len(modified_files),
        },
    )


def _append_risk_review_failure_history(
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
                step_class=step.step_class,
                posture=PostureProfile.P4_REOPEN,
                predicted_risk=100,
                actual_outcome="risk_review_failure",
                surfaced_surprises=[reason],
                verification_outcome="failed",
                dominant_risks=[],
                blast_radius_band=0,
            ),
        )


def _deferred_reassessment_inputs_ready(
    planspace: Path,
    sec_num: str,
    deferred_payload: dict,
) -> bool:
    required_inputs = [
        str(item).strip()
        for item in deferred_payload.get("reassessment_inputs", [])
        if str(item).strip()
    ]
    if not required_inputs:
        return False

    paths = PathRegistry(planspace)
    input_dir = paths.input_refs_dir(sec_num)
    available = {
        "modified-file-manifest": (
            input_dir / f"section-{sec_num}-modified-file-manifest.json"
        ),
        "alignment-check-result": (
            paths.artifacts / f"impl-align-{sec_num}-output.md"
        ),
    }
    for required_input in required_inputs:
        required_path = available.get(required_input)
        if required_path is None or not required_path.exists():
            return False
    return True


def _build_deferred_reassessment_package(
    planspace: Path,
    sec_num: str,
    risk_plan: RiskPlan,
) -> RiskPackage | None:
    scope = f"section-{sec_num}"
    package = read_package(PathRegistry(planspace), scope)
    if package is None:
        return None

    refreshed = refresh_package(
        package,
        completed_steps=list(risk_plan.accepted_frontier),
        new_evidence={},
    )
    deferred_step_ids = set(risk_plan.deferred_steps)
    deferred_steps = [
        step
        for step in refreshed.steps
        if step.step_id in deferred_step_ids
    ]
    if not deferred_steps:
        return None

    return RiskPackage(
        package_id=refreshed.package_id,
        layer=refreshed.layer,
        scope=refreshed.scope,
        origin_problem_id=refreshed.origin_problem_id,
        origin_source=refreshed.origin_source,
        steps=deferred_steps,
    )


def _maybe_reassess_deferred_steps(
    planspace: Path,
    sec_num: str,
    dispatch_fn: Callable,
    risk_plan: RiskPlan,
) -> RiskPlan | None:
    scope = f"section-{sec_num}"
    paths = PathRegistry(planspace)
    deferred_path = (
        paths.input_refs_dir(sec_num) / f"{scope}-risk-deferred.json"
    )
    deferred_payload = read_json(deferred_path)
    if not isinstance(deferred_payload, dict):
        return None
    if not risk_plan.deferred_steps:
        return None
    if not _deferred_reassessment_inputs_ready(planspace, sec_num, deferred_payload):
        return None

    reassessment_package = _build_deferred_reassessment_package(
        planspace,
        sec_num,
        risk_plan,
    )
    if reassessment_package is None:
        return None

    hints = _load_risk_hints(planspace, sec_num)
    return run_risk_loop(
        planspace,
        scope,
        "implementation",
        reassessment_package,
        dispatch_fn,
        max_iterations=hints["max_iterations"],
        posture_floor=hints["posture_floor"],
    )


def _run_risk_review(
    planspace: Path,
    sec_num: str,
    section: Section,
    dispatch_fn: Callable,
) -> RiskPlan | None:
    """Run ROAL risk review for a section before implementation.

    Returns the risk plan, or None if ROAL is skipped (engagement mode = SKIP).
    """
    scope = f"section-{sec_num}"
    paths = PathRegistry(planspace)
    package: RiskPackage | None = None

    try:
        package = build_package_from_proposal(scope, planspace)
        proposal_state = load_proposal_state(
            paths.proposals_dir() / f"{scope}-proposal-state.json"
        )
        hints = _load_risk_hints(planspace, sec_num)
        triage_signal = hints["signal"]
        triage_confidence = hints["triage_confidence"]
        stale_inputs = _has_stale_freshness_token(planspace, sec_num, triage_signal)
        recent_loop_signal = _has_recent_loop_detected_signal(
            planspace,
            sec_num,
            scope,
        )

        engagement_mode = determine_engagement(
            step_count=len(package.steps),
            file_count=max(len(section.related_files), 1),
            has_shared_seams=bool(proposal_state.get("shared_seam_candidates")),
            has_consequence_notes=bool(read_incoming_notes(planspace, sec_num)),
            has_stale_inputs=stale_inputs,
            has_recent_failures=section.solve_count > 1 or recent_loop_signal,
            has_tool_changes=False,
            triage_confidence=triage_confidence,
            freshness_changed=stale_inputs,
            risk_mode_hint=hints["risk_mode_hint"],
        )
        if engagement_mode == RiskMode.SKIP:
            log(f"Section {sec_num}: ROAL skipped (engagement mode = skip)")
            return None
        if engagement_mode == RiskMode.LIGHT:
            plan = run_lightweight_risk_check(
                planspace,
                scope,
                "implementation",
                package,
                dispatch_fn,
                posture_floor=hints["posture_floor"],
            )
        else:
            plan = run_risk_loop(
                planspace,
                scope,
                "implementation",
                package,
                dispatch_fn,
                max_iterations=hints["max_iterations"],
                posture_floor=hints["posture_floor"],
            )

        log(
            f"Section {sec_num}: ROAL plan accepted={len(plan.accepted_frontier)} "
            f"deferred={len(plan.deferred_steps)} reopened={len(plan.reopen_steps)}",
        )
        return plan
    except Exception as exc:  # noqa: BLE001
        reason = str(exc) or exc.__class__.__name__
        _append_risk_review_failure_history(planspace, package, reason)
        _write_risk_review_failure_blocker(planspace, sec_num, reason)
        log(
            f"Section {sec_num}: ROAL review failed ({reason}) "
            "— wrote risk_review_failure blocker and skipped implementation",
        )
        return _blocking_risk_plan(sec_num)


def _append_risk_history(
    planspace: Path,
    sec_num: str,
    risk_plan: RiskPlan,
    modified_files: list[str] | None,
    *,
    implementation_failed: bool = False,
) -> None:
    scope = f"section-{sec_num}"
    paths = PathRegistry(planspace)
    package = read_package(paths, scope)
    assessment_payload = read_risk_artifact(paths.risk_assessment(scope))
    prior_history = read_history(paths.risk_history())
    try:
        assessment = (
            deserialize_assessment(assessment_payload)
            if isinstance(assessment_payload, dict)
            else None
        )
    except (KeyError, TypeError, ValueError):
        assessment = None

    package_steps = {
        step.step_id: step
        for step in (package.steps if package is not None else [])
    }
    assessment_steps = {
        step.step_id: step
        for step in (assessment.step_assessments if assessment is not None else [])
    }

    modified = list(modified_files or [])
    if implementation_failed:
        accepted_outcome = "failure"
        accepted_surprises = ["implementation failed after ROAL acceptance"]
        accepted_verification = "failed"
    elif modified:
        accepted_outcome = "success"
        accepted_surprises = []
        accepted_verification = "passed"
    else:
        accepted_outcome = "warning"
        accepted_surprises = ["implementation completed without file modifications"]
        accepted_verification = None

    for decision in risk_plan.step_decisions:
        package_step = package_steps.get(decision.step_id)
        assessment_step = assessment_steps.get(decision.step_id)
        if package_step is None:
            continue
        if decision.decision == StepDecision.ACCEPT:
            actual_outcome = accepted_outcome
            surfaced_surprises = list(accepted_surprises)
            verification_outcome = accepted_verification
        elif decision.decision == StepDecision.REJECT_DEFER:
            actual_outcome = "deferred"
            surfaced_surprises = _unique_strings(
                decision.wait_for + list(risk_plan.expected_reassessment_inputs),
            )
            verification_outcome = None
        else:
            actual_outcome = "reopened"
            surfaced_surprises = (
                [decision.reason]
                if decision.reason
                else ["ROAL reopened this step for higher-level routing"]
            )
            verification_outcome = None
        append_history_entry(
            paths.risk_history(),
            RiskHistoryEntry(
                package_id=risk_plan.package_id,
                step_id=decision.step_id,
                layer=risk_plan.layer,
                step_class=package_step.step_class,
                posture=decision.posture or PostureProfile.P4_REOPEN,
                predicted_risk=(
                    decision.residual_risk
                    if decision.residual_risk is not None
                    else 100
                ),
                actual_outcome=actual_outcome,
                surfaced_surprises=list(surfaced_surprises),
                verification_outcome=verification_outcome,
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
            and actual_outcome in {"success", "warning"}
            and assessment_step is not None
        ):
            current_signature = pattern_signature(
                package_step.step_class,
                assessment_step.dominant_risks,
                assessment_step.modifiers.blast_radius,
            )
            prior_rejections = [
                entry
                for entry in prior_history
                if pattern_signature(
                    entry.step_class,
                    entry.dominant_risks,
                    entry.blast_radius_band,
                ) == current_signature
                and entry.actual_outcome.strip().lower() in {"deferred", "reopened"}
            ]
            if prior_rejections:
                append_history_entry(
                    paths.risk_history(),
                    RiskHistoryEntry(
                        package_id=risk_plan.package_id,
                        step_id=decision.step_id,
                        layer=risk_plan.layer,
                        step_class=package_step.step_class,
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


def run_implementation_pass(
    proposal_results: dict[str, ProposalPassResult],
    sections_by_num: dict[str, Section],
    planspace: Path,
    codespace: Path,
    parent: str,
) -> dict[str, SectionResult]:
    """Run the implementation pass for execution-ready sections."""
    paths = PathRegistry(planspace)
    ready_sections = sorted(
        sec_num
        for sec_num, proposal_result in proposal_results.items()
        if proposal_result.execution_ready
    )
    impl_completed: set[str] = set()
    section_results: dict[str, SectionResult] = {}

    for sec_num in ready_sections:
        if handle_pending_messages(planspace, [], impl_completed):
            log("Aborted by parent during implementation pass")
            mailbox_send(planspace, parent, "fail:aborted")
            raise ImplementationPassExit

        if alignment_changed_pending(planspace):
            if _check_and_clear_alignment_changed(planspace):
                log("Alignment changed during implementation pass "
                    "— restarting from Phase 1")
                raise ImplementationPassRestart

        section = sections_by_num[sec_num]
        log(f"=== Section {sec_num} implementation pass ===")
        subprocess.run(  # noqa: S603
            [
                "bash",
                str(DB_SH),  # noqa: S607
                "log",
                str(planspace / "run.db"),
                "lifecycle",
                f"start:section:{sec_num}:impl",
                f"round {section.solve_count}",
                "--agent",
                AGENT_NAME,
            ],
            capture_output=True,
            text=True,
        )

        readiness = resolve_readiness(paths.artifacts, sec_num)
        if not readiness.get("ready"):
            log(
                f"Section {sec_num}: implementation pass skipped — "
                "readiness check failed before dispatch",
            )
            continue

        risk_plan = _run_risk_review(
            planspace,
            sec_num,
            section,
            dispatch_agent,
        )
        if risk_plan is None:
            pass
        else:
            if risk_plan.accepted_frontier:
                accepted_artifact = _write_accepted_steps(planspace, sec_num, risk_plan)
                log(
                    f"Section {sec_num}: wrote ROAL accepted frontier artifact "
                    f"to {accepted_artifact}",
                )
            if risk_plan.deferred_steps:
                deferred_artifact = _write_deferred_steps(planspace, sec_num, risk_plan)
                log(
                    f"Section {sec_num}: parked deferred ROAL work in "
                    f"{deferred_artifact}",
                )
            if risk_plan.reopen_steps:
                blocker_path = _write_reopen_blocker(planspace, sec_num, risk_plan)
                log(
                    f"Section {sec_num}: routed reopened ROAL steps via "
                    f"{blocker_path}",
                )

        if risk_plan is not None and not risk_plan.accepted_frontier:
            reasons = [
                decision.reason
                for decision in risk_plan.step_decisions
                if decision.reason
            ]
            log(
                f"Section {sec_num}: implementation skipped by ROAL — "
                f"{reasons[0] if reasons else 'all steps rejected'}",
            )
            continue

        modified_files = run_section(
            planspace,
            codespace,
            section,
            parent,
            all_sections=list(sections_by_num.values()),
            pass_mode="implementation",
        )

        if _check_and_clear_alignment_changed(planspace):
            log("Alignment changed during implementation — "
                "restarting from Phase 1")
            raise ImplementationPassRestart

        if modified_files is None:
            log(f"Section {sec_num}: implementation returned None")
            subprocess.run(  # noqa: S603
                [
                    "bash",
                    str(DB_SH),  # noqa: S607
                    "log",
                    str(planspace / "run.db"),
                    "lifecycle",
                    f"end:section:{sec_num}:impl",
                    "failed",
                    "--agent",
                    AGENT_NAME,
                ],
                capture_output=True,
                text=True,
            )
            if risk_plan is not None:
                _append_risk_history(
                    planspace,
                    sec_num,
                    risk_plan,
                    None,
                    implementation_failed=True,
                )
            continue

        impl_completed.add(sec_num)
        all_modified_files = list(modified_files)
        if risk_plan is not None:
            _append_risk_history(planspace, sec_num, risk_plan, all_modified_files)
            manifest_path = _write_modified_file_manifest(
                planspace,
                sec_num,
                all_modified_files,
            )
            log(
                f"Section {sec_num}: wrote modified file manifest "
                f"to {manifest_path}",
            )
            reassessed_plan = _maybe_reassess_deferred_steps(
                planspace,
                sec_num,
                dispatch_agent,
                risk_plan,
            )
            if reassessed_plan is not None:
                log(
                    f"Section {sec_num}: reassessed deferred ROAL steps "
                    f"accepted={len(reassessed_plan.accepted_frontier)} "
                    f"deferred={len(reassessed_plan.deferred_steps)} "
                    f"reopened={len(reassessed_plan.reopen_steps)}",
                )
                if reassessed_plan.accepted_frontier:
                    accepted_artifact = _write_accepted_steps(
                        planspace,
                        sec_num,
                        reassessed_plan,
                    )
                    log(
                        f"Section {sec_num}: updated ROAL accepted frontier "
                        f"after reassessment in {accepted_artifact}",
                    )
                deferred_artifact = _write_deferred_steps(
                    planspace,
                    sec_num,
                    reassessed_plan,
                )
                log(
                    f"Section {sec_num}: refreshed deferred ROAL artifact "
                    f"in {deferred_artifact}",
                )
                if reassessed_plan.reopen_steps:
                    blocker_path = _write_reopen_blocker(
                        planspace,
                        sec_num,
                        reassessed_plan,
                    )
                    log(
                        f"Section {sec_num}: routed reassessed reopened steps "
                        f"via {blocker_path}",
                    )
        mailbox_send(
            planspace,
            parent,
            f"done:{sec_num}:{len(all_modified_files)} files modified",
        )

        section_results[sec_num] = SectionResult(
            section_number=sec_num,
            aligned=True,
            modified_files=all_modified_files,
        )

        baseline_hash_dir = paths.section_inputs_hashes_dir()
        baseline_hash_dir.mkdir(parents=True, exist_ok=True)
        paths.section_input_hash(sec_num).write_text(
            _section_inputs_hash(sec_num, planspace, codespace, sections_by_num),
            encoding="utf-8",
        )

        phase2_hash_dir = paths.phase2_inputs_hashes_dir()
        phase2_hash_dir.mkdir(parents=True, exist_ok=True)
        paths.phase2_input_hash(sec_num).write_text(
            _section_inputs_hash(sec_num, planspace, codespace, sections_by_num),
            encoding="utf-8",
        )

        log(f"Section {sec_num}: implementation done")
        subprocess.run(  # noqa: S603
            [
                "bash",
                str(DB_SH),  # noqa: S607
                "log",
                str(planspace / "run.db"),
                "lifecycle",
                f"end:section:{sec_num}:impl",
                "done",
                "--agent",
                AGENT_NAME,
            ],
            capture_output=True,
            text=True,
        )

    return section_results
