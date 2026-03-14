"""Adaptive coordination loop helpers for the section loop."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from coordination.problem_types import Problem
from coordination.service.problem_resolver import _collect_outstanding_problems
from coordination.types import CoordinationStatus
from orchestrator.path_registry import PathRegistry
from orchestrator.engine.strategic_state_builder import build_strategic_state
from containers import Services
from coordination.engine.global_coordinator import (
    MAX_COORDINATION_ROUNDS,
    MIN_COORDINATION_ROUNDS,
    run_global_coordination,
)
from coordination.service.stall_detector import StallDetector
from orchestrator.types import Section, SectionResult, ControlSignal
from pipeline.context import DispatchContext
from signals.types import TRUNCATE_DETAIL, TRUNCATE_MEDIUM


@dataclass(frozen=True)
class AssessmentResult:
    """Structured result from ``_assess_initial_state``."""

    misaligned: list[SectionResult] = field(default_factory=list)
    outstanding: list[Problem] = field(default_factory=list)
    early_exit_reason: str | None = None


def _check_alignment(planspace: Path, parent: str) -> bool:
    """Poll for alignment changes.  Returns True if changed."""
    ctrl = Services.pipeline_control().poll_control_messages(planspace, parent)
    if ctrl == ControlSignal.ALIGNMENT_CHANGED:
        Services.logger().log("Alignment changed — restarting from Phase 1")
        return True
    return False


def _assess_initial_state(
    section_results: dict[str, SectionResult],
    sections_by_num: dict[str, Section],
    planspace: Path,
    parent: str,
    decisions_dir: Path,
) -> AssessmentResult:
    """Check if coordination is needed at all.

    Returns an ``AssessmentResult``.
    If ``early_exit_reason`` is not None, the caller should return it.
    """
    misaligned = [r for r in section_results.values() if not r.aligned]
    outstanding: list[Problem] = []

    if not misaligned:
        outstanding = _collect_outstanding_problems(
            section_results, sections_by_num, planspace,
        )
        if outstanding:
            types = [p.type for p in outstanding]
            Services.logger().log(
                f"{len(outstanding)} outstanding cross-section problems "
                f"remain (types: {types}) — cannot declare completion",
            )
        else:
            if _check_alignment(planspace, parent):
                return AssessmentResult(misaligned=misaligned, outstanding=outstanding, early_exit_reason=CoordinationStatus.RESTART_PHASE1)
            Services.logger().log("=== All sections ALIGNED after initial pass ===")
            build_strategic_state(decisions_dir, section_results, planspace)
            Services.communicator().mailbox_send(planspace, parent, "complete")
            return AssessmentResult(misaligned=misaligned, outstanding=outstanding, early_exit_reason=CoordinationStatus.COMPLETE)

    return AssessmentResult(misaligned=misaligned, outstanding=outstanding)


def _report_result(
    section_results: dict[str, SectionResult],
    sections_by_num: dict[str, Section],
    planspace: Path,
    parent: str,
    decisions_dir: Path,
    round_num: int,
    termination_reason: str,
) -> str:
    """Summarize coordination result and notify parent."""
    paths = PathRegistry(planspace)
    remaining = [r for r in section_results.values() if not r.aligned]

    if remaining:
        Services.logger().log(
            f"=== Coordination finished after {round_num} rounds, "
            f"{len(remaining)} sections still unresolved ===",
        )
        build_strategic_state(decisions_dir, section_results, planspace)
        for result in remaining:
            summary = (result.problems or "unknown")[:TRUNCATE_MEDIUM]
            Services.logger().log(f"  - Section {result.section_number}: {summary}")
            Services.communicator().mailbox_send(
                planspace, parent,
                f"fail:{result.section_number}:coordination_exhausted:{summary}",
            )
        return termination_reason

    outstanding = _collect_outstanding_problems(
        section_results, sections_by_num, planspace,
    )
    if outstanding:
        Services.logger().log(
            f"=== Coordination exhausted after {round_num} rounds: all "
            f"sections aligned but {len(outstanding)} outstanding problems "
            "remain ===",
        )
        build_strategic_state(decisions_dir, section_results, planspace)
        rollup_dir = paths.coordination_dir()
        rollup_dir.mkdir(parents=True, exist_ok=True)
        Services.artifact_io().write_json(
            rollup_dir / "coordination-exhausted.json",
            [
                {
                    "type": p.type,
                    "section": p.section,
                    "description": p.description[:TRUNCATE_DETAIL],
                }
                for p in outstanding
            ],
        )
        Services.communicator().mailbox_send(
            planspace, parent,
            f"fail:coordination_exhausted:outstanding:{len(outstanding)}",
        )

    return termination_reason


def run_coordination_loop(
    section_results: dict[str, SectionResult],
    sections_by_num: dict[str, Section],
    ctx: DispatchContext,
) -> str:
    """Run the adaptive coordination loop until completion or exhaustion."""
    decisions_dir = ctx.paths.decisions_dir()

    # --- Initial assessment -----------------------------------------------
    assessment = _assess_initial_state(
        section_results, sections_by_num, ctx.planspace, ctx.parent,
        decisions_dir,
    )
    if assessment.early_exit_reason is not None:
        return assessment.early_exit_reason

    outstanding_count = len(assessment.outstanding) if not assessment.misaligned else 0
    if assessment.misaligned:
        Services.logger().log(
            f"{len(assessment.misaligned)} sections need coordination: "
            f"{sorted(r.section_number for r in assessment.misaligned)}",
        )
    else:
        Services.logger().log(
            "All sections aligned but "
            f"{outstanding_count} outstanding cross-section problems "
            "need coordination",
        )

    # --- Coordination loop ------------------------------------------------
    stall = StallDetector(ctx.planspace, ctx.parent)
    stall.set_initial(len(assessment.misaligned) + outstanding_count)
    termination_reason = CoordinationStatus.EXHAUSTED

    for round_num in range(1, MAX_COORDINATION_ROUNDS + 1):
        if _check_alignment(ctx.planspace, ctx.parent):
            return CoordinationStatus.RESTART_PHASE1

        Services.logger().log(f"=== Coordination round {round_num} ===")
        Services.communicator().mailbox_send(
            ctx.planspace, ctx.parent,
            f"status:coordination:round-{round_num}",
        )

        all_done = run_global_coordination(
            section_results, sections_by_num, ctx,
        )

        if Services.pipeline_control().check_alignment_and_return(
            ctx.planspace, _check_and_clear_alignment_changed,
        ):
            Services.logger().log("Alignment changed during coordination — restarting from Phase 1")
            return CoordinationStatus.RESTART_PHASE1

        if all_done:
            if _check_alignment(ctx.planspace, ctx.parent):
                return CoordinationStatus.RESTART_PHASE1
            Services.logger().log(f"=== All sections ALIGNED after coordination round {round_num} ===")
            build_strategic_state(decisions_dir, section_results, ctx.planspace)
            Services.communicator().mailbox_send(ctx.planspace, ctx.parent, "complete")
            return CoordinationStatus.COMPLETE

        # Measure progress
        remaining = [r for r in section_results.values() if not r.aligned]
        remaining_outstanding = (
            _collect_outstanding_problems(
                section_results, sections_by_num, ctx.planspace,
            )
            if not remaining
            else []
        )
        cur_unresolved = len(remaining) + len(remaining_outstanding)
        Services.logger().log(
            f"Coordination round {round_num}: {cur_unresolved} unresolved "
            f"({len(remaining)} misaligned, "
            f"{len(remaining_outstanding)} outstanding)",
        )

        stall.update(cur_unresolved, round_num)
        if round_num >= MIN_COORDINATION_ROUNDS and stall.should_terminate:
            Services.logger().log(
                f"Coordination stalled ({stall.stall_count} rounds "
                "without improvement) — stopping",
            )
            termination_reason = CoordinationStatus.STALLED
            break

    # --- Result reporting -------------------------------------------------
    return _report_result(
        section_results, sections_by_num, ctx.planspace, ctx.parent,
        decisions_dir, round_num, termination_reason,
    )


_check_and_clear_alignment_changed = Services.change_tracker().make_alignment_checker()
