"""Alignment checking and signal handling for the proposal loop.

Extracted from proposal_cycle.py to isolate alignment dispatch
and signal interpretation from the main loop orchestration.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from containers import (
        AgentDispatcher,
        DispatchHelperService,
        LogService,
        ModelPolicyService,
        PipelineControlService,
    )
    from dispatch.prompt.writers import Writers as PromptWriters
    from proposal.service.cycle_control import CycleControl

from orchestrator.path_registry import PathRegistry
from orchestrator.types import PauseType
from dispatch.types import ALIGNMENT_CHANGED_PENDING, DispatchResult
from signals.types import SIGNAL_UNDERSPEC


class AlignmentHandler:
    def __init__(
        self,
        logger: LogService,
        policies: ModelPolicyService,
        dispatcher: AgentDispatcher,
        dispatch_helpers: DispatchHelperService,
        pipeline_control: PipelineControlService,
        cycle_control: CycleControl,
        prompt_writers: PromptWriters,
    ) -> None:
        self._logger = logger
        self._policies = policies
        self._dispatcher = dispatcher
        self._dispatch_helpers = dispatch_helpers
        self._pipeline_control = pipeline_control
        self._cycle_control = cycle_control
        self._prompt_writers = prompt_writers

    def run_alignment_check(
        self,
        section,
        planspace: Path,
        codespace: Path,
    ) -> tuple[DispatchResult, Path] | None:
        """Dispatch the alignment judge and return (result, output_path).

        Returns None if the caller should abort (ALIGNMENT_CHANGED_PENDING).
        """
        paths = PathRegistry(planspace)
        policy = self._policies.load(planspace)
        section_number = section.number
        artifacts = paths.artifacts
        self._logger.log(f"Section {section_number}: proposal alignment check")
        align_prompt = self._prompt_writers.write_integration_alignment_prompt(
            section,
            planspace,
            codespace,
        )
        align_output = artifacts / f"intg-align-{section_number}-output.md"
        intent_sec_dir = paths.intent_section_dir(section_number)
        has_intent_artifacts = (
            intent_sec_dir.exists() and (intent_sec_dir / "problem.md").exists()
        )
        alignment_agent_file = (
            "intent-judge.md" if has_intent_artifacts else "alignment-judge.md"
        )
        alignment_model = (
            self._policies.resolve(policy, "intent_judge")
            if has_intent_artifacts
            else self._policies.resolve(policy, "alignment")
        )
        align_result = self._dispatcher.dispatch(
            alignment_model,
            align_prompt,
            align_output,
            planspace,
            codespace=codespace,
            section_number=section_number,
            agent_file=alignment_agent_file,
        )
        if align_result == ALIGNMENT_CHANGED_PENDING:
            self._logger.log(f"Section {section_number}: alignment changed during proposal alignment check — aborting")
            return None

        return align_result, align_output

    def handle_alignment_signals(
        self,
        section_number: str,
        planspace: Path,
    ) -> str | None:
        """Check alignment-judge signals for underspec.

        Returns:
            "continue" — underspec handled, caller should retry
            "abort" — caller should return None
            None — no underspec signal, proceed normally
        """
        paths = PathRegistry(planspace)
        signal, detail = self._dispatch_helpers.check_agent_signals(
            signal_path=paths.signals_dir() / f"proposal-align-{section_number}-signal.json",
        )
        if signal != SIGNAL_UNDERSPEC:
            return None

        response = self._pipeline_control.pause_for_parent(
            planspace,
            f"pause:{PauseType.UNDERSPEC}:{section_number}:{detail}",
        )
        return self._cycle_control.handle_pause_response(planspace, section_number, response)
