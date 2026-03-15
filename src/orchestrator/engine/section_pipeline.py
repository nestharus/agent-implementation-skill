from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from containers import ArtifactIOService, LogService, PipelineControlService
    from intent.engine.intent_initializer import IntentInitializer
    from intent.service.recurrence_emitter import RecurrenceEmitter
    from proposal.engine.proposal_cycle import ProposalCycle
    from proposal.engine.readiness_gate import ReadinessGate
    from proposal.service.excerpt_extractor import ExcerptExtractor
    from proposal.service.problem_frame_gate import ProblemFrameGate

from implementation.service.triage_orchestrator import TriageOrchestrator
from orchestrator.path_registry import PathRegistry
from implementation.service.microstrategy_generator import MicrostrategyGenerator
from pipeline.context import DispatchContext
from proposal.service.readiness_resolver import ReadinessResolver
from implementation.engine.implementation_cycle import ImplementationCycle
from dispatch.service.tool_surface_writer import ToolSurfaceWriter
from dispatch.service.tool_validator import ToolValidator
from dispatch.service.tool_bridge import ToolBridge

from coordination.service.completion_handler import CompletionHandler
from implementation.service.impact_analyzer import ImpactAnalyzer
from orchestrator.types import ProposalPassResult, Section
from reconciliation.engine.cross_section_reconciler import CrossSectionReconciler
from signals.types import (
    ACTION_ABORT, ACTION_SKIP,
    PASS_MODE_FULL, PASS_MODE_IMPLEMENTATION, PASS_MODE_PROPOSAL,
)


_DEFAULT_PROPOSAL_CYCLE_MAX = 5
_DEFAULT_IMPLEMENTATION_CYCLE_MAX = 5
_RECURRENCE_LOOP_THRESHOLD = 2

# Sentinel object used by _resolve_readiness_and_route to signal "proceed
# to implementation steps" without conflicting with any valid return value.
_CONTINUE = object()


class SectionPipeline:
    def __init__(
        self,
        logger: LogService,
        artifact_io: ArtifactIOService,
        pipeline_control: PipelineControlService,
        implementation_cycle: ImplementationCycle | None = None,
        intent_initializer: IntentInitializer | None = None,
        microstrategy_generator: MicrostrategyGenerator | None = None,
        recurrence_emitter: RecurrenceEmitter | None = None,
        triage_orchestrator: TriageOrchestrator | None = None,
        cross_section_reconciler: CrossSectionReconciler | None = None,
        completion_handler: CompletionHandler | None = None,
        excerpt_extractor: ExcerptExtractor | None = None,
        problem_frame_gate: ProblemFrameGate | None = None,
        proposal_cycle: ProposalCycle | None = None,
        readiness_gate: ReadinessGate | None = None,
        tool_surface_writer: ToolSurfaceWriter | None = None,
        tool_validator: ToolValidator | None = None,
        tool_bridge: ToolBridge | None = None,
        readiness_resolver: ReadinessResolver | None = None,
    ) -> None:
        self._logger = logger
        self._artifact_io = artifact_io
        self._pipeline_control = pipeline_control
        self._implementation_cycle = implementation_cycle
        self._intent_initializer = intent_initializer
        self._microstrategy_generator = microstrategy_generator
        self._recurrence_emitter = recurrence_emitter
        self._triage_orchestrator = triage_orchestrator
        self._cross_section_reconciler = cross_section_reconciler
        self._completion_handler = completion_handler
        self._excerpt_extractor = excerpt_extractor
        self._problem_frame_gate = problem_frame_gate
        self._proposal_cycle = proposal_cycle
        self._readiness_gate = readiness_gate
        self._tool_surface_writer = tool_surface_writer
        self._tool_validator = tool_validator
        self._tool_bridge = tool_bridge
        self._readiness_resolver = readiness_resolver or ReadinessResolver(artifact_io=artifact_io)

    # ---------------------------------------------------------------------------
    # Private helpers -- each encapsulates one concern from the section pipeline
    # ---------------------------------------------------------------------------

    def _read_notes(
        self,
        section: Section, planspace: Path, codespace: Path,
    ) -> list[dict]:
        """Read incoming notes from other sections and log if any arrived."""
        if self._completion_handler is None:
            return []
        incoming_notes = self._completion_handler.read_incoming_notes(section, planspace, codespace)
        if incoming_notes:
            self._logger.log(f"Section {section.number}: received incoming notes from "
                f"other sections")
        return incoming_notes

    def _run_impact_triage(
        self,
        section: Section,
        planspace: Path,
        codespace: Path,
        incoming_notes: list[dict],
    ) -> tuple[bool, list[str] | None]:
        """Run impact triage and return (should_continue, early_return_value).

        Returns ``(True, None)`` when the pipeline should continue.
        Returns ``(False, value)`` when the caller should return ``value``.
        """
        if self._triage_orchestrator is None:
            return True, None
        triage_status, triage_files = self._triage_orchestrator.run_impact_triage(
            section,
            planspace,
            codespace,
            incoming_notes,
        )
        if triage_status == ACTION_ABORT:
            return False, None
        if triage_status == ACTION_SKIP:
            return False, triage_files if triage_files is not None else []
        return True, None

    def _run_intent_bootstrap_phase(
        self,
        section: Section,
        planspace: Path,
        codespace: Path,
        incoming_notes: list[dict],
    ) -> dict | None:
        """Run intent bootstrap.

        Returns the cycle budget dict, or ``None`` if the section should abort.
        """
        if self._intent_initializer is None:
            return {
                "proposal_max": _DEFAULT_PROPOSAL_CYCLE_MAX,
                "implementation_max": _DEFAULT_IMPLEMENTATION_CYCLE_MAX,
            }
        return self._intent_initializer.run_intent_bootstrap(
            section,
            planspace,
            codespace,
            incoming_notes,
        )

    def _resolve_readiness_and_route(
        self,
        section: Section,
        planspace: Path,
        pass_mode: str,
        codespace: Path,
    ) -> list[str] | ProposalPassResult | None:
        """Resolve readiness, route blockers, and return early if not ready.

        Returns a sentinel ``_CONTINUE`` when the caller should proceed to
        implementation steps.  Any other value is the final return for
        ``run_section``.
        """
        readiness_result = self._readiness_gate.resolve_and_route(
            section,
            planspace,
            pass_mode,
            codespace=codespace,
        )
        if not readiness_result.ready:
            return readiness_result.proposal_pass_result
        if pass_mode == PASS_MODE_PROPOSAL:
            return readiness_result.proposal_pass_result
        return _CONTINUE

    # ---------------------------------------------------------------------------
    # Implementation-step helpers (from _run_section_implementation_steps)
    # ---------------------------------------------------------------------------

    def _check_upstream_freshness(
        self,
        section: Section,
        planspace: Path,
    ) -> bool:
        """Check readiness and reconciliation freshness gates.

        Returns ``True`` if implementation may proceed, ``False`` otherwise.
        """
        if self._readiness_resolver is not None:
            readiness = self._readiness_resolver.resolve_readiness(planspace, section.number)
            if not readiness.ready:
                self._logger.log(f"Section {section.number}: implementation steps blocked — "
                    f"upstream freshness check failed (execution_ready is false)")
                return False

        if self._cross_section_reconciler is not None:
            recon_result = self._cross_section_reconciler.load_reconciliation_result(planspace, section.number)
            if recon_result and recon_result.get("affected"):
                self._logger.log(f"Section {section.number}: implementation steps blocked — "
                    f"reconciliation result marks section as affected")
                return False

        return True

    def _load_cycle_budget(self, paths: PathRegistry, section_number: str) -> dict:
        """Load the per-section cycle budget, falling back to defaults."""
        cycle_budget_path = paths.cycle_budget(section_number)
        cycle_budget = {
            "proposal_max": _DEFAULT_PROPOSAL_CYCLE_MAX,
            "implementation_max": _DEFAULT_IMPLEMENTATION_CYCLE_MAX,
        }
        loaded = self._artifact_io.read_json(cycle_budget_path)
        if loaded is not None:
            cycle_budget.update(loaded)
        return cycle_budget

    def _count_pre_impl_tools(self, paths: PathRegistry) -> int:
        """Read tool registry and return the tool count."""
        tool_registry_path = paths.tool_registry()
        registry = self._artifact_io.read_json(tool_registry_path)
        if registry is None:
            return 0
        all_tools = (registry if isinstance(registry, list)
                     else registry.get("tools", []))
        return len(all_tools)

    def _run_implementation_pass(
        self,
        planspace: Path, codespace: Path, section: Section,
        *,
        all_sections: list[Section] | None = None,
    ) -> list[str] | None:
        """Execute implementation for a section whose proposal is already aligned."""
        if self._cross_section_reconciler is not None:
            recon_result = self._cross_section_reconciler.load_reconciliation_result(planspace, section.number)
            if recon_result and recon_result.get("affected"):
                self._logger.log(f"Section {section.number}: implementation pass blocked — "
                    f"reconciliation result marks section as affected")
                return None

        if self._readiness_resolver is not None:
            readiness = self._readiness_resolver.resolve_readiness(planspace, section.number)
            if not readiness.ready:
                self._logger.log(f"Section {section.number}: implementation pass skipped — "
                    f"execution_ready is false")
                return None

        return self._run_section_implementation_steps(
            planspace, codespace, section,
            all_sections=all_sections,
        )

    def run_section(
        self,
        planspace: Path, codespace: Path, section: Section,
        all_sections: list[Section] | None = None,
        *,
        pass_mode: str = PASS_MODE_FULL,
    ) -> list[str] | ProposalPassResult | None:
        """Run a section through the strategic flow.

        0. Read incoming notes from other sections (pre-section)
        1. Section setup (once) -- extract proposal/alignment excerpts
        2. Integration proposal loop -- proposer writes, alignment judge checks
        3. Strategic implementation -- implementor writes, alignment judge checks
        4. Post-completion -- snapshot, impact analysis, consequence notes

        Parameters
        ----------
        pass_mode:
            ``"full"`` (default) -- run the complete pipeline (legacy behavior).
            ``"proposal"`` -- run exploration through readiness resolution, then
            stop.  Returns a ``ProposalPassResult``.  No code files are modified.
            ``"implementation"`` -- assume proposal is aligned and ready.  Pick
            up from the readiness artifact and run microstrategy through
            post-completion.  Only proceeds if ``execution_ready == true``.

        Returns
        -------
        - ``list[str]`` of modified files on successful implementation
          (``"full"`` or ``"implementation"`` mode).
        - ``ProposalPassResult`` when ``pass_mode="proposal"`` completes.
        - ``None`` if paused/aborted (waiting for parent).
        """
        # Implementation-only mode: skip proposal steps, jump to execution
        if pass_mode == PASS_MODE_IMPLEMENTATION:
            return self._run_implementation_pass(
                planspace, codespace, section,
                all_sections=all_sections,
            )

        # Recurrence signal
        if self._recurrence_emitter is not None and section.solve_count >= _RECURRENCE_LOOP_THRESHOLD:
            self._recurrence_emitter.emit_recurrence_signal(
                planspace, section.number, section.solve_count,
            )

        # Step 0: Read incoming notes from other sections
        incoming_notes = self._read_notes(section, planspace, codespace)

        # Step 0c: Impact triage -- skip expensive steps if notes are trivial
        should_continue, early_return = self._run_impact_triage(
            section, planspace, codespace, incoming_notes,
        )
        if not should_continue:
            return early_return

        # Step 0b: Surface section-relevant tools from tool registry
        if self._tool_surface_writer is not None:
            self._tool_surface_writer.surface_tool_registry(
                section_number=section.number,
                planspace=planspace,
                codespace=codespace,
            )

        # Step 1: Section setup -- extract excerpts from global documents
        if self._excerpt_extractor is not None:
            if self._excerpt_extractor.extract_excerpts(section, planspace, codespace) is None:
                return None

        # Step 1a: Problem frame quality gate (enforced)
        if self._problem_frame_gate is not None:
            if self._problem_frame_gate.validate_problem_frame(section, planspace, codespace) is None:
                return None

        # Step 1b: Intent bootstrap
        cycle_budget = self._run_intent_bootstrap_phase(
            section, planspace, codespace, incoming_notes,
        )
        if cycle_budget is None:
            return None

        # Step 2: Proposal loop
        if self._proposal_cycle is not None:
            from containers import Services as _Services
            if self._proposal_cycle.run_proposal_loop(
                section,
                DispatchContext(planspace=planspace, codespace=codespace, _policies=_Services.policies()),
                cycle_budget, incoming_notes,
            ) is None:
                return None

        # Step 2b: Readiness resolution and routing
        if self._readiness_gate is not None:
            readiness_outcome = self._resolve_readiness_and_route(
                section, planspace, pass_mode, codespace,
            )
            if readiness_outcome is not _CONTINUE:
                return readiness_outcome

        # Step 3+: Implementation steps
        return self._run_section_implementation_steps(
            planspace, codespace, section,
            all_sections=all_sections,
        )

    def _run_section_implementation_steps(
        self,
        planspace: Path, codespace: Path, section: Section,
        *,
        all_sections: list[Section] | None = None,
    ) -> list[str] | None:
        """Execute microstrategy through post-completion for a section."""
        paths = PathRegistry(planspace)

        # Upstream freshness gate
        if not self._check_upstream_freshness(section, planspace):
            return None

        # Load cycle budget and pre-implementation tool count
        cycle_budget = self._load_cycle_budget(paths, section.number)
        pre_tool_total = self._count_pre_impl_tools(paths)

        # Step 2.5: Generate microstrategy
        if self._microstrategy_generator is not None:
            microstrategy_result = self._microstrategy_generator.run_microstrategy(
                section, planspace, codespace,
            )
            microstrategy_blocker = paths.microstrategy_blocker_signal(section.number)
            if microstrategy_result is None and microstrategy_blocker.exists():
                return None

        # Step 3: Strategic implementation
        if self._implementation_cycle is None:
            return []
        actually_changed = self._implementation_cycle.run_implementation_loop(
            section, planspace, codespace, cycle_budget,
        )
        if actually_changed is None:
            return None

        # Step 3b-3c: Validate tool registry and handle friction
        if self._tool_validator is not None:
            self._tool_validator.validate_tool_registry_after_implementation(
                section_number=section.number,
                pre_tool_total=pre_tool_total,
                planspace=planspace,
                codespace=codespace,
            )
        if self._tool_bridge is not None:
            self._tool_bridge.handle_tool_friction(
                section_number=section.number,
                section_path=section.path,
                all_sections=all_sections,
                planspace=planspace,
                codespace=codespace,
            )

        # Step 4: Post-completion
        if actually_changed and all_sections and self._completion_handler is not None:
            self._completion_handler.post_section_completion(
                section, actually_changed, all_sections,
                planspace, codespace,
            )

        return actually_changed


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def build_section_pipeline() -> SectionPipeline:
    """Construct a fully-wired ``SectionPipeline`` from the Services container.

    This is a convenience builder for callers that do not manage their own
    dependency graph.  The orchestrator's ``main()`` entry-point builds
    the phases directly; this helper exists for tests and simpler call
    sites.
    """
    from containers import Services
    from coordination.service.completion_handler import CompletionHandler as _CH
    from implementation.service.impact_analyzer import ImpactAnalyzer as _IA
    from proposal.service.excerpt_extractor import ExcerptExtractor as _EE
    from proposal.service.problem_frame_gate import ProblemFrameGate as _PFG
    from reconciliation.engine.cross_section_reconciler import CrossSectionReconciler as _CSR
    from reconciliation.repository.queue import Queue as _Q
    from reconciliation.repository.results import Results as _RR
    from reconciliation.service.adjudicator import Adjudicator as _ADJ
    from proposal.service.cycle_control import CycleControl as _CC
    from dispatch.prompt.writers import Writers as _PW

    s = Services

    impact_analyzer = _IA(
        communicator=s.communicator(),
        config=s.config(),
        context_assembly=s.context_assembly(),
        cross_section=s.cross_section(),
        dispatcher=s.dispatcher(),
        logger=s.logger(),
        policies=s.policies(),
        prompt_guard=s.prompt_guard(),
        task_router=s.task_router(),
    )
    completion_handler = _CH(
        artifact_io=s.artifact_io(),
        change_tracker=s.change_tracker(),
        communicator=s.communicator(),
        hasher=s.hasher(),
        impact_analyzer=impact_analyzer,
        logger=s.logger(),
    )
    cycle_control = _CC(
        logger=s.logger(),
        artifact_io=s.artifact_io(),
        communicator=s.communicator(),
        pipeline_control=s.pipeline_control(),
        cross_section=s.cross_section(),
        dispatcher=s.dispatcher(),
        dispatch_helpers=s.dispatch_helpers(),
        task_router=s.task_router(),
        flow_ingestion=s.flow_ingestion(),
    )
    prompt_writers = _PW(
        task_router=s.task_router(),
        prompt_guard=s.prompt_guard(),
        logger=s.logger(),
        communicator=s.communicator(),
        section_alignment=s.section_alignment(),
        artifact_io=s.artifact_io(),
        cross_section=s.cross_section(),
        config=s.config(),
    )
    excerpt_extractor = _EE(
        logger=s.logger(),
        policies=s.policies(),
        dispatcher=s.dispatcher(),
        dispatch_helpers=s.dispatch_helpers(),
        communicator=s.communicator(),
        pipeline_control=s.pipeline_control(),
        task_router=s.task_router(),
        cycle_control=cycle_control,
        prompt_writers=prompt_writers,
    )
    problem_frame_gate = _PFG(
        logger=s.logger(),
        policies=s.policies(),
        dispatcher=s.dispatcher(),
        task_router=s.task_router(),
        artifact_io=s.artifact_io(),
        communicator=s.communicator(),
        hasher=s.hasher(),
        prompt_guard=s.prompt_guard(),
        section_alignment=s.section_alignment(),
        cross_section=s.cross_section(),
        config=s.config(),
    )
    cross_section_reconciler = _CSR(
        artifact_io=s.artifact_io(),
        results=_RR(
            artifact_io=s.artifact_io(),
            hasher=s.hasher(),
        ),
        queue=_Q(artifact_io=s.artifact_io()),
        adjudicator=_ADJ(
            artifact_io=s.artifact_io(),
            prompt_guard=s.prompt_guard(),
            policies=s.policies(),
            dispatcher=s.dispatcher(),
            task_router=s.task_router(),
        ),
    )

    return SectionPipeline(
        logger=s.logger(),
        artifact_io=s.artifact_io(),
        pipeline_control=s.pipeline_control(),
        completion_handler=completion_handler,
        excerpt_extractor=excerpt_extractor,
        problem_frame_gate=problem_frame_gate,
        cross_section_reconciler=cross_section_reconciler,
    )


# ---------------------------------------------------------------------------
# Pure functions -- no Services usage
# ---------------------------------------------------------------------------

