"""Implementation-pass orchestration helpers for the section loop."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Callable

from orchestrator.path_registry import PathRegistry
from coordination.repository.notes import read_incoming_notes
from proposal.repository.state import load_proposal_state
from risk.service.engagement import determine_engagement
from risk.engine.risk_assessor import run_lightweight_risk_check, run_risk_loop
from risk.service.package_builder import build_package_from_proposal, read_package, refresh_package
from risk.types import (
    RiskMode,
    RiskPackage,
    RiskPlan,
    StepDecision,
)
from proposal.service.readiness_resolver import resolve_readiness
from containers import Services
from _config import AGENT_NAME, DB_SH
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

    Returns the risk plan, or None on failure.
    """
    scope = f"section-{sec_num}"
    paths = PathRegistry(planspace)
    package: RiskPackage | None = None

    try:
        package = build_package_from_proposal(scope, planspace)
        proposal_state = load_proposal_state(
            paths.proposals_dir() / f"{scope}-proposal-state.json"
        )
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
            has_shared_seams=bool(proposal_state.get("shared_seam_candidates")),
            has_consequence_notes=bool(read_incoming_notes(planspace, sec_num)),
            has_stale_inputs=stale_inputs,
            has_recent_failures=section.solve_count > 1 or recent_loop_signal,
            has_tool_changes=False,
            triage_confidence=triage_confidence,
            freshness_changed=stale_inputs,
            risk_mode_hint=hints["risk_mode_hint"],
        )
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
        if Services.pipeline_control().handle_pending_messages(planspace, [], impl_completed):
            Services.logger().log("Aborted by parent during implementation pass")
            Services.communicator().mailbox_send(planspace, parent, "fail:aborted")
            raise ImplementationPassExit

        if Services.pipeline_control().alignment_changed_pending(planspace):
            if _check_and_clear_alignment_changed(planspace):
                Services.logger().log("Alignment changed during implementation pass "
                    "— restarting from Phase 1")
                raise ImplementationPassRestart

        section = sections_by_num[sec_num]
        Services.logger().log(f"=== Section {sec_num} implementation pass ===")
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

        readiness = resolve_readiness(planspace, sec_num)
        if not readiness.get("ready"):
            Services.logger().log(
                f"Section {sec_num}: implementation pass skipped — "
                "readiness check failed before dispatch",
            )
            continue

        risk_plan = _run_risk_review(
            planspace,
            sec_num,
            section,
            Services.dispatcher().dispatch,
        )
        if risk_plan is None:
            refresh_roal_input_index(
                planspace,
                sec_num,
                replace_kinds=IMPLEMENTATION_ROAL_KINDS,
                new_entries=[],
            )
        else:
            _persist_roal_artifacts(planspace, sec_num, risk_plan)

        if risk_plan is not None and not risk_plan.accepted_frontier:
            reasons = [
                decision.reason
                for decision in risk_plan.step_decisions
                if decision.reason
            ]
            Services.logger().log(
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
            Services.logger().log("Alignment changed during implementation — "
                "restarting from Phase 1")
            raise ImplementationPassRestart

        if modified_files is None:
            Services.logger().log(f"Section {sec_num}: implementation returned None")
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
                append_risk_history(
                    planspace,
                    sec_num,
                    risk_plan,
                    None,
                    implementation_failed=True,
                )
            continue

        impl_completed.add(sec_num)
        all_modified_files = list(modified_files)
        current_risk_plan = risk_plan
        frontier_iterations = 0
        frontier_failed = False
        final_problem: str | None = None
        if risk_plan is not None:
            append_risk_history(planspace, sec_num, risk_plan, all_modified_files)
            while frontier_iterations < _MAX_FRONTIER_ITERATIONS:
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
                    Services.dispatcher().dispatch,
                    current_risk_plan,
                )
                if reassessed_plan is None:
                    break

                frontier_iterations += 1
                current_risk_plan = reassessed_plan
                Services.logger().log(
                    f"Section {sec_num}: reassessed deferred ROAL steps "
                    f"accepted={len(reassessed_plan.accepted_frontier)} "
                    f"deferred={len(reassessed_plan.deferred_steps)} "
                    f"reopened={len(reassessed_plan.reopen_steps)}",
                )
                _persist_roal_artifacts(planspace, sec_num, reassessed_plan)

                if not reassessed_plan.accepted_frontier:
                    break

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
                    pass_mode="implementation",
                )

                if _check_and_clear_alignment_changed(planspace):
                    Services.logger().log("Alignment changed during deferred frontier execution "
                        "— restarting from Phase 1")
                    raise ImplementationPassRestart

                if deferred_modified is None:
                    Services.logger().log(f"Section {sec_num}: deferred frontier slice returned None")
                    append_risk_history(
                        planspace,
                        sec_num,
                        reassessed_plan,
                        None,
                        implementation_failed=True,
                    )
                    frontier_failed = True
                    final_problem = "deferred frontier execution failed"
                    break

                if deferred_modified:
                    all_modified_files.extend(deferred_modified)

                append_risk_history(
                    planspace,
                    sec_num,
                    reassessed_plan,
                    list(deferred_modified or []),
                )

                if reassessed_plan.reopen_steps:
                    break
                if not reassessed_plan.deferred_steps:
                    break

            if not frontier_failed and current_risk_plan is not None:
                final_problem = _describe_remaining_risk_work(
                    current_risk_plan,
                    frontier_cap_reached=(
                        frontier_iterations >= _MAX_FRONTIER_ITERATIONS
                        and bool(current_risk_plan.deferred_steps)
                    ),
                )
        Services.communicator().mailbox_send(
            planspace,
            parent,
            f"done:{sec_num}:{len(all_modified_files)} files modified",
        )

        section_results[sec_num] = SectionResult(
            section_number=sec_num,
            aligned=final_problem is None,
            problems=final_problem,
            modified_files=all_modified_files,
        )

        baseline_hash_dir = paths.section_inputs_hashes_dir()
        baseline_hash_dir.mkdir(parents=True, exist_ok=True)
        paths.section_input_hash(sec_num).write_text(
            Services.pipeline_control().section_inputs_hash(sec_num, planspace, codespace, sections_by_num),
            encoding="utf-8",
        )

        phase2_hash_dir = paths.phase2_inputs_hashes_dir()
        phase2_hash_dir.mkdir(parents=True, exist_ok=True)
        paths.phase2_input_hash(sec_num).write_text(
            Services.pipeline_control().section_inputs_hash(sec_num, planspace, codespace, sections_by_num),
            encoding="utf-8",
        )

        Services.logger().log(f"Section {sec_num}: implementation done")
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
