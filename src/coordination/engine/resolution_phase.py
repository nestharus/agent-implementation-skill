"""Blocker resolution phase for pre-implementation readiness unblocking.

.. deprecated::
    DEAD CODE -- global batch resolution phase is fully replaced by
    reactive per-section coordination (fractal pipeline design, Gap 3).
    The ``ResolutionPhase`` orchestration class and all callers are
    dead code.  Blocker resolution now fires reactively when a section's
    readiness check discovers friction, not in a global batch.  This
    module exists only for reference and will be deleted in the next
    cleanup pass.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from coordination.engine.global_coordinator import GlobalCoordinator
from coordination.service.stall_detector import StallDetector
from orchestrator.types import ProposalPassResult, Section
from pipeline.context import DispatchContext

if TYPE_CHECKING:
    from containers import Communicator, LogService, ModelPolicyService
    from proposal.service.readiness_resolver import ReadinessResolver


MAX_RESOLUTION_ROUNDS = 3


class ResolutionPhase:
    """Orchestrates the blocker resolution loop.

    Attempts up to ``MAX_RESOLUTION_ROUNDS`` of coordination-driven
    resolution.  Uses ``StallDetector`` for convergence detection.
    """

    def __init__(
        self,
        *,
        global_coordinator: GlobalCoordinator,
        readiness_resolver: ReadinessResolver,
        logger: LogService,
        policies: ModelPolicyService,
        communicator: Communicator,
    ) -> None:
        self._global_coordinator = global_coordinator
        self._readiness_resolver = readiness_resolver
        self._logger = logger
        self._policies = policies
        self._communicator = communicator

    def run_resolution_phase(
        self,
        proposal_results: dict[str, ProposalPassResult],
        blocked_sections: list[str],
        sections_by_num: dict[str, Section],
        ctx: DispatchContext,
    ) -> list[str]:
        """Run the blocker resolution loop.

        Modifies *proposal_results* in-place: newly ready sections get
        ``execution_ready=True`` and their blockers are cleared.

        Returns the updated list of still-blocked section numbers.
        """
        if not blocked_sections:
            return blocked_sections

        self._logger.log(
            f"=== Blocker resolution: {len(blocked_sections)} blocked sections ===",
        )

        stall = StallDetector(
            ctx.planspace,
            logger=self._logger,
            policies=self._policies,
            communicator=self._communicator,
        )
        stall.set_initial(len(blocked_sections))

        remaining_blocked = list(blocked_sections)

        for round_num in range(1, MAX_RESOLUTION_ROUNDS + 1):
            self._logger.log(
                f"  blocker-resolution round {round_num}/{MAX_RESOLUTION_ROUNDS}",
            )

            newly_ready = self._global_coordinator.run_blocker_resolution(
                proposal_results, sections_by_num, ctx,
                readiness_resolver=self._readiness_resolver,
            )

            # Update proposal_results for newly ready sections
            for sec_num in newly_ready:
                if sec_num in proposal_results:
                    proposal_results[sec_num].execution_ready = True
                    proposal_results[sec_num].blockers.clear()

            remaining_blocked = [
                s for s in remaining_blocked if s not in newly_ready
            ]

            if not remaining_blocked:
                self._logger.log(
                    "  blocker-resolution: all sections unblocked",
                )
                break

            stall.update(len(remaining_blocked), round_num)
            if stall.should_terminate:
                self._logger.log(
                    f"  blocker-resolution: stalled after {round_num} rounds "
                    f"({len(remaining_blocked)} still blocked)",
                )
                break

        self._logger.log(
            f"=== Blocker resolution complete: "
            f"{len(blocked_sections) - len(remaining_blocked)} unblocked, "
            f"{len(remaining_blocked)} still blocked ===",
        )

        return remaining_blocked
