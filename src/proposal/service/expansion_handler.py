"""Intent expansion handling for the proposal loop."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from containers import (
        ArtifactIOService,
        Communicator,
        LogService,
        PipelineControlService,
    )
    from intent.engine.expansion_orchestrator import ExpansionOrchestrator
    from proposal.service.cycle_control import CycleControl

from signals.types import ACTION_ABORT, ACTION_BREAK, ACTION_CONTINUE


class ExpansionHandler:
    def __init__(
        self,
        logger: LogService,
        artifact_io: ArtifactIOService,
        communicator: Communicator,
        pipeline_control: PipelineControlService,
        cycle_control: CycleControl,
        expansion_orchestrator: ExpansionOrchestrator,
    ) -> None:
        self._logger = logger
        self._artifact_io = artifact_io
        self._communicator = communicator
        self._pipeline_control = pipeline_control
        self._cycle_control = cycle_control
        self._expansion_orchestrator = expansion_orchestrator

    def run_aligned_expansion(
        self,
        section_number: str,
        planspace: Path,
        codespace: Path,
        expansion_counts: dict[str, int],
    ) -> str | None:
        """Handle intent expansion when the proposal is aligned but surfaces exist.

        Returns:
            "continue" — caller should re-propose
            "break" — caller should accept alignment
            None — caller should abort (return None)
        """
        expansion_count = expansion_counts.get(section_number, 0)

        self._logger.log(
            f"Section {section_number}: surfaces found — "
            f"running expansion cycle"
        )
        self._communicator.send_to_parent(
            planspace,
            f"summary:intent-expand:{section_number}:cycle-{expansion_count + 1}",
        )
        delta_result = self._expansion_orchestrator.run_expansion_cycle(
            section_number,
            planspace,
            codespace,
        )
        expansion_counts[section_number] = expansion_count + 1

        if delta_result.get("needs_user_input"):
            gate_response = self._expansion_orchestrator.handle_user_gate(
                section_number,
                planspace,
                delta_result,
            )
            if gate_response:
                result = self._cycle_control.handle_pause_response(planspace, section_number, gate_response)
                if result == ACTION_ABORT:
                    return None

        if delta_result.get("restart_required"):
            self._logger.log(
                f"Section {section_number}: intent "
                f"expanded — re-proposing"
            )
            return ACTION_CONTINUE

        return ACTION_BREAK

    def run_misaligned_expansion(
        self,
        section_number: str,
        planspace: Path,
        codespace: Path,
        expansion_counts: dict[str, int],
    ) -> None:
        """Handle intent expansion on a misaligned pass with definition-gap surfaces.

        Runs the expansion cycle, persisting decisions from user gates.
        This is fire-and-forget — the caller always continues the proposal
        loop regardless.
        """
        expansion_count = expansion_counts.get(section_number, 0)

        self._logger.log(
            f"Section {section_number}: definition-gap surfaces "
            f"found on misaligned pass — running expansion"
        )
        delta_result = self._expansion_orchestrator.run_expansion_cycle(
            section_number,
            planspace,
            codespace,
        )
        expansion_counts[section_number] = expansion_count + 1

        if delta_result.get("needs_user_input"):
            gate_response = self._expansion_orchestrator.handle_user_gate(
                section_number,
                planspace,
                delta_result,
            )
            if gate_response:
                result = self._cycle_control.handle_pause_response(planspace, section_number, gate_response)
                if result == ACTION_ABORT:
                    return
