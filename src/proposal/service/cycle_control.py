"""Loop control, budget enforcement, signal handling, and dispatch for the proposal loop.

Extracted from proposal_cycle.py to isolate early-abort checks,
budget enforcement, dispatch orchestration, and proposal signal
handling from the main loop.
"""

from __future__ import annotations

from pathlib import Path

from containers import Services
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


def write_scope_delta(
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
    signal_payload = Services.artifact_io().read_json_or_default(signal_path, {})
    scope_delta = {
        "delta_id": f"delta-{section_number}-{origin}-oos",
        "section": section_number,
        "signal": SIGNAL_OUT_OF_SCOPE,
        "detail": detail,
        "requires_root_reframing": True,
        "signal_path": str(signal_path),
        "signal_payload": signal_payload,
    }
    Services.artifact_io().write_json(
        paths.scope_delta_section(section_number),
        scope_delta,
    )


def handle_pause_response(
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
        Services.cross_section().persist_decision(planspace, section_number, payload)
    if Services.pipeline_control().alignment_changed_pending(planspace):
        return ACTION_ABORT
    return ACTION_CONTINUE


def check_early_abort(
    section_number: str,
    planspace: Path,
    parent: str,
) -> bool:
    """Check pending messages and alignment changes.

    Returns True if the loop should abort (caller returns None).
    """
    if Services.pipeline_control().handle_pending_messages(planspace):
        Services.communicator().mailbox_send(planspace, parent, f"fail:{section_number}:aborted")
        return True

    if Services.pipeline_control().alignment_changed_pending(planspace):
        Services.logger().log(
            f"Section {section_number}: alignment changed — "
            "aborting section to restart Phase 1"
        )
        return True

    return False


def check_budget_exceeded(
    section_number: str,
    planspace: Path,
    parent: str,
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

    Services.logger().log(
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
    Services.artifact_io().write_json(budget_signal_path, budget_signal)
    Services.communicator().mailbox_send(
        planspace,
        parent,
        f"budget-exhausted:{section_number}:proposal:{proposal_attempt - 1}",
    )
    response = Services.pipeline_control().pause_for_parent(
        planspace,
        parent,
        f"pause:{PauseType.BUDGET_EXHAUSTED}:{section_number}:proposal loop exceeded "
        f"{cycle_budget['proposal_max']} attempts",
    )
    if not response.startswith(RESUME_PREFIX):
        return True
    reloaded = Services.artifact_io().read_json(cycle_budget_path)
    if reloaded is not None:
        cycle_budget.update(reloaded)
    return False


def dispatch_proposal(
    section_number: str,
    planspace: Path,
    codespace: Path,
    parent: str,
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
    intg_result = Services.dispatcher().dispatch(
        proposal_model,
        intg_prompt,
        intg_output,
        planspace,
        parent,
        intg_agent,
        codespace=codespace,
        section_number=section_number,
        agent_file=Services.task_router().agent_for("proposal.integration"),
    )
    if intg_result == ALIGNMENT_CHANGED_PENDING:
        Services.logger().log(f"Section {section_number}: alignment changed during integration dispatch — aborting")
        return None
    Services.communicator().mailbox_send(
        planspace,
        parent,
        f"summary:proposal:{section_number}:{Services.dispatch_helpers().summarize_output(intg_result.output)}",
    )

    if intg_result.status is DispatchStatus.TIMEOUT:
        Services.logger().log(
            f"Section {section_number}: integration proposal agent "
            f"timed out"
        )
        Services.communicator().mailbox_send(
            planspace,
            parent,
            f"fail:{section_number}:integration proposal agent timed out",
        )
        return None

    Services.flow_ingestion().ingest_and_submit(
        planspace,
        submitted_by=f"proposal-{section_number}",
        signal_path=paths.task_request_signal("proposal", section_number),
        origin_refs=[str(integration_proposal)],
    )

    return intg_result


def handle_proposal_signals(
    section_number: str,
    planspace: Path,
    parent: str,
) -> str | None:
    """Check agent signals after proposal dispatch.

    Returns:
        "continue" — signal handled, caller should retry the loop
        "abort" — caller should return None
        None — no signal, proceed normally
    """
    paths = PathRegistry(planspace)
    signal, detail = Services.dispatch_helpers().check_agent_signals(
        signal_path=paths.proposal_signal(section_number),
    )
    if not signal:
        return None

    if signal in (SIGNAL_NEEDS_PARENT, SIGNAL_OUT_OF_SCOPE):
        append_open_problem(planspace, section_number, detail, signal)
        Services.communicator().mailbox_send(
            planspace,
            parent,
            f"open-problem:{section_number}:{signal}:{detail[:TRUNCATE_DETAIL]}",
        )
    if signal == SIGNAL_OUT_OF_SCOPE:
        write_scope_delta(
            planspace, paths.proposal_signal(section_number),
            section_number, detail, "proposal",
        )
    update_blocker_rollup(planspace)
    response = Services.pipeline_control().pause_for_parent(
        planspace,
        parent,
        f"pause:{signal}:{section_number}:{detail}",
    )
    return handle_pause_response(planspace, section_number, response)
