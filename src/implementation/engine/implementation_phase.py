"""Implementation-pass orchestration helpers for the section loop."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from orchestrator.path_registry import PathRegistry
from coordination.repository.notes import read_incoming_notes
from proposal.repository.state import load_proposal_state
from risk.service.engagement import determine_engagement
from risk.engine.risk_assessor import run_lightweight_risk_check, run_risk_loop
from risk.service.package_builder import build_package_from_proposal, read_package, refresh_package
from risk.types import (
    EngagementContext,
    RiskMode,
    RiskPackage,
    RiskPlan,
    StepDecision,
)
from proposal.service.readiness_resolver import resolve_readiness
from containers import Services
from implementation.engine.section_pipeline import run_section
from implementation.repository.roal_index import (
    IMPLEMENTATION_ROAL_KINDS,
    refresh_roal_input_index,
)
from implementation.service.risk_artifact_writer import (
    blocking_risk_plan,
    has_recent_loop_detected_signal,
    has_stale_freshness_token,
    load_risk_hints,
    write_accepted_steps,
    write_deferred_steps,
    write_modified_file_manifest,
    write_reopen_blocker,
    write_risk_review_failure_blocker,
)
from implementation.service.risk_history_recorder import (
    append_risk_history,
    append_risk_review_failure_history,
)
from orchestrator.types import ProposalPassResult, Section, SectionResult
from signals.types import PASS_MODE_IMPLEMENTATION


@dataclass(frozen=True)
class FrontierSliceResult:
    """Result of a single deferred-frontier reassessment iteration."""

    failed: bool = False
    problem: str | None = None
    plan: RiskPlan | None = None
    should_break: bool = False

_MAX_FRONTIER_ITERATIONS = 3


class ImplementationPassExit(Exception):
    """Raised when the implementation pass should stop the outer run."""


class ImplementationPassRestart(Exception):
    """Raised when Phase 1 should restart after an alignment change."""


_check_and_clear_alignment_changed = Services.change_tracker().make_alignment_checker()


def _persist_roal_artifacts(
    planspace: Path,
    sec_num: str,
    risk_plan: RiskPlan,
) -> None:
    entries: list[dict] = []
    if risk_plan.accepted_frontier:
        accepted_artifact = write_accepted_steps(planspace, sec_num, risk_plan)
        entries.append({
            "kind": "accepted_frontier",
            "path": str(accepted_artifact),
            "produced_by": "implementation_pass",
        })
        Services.logger().log(
            f"Section {sec_num}: persisted ROAL accepted frontier artifact "
            f"to {accepted_artifact}",
        )
    if risk_plan.deferred_steps:
        deferred_artifact = write_deferred_steps(planspace, sec_num, risk_plan)
        entries.append({
            "kind": "deferred",
            "path": str(deferred_artifact),
            "produced_by": "implementation_pass",
        })
        Services.logger().log(
            f"Section {sec_num}: persisted deferred ROAL artifact "
            f"in {deferred_artifact}",
        )
    if risk_plan.reopen_steps:
        blocker_path = write_reopen_blocker(planspace, sec_num, risk_plan)
        entries.append({
            "kind": "reopen",
            "path": str(blocker_path),
            "produced_by": "implementation_pass",
        })
        Services.logger().log(
            f"Section {sec_num}: persisted ROAL reopen blocker "
            f"via {blocker_path}",
        )
    refresh_roal_input_index(
        planspace,
        sec_num,
        replace_kinds=IMPLEMENTATION_ROAL_KINDS,
        new_entries=entries,
    )


def _describe_remaining_risk_work(
    risk_plan: RiskPlan,
    *,
    frontier_cap_reached: bool = False,
) -> str | None:
    if risk_plan.reopen_steps:
        reopen_reason = next(
            (
                decision.reason
                for decision in risk_plan.step_decisions
                if decision.decision == StepDecision.REJECT_REOPEN
                and decision.step_id in risk_plan.reopen_steps
                and decision.reason
            ),
            None,
        )
        if reopen_reason:
            return reopen_reason
        return (
            "ROAL reopened steps remain: "
            + ", ".join(risk_plan.reopen_steps)
        )
    if risk_plan.deferred_steps:
        prefix = (
            "ROAL deferred steps remain after bounded frontier execution"
            if frontier_cap_reached
            else "ROAL deferred steps remain"
        )
        return f"{prefix}: {', '.join(risk_plan.deferred_steps)}"
    return None


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
    available = {
        "modified-file-manifest": paths.modified_file_manifest(sec_num),
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
    risk_plan: RiskPlan,
) -> RiskPlan | None:
    scope = f"section-{sec_num}"
    paths = PathRegistry(planspace)
    deferred_path = paths.risk_deferred(sec_num)
    deferred_payload = Services.artifact_io().read_json(deferred_path)
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

    hints = load_risk_hints(planspace, sec_num)
    return run_risk_loop(
        planspace,
        scope,
        "implementation",
        reassessment_package,
        max_iterations=hints["max_iterations"],
        posture_floor=hints["posture_floor"],
    )


def _run_risk_review(
    planspace: Path,
    section: Section,
) -> RiskPlan | None:
    """Run ROAL risk review for a section before implementation.

    Returns the risk plan, or None on failure.
    """
    sec_num = section.number
    scope = f"section-{sec_num}"
    paths = PathRegistry(planspace)
    package: RiskPackage | None = None

    try:
        package = build_package_from_proposal(scope, planspace)
        proposal_state = load_proposal_state(paths.proposal_state(sec_num))
        hints = load_risk_hints(planspace, sec_num)
        triage_signal = hints["signal"]
        triage_confidence = hints["triage_confidence"]
        stale_inputs = has_stale_freshness_token(planspace, sec_num, triage_signal)
        recent_loop_signal = has_recent_loop_detected_signal(
            planspace,
            sec_num,
            scope,
        )

        engagement_mode = determine_engagement(
            step_count=len(package.steps),
            file_count=max(len(section.related_files), 1),
            ctx=EngagementContext(
                has_shared_seams=bool(proposal_state.get("shared_seam_candidates")),
                has_consequence_notes=bool(read_incoming_notes(planspace, sec_num)),
                has_stale_inputs=stale_inputs,
                has_recent_failures=section.solve_count > 1 or recent_loop_signal,
                freshness_changed=stale_inputs,
            ),
            triage_confidence=triage_confidence,
            risk_mode_hint=hints["risk_mode_hint"],
        )
        if engagement_mode == RiskMode.LIGHT:
            plan = run_lightweight_risk_check(
                planspace,
                scope,
                "implementation",
                package,
                posture_floor=hints["posture_floor"],
            )
        else:
            plan = run_risk_loop(
                planspace,
                scope,
                "implementation",
                package,
                max_iterations=hints["max_iterations"],
                posture_floor=hints["posture_floor"],
            )

        Services.logger().log(
            f"Section {sec_num}: ROAL plan accepted={len(plan.accepted_frontier)} "
            f"deferred={len(plan.deferred_steps)} reopened={len(plan.reopen_steps)}",
        )
        return plan
    except Exception as exc:  # noqa: BLE001
        reason = str(exc) or exc.__class__.__name__
        append_risk_review_failure_history(planspace, package, reason)
        write_risk_review_failure_blocker(planspace, sec_num, reason)
        Services.logger().log(
            f"Section {sec_num}: ROAL review failed ({reason}) "
            "— wrote risk_review_failure blocker and skipped implementation",
        )
        return blocking_risk_plan(sec_num)


def _check_abort_conditions(
    planspace: Path,
    parent: str,
) -> None:
    """Check for abort/restart signals before processing a section.

    Raises ImplementationPassExit on parent abort,
    ImplementationPassRestart on alignment change.
    """
    if Services.pipeline_control().handle_pending_messages(planspace):
        Services.logger().log("Aborted by parent during implementation pass")
        Services.communicator().mailbox_send(planspace, parent, "fail:aborted")
        raise ImplementationPassExit

    if Services.pipeline_control().alignment_changed_pending(planspace):
        if _check_and_clear_alignment_changed(planspace):
            Services.logger().log("Alignment changed during implementation pass "
                "— restarting from Phase 1")
            raise ImplementationPassRestart


def _execute_frontier_slice(
    planspace: Path,
    codespace: Path,
    section: Section,
    parent: str,
    sections_by_num: dict[str, Section],
    current_risk_plan: RiskPlan,
    all_modified_files: list[str],
    frontier_iterations: int,
) -> FrontierSliceResult:
    """Execute one deferred-frontier reassessment iteration.

    Raises ImplementationPassRestart on alignment change.
    """
    sec_num = section.number
    manifest_path = write_modified_file_manifest(
        planspace,
        sec_num,
        all_modified_files,
    )
    Services.logger().log(
        f"Section {sec_num}: wrote modified file manifest "
        f"to {manifest_path}",
    )

    reassessed_plan = _maybe_reassess_deferred_steps(
        planspace,
        sec_num,
        current_risk_plan,
    )
    if reassessed_plan is None:
        return FrontierSliceResult(should_break=True)

    Services.logger().log(
        f"Section {sec_num}: reassessed deferred ROAL steps "
        f"accepted={len(reassessed_plan.accepted_frontier)} "
        f"deferred={len(reassessed_plan.deferred_steps)} "
        f"reopened={len(reassessed_plan.reopen_steps)}",
    )
    _persist_roal_artifacts(planspace, sec_num, reassessed_plan)

    if not reassessed_plan.accepted_frontier:
        return FrontierSliceResult(plan=reassessed_plan, should_break=True)

    Services.logger().log(
        f"Section {sec_num}: dispatching deferred frontier slice "
        f"(iteration {frontier_iterations}, "
        f"accepted={len(reassessed_plan.accepted_frontier)})",
    )
    deferred_modified = run_section(
        planspace,
        codespace,
        section,
        parent,
        all_sections=list(sections_by_num.values()),
        pass_mode=PASS_MODE_IMPLEMENTATION,
    )

    Services.pipeline_control().check_alignment_and_raise(
        planspace,
        _check_and_clear_alignment_changed,
        ImplementationPassRestart,
        "Alignment changed during deferred frontier execution "
        "— restarting from Phase 1",
    )

    if deferred_modified is None:
        Services.logger().log(f"Section {sec_num}: deferred frontier slice returned None")
        append_risk_history(
            planspace,
            sec_num,
            reassessed_plan,
            None,
            implementation_failed=True,
        )
        return FrontierSliceResult(
            failed=True,
            problem="deferred frontier execution failed",
            plan=reassessed_plan,
            should_break=True,
        )

    if deferred_modified:
        all_modified_files.extend(deferred_modified)

    append_risk_history(
        planspace,
        sec_num,
        reassessed_plan,
        list(deferred_modified or []),
    )

    should_break = bool(reassessed_plan.reopen_steps) or not reassessed_plan.deferred_steps
    return FrontierSliceResult(plan=reassessed_plan, should_break=should_break)


def _run_frontier_iterations(
    planspace: Path,
    codespace: Path,
    section: Section,
    parent: str,
    sections_by_num: dict[str, Section],
    risk_plan: RiskPlan,
    all_modified_files: list[str],
) -> str | None:
    """Execute deferred-frontier reassessment iterations for a section.

    Returns a problem description, or ``None`` if all frontier work completed.

    Raises ImplementationPassRestart on alignment change.
    """
    current_risk_plan = risk_plan
    frontier_iterations = 0
    frontier_failed = False
    final_problem: str | None = None

    while frontier_iterations < _MAX_FRONTIER_ITERATIONS:
        result = _execute_frontier_slice(
            planspace,
            codespace,
            section,
            parent,
            sections_by_num,
            current_risk_plan,
            all_modified_files,
            frontier_iterations + 1,
        )
        if result.plan is not None:
            frontier_iterations += 1
            current_risk_plan = result.plan
        if result.failed:
            frontier_failed = True
            final_problem = result.problem
        if result.should_break:
            break

    if not frontier_failed and current_risk_plan is not None:
        final_problem = _describe_remaining_risk_work(
            current_risk_plan,
            frontier_cap_reached=(
                frontier_iterations >= _MAX_FRONTIER_ITERATIONS
                and bool(current_risk_plan.deferred_steps)
            ),
        )

    return final_problem


def _persist_section_hashes(
    sec_num: str,
    planspace: Path,
    sections_by_num: dict[str, Section],
) -> None:
    """Write baseline and phase2 section-input hashes after implementation."""
    paths = PathRegistry(planspace)
    cur_hash = Services.pipeline_control().section_inputs_hash(
        sec_num, planspace, sections_by_num,
    )

    baseline_hash_dir = paths.section_inputs_hashes_dir()
    baseline_hash_dir.mkdir(parents=True, exist_ok=True)
    paths.section_input_hash(sec_num).write_text(cur_hash, encoding="utf-8")

    phase2_hash_dir = paths.phase2_inputs_hashes_dir()
    phase2_hash_dir.mkdir(parents=True, exist_ok=True)
    paths.phase2_input_hash(sec_num).write_text(cur_hash, encoding="utf-8")


def _prepare_risk_plan(
    planspace: Path, section: Section,
) -> tuple[RiskPlan | None, bool]:
    """Run risk review, persist ROAL artifacts, check accepted frontier.

    Returns (risk_plan, should_skip).
    """
    sec_num = section.number
    risk_plan = _run_risk_review(planspace, section)
    if risk_plan is None:
        refresh_roal_input_index(
            planspace, sec_num,
            replace_kinds=IMPLEMENTATION_ROAL_KINDS, new_entries=[],
        )
        return None, False

    _persist_roal_artifacts(planspace, sec_num, risk_plan)

    if not risk_plan.accepted_frontier:
        reasons = [d.reason for d in risk_plan.step_decisions if d.reason]
        Services.logger().log(
            f"Section {sec_num}: implementation skipped by ROAL — "
            f"{reasons[0] if reasons else 'all steps rejected'}",
        )
        return risk_plan, True

    return risk_plan, False


def _handle_failed_impl(
    planspace: Path, sec_num: str, risk_plan: RiskPlan | None,
) -> None:
    """Log and record history for a failed implementation dispatch."""
    Services.logger().log(f"Section {sec_num}: implementation returned None")
    Services.logger().log_lifecycle(planspace, f"end:section:{sec_num}:impl", "failed")
    if risk_plan is not None:
        append_risk_history(
            planspace, sec_num, risk_plan, None,
            implementation_failed=True,
        )


def _implement_section(
    section: Section,
    sections_by_num: dict[str, Section],
    planspace: Path,
    codespace: Path,
    parent: str,
) -> SectionResult | None:
    """Process a single section through the implementation pipeline.

    Returns a ``SectionResult`` when the section was successfully
    implemented, or ``None`` when it was skipped or failed.
    """
    sec_num = section.number
    Services.logger().log(f"=== Section {sec_num} implementation pass ===")
    Services.logger().log_lifecycle(planspace, f"start:section:{sec_num}:impl", f"round {section.solve_count}")

    readiness = resolve_readiness(planspace, sec_num)
    if not readiness.ready:
        Services.logger().log(
            f"Section {sec_num}: implementation pass skipped — "
            "readiness check failed before dispatch",
        )
        return None

    risk_plan, should_skip = _prepare_risk_plan(planspace, section)
    if should_skip:
        return None

    modified_files = run_section(
        planspace, codespace, section, parent,
        all_sections=list(sections_by_num.values()),
        pass_mode=PASS_MODE_IMPLEMENTATION,
    )

    Services.pipeline_control().check_alignment_and_raise(
        planspace,
        _check_and_clear_alignment_changed,
        ImplementationPassRestart,
        "Alignment changed during implementation — restarting from Phase 1",
    )

    if modified_files is None:
        _handle_failed_impl(planspace, sec_num, risk_plan)
        return None

    all_modified_files = list(modified_files)
    final_problem: str | None = None
    if risk_plan is not None:
        append_risk_history(planspace, sec_num, risk_plan, all_modified_files)
        final_problem = _run_frontier_iterations(
            planspace, codespace, section, parent,
            sections_by_num, risk_plan, all_modified_files,
        )

    Services.communicator().mailbox_send(
        planspace, parent,
        f"done:{sec_num}:{len(all_modified_files)} files modified",
    )

    _persist_section_hashes(sec_num, planspace, sections_by_num)
    Services.logger().log(f"Section {sec_num}: implementation done")
    Services.logger().log_lifecycle(planspace, f"end:section:{sec_num}:impl", "done")

    return SectionResult(
        section_number=sec_num,
        aligned=final_problem is None,
        problems=final_problem,
        modified_files=all_modified_files,
    )


def run_implementation_pass(
    proposal_results: dict[str, ProposalPassResult],
    sections_by_num: dict[str, Section],
    planspace: Path,
    codespace: Path,
    parent: str,
) -> dict[str, SectionResult]:
    """Run the implementation pass for execution-ready sections."""
    ready_sections = sorted(
        sec_num
        for sec_num, proposal_result in proposal_results.items()
        if proposal_result.execution_ready
    )
    section_results: dict[str, SectionResult] = {}

    for sec_num in ready_sections:
        _check_abort_conditions(planspace, parent)

        result = _implement_section(
            sections_by_num[sec_num],
            sections_by_num,
            planspace,
            codespace,
            parent,
        )
        if result is not None:
            section_results[sec_num] = result

    return section_results
