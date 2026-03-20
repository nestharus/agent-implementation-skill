from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from coordination.repository.notes import list_notes_to
from orchestrator.path_registry import PathRegistry
from implementation.service.traceability_writer import TraceabilityWriter
from implementation.service.change_verifier import ChangeVerifier
from implementation.service.trace_map_builder import TraceMapBuilder
from flow.types.context import FlowEnvelope
from flow.types.schema import TaskSpec
from dispatch.types import ALIGNMENT_CHANGED_PENDING, DispatchResult, DispatchStatus
from signals.types import ACTION_ABORT, SIGNAL_UNDERSPEC, TRUNCATE_DETAIL

if TYPE_CHECKING:
    from containers import (
        AgentDispatcher,
        ArtifactIOService,
        Communicator,
        DispatchHelperService,
        FlowIngestionService,
        LogService,
        ModelPolicyService,
        PipelineControlService,
        SectionAlignmentService,
        StalenessDetectionService,
        TaskRouterService,
    )
    from dispatch.prompt.writers import Writers as PromptWriters
    from intake.service.assessment_evaluator import AssessmentEvaluator
    from proposal.service.cycle_control import CycleControl
    from verification.service.chain_builder import VerificationChainBuilder


# ---------------------------------------------------------------------------
# Loop-control sentinels (private to this module)
# ---------------------------------------------------------------------------

from enum import Enum


class _LoopAction(str, Enum):
    ABORT = "ABORT"       # return None from loop
    CONTINUE = "CONTINUE" # continue to next iteration
    PROCEED = "PROCEED"   # fall through, keep going in current iteration

    def __str__(self) -> str:  # noqa: D105
        return self.value


_ABORT = _LoopAction.ABORT
_CONTINUE = _LoopAction.CONTINUE
_PROCEED = _LoopAction.PROCEED


class ImplementationCycle:
    """Run strategic implementation until aligned, then return changed files.

    All cross-cutting services are received via constructor injection.
    """

    def __init__(
        self,
        artifact_io: ArtifactIOService,
        assessment_evaluator: AssessmentEvaluator,
        change_verifier: ChangeVerifier,
        communicator: Communicator,
        cycle_control: CycleControl,
        dispatcher: AgentDispatcher,
        dispatch_helpers: DispatchHelperService,
        flow_ingestion: FlowIngestionService,
        logger: LogService,
        pipeline_control: PipelineControlService,
        policies: ModelPolicyService,
        section_alignment: SectionAlignmentService,
        staleness: StalenessDetectionService,
        task_router: TaskRouterService,
        prompt_writers: PromptWriters,
        trace_map_builder: TraceMapBuilder,
        traceability_writer: TraceabilityWriter,
        verification_chain_builder: VerificationChainBuilder | None = None,
    ) -> None:
        self._artifact_io = artifact_io
        self._assessment_evaluator = assessment_evaluator
        self._change_verifier = change_verifier
        self._communicator = communicator
        self._cycle_control = cycle_control
        self._dispatcher = dispatcher
        self._dispatch_helpers = dispatch_helpers
        self._flow_ingestion = flow_ingestion
        self._logger = logger
        self._pipeline_control = pipeline_control
        self._policies = policies
        self._section_alignment = section_alignment
        self._staleness = staleness
        self._task_router = task_router
        self._prompt_writers = prompt_writers
        self._trace_map_builder = trace_map_builder
        self._traceability_writer = traceability_writer
        self._verification_chain_builder = verification_chain_builder

    def run_implementation_loop(
        self,
        section,
        planspace: Path,
        codespace: Path,
    ) -> list[str] | None:
        """Dispatch implementation agent ONCE, check alignment ONCE.

        Single-shot handler: the state machine handles retry via
        IMPL_ASSESSING -> IMPLEMENTING transitions.

        Returns:
            - ``list[str]`` of changed files when aligned (finalized),
            - ``[]`` with logged problems when misaligned (caller retries),
            - ``None`` on abort.
        """
        all_known_paths = list(section.related_files)
        pre_hashes = self._staleness.snapshot_files(codespace, all_known_paths)

        impl_problems: str | None = None
        impl_attempt = 1

        if self._cycle_control.check_early_abort(section.number, planspace):
            return None

        self._log_attempt(section.number, impl_attempt, impl_problems)

        impl_result = self._dispatch_implementation(
            section, planspace, codespace,
            impl_problems,
        )
        if impl_result is None:
            return None

        dispatch_action = self._handle_post_dispatch(
            section.number, planspace,
        )
        if dispatch_action == _ABORT:
            return None
        if dispatch_action == _CONTINUE:
            return None

        align_result = self._dispatch_alignment_check(
            section, planspace, codespace,
        )
        if align_result is None:
            return None

        timeout_action = self._handle_alignment_timeout(
            align_result, section.number,
        )
        if timeout_action == _CONTINUE:
            # Timeout -- caller should retry
            return self._finalize(planspace, codespace, section, pre_hashes)

        problems = self._extract_alignment_problems(
            align_result, section.number, planspace, codespace,
        )

        underspec_action = self._handle_underspec_signal(
            section.number, planspace,
        )
        if underspec_action == _ABORT:
            return None
        if underspec_action == _CONTINUE:
            return None

        if problems is None:
            self._logger.log(f"Section {section.number}: implementation ALIGNED")
            self._communicator.send_to_parent(
                planspace,
                f"summary:impl-align:{section.number}:ALIGNED",
            )
            return self._finalize(planspace, codespace, section, pre_hashes)

        # Misaligned -- log and return finalized result; state machine retries
        self._log_alignment_problems(
            section.number, impl_attempt, problems, planspace,
        )
        return self._finalize(planspace, codespace, section, pre_hashes)

    # -----------------------------------------------------------------------
    # Logging helpers
    # -----------------------------------------------------------------------

    def _log_attempt(
        self,
        section_number: str, impl_attempt: int, impl_problems: str | None,
    ) -> None:
        """Log the start of an implementation attempt."""
        tag = "fix " if impl_problems else ""
        self._logger.log(
            f"Section {section_number}: {tag}strategic implementation "
            f"(attempt {impl_attempt})"
        )

    def _log_alignment_problems(
        self,
        section_number: str,
        impl_attempt: int,
        problems: str,
        planspace: Path,
    ) -> None:
        """Log and notify parent about alignment problems found."""
        short = problems[:TRUNCATE_DETAIL]
        self._logger.log(
            f"Section {section_number}: implementation problems "
            f"(attempt {impl_attempt}): {short}"
        )
        self._communicator.send_to_parent(
            planspace,
            f"summary:impl-align:{section_number}:PROBLEMS-attempt-{impl_attempt}:{short}",
        )

    # -----------------------------------------------------------------------
    # Implementation dispatch
    # -----------------------------------------------------------------------

    def _dispatch_implementation(
        self,
        section,
        planspace: Path,
        codespace: Path,
        impl_problems: str | None,
    ) -> DispatchResult | None:
        """Write the implementation prompt, dispatch to the agent, return result.

        Returns ``None`` when the caller should ``return None`` (prompt
        blocked, alignment changed, or timeout).
        """
        artifacts = PathRegistry(planspace).artifacts
        policy = self._policies.load(planspace)
        impl_prompt = self._prompt_writers.write_strategic_impl_prompt(
            section,
            planspace,
            codespace,
            impl_problems,
        )
        if impl_prompt is None:
            self._logger.log(
                f"Section {section.number}: strategic impl prompt "
                f"blocked by template safety — skipping dispatch"
            )
            return None

        impl_output = artifacts / f"impl-{section.number}-output.md"
        impl_agent = f"impl-{section.number}"
        impl_result = self._dispatcher.dispatch(
            self._policies.resolve(policy, "implementation"),
            impl_prompt,
            impl_output,
            planspace,
            agent_name=impl_agent,
            codespace=codespace,
            section_number=section.number,
            agent_file=self._task_router.agent_for("implementation.strategic"),
        )
        if impl_result == ALIGNMENT_CHANGED_PENDING:
            self._logger.log(f"Section {section.number}: alignment changed during implementation dispatch — aborting")
            return None

        self._communicator.send_to_parent(
            planspace,
            f"summary:impl:{section.number}:{self._dispatch_helpers.summarize_output(impl_result.output)}",
        )

        if impl_result.status is DispatchStatus.TIMEOUT:
            self._logger.log(f"Section {section.number}: implementation agent timed out")
            self._communicator.send_to_parent(
                planspace,
                f"fail:{section.number}:implementation agent timed out",
            )
            return None

        return impl_result

    # -----------------------------------------------------------------------
    # Post-dispatch: task ingestion + signal handling
    # -----------------------------------------------------------------------

    def _handle_post_dispatch(
        self,
        section_number: str,
        planspace: Path,
    ) -> str:
        """Ingest tasks and check agent signals after implementation dispatch.

        Returns ``_ABORT``, ``_CONTINUE``, or ``_PROCEED``.
        """
        paths = PathRegistry(planspace)
        artifacts = paths.artifacts
        self._flow_ingestion.ingest_and_submit(
            planspace,
            submitted_by=f"implementation-{section_number}",
            signal_path=paths.task_request_signal("impl", section_number),
            origin_refs=[str(artifacts / f"impl-{section_number}-output.md")],
        )

        signal, detail = self._dispatch_helpers.check_agent_signals(
            signal_path=paths.impl_signal(section_number),
        )
        if signal:
            return self._handle_signal_pause(
                signal, detail, section_number, planspace,
            )
        return _PROCEED

    def _handle_signal_pause(
        self,
        signal: str,
        detail: str,
        section_number: str,
        planspace: Path,
    ) -> str:
        """Pause for parent after an agent signal; return loop action."""
        response = self._pipeline_control.pause_for_parent(
            planspace,
            f"pause:{signal}:{section_number}:{detail}",
        )
        result = self._cycle_control.handle_pause_response(planspace, section_number, response)
        if result == ACTION_ABORT:
            return _ABORT
        return _CONTINUE

    # -----------------------------------------------------------------------
    # Alignment check dispatch
    # -----------------------------------------------------------------------

    def _dispatch_alignment_check(
        self,
        section,
        planspace: Path,
        codespace: Path,
    ) -> DispatchResult | None:
        """Dispatch the alignment check agent. Return result or None to abort."""
        artifacts = PathRegistry(planspace).artifacts
        policy = self._policies.load(planspace)
        self._logger.log(f"Section {section.number}: implementation alignment check")
        impl_align_prompt = self._prompt_writers.write_impl_alignment_prompt(
            section,
            planspace,
            codespace,
        )
        impl_align_output = artifacts / f"impl-align-{section.number}-output.md"
        impl_align_result = self._dispatcher.dispatch(
            self._policies.resolve(policy, "alignment"),
            impl_align_prompt,
            impl_align_output,
            planspace,
            codespace=codespace,
            section_number=section.number,
            agent_file=self._task_router.agent_for("staleness.alignment_check"),
        )
        if impl_align_result == ALIGNMENT_CHANGED_PENDING:
            self._logger.log(f"Section {section.number}: alignment changed during alignment check — aborting")
            return None

        return impl_align_result

    def _handle_alignment_timeout(
        self,
        impl_align_result: DispatchResult, section_number: str,
    ) -> str:
        """Return ``_CONTINUE`` if the alignment check timed out, else ``_PROCEED``."""
        if impl_align_result.status is DispatchStatus.TIMEOUT:
            self._logger.log(
                f"Section {section_number}: implementation alignment check "
                f"timed out — retrying"
            )
            return _CONTINUE
        return _PROCEED

    def _extract_alignment_problems(
        self,
        impl_align_result: DispatchResult,
        section_number: str,
        planspace: Path,
        codespace: Path,
    ) -> str | None:
        """Extract alignment problems from the alignment check result."""
        artifacts = PathRegistry(planspace).artifacts
        policy = self._policies.load(planspace)
        impl_align_output = artifacts / f"impl-align-{section_number}-output.md"
        return self._section_alignment.extract_problems(
            impl_align_result.output,
            output_path=impl_align_output,
            planspace=planspace,
            codespace=codespace,
            adjudicator_model=self._policies.resolve(policy, "adjudicator"),
        )

    def _handle_underspec_signal(
        self,
        section_number: str,
        planspace: Path,
    ) -> str:
        """Check for underspec signal after alignment; return loop action."""
        paths = PathRegistry(planspace)
        signal, detail = self._dispatch_helpers.check_agent_signals(
            signal_path=paths.signals_dir() / f"impl-align-{section_number}-signal.json",
        )
        if signal == SIGNAL_UNDERSPEC:
            return self._handle_signal_pause(
                signal, detail, section_number, planspace,
            )
        return _PROCEED

    # -----------------------------------------------------------------------
    # Post-loop: verification, traceability, assessment
    # -----------------------------------------------------------------------

    def _finalize(
        self,
        planspace: Path,
        codespace: Path,
        section,
        pre_hashes: dict[str, str],
    ) -> list[str]:
        """Verify changes, record traceability, build trace map, queue assessment."""
        actually_changed = self._change_verifier.verify_changed_files(
            planspace, codespace, section, pre_hashes,
        )

        for changed_file in actually_changed:
            self._communicator.record_traceability(
                planspace,
                section.number,
                changed_file,
                f"section-{section.number}-integration-proposal.md",
                "implementation change",
            )

        self._traceability_writer.write_traceability_index(planspace, section, actually_changed)

        self._trace_map_builder.build_trace_map(
            planspace, codespace, section.number,
            actually_changed, list(section.related_files),
        )
        self._dispatch_post_impl_assessment(section.number, planspace)
        self._submit_verification_chain(section.number, planspace)

        return actually_changed

    def _dispatch_post_impl_assessment(
        self,
        section_number: str,
        planspace: Path,
    ) -> None:
        """Queue a post-implementation governance assessment for a section."""
        paths = PathRegistry(planspace)
        prompt_path = self._assessment_evaluator.write_post_impl_assessment_prompt(
            section_number,
            planspace,
        )
        if prompt_path is None:
            self._logger.log(
                f"Section {section_number}: post-implementation assessment "
                "prompt blocked — skipping dispatch"
            )
            return

        self._flow_ingestion.submit_chain(
            FlowEnvelope(
                db_path=paths.run_db(),
                submitted_by=f"post-impl-{section_number}",
                origin_refs=[
                    str(paths.trace_dir() / f"section-{section_number}.json"),
                    str(paths.trace_map(section_number)),
                    str(paths.proposal(section_number)),
                ],
                planspace=planspace,
            ),
            [
                TaskSpec(
                    task_type="implementation.post_assessment",
                    concern_scope=f"section-{section_number}",
                    payload_path=str(prompt_path),
                    problem_id=f"post-impl-{section_number}",
                )
            ],
        )

    def _submit_verification_chain(
        self,
        section_number: str,
        planspace: Path,
    ) -> None:
        """Build and submit the posture-gated verification chain for a section."""
        if self._verification_chain_builder is None:
            return

        paths = PathRegistry(planspace)

        # Read ROAL posture from the accepted-steps artifact written before
        # implementation dispatch.  Falls back to P2 (standard) when the
        # artifact is absent or malformed.
        accepted_payload = self._artifact_io.read_json(
            paths.risk_accepted_steps(section_number),
        )
        roal_posture = "P2"
        if isinstance(accepted_payload, dict):
            roal_posture = accepted_payload.get("posture", "P2")

        has_incoming_consequence_notes = bool(
            list_notes_to(paths, section_number),
        )

        chain = self._verification_chain_builder.build_verification_chain(
            section_number=section_number,
            planspace=planspace,
            roal_posture=roal_posture,
            has_incoming_consequence_notes=has_incoming_consequence_notes,
        )

        if not chain:
            return

        self._flow_ingestion.submit_chain(
            FlowEnvelope(
                db_path=paths.run_db(),
                submitted_by=f"verification-{section_number}",
                origin_refs=[
                    str(paths.trace_dir() / f"section-{section_number}.json"),
                    str(paths.trace_map(section_number)),
                    str(paths.proposal(section_number)),
                ],
                planspace=planspace,
            ),
            chain,
        )
