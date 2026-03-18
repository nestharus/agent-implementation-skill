from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from containers import (
        ArtifactIOService,
        ChangeTrackerService,
        Communicator,
        ConfigService,
        LogService,
        PipelineControlService,
        PromptGuard,
        SectionAlignmentService,
    )
    from flow.engine.flow_submitter import FlowSubmitter
    from scan.codemap.codemap_builder import CodemapBuilder

from intake.service.assessment_evaluator import AssessmentEvaluator
from intake.repository.governance_loader import GovernanceLoader
from coordination.engine.coordination_controller import CoordinationController
from coordination.engine.resolution_phase import ResolutionPhase
from coordination.types import CoordinationStatus
from implementation.engine.implementation_phase import (
    ImplementationPassExit,
    ImplementationPassRestart,
    ImplementationPhase,
)
from orchestrator.path_registry import PathRegistry
from scan.service.project_mode import ProjectModeResolver
from orchestrator.engine.section_pipeline import SectionPipeline, build_section_pipeline
from proposal.engine.proposal_phase import (
    ProposalPassExit,
    run_proposal_pass,
    submit_proposal_fanout,
)
from reconciliation.engine.cross_section_reconciler import CrossSectionReconciler
from reconciliation.engine.reconciliation_phase import ReconciliationPhase, ReconciliationPhaseExit
from scan.service.section_loader import load_sections
from flow.service.task_db_client import count_pending_tasks, init_db
from pipeline.context import DispatchContext
from signals.types import TRUNCATE_SUMMARY
from orchestrator.engine.strategic_state_builder import StrategicStateBuilder
from orchestrator.repository.cycle_state import CycleState
from orchestrator.types import PipelineAbortError, SectionResult

logging.basicConfig(
    level=logging.INFO,
    format="[%(name)s] %(message)s",
    stream=sys.stderr,
)

_MAX_BLOCKERS_IN_SUMMARY = 3


class PipelineOrchestrator:
    def __init__(
        self,
        communicator: Communicator,
        logger: LogService,
        config: ConfigService,
        artifact_io: ArtifactIOService,
        prompt_guard: PromptGuard,
        section_alignment: SectionAlignmentService,
        change_tracker: ChangeTrackerService,
        pipeline_control: PipelineControlService,
        coordination_controller: CoordinationController,
        implementation_phase: ImplementationPhase,
        reconciliation_phase: ReconciliationPhase,
        resolution_phase: ResolutionPhase | None = None,
        codemap_builder: CodemapBuilder | None = None,
        section_pipeline: SectionPipeline | None = None,
        flow_submitter: FlowSubmitter | None = None,
    ) -> None:
        self._communicator = communicator
        self._logger = logger
        self._config = config
        self._artifact_io = artifact_io
        self._prompt_guard = prompt_guard
        self._section_alignment = section_alignment
        self._change_tracker = change_tracker
        self._pipeline_control = pipeline_control
        self._coordination_controller = coordination_controller
        self._implementation_phase = implementation_phase
        self._reconciliation_phase = reconciliation_phase
        self._resolution_phase = resolution_phase
        self._codemap_builder = codemap_builder
        self._section_pipeline = section_pipeline
        self._flow_submitter = flow_submitter
        self._strategic_state_builder = StrategicStateBuilder(artifact_io=artifact_io)
        self._check_and_clear_alignment_changed = change_tracker.make_alignment_checker()

    def main(self) -> None:
        """Run the section loop orchestrator CLI."""
        import argparse

        parser = argparse.ArgumentParser(
            description="Section loop orchestrator for the implementation pipeline.",
        )
        parser.add_argument("planspace", type=Path,
                            help="Path to the planspace directory")
        parser.add_argument("codespace", type=Path,
                            help="Path to the codespace directory")
        parser.add_argument("--global-proposal", type=Path, required=True,
                            dest="global_proposal",
                            help="Path to the global proposal document")
        parser.add_argument("--global-alignment", type=Path, required=True,
                            dest="global_alignment",
                            help="Path to the global alignment document")
        parser.add_argument("--parent", type=str, default="orchestrator",
                            help="Parent agent mailbox name (default: orchestrator)")

        args = parser.parse_args()

        # Validate paths
        if not args.global_proposal.exists():
            print(f"Error: global proposal not found: {args.global_proposal}")
            sys.exit(1)
        if not args.global_alignment.exists():
            print(f"Error: global alignment not found: {args.global_alignment}")
            sys.exit(1)

        paths = PathRegistry(args.planspace)
        sections_dir = paths.sections_dir()
        self._communicator.mailbox_register(args.planspace)
        self._communicator.set_parent(args.parent)
        self._pipeline_control.set_parent(args.parent)
        self._logger.log(f"Registered: {self._config.agent_name} (parent: {args.parent})")

        from containers import Services
        ctx = DispatchContext(planspace=args.planspace, codespace=args.codespace, _policies=Services.policies())

        try:
            self._run_loop(ctx, sections_dir,
                      args.global_proposal, args.global_alignment)
        except PipelineAbortError:
            self._logger.log("Pipeline aborted")
        finally:
            self._communicator.mailbox_cleanup(args.planspace)
            self._logger.log("Mailbox cleaned up")

    def _run_phase2(
        self,
        sections_by_num: dict,
        cycle: CycleState,
        ctx: DispatchContext,
    ) -> CoordinationStatus | str:
        """Run Phase 2: strategic state, global recheck, and coordination."""
        section_results = cycle.section_results
        self._strategic_state_builder.build_strategic_state(PathRegistry(ctx.planspace).decisions_dir(), section_results, ctx.planspace)

        evaluator = AssessmentEvaluator(
            artifact_io=self._artifact_io,
            prompt_guard=self._prompt_guard,
        )
        promoted = evaluator.promote_debt_signals(ctx.planspace)
        if promoted:
            self._logger.log(f"Stabilization: promoted {len(promoted)} debt entries to staging")

        phase2_status = self._section_alignment.run_global_recheck(
            sections_by_num, section_results, ctx.planspace, ctx.codespace,
        )
        if phase2_status == CoordinationStatus.RESTART_PHASE1:
            return CoordinationStatus.RESTART_PHASE1

        coordination_status = self._coordination_controller.run_coordination_loop(
            section_results, sections_by_num, ctx,
        )
        return coordination_status or CoordinationStatus.COMPLETE

    def _refresh_codemap_after_implementation(
        self,
        section_results: dict[str, SectionResult],
        ctx: DispatchContext,
    ) -> None:
        """Trigger a targeted codemap refresh after the implementation pass.

        Collects modified files from all section results. If any files were
        modified, triggers a codemap rebuild. The fingerprint mechanism in
        ``CodemapBuilder._try_reuse_existing()`` detects whether the
        codespace actually changed and short-circuits when nothing is stale.
        """
        if self._codemap_builder is None:
            return

        modified_files = [
            f
            for result in section_results.values()
            for f in result.modified_files
        ]
        if not modified_files:
            return

        paths = PathRegistry(ctx.planspace)
        self._logger.log(
            f"[CODEMAP] Triggering post-implementation refresh "
            f"({len(modified_files)} files modified)",
        )
        ok = self._codemap_builder.run_codemap_build(
            codemap_path=paths.codemap(),
            codespace=ctx.codespace,
            artifacts_dir=paths.artifacts,
            scan_log_dir=paths.scan_logs_dir(),
            fingerprint_path=paths.codemap_fingerprint(),
        )
        if ok:
            self._logger.log("[CODEMAP] Post-implementation refresh complete")
        else:
            self._logger.log(
                "[CODEMAP] Post-implementation refresh failed "
                "— continuing with existing codemap",
            )

    def _submit_and_await_proposal_fanout(
        self,
        all_sections: list,
        sections_by_num: dict,
        ctx: DispatchContext,
    ) -> dict | None:
        """Submit the proposal fanout and poll until the gate fires.

        Returns proposal results dict, or ``None`` if aborted.
        """
        from orchestrator.types import ProposalPassResult

        gate_id, flow_id = submit_proposal_fanout(
            all_sections, ctx.planspace, self._flow_submitter,
        )
        self._logger.log(
            f"Submitted proposal fanout: gate={gate_id}, flow={flow_id}, "
            f"{len(all_sections)} sections",
        )

        paths = PathRegistry(ctx.planspace)
        db_path = paths.run_db()
        gate_signal = paths.signals_dir() / "proposal-gate-complete.json"

        # Remove any stale gate-complete signal from a previous iteration.
        if gate_signal.exists():
            gate_signal.unlink()

        # Poll until the gate fires or is aborted.  The dispatcher runs
        # in the same process loop (or externally) and calls
        # ``reconcile_task_completion`` which writes the signal file.
        import time

        _POLL_INTERVAL = 2.0
        while True:
            if self._pipeline_control.handle_pending_messages(ctx.planspace):
                self._logger.log("Aborted by parent during proposal fanout")
                self._communicator.send_to_parent(ctx.planspace, "fail:aborted")
                return None

            if gate_signal.exists():
                self._logger.log("Proposal gate fired — loading results")
                break

            pending = count_pending_tasks(db_path, flow_id=flow_id)
            if pending == 0:
                self._logger.log("No pending tasks in proposal flow — proceeding")
                break

            time.sleep(_POLL_INTERVAL)

        # Load proposal results from disk (written by each section task).
        proposal_results: dict[str, ProposalPassResult] = {}
        for section in all_sections:
            sec_num = section.number
            readiness_path = paths.execution_ready(sec_num)
            data = self._artifact_io.read_json(readiness_path)
            if data is not None and isinstance(data, dict):
                proposal_results[sec_num] = ProposalPassResult(
                    section_number=sec_num,
                    proposal_aligned=data.get("proposal_aligned", False),
                    execution_ready=data.get("execution_ready", False),
                    blockers=data.get("blockers", []),
                    needs_reconciliation=data.get("needs_reconciliation", False),
                    proposal_state_path=data.get("proposal_state_path", ""),
                )
            else:
                # Section had no readiness artifact — record as blocked.
                proposal_results[sec_num] = ProposalPassResult(
                    section_number=sec_num,
                    proposal_aligned=False,
                    execution_ready=False,
                    blockers=[{
                        "type": "missing_readiness",
                        "description": (
                            f"Section {sec_num} proposal task did not "
                            f"produce a readiness artifact"
                        ),
                    }],
                )

        ready = sum(1 for pr in proposal_results.values() if pr.execution_ready)
        blocked = len(proposal_results) - ready
        self._logger.log(
            f"=== Phase 1a complete: {len(proposal_results)} sections proposed "
            f"({ready} ready, {blocked} blocked) ===",
        )
        return proposal_results

    def _run_loop(self, ctx: DispatchContext,
                  sections_dir: Path, global_proposal: Path,
                  global_alignment: Path) -> None:
        # Governance bootstrap is demand-driven via build_governance_indexes()
        governance_loader = GovernanceLoader(artifact_io=self._artifact_io)
        governance_loader.build_governance_indexes(ctx.codespace, ctx.planspace)

        # Project mode: resolve only if not already determined by scan
        _paths = PathRegistry(ctx.planspace)
        if not _paths.project_mode_json().exists():
            _mode_resolver = ProjectModeResolver(
                artifact_io=self._artifact_io,
                logger=self._logger,
                pipeline_control=self._pipeline_control,
            )
            project_mode, mode_constraints = _mode_resolver.resolve_project_mode(ctx.planspace)
            _mode_resolver.write_mode_contract(ctx.planspace, project_mode, mode_constraints)

        all_sections = load_sections(sections_dir)
        for sec in all_sections:
            sec.global_proposal_path = global_proposal
            sec.global_alignment_path = global_alignment
        sections_by_num = {s.number: s for s in all_sections}
        self._logger.log(f"Loaded {len(all_sections)} sections")

        paths = PathRegistry(ctx.planspace)
        cycle = CycleState(
            artifact_io=self._artifact_io,
            proposal_path=paths.proposal_results(),
            section_path=paths.section_results(),
        )

        while True:
            cycle.clear_all()
            if self._flow_submitter is not None:
                proposal_results = self._submit_and_await_proposal_fanout(
                    all_sections, sections_by_num, ctx,
                )
                if proposal_results is None:
                    return
            else:
                try:
                    proposal_results = run_proposal_pass(
                        all_sections, sections_by_num, ctx.planspace, ctx.codespace,
                        section_pipeline=self._section_pipeline,
                    )
                except ProposalPassExit:
                    return
            cycle.update_proposals(proposal_results)

            try:
                reconciliation = self._reconciliation_phase.run_reconciliation_phase(
                    cycle.proposal_results, sections_by_num, all_sections,
                    ctx.planspace, ctx.codespace,
                )
            except ReconciliationPhaseExit:
                return

            cycle.flush()  # reconciliation mutates proposal_results in-place

            blocked_sections = reconciliation.removed_section_numbers
            if reconciliation.alignment_changed:
                continue

            if blocked_sections and self._resolution_phase is not None:
                blocked_sections = self._resolution_phase.run_resolution_phase(
                    cycle.proposal_results, blocked_sections,
                    sections_by_num, ctx,
                )
                cycle.flush()

            try:
                cycle.update_sections(
                    self._implementation_phase.run_implementation_pass(
                        cycle.proposal_results, sections_by_num,
                        ctx.planspace, ctx.codespace,
                    ),
                )
            except ImplementationPassRestart:
                continue
            except ImplementationPassExit:
                return

            _record_blocked_sections(
                blocked_sections, cycle.proposal_results, cycle.section_results,
            )
            cycle.flush()

            self._refresh_codemap_after_implementation(
                cycle.section_results, ctx,
            )

            implemented_sections = [
                sec_num for sec_num, result in cycle.section_results.items()
                if result.aligned
            ]
            self._logger.log(f"=== Phase 1 complete: {len(implemented_sections)} sections "
                f"implemented, {len(blocked_sections)} blocked ===")

            status = self._run_phase2(
                sections_by_num, cycle, ctx,
            )
            if status == CoordinationStatus.RESTART_PHASE1:
                continue
            return


# Pure function -- no Services usage

def _record_blocked_sections(
    blocked_sections: list[str],
    proposal_results: dict,
    section_results: dict[str, SectionResult],
) -> None:
    """Record blocked sections as non-aligned results for Phase 2."""
    for sec_num in blocked_sections:
        pr = proposal_results[sec_num]
        blocker_summary = "; ".join(
            b.get("description", "unknown")[:TRUNCATE_SUMMARY]
            for b in pr.blockers[:_MAX_BLOCKERS_IN_SUMMARY]
        ) or "execution not ready"
        section_results.setdefault(sec_num, SectionResult(
            section_number=sec_num,
            aligned=False,
            problems=f"readiness blocked: {blocker_summary}",
        ))


def _build_global_coordinator(*, halt_event=None):
    """Build the GlobalCoordinator with its full dependency chain.

    Separated from ``_build_coordination_controller`` so the same
    GlobalCoordinator instance can be shared between the coordination
    controller and the resolution phase.

    *halt_event* is an optional ``threading.Event`` that, when set,
    causes the plan executor to abort early.
    """
    from containers import Services
    from coordination.engine.global_coordinator import GlobalCoordinator
    from coordination.engine.plan_executor import PlanExecutor
    from coordination.prompt.writers import Writers
    from coordination.service.completion_handler import CompletionHandler
    from coordination.service.planner import Planner
    from coordination.service.problem_resolver import ProblemResolver
    from implementation.service.impact_analyzer import ImpactAnalyzer
    from implementation.service.scope_delta_aggregator import ScopeDeltaAggregator

    problem_resolver = ProblemResolver(
        artifact_io=Services.artifact_io(),
        communicator=Services.communicator(),
        logger=Services.logger(),
        signals=Services.signals(),
    )
    completion_handler = CompletionHandler(
        artifact_io=Services.artifact_io(),
        change_tracker=Services.change_tracker(),
        communicator=Services.communicator(),
        hasher=Services.hasher(),
        impact_analyzer=ImpactAnalyzer(
            communicator=Services.communicator(),
            config=Services.config(),
            context_assembly=Services.context_assembly(),
            cross_section=Services.cross_section(),
            dispatcher=Services.dispatcher(),
            logger=Services.logger(),
            policies=Services.policies(),
            prompt_guard=Services.prompt_guard(),
            task_router=Services.task_router(),
        ),
        logger=Services.logger(),
    )
    writers = Writers(
        artifact_io=Services.artifact_io(),
        communicator=Services.communicator(),
        logger=Services.logger(),
        prompt_guard=Services.prompt_guard(),
        task_router=Services.task_router(),
    )
    plan_executor = PlanExecutor(
        artifact_io=Services.artifact_io(),
        communicator=Services.communicator(),
        dispatch_helpers=Services.dispatch_helpers(),
        dispatcher=Services.dispatcher(),
        flow_ingestion=Services.flow_ingestion(),
        hasher=Services.hasher(),
        logger=Services.logger(),
        pipeline_control=Services.pipeline_control(),
        task_router=Services.task_router(),
        writers=writers,
        halt_event=halt_event,
    )
    planner = Planner(
        artifact_io=Services.artifact_io(),
        communicator=Services.communicator(),
        logger=Services.logger(),
        prompt_guard=Services.prompt_guard(),
    )
    scope_delta_aggregator = ScopeDeltaAggregator(
        artifact_io=Services.artifact_io(),
        communicator=Services.communicator(),
        dispatcher=Services.dispatcher(),
        logger=Services.logger(),
        policies=Services.policies(),
        prompt_guard=Services.prompt_guard(),
        task_router=Services.task_router(),
    )
    global_coordinator = GlobalCoordinator(
        artifact_io=Services.artifact_io(),
        communicator=Services.communicator(),
        completion_handler=completion_handler,
        dispatch_helpers=Services.dispatch_helpers(),
        dispatcher=Services.dispatcher(),
        logger=Services.logger(),
        plan_executor=plan_executor,
        planner=planner,
        pipeline_control=Services.pipeline_control(),
        policies=Services.policies(),
        problem_resolver=problem_resolver,
        scope_delta_aggregator=scope_delta_aggregator,
        section_alignment=Services.section_alignment(),
        task_router=Services.task_router(),
    )
    return global_coordinator, problem_resolver


def _build_coordination_controller(global_coordinator=None):
    """Build the full CoordinationController dependency chain.

    If *global_coordinator* is provided, reuses that instance (and its
    problem_resolver) instead of creating new ones.
    """
    from containers import Services
    from coordination.service.problem_resolver import ProblemResolver

    if global_coordinator is not None:
        # Reuse the shared instance's problem resolver
        problem_resolver = global_coordinator._problem_resolver
    else:
        global_coordinator, problem_resolver = _build_global_coordinator()

    return CoordinationController(
        artifact_io=Services.artifact_io(),
        change_tracker=Services.change_tracker(),
        communicator=Services.communicator(),
        global_coordinator=global_coordinator,
        logger=Services.logger(),
        pipeline_control=Services.pipeline_control(),
        policies=Services.policies(),
        problem_resolver=problem_resolver,
    )


def _build_resolution_phase(global_coordinator=None):
    """Build the ResolutionPhase with its dependency chain.

    If *global_coordinator* is provided, reuses that instance.
    """
    from containers import ArtifactIOService, Services
    from proposal.service.readiness_resolver import ReadinessResolver

    if global_coordinator is None:
        global_coordinator, _ = _build_global_coordinator()

    readiness_resolver = ReadinessResolver(
        artifact_io=ArtifactIOService(),
    )

    return ResolutionPhase(
        global_coordinator=global_coordinator,
        readiness_resolver=readiness_resolver,
        logger=Services.logger(),
        policies=Services.policies(),
        communicator=Services.communicator(),
    )


def _build_reconciliation_phase(
    section_pipeline: SectionPipeline | None = None,
) -> ReconciliationPhase:
    """Build the ReconciliationPhase with its full dependency chain."""
    from containers import Services
    from reconciliation.repository.queue import Queue
    from reconciliation.repository.results import Results
    from reconciliation.service.adjudicator import Adjudicator
    return ReconciliationPhase(
        logger=Services.logger(),
        artifact_io=Services.artifact_io(),
        pipeline_control=Services.pipeline_control(),
        change_tracker=Services.change_tracker(),
        cross_section_reconciler=CrossSectionReconciler(
            artifact_io=Services.artifact_io(),
            results=Results(
                artifact_io=Services.artifact_io(),
                hasher=Services.hasher(),
            ),
            queue=Queue(artifact_io=Services.artifact_io()),
            adjudicator=Adjudicator(
                artifact_io=Services.artifact_io(),
                prompt_guard=Services.prompt_guard(),
                policies=Services.policies(),
                dispatcher=Services.dispatcher(),
                task_router=Services.task_router(),
            ),
        ),
        section_pipeline=section_pipeline,
    )


def _build_implementation_phase(
    section_pipeline: SectionPipeline | None = None,
) -> ImplementationPhase:
    """Build the ImplementationPhase with its full dependency chain."""
    from containers import FreshnessService, Services
    from implementation.repository.roal_index import RoalIndex
    from implementation.service.risk_artifacts import RiskArtifacts
    return ImplementationPhase(
        artifact_io=Services.artifact_io(),
        change_tracker=Services.change_tracker(),
        communicator=Services.communicator(),
        logger=Services.logger(),
        pipeline_control=Services.pipeline_control(),
        risk_assessment=Services.risk_assessment(),
        risk_artifacts=RiskArtifacts(
            artifact_io=Services.artifact_io(),
            freshness=FreshnessService(),
        ),
        roal_index=RoalIndex(artifact_io=Services.artifact_io()),
        section_pipeline=section_pipeline,
    )


def _build_codemap_builder():
    """Build the CodemapBuilder with injected dependencies."""
    from containers import Services
    from scan.codemap.codemap_builder import CodemapBuilder

    return CodemapBuilder(
        prompt_guard=Services.prompt_guard(),
        task_router=Services.task_router(),
        artifact_io=Services.artifact_io(),
    )


def _build_flow_submitter():
    """Build the FlowSubmitter with injected dependencies."""
    from containers import Services
    from flow.engine.flow_submitter import FlowSubmitter as _FS
    from flow.repository.flow_context_store import FlowContextStore

    return _FS(
        freshness=Services.freshness(),
        flow_context_store=FlowContextStore(Services.artifact_io()),
    )


def main() -> None:
    from containers import Services
    pipeline = build_section_pipeline()
    global_coordinator, _ = _build_global_coordinator()
    PipelineOrchestrator(
        communicator=Services.communicator(),
        logger=Services.logger(),
        config=Services.config(),
        artifact_io=Services.artifact_io(),
        prompt_guard=Services.prompt_guard(),
        section_alignment=Services.section_alignment(),
        change_tracker=Services.change_tracker(),
        pipeline_control=Services.pipeline_control(),
        coordination_controller=_build_coordination_controller(global_coordinator),
        implementation_phase=_build_implementation_phase(section_pipeline=pipeline),
        reconciliation_phase=_build_reconciliation_phase(section_pipeline=pipeline),
        resolution_phase=_build_resolution_phase(global_coordinator),
        codemap_builder=_build_codemap_builder(),
        section_pipeline=pipeline,
        flow_submitter=_build_flow_submitter(),
    ).main()


if __name__ == "__main__":
    main()
