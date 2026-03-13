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
    _append_open_problem,
    _update_blocker_rollup,
)
from dispatch.types import ALIGNMENT_CHANGED_PENDING
from signals.types import SIGNAL_NEEDS_PARENT, SIGNAL_OUT_OF_SCOPE


def check_early_abort(
    section_number: str,
    planspace: Path,
    parent: str,
) -> bool:
    """Check pending messages and alignment changes.

    Returns True if the loop should abort (caller returns None).
    """
    if Services.pipeline_control().handle_pending_messages(planspace, [], set()):
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
    paths: PathRegistry,
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
        paths.signals_dir()
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
        f"pause:budget_exhausted:{section_number}:proposal loop exceeded "
        f"{cycle_budget['proposal_max']} attempts",
    )
    if not response.startswith("resume"):
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
    paths: PathRegistry,
    integration_proposal: Path,
) -> str | None:
    """Dispatch the proposal agent, handle timeout, send summary, and ingest.

    Returns the dispatch result string, or None if the caller should abort.
    """
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
        return None
    Services.communicator().mailbox_send(
        planspace,
        parent,
        f"summary:proposal:{section_number}:{Services.dispatch_helpers().summarize_output(intg_result)}",
    )

    if intg_result.startswith("TIMEOUT:"):
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
        db_path=paths.run_db(),
        submitted_by=f"proposal-{section_number}",
        signal_path=paths.task_request_signal("proposal", section_number),
        origin_refs=[str(integration_proposal)],
    )

    return intg_result


def handle_proposal_signals(
    section_number: str,
    planspace: Path,
    parent: str,
    codespace: Path,
    intg_result: str,
    paths: PathRegistry,
) -> str | None:
    """Check agent signals after proposal dispatch.

    Returns:
        "continue" — signal handled, caller should retry the loop
        "abort" — caller should return None
        None — no signal, proceed normally
    """
    intg_output = paths.artifacts / f"intg-proposal-{section_number}-output.md"
    paths.signals_dir().mkdir(parents=True, exist_ok=True)
    signal, detail = Services.dispatch_helpers().check_agent_signals(
        intg_result,
        signal_path=paths.proposal_signal(section_number),
        output_path=intg_output,
        planspace=planspace,
        parent=parent,
        codespace=codespace,
    )
    if not signal:
        return None

    if signal in (SIGNAL_NEEDS_PARENT, SIGNAL_OUT_OF_SCOPE):
        _append_open_problem(planspace, section_number, detail, signal)
        Services.communicator().mailbox_send(
            planspace,
            parent,
            f"open-problem:{section_number}:{signal}:{detail[:200]}",
        )
    if signal == SIGNAL_OUT_OF_SCOPE:
        scope_delta_dir = paths.scope_deltas_dir()
        scope_delta_dir.mkdir(parents=True, exist_ok=True)
        proposal_sig_path = paths.proposal_signal(section_number)
        signal_payload = Services.artifact_io().read_json_or_default(proposal_sig_path, {})
        scope_delta = {
            "delta_id": f"delta-{section_number}-proposal-oos",
            "section": section_number,
            "signal": SIGNAL_OUT_OF_SCOPE,
            "detail": detail,
            "requires_root_reframing": True,
            "signal_path": str(proposal_sig_path),
            "signal_payload": signal_payload,
        }
        Services.artifact_io().write_json(
            paths.scope_delta_section(section_number),
            scope_delta,
        )
    _update_blocker_rollup(planspace)
    response = Services.pipeline_control().pause_for_parent(
        planspace,
        parent,
        f"pause:{signal}:{section_number}:{detail}",
    )
    if not response.startswith("resume"):
        return "abort"
    payload = response.partition(":")[2].strip()
    if payload:
        Services.cross_section().persist_decision(planspace, section_number, payload)
    if Services.pipeline_control().alignment_changed_pending(planspace):
        return "abort"
    return "continue"
