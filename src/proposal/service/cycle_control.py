"""Loop control, budget enforcement, signal handling, and dispatch for the proposal loop.

Extracted from proposal_cycle.py to isolate early-abort checks,
budget enforcement, dispatch orchestration, and proposal signal
handling from the main loop.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from containers import (
        AgentDispatcher,
        ArtifactIOService,
        Communicator,
        CrossSectionService,
        DispatchHelperService,
        FlowIngestionService,
        LogService,
        PipelineControlService,
        TaskRouterService,
    )

from orchestrator.path_registry import PathRegistry
from signals.service.blocker_manager import (
    append_open_problem,
    update_blocker_rollup,
)
from dispatch.types import ALIGNMENT_CHANGED_PENDING, DispatchResult, DispatchStatus
from orchestrator.types import PauseType
from signals.types import (
    ACTION_ABORT, ACTION_CONTINUE,
    RESUME_PREFIX,
    SIGNAL_NEEDS_PARENT, SIGNAL_OUT_OF_SCOPE,
    TRUNCATE_DETAIL,
)


class CycleControl:
    def __init__(
        self,
        logger: LogService,
        artifact_io: ArtifactIOService,
        communicator: Communicator,
        pipeline_control: PipelineControlService,
        cross_section: CrossSectionService,
        dispatcher: AgentDispatcher,
        dispatch_helpers: DispatchHelperService,
        task_router: TaskRouterService,
        flow_ingestion: FlowIngestionService,
    ) -> None:
        self._logger = logger
        self._artifact_io = artifact_io
        self._communicator = communicator
        self._pipeline_control = pipeline_control
        self._cross_section = cross_section
        self._dispatcher = dispatcher
        self._dispatch_helpers = dispatch_helpers
        self._task_router = task_router
        self._flow_ingestion = flow_ingestion

    def write_scope_delta(
        self,
        planspace: Path, signal_path: Path, section_number: str,
        detail: str, origin: str,
    ) -> None:
        """Write a scope delta artifact for an out-of-scope signal.

        ``origin`` identifies where the OOS signal came from (e.g. "setup",
        "proposal") and is used in the delta ID.
        """
        paths = PathRegistry(planspace)
        scope_delta_dir = paths.scope_deltas_dir()
        scope_delta_dir.mkdir(parents=True, exist_ok=True)
        signal_payload = self._artifact_io.read_json_or_default(signal_path, {})
        scope_delta = {
            "delta_id": f"delta-{section_number}-{origin}-oos",
            "section": section_number,
            "signal": SIGNAL_OUT_OF_SCOPE,
            "detail": detail,
            "requires_root_reframing": True,
            "signal_path": str(signal_path),
            "signal_payload": signal_payload,
        }
        self._artifact_io.write_json(
            paths.scope_delta_section(section_number),
            scope_delta,
        )

    def handle_pause_response(
        self,
        planspace: Path,
        section_number: str,
        response: str,
    ) -> str:
        """Process a pause_for_parent response and return an action.

        Returns ``ACTION_ABORT`` if the parent rejected or alignment changed,
        ``ACTION_CONTINUE`` otherwise.  Persists any payload decision.
        """
        if not response.startswith(RESUME_PREFIX):
            return ACTION_ABORT
        payload = response.partition(":")[2].strip()
        if payload:
            self._cross_section.persist_decision(planspace, section_number, payload)
        if self._pipeline_control.alignment_changed_pending(planspace):
            return ACTION_ABORT
        return ACTION_CONTINUE

    def check_early_abort(
        self,
        section_number: str,
        planspace: Path,
    ) -> bool:
        """Check pending messages and alignment changes.

        Returns True if the loop should abort (caller returns None).
        """
        if self._pipeline_control.handle_pending_messages(planspace):
            self._communicator.send_to_parent(planspace, f"fail:{section_number}:aborted")
            return True

        if self._pipeline_control.alignment_changed_pending(planspace):
            self._logger.log(
                f"Section {section_number}: alignment changed — "
                "aborting section to restart Phase 1"
            )
            return True

        return False

    def check_budget_exceeded(
        self,
        section_number: str,
        planspace: Path,
        proposal_attempt: int,
        cycle_budget: dict,
        cycle_budget_path: Path,
    ) -> bool | None:
        """Handle proposal cycle budget exhaustion.

        Returns:
            None — budget not exceeded, continue normally
            True — budget exceeded and parent rejected resume (caller returns None)
            False — budget exceeded but parent resumed (caller continues)
        """
        if proposal_attempt <= cycle_budget["proposal_max"]:
            return None

        self._logger.log(
            f"Section {section_number}: proposal cycle budget exhausted "
            f"({cycle_budget['proposal_max']} attempts)"
        )
        budget_signal = {
            "section": section_number,
            "loop": "proposal",
            "attempts": proposal_attempt - 1,
            "budget": cycle_budget["proposal_max"],
            "escalate": True,
        }
        budget_signal_path = (
            PathRegistry(planspace).signals_dir()
            / f"section-{section_number}-proposal-budget-exhausted.json"
        )
        self._artifact_io.write_json(budget_signal_path, budget_signal)
        self._communicator.send_to_parent(
            planspace,
            f"budget-exhausted:{section_number}:proposal:{proposal_attempt - 1}",
        )
        response = self._pipeline_control.pause_for_parent(
            planspace,
            f"pause:{PauseType.BUDGET_EXHAUSTED}:{section_number}:proposal loop exceeded "
            f"{cycle_budget['proposal_max']} attempts",
        )
        if not response.startswith(RESUME_PREFIX):
            return True
        reloaded = self._artifact_io.read_json(cycle_budget_path)
        if reloaded is not None:
            cycle_budget.update(reloaded)
        return False

    def dispatch_proposal(
        self,
        section_number: str,
        planspace: Path,
        codespace: Path,
        proposal_model: str,
        intg_prompt: Path,
        integration_proposal: Path,
    ) -> DispatchResult | None:
        """Dispatch the proposal agent, handle timeout, send summary, and ingest.

        Returns the dispatch result, or None if the caller should abort.
        """
        paths = PathRegistry(planspace)
        intg_output = paths.artifacts / f"intg-proposal-{section_number}-output.md"
        intg_agent = f"intg-proposal-{section_number}"
        intg_result = self._dispatcher.dispatch(
            proposal_model,
            intg_prompt,
            intg_output,
            planspace,
            intg_agent,
            codespace=codespace,
            section_number=section_number,
            agent_file=self._task_router.agent_for("proposal.integration"),
        )
        if intg_result == ALIGNMENT_CHANGED_PENDING:
            self._logger.log(f"Section {section_number}: alignment changed during integration dispatch — aborting")
            return None
        self._communicator.send_to_parent(
            planspace,
            f"summary:proposal:{section_number}:{self._dispatch_helpers.summarize_output(intg_result.output)}",
        )

        if intg_result.status is DispatchStatus.TIMEOUT:
            self._logger.log(
                f"Section {section_number}: integration proposal agent "
                f"timed out"
            )
            self._communicator.send_to_parent(
                planspace,
                f"fail:{section_number}:integration proposal agent timed out",
            )
            return None

        self._flow_ingestion.ingest_and_submit(
            planspace,
            submitted_by=f"proposal-{section_number}",
            signal_path=paths.task_request_signal("proposal", section_number),
            origin_refs=[str(integration_proposal)],
        )

        return intg_result

    def handle_proposal_signals(
        self,
        section_number: str,
        planspace: Path,
    ) -> str | None:
        """Check agent signals after proposal dispatch.

        Returns:
            "continue" — signal handled, caller should retry the loop
            "abort" — caller should return None
            None — no signal, proceed normally
        """
        paths = PathRegistry(planspace)
        signal, detail = self._dispatch_helpers.check_agent_signals(
            signal_path=paths.proposal_signal(section_number),
        )
        if not signal:
            return None

        if signal in (SIGNAL_NEEDS_PARENT, SIGNAL_OUT_OF_SCOPE):
            append_open_problem(planspace, section_number, detail, signal)
            self._communicator.send_to_parent(
                planspace,
                f"open-problem:{section_number}:{signal}:{detail[:TRUNCATE_DETAIL]}",
            )
        if signal == SIGNAL_OUT_OF_SCOPE:
            self.write_scope_delta(
                planspace, paths.proposal_signal(section_number),
                section_number, detail, "proposal",
            )
        update_blocker_rollup(planspace)
        response = self._pipeline_control.pause_for_parent(
            planspace,
            f"pause:{signal}:{section_number}:{detail}",
        )
        return self.handle_pause_response(planspace, section_number, response)


# Backward-compat wrappers

def _get_cycle_control() -> CycleControl:
    from containers import Services
    return CycleControl(
        logger=Services.logger(),
        artifact_io=Services.artifact_io(),
        communicator=Services.communicator(),
        pipeline_control=Services.pipeline_control(),
        cross_section=Services.cross_section(),
        dispatcher=Services.dispatcher(),
        dispatch_helpers=Services.dispatch_helpers(),
        task_router=Services.task_router(),
        flow_ingestion=Services.flow_ingestion(),
    )


def write_scope_delta(
    planspace: Path, signal_path: Path, section_number: str,
    detail: str, origin: str,
) -> None:
    """Write a scope delta artifact for an out-of-scope signal."""
    _get_cycle_control().write_scope_delta(
        planspace, signal_path, section_number, detail, origin,
    )


def handle_pause_response(
    planspace: Path,
    section_number: str,
    response: str,
) -> str:
    """Process a pause_for_parent response and return an action."""
    return _get_cycle_control().handle_pause_response(
        planspace, section_number, response,
    )


def check_early_abort(
    section_number: str,
    planspace: Path,
) -> bool:
    """Check pending messages and alignment changes."""
    return _get_cycle_control().check_early_abort(
        section_number, planspace,
    )


def check_budget_exceeded(
    section_number: str,
    planspace: Path,
    proposal_attempt: int,
    cycle_budget: dict,
    cycle_budget_path: Path,
) -> bool | None:
    """Handle proposal cycle budget exhaustion."""
    return _get_cycle_control().check_budget_exceeded(
        section_number, planspace,
        proposal_attempt, cycle_budget, cycle_budget_path,
    )


def dispatch_proposal(
    section_number: str,
    planspace: Path,
    codespace: Path,
    proposal_model: str,
    intg_prompt: Path,
    integration_proposal: Path,
) -> DispatchResult | None:
    """Dispatch the proposal agent, handle timeout, send summary, and ingest."""
    return _get_cycle_control().dispatch_proposal(
        section_number, planspace, codespace,
        proposal_model, intg_prompt, integration_proposal,
    )


def handle_proposal_signals(
    section_number: str,
    planspace: Path,
) -> str | None:
    """Check agent signals after proposal dispatch."""
    return _get_cycle_control().handle_proposal_signals(
        section_number, planspace,
    )
