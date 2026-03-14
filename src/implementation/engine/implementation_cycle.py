from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from orchestrator.path_registry import PathRegistry
from intake.service.assessment_evaluator import write_post_impl_assessment_prompt
from dispatch.prompt.writers import write_impl_alignment_prompt, write_strategic_impl_prompt
from implementation.service.traceability_writer import write_traceability_index
from implementation.service.change_verifier import verify_changed_files
from implementation.service.trace_map_builder import build_trace_map
from flow.types.context import FlowEnvelope
from flow.types.schema import TaskSpec
from dispatch.types import ALIGNMENT_CHANGED_PENDING, DispatchResult, DispatchStatus
from proposal.service.cycle_control import check_early_abort, handle_pause_response
from orchestrator.types import PauseType
from signals.types import ACTION_ABORT, RESUME_PREFIX, SIGNAL_UNDERSPEC, TRUNCATE_DETAIL

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
        communicator: Communicator,
        dispatcher: AgentDispatcher,
        dispatch_helpers: DispatchHelperService,
        flow_ingestion: FlowIngestionService,
        logger: LogService,
        pipeline_control: PipelineControlService,
        policies: ModelPolicyService,
        section_alignment: SectionAlignmentService,
        staleness: StalenessDetectionService,
        task_router: TaskRouterService,
    ) -> None:
        self._artifact_io = artifact_io
        self._communicator = communicator
        self._dispatcher = dispatcher
        self._dispatch_helpers = dispatch_helpers
        self._flow_ingestion = flow_ingestion
        self._logger = logger
        self._pipeline_control = pipeline_control
        self._policies = policies
        self._section_alignment = section_alignment
        self._staleness = staleness
        self._task_router = task_router

    def run_implementation_loop(
        self,
        section,
        planspace: Path,
        codespace: Path,
        cycle_budget: dict,
    ) -> list[str] | None:
        """Run strategic implementation until aligned, then return changed files."""
        cycle_budget_path = PathRegistry(planspace).cycle_budget(section.number)

        all_known_paths = list(section.related_files)
        pre_hashes = self._staleness.snapshot_files(codespace, all_known_paths)

        impl_problems: str | None = None
        impl_attempt = 0

        while True:
            if check_early_abort(section.number, planspace):
                return None

            impl_attempt += 1

            budget_action = self._check_budget(
                impl_attempt, cycle_budget, planspace,
                section.number, cycle_budget_path,
            )
            if budget_action == _ABORT:
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
                continue

            align_result = self._dispatch_alignment_check(
                section, planspace, codespace,
            )
            if align_result is None:
                return None

            timeout_action = self._handle_alignment_timeout(
                align_result, section.number,
            )
            if timeout_action == _CONTINUE:
                impl_problems = "Previous alignment check timed out."
                continue

            problems = self._extract_alignment_problems(
                align_result, section.number, planspace, codespace,
            )

            underspec_action = self._handle_underspec_signal(
                section.number, planspace,
            )
            if underspec_action == _ABORT:
                return None
            if underspec_action == _CONTINUE:
                continue

            if problems is None:
                self._logger.log(f"Section {section.number}: implementation ALIGNED")
                self._communicator.send_to_parent(
                    planspace,
                    f"summary:impl-align:{section.number}:ALIGNED",
                )
                break

            impl_problems = problems
            self._log_alignment_problems(
                section.number, impl_attempt, problems, planspace,
            )

        return self._finalize(
            planspace, codespace, section, pre_hashes,
        )

    # -----------------------------------------------------------------------
    # Budget enforcement
    # -----------------------------------------------------------------------

    def _check_budget(
        self,
        impl_attempt: int,
        cycle_budget: dict,
        planspace: Path,
        section_number: str,
        cycle_budget_path: Path,
    ) -> str:
        """Enforce the implementation cycle budget.

        Returns ``_ABORT`` if the parent declines to resume, ``_PROCEED``
        otherwise (including after a successful budget reload).
        """
        if impl_attempt <= cycle_budget["implementation_max"]:
            return _PROCEED

        paths = PathRegistry(planspace)
        self._logger.log(
            f"Section {section_number}: implementation cycle budget "
            f"exhausted ({cycle_budget['implementation_max']} attempts)"
        )
        budget_signal = {
            "section": section_number,
            "loop": "implementation",
            "attempts": impl_attempt - 1,
            "budget": cycle_budget["implementation_max"],
            "escalate": True,
        }
        budget_signal_path = paths.impl_budget_exhausted_signal(section_number)
        self._artifact_io.write_json(budget_signal_path, budget_signal)
        self._communicator.send_to_parent(
            planspace,
            f"budget-exhausted:{section_number}:implementation:{impl_attempt - 1}",
        )
        response = self._pipeline_control.pause_for_parent(
            planspace,
            f"pause:{PauseType.BUDGET_EXHAUSTED}:{section_number}:implementation loop exceeded "
            f"{cycle_budget['implementation_max']} attempts",
        )
        if not response.startswith(RESUME_PREFIX):
            return _ABORT
        reloaded = self._artifact_io.read_json(cycle_budget_path)
        if reloaded is not None:
            cycle_budget.update(reloaded)
        return _PROCEED

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
        impl_prompt = write_strategic_impl_prompt(
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
        result = handle_pause_response(planspace, section_number, response)
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
        impl_align_prompt = write_impl_alignment_prompt(
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
        actually_changed = verify_changed_files(
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

        write_traceability_index(planspace, section, actually_changed)

        build_trace_map(
            planspace, codespace, section.number,
            actually_changed, list(section.related_files),
        )
        self._dispatch_post_impl_assessment(section.number, planspace)

        return actually_changed

    def _dispatch_post_impl_assessment(
        self,
        section_number: str,
        planspace: Path,
    ) -> None:
        """Queue a post-implementation governance assessment for a section."""
        paths = PathRegistry(planspace)
        prompt_path = write_post_impl_assessment_prompt(
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


# ---------------------------------------------------------------------------
# Backward-compat free function wrapper
# ---------------------------------------------------------------------------


def run_implementation_loop(
    section,
    planspace: Path,
    codespace: Path,
    cycle_budget: dict,
) -> list[str] | None:
    """Run strategic implementation until aligned, then return changed files."""
    from containers import Services
    cycle = ImplementationCycle(
        artifact_io=Services.artifact_io(),
        communicator=Services.communicator(),
        dispatcher=Services.dispatcher(),
        dispatch_helpers=Services.dispatch_helpers(),
        flow_ingestion=Services.flow_ingestion(),
        logger=Services.logger(),
        pipeline_control=Services.pipeline_control(),
        policies=Services.policies(),
        section_alignment=Services.section_alignment(),
        staleness=Services.staleness(),
        task_router=Services.task_router(),
    )
    return cycle.run_implementation_loop(
        section, planspace, codespace, cycle_budget,
    )
