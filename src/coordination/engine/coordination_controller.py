"""Adaptive coordination loop helpers for the section loop."""

from __future__ import annotations

import itertools
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from coordination.problem_types import Problem
from coordination.service.problem_resolver import ProblemResolver
from coordination.types import CoordinationStatus
from orchestrator.path_registry import PathRegistry
from orchestrator.engine.strategic_state_builder import StrategicStateBuilder
from coordination.engine.global_coordinator import (
    GlobalCoordinator,
    MIN_COORDINATION_ROUNDS,
)
from coordination.service.stall_detector import StallDetector
from orchestrator.types import Section, SectionResult, ControlSignal
from pipeline.context import DispatchContext
from signals.types import TRUNCATE_DETAIL, TRUNCATE_MEDIUM

if TYPE_CHECKING:
    from containers import (
        ArtifactIOService,
        ChangeTrackerService,
        Communicator,
        LogService,
        ModelPolicyService,
        PipelineControlService,
    )


@dataclass(frozen=True)
class AssessmentResult:
    """Structured result from ``_assess_initial_state``."""

    misaligned: list[SectionResult] = field(default_factory=list)
    outstanding: list[Problem] = field(default_factory=list)
    early_exit_reason: str | None = None


class CoordinationController:
    """Adaptive coordination loop controller."""

    def __init__(
        self,
        *,
        artifact_io: ArtifactIOService,
        change_tracker: ChangeTrackerService,
        communicator: Communicator,
        global_coordinator: GlobalCoordinator,
        logger: LogService,
        pipeline_control: PipelineControlService,
        policies: ModelPolicyService,
        problem_resolver: ProblemResolver,
    ) -> None:
        self._artifact_io = artifact_io
        self._change_tracker = change_tracker
        self._communicator = communicator
        self._global_coordinator = global_coordinator
        self._logger = logger
        self._pipeline_control = pipeline_control
        self._policies = policies
        self._problem_resolver = problem_resolver
        self._strategic_state_builder = StrategicStateBuilder(artifact_io=artifact_io)

    def _check_alignment(self, planspace: Path) -> bool:
        """Poll for alignment changes.  Returns True if changed."""
        ctrl = self._pipeline_control.poll_control_messages(planspace)
        if ctrl == ControlSignal.ALIGNMENT_CHANGED:
            self._logger.log("Alignment changed \u2014 restarting from Phase 1")
            return True
        return False

    def _assess_initial_state(
        self,
        section_results: dict[str, SectionResult],
        sections_by_num: dict[str, Section],
        planspace: Path,
        decisions_dir: Path,
    ) -> AssessmentResult:
        """Check if coordination is needed at all.

        Returns an ``AssessmentResult``.
        If ``early_exit_reason`` is not None, the caller should return it.
        """
        misaligned = [r for r in section_results.values() if not r.aligned]
        outstanding: list[Problem] = []

        if not misaligned:
            outstanding = self._problem_resolver.collect_outstanding_problems(
                section_results, sections_by_num, planspace,
            )
            if outstanding:
                types = [p.type for p in outstanding]
                self._logger.log(
                    f"{len(outstanding)} outstanding cross-section problems "
                    f"remain (types: {types}) \u2014 cannot declare completion",
                )
            else:
                if self._check_alignment(planspace):
                    return AssessmentResult(misaligned=misaligned, outstanding=outstanding, early_exit_reason=CoordinationStatus.RESTART_PHASE1)
                self._logger.log("=== All sections ALIGNED after initial pass ===")
                self._strategic_state_builder.build_strategic_state(decisions_dir, section_results, planspace)
                self._communicator.send_to_parent(planspace, "complete")
                return AssessmentResult(misaligned=misaligned, outstanding=outstanding, early_exit_reason=CoordinationStatus.COMPLETE)

        return AssessmentResult(misaligned=misaligned, outstanding=outstanding)

    def _report_result(
        self,
        section_results: dict[str, SectionResult],
        sections_by_num: dict[str, Section],
        planspace: Path,
        decisions_dir: Path,
        round_num: int,
        termination_reason: str,
    ) -> str:
        """Summarize coordination result and notify parent."""
        paths = PathRegistry(planspace)
        remaining = [r for r in section_results.values() if not r.aligned]

        if remaining:
            self._logger.log(
                f"=== Coordination finished after {round_num} rounds, "
                f"{len(remaining)} sections still unresolved ===",
            )
            self._strategic_state_builder.build_strategic_state(decisions_dir, section_results, planspace)
            for result in remaining:
                summary = (result.problems or "unknown")[:TRUNCATE_MEDIUM]
                self._logger.log(f"  - Section {result.section_number}: {summary}")
                self._communicator.send_to_parent(
                    planspace,
                    f"fail:{result.section_number}:coordination_exhausted:{summary}",
                )
            return termination_reason

        outstanding = self._problem_resolver.collect_outstanding_problems(
            section_results, sections_by_num, planspace,
        )
        if outstanding:
            self._logger.log(
                f"=== Coordination exhausted after {round_num} rounds: all "
                f"sections aligned but {len(outstanding)} outstanding problems "
                "remain ===",
            )
            self._strategic_state_builder.build_strategic_state(decisions_dir, section_results, planspace)
            rollup_dir = paths.coordination_dir()
            self._artifact_io.write_json(
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
            self._communicator.send_to_parent(
                planspace,
                f"fail:coordination_exhausted:outstanding:{len(outstanding)}",
            )

        return termination_reason

    def run_coordination_loop(
        self,
        section_results: dict[str, SectionResult],
        sections_by_num: dict[str, Section],
        ctx: DispatchContext,
    ) -> str:
        """Run the adaptive coordination loop until completion or exhaustion."""
        decisions_dir = ctx.paths.decisions_dir()

        # --- Initial assessment -----------------------------------------------
        assessment = self._assess_initial_state(
            section_results, sections_by_num, ctx.planspace,
            decisions_dir,
        )
        if assessment.early_exit_reason is not None:
            return assessment.early_exit_reason

        outstanding_count = len(assessment.outstanding) if not assessment.misaligned else 0
        if assessment.misaligned:
            self._logger.log(
                f"{len(assessment.misaligned)} sections need coordination: "
                f"{sorted(r.section_number for r in assessment.misaligned)}",
            )
        else:
            self._logger.log(
                "All sections aligned but "
                f"{outstanding_count} outstanding cross-section problems "
                "need coordination",
            )

        # --- Coordination loop ------------------------------------------------
        _check_and_clear_alignment_changed = self._change_tracker.make_alignment_checker()
        stall = StallDetector(
            ctx.planspace,
            logger=self._logger,
            policies=self._policies,
            communicator=self._communicator,
        )
        stall.set_initial(len(assessment.misaligned) + outstanding_count)
        termination_reason = CoordinationStatus.EXHAUSTED

        for round_num in itertools.count(1):
            if self._check_alignment(ctx.planspace):
                return CoordinationStatus.RESTART_PHASE1

            self._logger.log(f"=== Coordination round {round_num} ===")
            self._communicator.send_to_parent(
                ctx.planspace,
                f"status:coordination:round-{round_num}",
            )

            all_done = self._global_coordinator.run_global_coordination(
                section_results, sections_by_num, ctx,
            )

            if self._pipeline_control.check_alignment_and_return(
                ctx.planspace, _check_and_clear_alignment_changed,
            ):
                self._logger.log("Alignment changed during coordination \u2014 restarting from Phase 1")
                return CoordinationStatus.RESTART_PHASE1

            if all_done:
                if self._check_alignment(ctx.planspace):
                    return CoordinationStatus.RESTART_PHASE1
                self._logger.log(f"=== All sections ALIGNED after coordination round {round_num} ===")
                self._strategic_state_builder.build_strategic_state(decisions_dir, section_results, ctx.planspace)
                self._communicator.send_to_parent(ctx.planspace, "complete")
                return CoordinationStatus.COMPLETE

            # Measure progress
            remaining = [r for r in section_results.values() if not r.aligned]
            remaining_outstanding = (
                self._problem_resolver.collect_outstanding_problems(
                    section_results, sections_by_num, ctx.planspace,
                )
                if not remaining
                else []
            )
            cur_unresolved = len(remaining) + len(remaining_outstanding)
            self._logger.log(
                f"Coordination round {round_num}: {cur_unresolved} unresolved "
                f"({len(remaining)} misaligned, "
                f"{len(remaining_outstanding)} outstanding)",
            )

            stall.update(cur_unresolved, round_num)
            if round_num >= MIN_COORDINATION_ROUNDS and stall.should_terminate:
                self._logger.log(
                    f"Coordination stalled ({stall.stall_count} rounds "
                    "without improvement) \u2014 stopping",
                )
                termination_reason = CoordinationStatus.STALLED
                break

        # --- Result reporting -------------------------------------------------
        return self._report_result(
            section_results, sections_by_num, ctx.planspace,
            decisions_dir, round_num, termination_reason,
        )

