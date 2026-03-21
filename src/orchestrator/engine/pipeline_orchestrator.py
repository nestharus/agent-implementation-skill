from __future__ import annotations

import logging
import sys
import time
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

from intake.repository.governance_loader import GovernanceLoader
from orchestrator.path_registry import PathRegistry
from scan.service.project_mode import ProjectModeResolver
from orchestrator.engine.section_pipeline import SectionPipeline, build_section_pipeline
from flow.service.task_db_client import (
    count_pending_tasks,
    count_tasks_by_type,
)
from flow.types.context import FlowEnvelope, new_flow_id
from flow.types.schema import BranchSpec, GateSpec, TaskSpec
from pipeline.context import DispatchContext
from signals.types import TRUNCATE_SUMMARY
from orchestrator.engine.strategic_state_builder import StrategicStateBuilder
from orchestrator.types import PipelineAbortError, SectionResult

logging.basicConfig(
    level=logging.INFO,
    format="[%(name)s] %(message)s",
    stream=sys.stderr,
)

_MAX_BLOCKERS_IN_SUMMARY = 3

_COMPLETION_POLL_INTERVAL = 2.0


class PipelineOrchestrator:
    """Thin starter that submits per-section task chains and waits.

    The actual section work is driven by the task dispatcher. Each
    section gets a chain: ``section.propose -> section.readiness_check``.
    Completion handlers in the reconciler submit follow-on work
    (``section.implement -> section.verify``) when readiness passes.
    """

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
        coordination_controller: object | None = None,
        implementation_phase: object | None = None,
        reconciliation_phase: object | None = None,
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
        self._codemap_builder = codemap_builder
        self._section_pipeline = section_pipeline
        self._flow_submitter = flow_submitter
        self._strategic_state_builder = StrategicStateBuilder(artifact_io=artifact_io)

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
                "\u2014 continuing with existing codemap",
            )

    def _run_loop(self, ctx: DispatchContext,
                  sections_dir: Path, global_proposal: Path,
                  global_alignment: Path) -> None:
        """Drive per-section state machines via the task queue.

        Uses the ``StateMachineOrchestrator`` to drive each section
        independently through its lifecycle.  The state machine submits
        tasks; the task dispatcher executes them; the reconciler advances
        the state on completion.

        Governance initialization and project mode resolution run before
        the state machine starts -- these are one-time bootstrap concerns
        that the state machine does not own.
        """
        from orchestrator.engine.state_machine_orchestrator import (
            StateMachineOrchestrator,
        )

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

        paths = PathRegistry(ctx.planspace)
        db_path = paths.run_db()

        # The state machine waits for bootstrap (discover_substrate) to
        # populate section_states rows.  On resume, rows already exist
        # and the loop proceeds immediately.
        sm = StateMachineOrchestrator(
            logger_service=self._logger,
            artifact_io=self._artifact_io,
            flow_submitter=self._flow_submitter,
            pipeline_control=self._pipeline_control,
        )

        sm.run(db_path, ctx.planspace)


# ---------------------------------------------------------------------------
# Section chain submission
# ---------------------------------------------------------------------------


def submit_section_chains(
    all_sections: list,
    planspace: Path,
    flow_submitter: FlowSubmitter,
) -> str:
    """Submit one task chain per section into run.db.

    Each section gets: ``section.propose -> section.readiness_check``

    The readiness_check completion handler decides what comes next:
    if ready, it submits ``section.implement -> section.verify``.
    If blocked, it emits blocker signals.

    Returns the flow_id for the submitted chains.
    """
    paths = PathRegistry(planspace)
    db_path = paths.run_db()
    flow_id = new_flow_id()

    branches: list[BranchSpec] = []
    for section in all_sections:
        sec_num = section.number
        branches.append(
            BranchSpec(
                label=f"section-{sec_num}-chain",
                steps=[
                    TaskSpec(
                        task_type="section.propose",
                        concern_scope=f"section-{sec_num}",
                        payload_path=str(section.path),
                        priority="normal",
                    ),
                    TaskSpec(
                        task_type="section.readiness_check",
                        concern_scope=f"section-{sec_num}",
                        payload_path=str(section.path),
                        priority="normal",
                    ),
                ],
            ),
        )

    env = FlowEnvelope(
        db_path=db_path,
        submitted_by="pipeline_orchestrator",
        flow_id=flow_id,
        planspace=planspace,
    )

    flow_submitter.submit_fanout(env, branches)
    return flow_id


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
        flow_submitter=Services.flow_ingestion()._get_submitter(),
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
    from coordination.engine.coordination_controller import CoordinationController
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
        problem_resolver=problem_resolver,
    )


def _build_reconciliation_phase(
    section_pipeline: SectionPipeline | None = None,
):
    """Build the ReconciliationPhase with its full dependency chain."""
    from containers import Services
    from reconciliation.engine.cross_section_reconciler import CrossSectionReconciler
    from reconciliation.engine.reconciliation_phase import ReconciliationPhase
    from reconciliation.repository.queue import Queue
    from reconciliation.repository.results import Results
    from reconciliation.service.adjudicator import Adjudicator
    return ReconciliationPhase(
        logger=Services.logger(),
        artifact_io=Services.artifact_io(),
        pipeline_control=Services.pipeline_control(),
        change_tracker=Services.change_tracker(),
        communicator=Services.communicator(),
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
):
    """Build the ImplementationPhase with its full dependency chain."""
    from containers import FreshnessService, Services
    from implementation.engine.implementation_phase import ImplementationPhase
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
    PipelineOrchestrator(
        communicator=Services.communicator(),
        logger=Services.logger(),
        config=Services.config(),
        artifact_io=Services.artifact_io(),
        prompt_guard=Services.prompt_guard(),
        section_alignment=Services.section_alignment(),
        change_tracker=Services.change_tracker(),
        pipeline_control=Services.pipeline_control(),
        codemap_builder=_build_codemap_builder(),
        section_pipeline=pipeline,
        flow_submitter=_build_flow_submitter(),
    ).main()


if __name__ == "__main__":
    main()
