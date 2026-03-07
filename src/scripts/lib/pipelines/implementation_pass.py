"""Implementation-pass orchestration helpers for the section loop."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Callable

from lib.core.artifact_io import read_json
from lib.services.alignment_change_tracker import (
    check_and_clear,
    check_pending as alignment_changed_pending,
)
from lib.core.path_registry import PathRegistry
from lib.repositories.note_repository import read_incoming_notes
from lib.repositories.proposal_state_repository import load_proposal_state
from lib.risk.engagement import determine_engagement
from lib.risk.history import append_history_entry
from lib.risk.loop import run_lightweight_risk_check, run_risk_loop
from lib.risk.package_builder import build_package_from_proposal, read_package
from lib.risk.serialization import deserialize_assessment, read_risk_artifact
from lib.risk.types import (
    PostureProfile,
    RiskHistoryEntry,
    RiskMode,
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

    try:
        package = build_package_from_proposal(scope, planspace)
        proposal_state = load_proposal_state(
            paths.proposals_dir() / f"{scope}-proposal-state.json"
        )
        triage_signal = read_json(paths.signals_dir() / f"intent-triage-{sec_num}.json")
        triage_confidence = "low"
        if isinstance(triage_signal, dict):
            triage_confidence = str(triage_signal.get("confidence", "low"))
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
            )
        else:
            plan = run_risk_loop(
                planspace,
                scope,
                "implementation",
                package,
                dispatch_fn,
            )

        log(
            f"Section {sec_num}: ROAL plan accepted={len(plan.accepted_frontier)} "
            f"deferred={len(plan.deferred_steps)} reopened={len(plan.reopen_steps)}",
        )
        return plan
    except Exception as exc:  # noqa: BLE001
        log(
            f"Section {sec_num}: ROAL review failed ({exc}) "
            "— continuing with standard implementation",
        )
        return None


def _append_risk_history(
    planspace: Path,
    sec_num: str,
    risk_plan: RiskPlan,
    modified_files: list[str],
) -> None:
    scope = f"section-{sec_num}"
    paths = PathRegistry(planspace)
    package = read_package(paths, scope)
    assessment_payload = read_risk_artifact(paths.risk_assessment(scope))
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

    actual_outcome = "success" if modified_files else "warning"
    surfaced_surprises = (
        []
        if modified_files
        else ["implementation completed without file modifications"]
    )
    for decision in risk_plan.step_decisions:
        if decision.decision != StepDecision.ACCEPT:
            continue
        package_step = package_steps.get(decision.step_id)
        assessment_step = assessment_steps.get(decision.step_id)
        if package_step is None:
            continue
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
                verification_outcome="passed" if modified_files else None,
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
            continue

        impl_completed.add(sec_num)
        if risk_plan is not None:
            _append_risk_history(planspace, sec_num, risk_plan, modified_files)
        mailbox_send(
            planspace,
            parent,
            f"done:{sec_num}:{len(modified_files)} files modified",
        )

        section_results[sec_num] = SectionResult(
            section_number=sec_num,
            aligned=True,
            modified_files=modified_files,
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
