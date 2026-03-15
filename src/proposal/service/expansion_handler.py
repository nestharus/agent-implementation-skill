"""Intent expansion handling for the proposal loop.

Manages the expansion cycle when structured surfaces are discovered,
including budget tracking, user gate handling, and escalation signals.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from containers import (
        ArtifactIOService,
        Communicator,
        LogService,
        PipelineControlService,
    )
    from proposal.service.cycle_control import CycleControl

from orchestrator.path_registry import PathRegistry
from intent.service.expansion_facade import handle_user_gate, run_expansion_cycle
from orchestrator.types import PauseType
from signals.types import ACTION_ABORT, ACTION_BREAK, ACTION_CONTINUE, RESUME_PREFIX


class ExpansionHandler:
    def __init__(
        self,
        logger: LogService,
        artifact_io: ArtifactIOService,
        communicator: Communicator,
        pipeline_control: PipelineControlService,
        cycle_control: CycleControl,
    ) -> None:
        self._logger = logger
        self._artifact_io = artifact_io
        self._communicator = communicator
        self._pipeline_control = pipeline_control
        self._cycle_control = cycle_control

    def _handle_budget_exhaustion(
        self,
        section_number: str, planspace: Path,
        expansion_count: int, expansion_max: int,
    ) -> str | None:
        """Handle the case where expansion budget is exhausted.

        Returns ``"break"`` to accept alignment, or ``None`` to abort.
        """
        self._logger.log(
            f"Section {section_number}: intent expansion "
            f"budget exhausted ({expansion_count}/{expansion_max}) "
            f"— pausing for decision"
        )
        stalled_signal = {
            "section": section_number,
            "reason": "expansion budget exhausted",
            "cycles": expansion_count,
        }
        self._artifact_io.write_json(
            PathRegistry(planspace).intent_stalled_signal(section_number),
            stalled_signal,
        )
        response = self._pipeline_control.pause_for_parent(
            planspace,
            f"pause:{PauseType.INTENT_STALLED}:{section_number}:"
            f"expansion budget exhausted ({expansion_count}/{expansion_max})",
        )
        if not response.startswith(RESUME_PREFIX):
            return None
        return ACTION_BREAK

    def run_aligned_expansion(
        self,
        section_number: str,
        planspace: Path,
        codespace: Path,
        intent_budgets: dict,
        expansion_counts: dict[str, int],
    ) -> str | None:
        """Handle intent expansion when the proposal is aligned but surfaces exist.

        Returns:
            "continue" — caller should re-propose
            "break" — caller should accept alignment
            None — caller should abort (return None)
        """
        expansion_max = intent_budgets.get("intent_expansion_max", 2)
        expansion_count = expansion_counts.get(section_number, 0)

        if expansion_count >= expansion_max:
            return self._handle_budget_exhaustion(
                section_number, planspace,
                expansion_count, expansion_max,
            )

        self._logger.log(
            f"Section {section_number}: surfaces found — "
            f"running expansion cycle"
        )
        self._communicator.send_to_parent(
            planspace,
            f"summary:intent-expand:{section_number}:cycle-{expansion_count + 1}",
        )
        delta_result = run_expansion_cycle(
            section_number,
            planspace,
            codespace,
            budgets=intent_budgets,
        )
        expansion_counts[section_number] = expansion_count + 1

        if delta_result.get("needs_user_input"):
            gate_response = handle_user_gate(
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
        intent_budgets: dict,
        expansion_counts: dict[str, int],
    ) -> None:
        """Handle intent expansion on a misaligned pass with definition-gap surfaces.

        Runs the expansion cycle if budget allows, persisting decisions from
        user gates.  This is fire-and-forget — the caller always continues
        the proposal loop regardless.
        """
        expansion_max = intent_budgets.get("intent_expansion_max", 2)
        expansion_count = expansion_counts.get(section_number, 0)

        if expansion_count >= expansion_max:
            self._logger.log(
                f"Section {section_number}: definition-gap surfaces "
                f"found on misaligned pass but expansion budget is "
                f"exhausted ({expansion_count}/{expansion_max})"
            )
            return

        self._logger.log(
            f"Section {section_number}: definition-gap surfaces "
            f"found on misaligned pass — running expansion"
        )
        delta_result = run_expansion_cycle(
            section_number,
            planspace,
            codespace,
            budgets=intent_budgets,
        )
        expansion_counts[section_number] = expansion_count + 1

        if delta_result.get("needs_user_input"):
            gate_response = handle_user_gate(
                section_number,
                planspace,
                delta_result,
            )
            if gate_response:
                result = self._cycle_control.handle_pause_response(planspace, section_number, gate_response)
                if result == ACTION_ABORT:
                    return
