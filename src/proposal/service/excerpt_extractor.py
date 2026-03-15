from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from containers import (
        AgentDispatcher,
        Communicator,
        DispatchHelperService,
        LogService,
        ModelPolicyService,
        PipelineControlService,
        TaskRouterService,
    )
    from dispatch.prompt.writers import Writers as PromptWriters
    from proposal.service.cycle_control import CycleControl

from proposal.repository.excerpts import EXCERPT_PROPOSAL, exists as excerpt_exists
from orchestrator.path_registry import PathRegistry
from signals.service.blocker_manager import (
    append_open_problem,
    update_blocker_rollup,
)
from dispatch.types import ALIGNMENT_CHANGED_PENDING
from signals.types import ACTION_ABORT, SIGNAL_NEEDS_PARENT, SIGNAL_OUT_OF_SCOPE, TRUNCATE_DETAIL


class ExcerptExtractor:
    def __init__(
        self,
        logger: LogService,
        policies: ModelPolicyService,
        dispatcher: AgentDispatcher,
        dispatch_helpers: DispatchHelperService,
        communicator: Communicator,
        pipeline_control: PipelineControlService,
        task_router: TaskRouterService,
        cycle_control: CycleControl,
        prompt_writers: PromptWriters,
    ) -> None:
        self._logger = logger
        self._policies = policies
        self._dispatcher = dispatcher
        self._dispatch_helpers = dispatch_helpers
        self._communicator = communicator
        self._pipeline_control = pipeline_control
        self._task_router = task_router
        self._cycle_control = cycle_control
        self._prompt_writers = prompt_writers

    def _handle_setup_signal(
        self,
        signal: str, detail: str, planspace: Path,
        section_number: str,
    ) -> str | None:
        """Handle a setup agent signal. Returns 'abort' or 'continue'."""
        if signal in (SIGNAL_NEEDS_PARENT, SIGNAL_OUT_OF_SCOPE):
            append_open_problem(planspace, section_number, detail, signal)
            self._communicator.send_to_parent(
                planspace,
                f"open-problem:{section_number}:{signal}:{detail[:TRUNCATE_DETAIL]}",
            )
        if signal == SIGNAL_OUT_OF_SCOPE:
            sig_path = PathRegistry(planspace).signals_dir() / f"setup-{section_number}-signal.json"
            self._cycle_control.write_scope_delta(planspace, sig_path, section_number, detail, "setup")
        update_blocker_rollup(planspace)
        response = self._pipeline_control.pause_for_parent(
            planspace,
            f"pause:{signal}:{section_number}:{detail}",
        )
        return self._cycle_control.handle_pause_response(planspace, section_number, response)

    def extract_excerpts(
        self,
        section,
        planspace: Path,
        codespace: Path,
    ) -> str | None:
        """Run the setup loop until both proposal and alignment excerpts exist."""
        policy = self._policies.load(planspace)
        paths = PathRegistry(planspace)
        signal_dir = paths.signals_dir()

        while (
            not excerpt_exists(planspace, section.number, EXCERPT_PROPOSAL)
            or not excerpt_exists(planspace, section.number, "alignment")
        ):
            self._logger.log(f"Section {section.number}: setup — extracting excerpts")
            setup_prompt = self._prompt_writers.write_section_setup_prompt(
                section, planspace, codespace,
                section.global_proposal_path, section.global_alignment_path,
            )
            setup_output = paths.artifacts / f"setup-{section.number}-output.md"
            setup_agent = f"setup-{section.number}"
            output = self._dispatcher.dispatch(
                policy["setup"], setup_prompt, setup_output,
                planspace, setup_agent,
                codespace=codespace, section_number=section.number,
                agent_file=self._task_router.agent_for("proposal.section_setup"),
            )
            if output == ALIGNMENT_CHANGED_PENDING:
                self._logger.log(f"Section {section.number}: alignment changed during setup dispatch — aborting")
                return None
            self._communicator.send_to_parent(
                planspace,
                f"summary:setup:{section.number}:{self._dispatch_helpers.summarize_output(output.output)}",
            )

            signal, detail = self._dispatch_helpers.check_agent_signals(
                signal_path=signal_dir / f"setup-{section.number}-signal.json",
            )
            if signal:
                result = self._handle_setup_signal(
                    signal, detail, planspace,
                    section.number,
                )
                if result == ACTION_ABORT:
                    return None
                continue

            if (
                not excerpt_exists(planspace, section.number, EXCERPT_PROPOSAL)
                or not excerpt_exists(planspace, section.number, "alignment")
            ):
                self._logger.log(
                    f"Section {section.number}: ERROR — setup failed to create "
                    f"excerpt files"
                )
                self._communicator.send_to_parent(
                    planspace,
                    f"fail:{section.number}:setup failed to create excerpt files",
                )
                return None
            break

        return "ok"
