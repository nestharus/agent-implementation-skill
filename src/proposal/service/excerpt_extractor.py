from __future__ import annotations

from pathlib import Path

from proposal.repository.excerpts import exists as excerpt_exists
from orchestrator.path_registry import PathRegistry
from containers import Services
from dispatch.prompt.writers import write_section_setup_prompt
from signals.service.blocker_manager import (
    append_open_problem,
    update_blocker_rollup,
)
from dispatch.types import ALIGNMENT_CHANGED_PENDING
from proposal.service.cycle_control import handle_pause_response, write_scope_delta
from signals.types import ACTION_ABORT, SIGNAL_NEEDS_PARENT, SIGNAL_OUT_OF_SCOPE, TRUNCATE_DETAIL


def _handle_setup_signal(
    signal: str, detail: str, planspace: Path, parent: str,
    section_number: str,
) -> str | None:
    """Handle a setup agent signal. Returns 'abort' or 'continue'."""
    if signal in (SIGNAL_NEEDS_PARENT, SIGNAL_OUT_OF_SCOPE):
        append_open_problem(planspace, section_number, detail, signal)
        Services.communicator().mailbox_send(
            planspace, parent,
            f"open-problem:{section_number}:{signal}:{detail[:TRUNCATE_DETAIL]}",
        )
    if signal == SIGNAL_OUT_OF_SCOPE:
        sig_path = PathRegistry(planspace).signals_dir() / f"setup-{section_number}-signal.json"
        write_scope_delta(planspace, sig_path, section_number, detail, "setup")
    update_blocker_rollup(planspace)
    response = Services.pipeline_control().pause_for_parent(
        planspace, parent,
        f"pause:{signal}:{section_number}:{detail}",
    )
    return handle_pause_response(planspace, section_number, response)


def extract_excerpts(
    section,
    planspace: Path,
    codespace: Path,
    parent: str,
) -> str | None:
    """Run the setup loop until both proposal and alignment excerpts exist."""
    policy = Services.policies().load(planspace)
    paths = PathRegistry(planspace)
    signal_dir = paths.signals_dir()

    while (
        not excerpt_exists(planspace, section.number, "proposal")
        or not excerpt_exists(planspace, section.number, "alignment")
    ):
        Services.logger().log(f"Section {section.number}: setup — extracting excerpts")
        setup_prompt = write_section_setup_prompt(
            section, planspace, codespace,
            section.global_proposal_path, section.global_alignment_path,
        )
        setup_output = paths.artifacts / f"setup-{section.number}-output.md"
        setup_agent = f"setup-{section.number}"
        output = Services.dispatcher().dispatch(
            policy["setup"], setup_prompt, setup_output,
            planspace, parent, setup_agent,
            codespace=codespace, section_number=section.number,
            agent_file=Services.task_router().agent_for("proposal.section_setup"),
        )
        if output == ALIGNMENT_CHANGED_PENDING:
            Services.logger().log(f"Section {section.number}: alignment changed during setup dispatch — aborting")
            return None
        Services.communicator().mailbox_send(
            planspace, parent,
            f"summary:setup:{section.number}:{Services.dispatch_helpers().summarize_output(output.output)}",
        )

        signal, detail = Services.dispatch_helpers().check_agent_signals(
            signal_path=signal_dir / f"setup-{section.number}-signal.json",
        )
        if signal:
            result = _handle_setup_signal(
                signal, detail, planspace, parent,
                section.number,
            )
            if result == ACTION_ABORT:
                return None
            continue

        if (
            not excerpt_exists(planspace, section.number, "proposal")
            or not excerpt_exists(planspace, section.number, "alignment")
        ):
            Services.logger().log(
                f"Section {section.number}: ERROR — setup failed to create "
                f"excerpt files"
            )
            Services.communicator().mailbox_send(
                planspace, parent,
                f"fail:{section.number}:setup failed to create excerpt files",
            )
            return None
        break

    return "ok"
