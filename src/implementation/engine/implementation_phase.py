"""Implementation-pass orchestration helpers for the section loop."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from orchestrator.path_registry import PathRegistry
from coordination.repository.notes import read_incoming_notes
from proposal.repository.state import State as ProposalStateRepo
from risk.service.engagement import determine_engagement
from risk.service.package_builder import PackageBuilder, refresh_package
from risk.types import (
    EngagementContext,
    RiskMode,
    RiskPackage,
    RiskPlan,
    StepDecision,
)
from orchestrator.engine.section_pipeline import SectionPipeline, build_section_pipeline
from implementation.repository.roal_index import (
    IMPLEMENTATION_ROAL_KINDS,
    RoalIndex,
)
from implementation.service.risk_artifacts import (
    RiskArtifacts,
    blocking_risk_plan,
)
from implementation.service.risk_history_recorder import (
    append_risk_history,
    append_risk_review_failure_history,
)
from orchestrator.types import ProposalPassResult, Section, SectionResult
from signals.types import PASS_MODE_IMPLEMENTATION

if TYPE_CHECKING:
    from containers import (
        ArtifactIOService,
        ChangeTrackerService,
        Communicator,
        LogService,
        PipelineControlService,
        RiskAssessmentService,
    )


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
    artifact_io: ArtifactIOService,
) -> RiskPackage | None:
    scope = f"section-{sec_num}"
    package = PackageBuilder(artifact_io=artifact_io).read_package(PathRegistry(planspace), scope)
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


class ImplementationPhase:
    """Implementation-pass orchestration for the section loop.

    All cross-cutting services are received via constructor injection.
    """

    def __init__(
        self,
        artifact_io: ArtifactIOService,
        change_tracker: ChangeTrackerService,
        communicator: Communicator,
        logger: LogService,
        pipeline_control: PipelineControlService,
        risk_assessment: RiskAssessmentService,
        risk_artifacts: RiskArtifacts,
        roal_index: RoalIndex,
        section_pipeline: SectionPipeline | None = None,
    ) -> None:
        self._artifact_io = artifact_io
        self._communicator = communicator
        self._logger = logger
        self._pipeline_control = pipeline_control
        self._risk_assessment = risk_assessment
        self._risk_artifacts = risk_artifacts
        self._roal_index = roal_index
        self._package_builder = PackageBuilder(artifact_io=artifact_io)
        self._section_pipeline = section_pipeline if section_pipeline is not None else build_section_pipeline()
        self._check_and_clear_alignment_changed = change_tracker.make_alignment_checker()

    def _persist_roal_artifacts(
        self,
        planspace: Path,
        sec_num: str,
        risk_plan: RiskPlan,
    ) -> None:
        entries: list[dict] = []
        if risk_plan.accepted_frontier:
            accepted_artifact = self._risk_artifacts.write_accepted_steps(planspace, sec_num, risk_plan)
            entries.append({
                "kind": "accepted_frontier",
                "path": str(accepted_artifact),
                "produced_by": "implementation_pass",
            })
            self._logger.log(
                f"Section {sec_num}: persisted ROAL accepted frontier artifact "
                f"to {accepted_artifact}",
            )
        if risk_plan.deferred_steps:
            deferred_artifact = self._risk_artifacts.write_deferred_steps(planspace, sec_num, risk_plan)
            entries.append({
                "kind": "deferred",
                "path": str(deferred_artifact),
                "produced_by": "implementation_pass",
            })
            self._logger.log(
                f"Section {sec_num}: persisted deferred ROAL artifact "
                f"in {deferred_artifact}",
            )
        if risk_plan.reopen_steps:
            blocker_path = self._risk_artifacts.write_reopen_blocker(planspace, sec_num, risk_plan)
            entries.append({
                "kind": "reopen",
                "path": str(blocker_path),
                "produced_by": "implementation_pass",
            })
            self._logger.log(
                f"Section {sec_num}: persisted ROAL reopen blocker "
                f"via {blocker_path}",
            )
        self._roal_index.refresh_roal_input_index(
            planspace,
            sec_num,
            replace_kinds=IMPLEMENTATION_ROAL_KINDS,
            new_entries=entries,
        )

    def _maybe_reassess_deferred_steps(
        self,
        planspace: Path,
        sec_num: str,
        risk_plan: RiskPlan,
    ) -> RiskPlan | None:
        scope = f"section-{sec_num}"
        paths = PathRegistry(planspace)
        deferred_path = paths.risk_deferred(sec_num)
        deferred_payload = self._artifact_io.read_json(deferred_path)
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
            artifact_io=self._artifact_io,
        )
        if reassessment_package is None:
            return None

        hints = self._risk_artifacts.load_risk_hints(planspace, sec_num)
        return self._risk_assessment.run_risk_loop(
            planspace,
            scope,
            "implementation",
            reassessment_package,
            max_iterations=hints["max_iterations"],
            posture_floor=hints["posture_floor"],
        )

    def _run_risk_review(
        self,
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
            package = self._package_builder.build_package_from_proposal(scope, planspace)
            proposal_state = ProposalStateRepo(artifact_io=self._artifact_io).load_proposal_state(paths.proposal_state(sec_num))
            hints = self._risk_artifacts.load_risk_hints(planspace, sec_num)
            triage_signal = hints["signal"]
            triage_confidence = hints["triage_confidence"]
            stale_inputs = self._risk_artifacts.has_stale_freshness_token(planspace, sec_num, triage_signal)
            recent_loop_signal = self._risk_artifacts.has_recent_loop_detected_signal(
                planspace,
                sec_num,
                scope,
            )

            engagement_mode = determine_engagement(
                step_count=len(package.steps),
                file_count=max(len(section.related_files), 1),
                ctx=EngagementContext(
                    has_shared_seams=bool(proposal_state.shared_seam_candidates),
                    has_consequence_notes=bool(read_incoming_notes(planspace, sec_num)),
                    has_stale_inputs=stale_inputs,
                    has_recent_failures=section.solve_count > 1 or recent_loop_signal,
                    freshness_changed=stale_inputs,
                ),
                triage_confidence=triage_confidence,
                risk_mode_hint=hints["risk_mode_hint"],
            )
            if engagement_mode == RiskMode.LIGHT:
                plan = self._risk_assessment.run_lightweight_check(
                    planspace,
                    scope,
                    "implementation",
                    package,
                    posture_floor=hints["posture_floor"],
                )
            else:
                plan = self._risk_assessment.run_risk_loop(
                    planspace,
                    scope,
                    "implementation",
                    package,
                    max_iterations=hints["max_iterations"],
                    posture_floor=hints["posture_floor"],
                )

            self._logger.log(
                f"Section {sec_num}: ROAL plan accepted={len(plan.accepted_frontier)} "
                f"deferred={len(plan.deferred_steps)} reopened={len(plan.reopen_steps)}",
            )
            return plan
        except Exception as exc:  # noqa: BLE001
            reason = str(exc) or exc.__class__.__name__
            append_risk_review_failure_history(planspace, package, reason)
            self._risk_artifacts.write_risk_review_failure_blocker(planspace, sec_num, reason)
            self._logger.log(
                f"Section {sec_num}: ROAL review failed ({reason}) "
                "— wrote risk_review_failure blocker and skipped implementation",
            )
            return blocking_risk_plan(sec_num)

    def _check_abort_conditions(
        self,
        planspace: Path,
    ) -> None:
        """Check for abort/restart signals before processing a section.

        Raises ImplementationPassExit on parent abort,
        ImplementationPassRestart on alignment change.
        """
        if self._pipeline_control.handle_pending_messages(planspace):
            self._logger.log("Aborted by parent during implementation pass")
            self._communicator.send_to_parent(planspace, "fail:aborted")
            raise ImplementationPassExit

        if self._pipeline_control.alignment_changed_pending(planspace):
            if self._check_and_clear_alignment_changed(planspace):
                self._logger.log("Alignment changed during implementation pass "
                    "— restarting from Phase 1")
                raise ImplementationPassRestart

    def _execute_frontier_slice(
        self,
        planspace: Path,
        codespace: Path,
        section: Section,
        sections_by_num: dict[str, Section],
        current_risk_plan: RiskPlan,
        all_modified_files: list[str],
        frontier_iterations: int,
    ) -> FrontierSliceResult:
        """Execute one deferred-frontier reassessment iteration.

        Raises ImplementationPassRestart on alignment change.
        """
        sec_num = section.number
        manifest_path = self._risk_artifacts.write_modified_file_manifest(
            planspace,
            sec_num,
            all_modified_files,
        )
        self._logger.log(
            f"Section {sec_num}: wrote modified file manifest "
            f"to {manifest_path}",
        )

        reassessed_plan = self._maybe_reassess_deferred_steps(
            planspace,
            sec_num,
            current_risk_plan,
        )
        if reassessed_plan is None:
            return FrontierSliceResult(should_break=True)

        self._logger.log(
            f"Section {sec_num}: reassessed deferred ROAL steps "
            f"accepted={len(reassessed_plan.accepted_frontier)} "
            f"deferred={len(reassessed_plan.deferred_steps)} "
            f"reopened={len(reassessed_plan.reopen_steps)}",
        )
        self._persist_roal_artifacts(planspace, sec_num, reassessed_plan)

        if not reassessed_plan.accepted_frontier:
            return FrontierSliceResult(plan=reassessed_plan, should_break=True)

        self._logger.log(
            f"Section {sec_num}: dispatching deferred frontier slice "
            f"(iteration {frontier_iterations}, "
            f"accepted={len(reassessed_plan.accepted_frontier)})",
        )
        deferred_modified = self._section_pipeline.run_section(
            planspace,
            codespace,
            section,
            all_sections=list(sections_by_num.values()),
            pass_mode=PASS_MODE_IMPLEMENTATION,
        )

        self._pipeline_control.check_alignment_and_raise(
            planspace,
            self._check_and_clear_alignment_changed,
            ImplementationPassRestart,
            "Alignment changed during deferred frontier execution "
            "— restarting from Phase 1",
        )

        if deferred_modified is None:
            self._logger.log(f"Section {sec_num}: deferred frontier slice returned None")
            append_risk_history(
                planspace,
                sec_num,
                reassessed_plan,
                None,
                implementation_failed=True,
                artifact_io=self._artifact_io,
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
            artifact_io=self._artifact_io,
        )

        should_break = bool(reassessed_plan.reopen_steps) or not reassessed_plan.deferred_steps
        return FrontierSliceResult(plan=reassessed_plan, should_break=should_break)

    def _run_frontier_iterations(
        self,
        planspace: Path,
        codespace: Path,
        section: Section,
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
            result = self._execute_frontier_slice(
                planspace,
                codespace,
                section,
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
        self,
        sec_num: str,
        planspace: Path,
        sections_by_num: dict[str, Section],
    ) -> None:
        """Write baseline and phase2 section-input hashes after implementation."""
        paths = PathRegistry(planspace)
        cur_hash = self._pipeline_control.section_inputs_hash(
            sec_num, planspace, sections_by_num,
        )

        paths.section_input_hash(sec_num).write_text(cur_hash, encoding="utf-8")
        paths.phase2_input_hash(sec_num).write_text(cur_hash, encoding="utf-8")

    def _prepare_risk_plan(
        self,
        planspace: Path, section: Section,
    ) -> tuple[RiskPlan | None, bool]:
        """Run risk review, persist ROAL artifacts, check accepted frontier.

        Returns (risk_plan, should_skip).
        """
        sec_num = section.number
        risk_plan = self._run_risk_review(planspace, section)
        if risk_plan is None:
            self._roal_index.refresh_roal_input_index(
                planspace, sec_num,
                replace_kinds=IMPLEMENTATION_ROAL_KINDS, new_entries=[],
            )
            return None, False

        self._persist_roal_artifacts(planspace, sec_num, risk_plan)

        if not risk_plan.accepted_frontier:
            reasons = [d.reason for d in risk_plan.step_decisions if d.reason]
            self._logger.log(
                f"Section {sec_num}: implementation skipped by ROAL — "
                f"{reasons[0] if reasons else 'all steps rejected'}",
            )
            return risk_plan, True

        return risk_plan, False

    def _handle_failed_impl(
        self,
        planspace: Path, sec_num: str, risk_plan: RiskPlan | None,
    ) -> None:
        """Log and record history for a failed implementation dispatch."""
        self._logger.log(f"Section {sec_num}: implementation returned None")
        self._logger.log_lifecycle(planspace, f"end:section:{sec_num}:impl", "failed")
        if risk_plan is not None:
            append_risk_history(
                planspace, sec_num, risk_plan, None,
                implementation_failed=True,
                artifact_io=self._artifact_io,
            )

    def _implement_section(
        self,
        section: Section,
        sections_by_num: dict[str, Section],
        planspace: Path,
        codespace: Path,
    ) -> SectionResult | None:
        """Process a single section through the implementation pipeline.

        Returns a ``SectionResult`` when the section was successfully
        implemented, or ``None`` when it was skipped or failed.
        """
        sec_num = section.number
        self._logger.log(f"=== Section {sec_num} implementation pass ===")
        self._logger.log_lifecycle(planspace, f"start:section:{sec_num}:impl", f"round {section.solve_count}")

        from proposal.service.readiness_resolver import ReadinessResolver
        readiness = ReadinessResolver(
            artifact_io=self._artifact_io,
        ).resolve_readiness(planspace, sec_num)
        if not readiness.ready:
            self._logger.log(
                f"Section {sec_num}: implementation pass skipped — "
                "readiness check failed before dispatch",
            )
            return None

        risk_plan, should_skip = self._prepare_risk_plan(planspace, section)
        if should_skip:
            return None

        modified_files = self._section_pipeline.run_section(
            planspace, codespace, section,
            all_sections=list(sections_by_num.values()),
            pass_mode=PASS_MODE_IMPLEMENTATION,
        )

        self._pipeline_control.check_alignment_and_raise(
            planspace,
            self._check_and_clear_alignment_changed,
            ImplementationPassRestart,
            "Alignment changed during implementation — restarting from Phase 1",
        )

        if modified_files is None:
            self._handle_failed_impl(planspace, sec_num, risk_plan)
            return None

        all_modified_files = list(modified_files)
        final_problem: str | None = None
        if risk_plan is not None:
            append_risk_history(planspace, sec_num, risk_plan, all_modified_files, artifact_io=self._artifact_io)
            final_problem = self._run_frontier_iterations(
                planspace, codespace, section,
                sections_by_num, risk_plan, all_modified_files,
            )

        self._communicator.send_to_parent(
            planspace,
            f"done:{sec_num}:{len(all_modified_files)} files modified",
        )

        self._persist_section_hashes(sec_num, planspace, sections_by_num)
        self._logger.log(f"Section {sec_num}: implementation done")
        self._logger.log_lifecycle(planspace, f"end:section:{sec_num}:impl", "done")

        return SectionResult(
            section_number=sec_num,
            aligned=final_problem is None,
            problems=final_problem,
            modified_files=all_modified_files,
        )

    def run_implementation_pass(
        self,
        proposal_results: dict[str, ProposalPassResult],
        sections_by_num: dict[str, Section],
        planspace: Path,
        codespace: Path,
    ) -> dict[str, SectionResult]:
        """Run the implementation pass for execution-ready sections."""
        ready_sections = sorted(
            sec_num
            for sec_num, proposal_result in proposal_results.items()
            if proposal_result.execution_ready
        )
        section_results: dict[str, SectionResult] = {}

        for sec_num in ready_sections:
            self._check_abort_conditions(planspace)

            result = self._implement_section(
                sections_by_num[sec_num],
                sections_by_num,
                planspace,
                codespace,
            )
            if result is not None:
                section_results[sec_num] = result

        return section_results
