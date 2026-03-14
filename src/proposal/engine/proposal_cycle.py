from __future__ import annotations

from pathlib import Path

from containers import Services
from intent.service.intent_triager import load_triage_result
from orchestrator.path_registry import PathRegistry
from implementation.service.section_reexplorer import _write_alignment_surface
from proposal.service.cycle_control import (
    check_early_abort,
    check_budget_exceeded,
    dispatch_proposal,
    handle_proposal_signals,
)
from proposal.service.proposal_prep import (
    build_proposal_prompt,
    resolve_proposal_model,
)
from proposal.service.alignment_handler import (
    handle_alignment_signals,
    run_alignment_check,
)
from proposal.service.surface_handler import (
    handle_aligned_surfaces,
    handle_misaligned_surfaces,
)
from signals.types import ACTION_ABORT, ACTION_CONTINUE, TRUNCATE_DETAIL


def _check_proposal_written(
    section_number: str,
    planspace: Path,
    parent: str,
    integration_proposal: Path,
) -> bool:
    """Return True if proposal file exists; log and abort-signal if missing."""
    if integration_proposal.exists():
        return True
    Services.logger().log(
        f"Section {section_number}: ERROR — integration proposal "
        f"not written"
    )
    Services.communicator().mailbox_send(
        planspace,
        parent,
        f"fail:{section_number}:integration proposal not written",
    )
    return False


def _evaluate_alignment(
    align_result: str,
    align_output: Path,
    planspace: Path,
    parent: str,
    codespace: Path,
) -> tuple[str | None, bool]:
    """Extract problems from alignment result, handling timeout.

    Returns (problems, is_timeout).  When is_timeout is True, problems
    holds the timeout retry message.
    """
    if align_result.startswith("TIMEOUT:"):
        return "Previous alignment check timed out.", True

    policy = Services.policies().load(planspace)
    problems = Services.section_alignment().extract_problems(
        align_result,
        output_path=align_output,
        planspace=planspace,
        parent=parent,
        codespace=codespace,
        adjudicator_model=Services.policies().resolve(policy, "adjudicator"),
    )
    return problems, False


def _log_misalignment_problems(
    section_number: str,
    planspace: Path,
    parent: str,
    problems: str,
    proposal_attempt: int,
) -> None:
    """Log and notify parent about alignment problems."""
    short = problems[:TRUNCATE_DETAIL]
    Services.logger().log(
        f"Section {section_number}: integration proposal problems "
        f"(attempt {proposal_attempt}): {short}"
    )
    Services.communicator().mailbox_send(
        planspace,
        parent,
        f"summary:proposal-align:{section_number}:"
        f"PROBLEMS-attempt-{proposal_attempt}:{short}",
    )


def _run_alignment_phase(
    section,
    planspace: Path,
    codespace: Path,
    parent: str,
    align_result: str,
    align_output: Path,
    intent_mode: str,
    intent_budgets: dict,
    expansion_counts: dict[str, int],
) -> tuple[str, str | None, str]:
    """Evaluate alignment and handle surfaces.

    Returns (action, problems, intent_mode) where action is:
    - ``'abort'`` to exit loop returning None,
    - ``'continue'`` to retry with problems,
    - ``'break'`` to exit loop with success.
    """
    problems, is_timeout = _evaluate_alignment(
        align_result, align_output, planspace, parent, codespace,
    )
    if is_timeout:
        Services.logger().log(
            f"Section {section.number}: proposal alignment check "
            f"timed out — retrying"
        )
        return ACTION_CONTINUE, problems, intent_mode

    align_signal = handle_alignment_signals(
        section.number, planspace, parent, codespace,
        align_result, align_output,
    )
    if align_signal == ACTION_ABORT:
        return ACTION_ABORT, None, intent_mode
    if align_signal == ACTION_CONTINUE:
        return ACTION_CONTINUE, None, intent_mode

    if problems is None:
        action, intent_mode, reproposal_reason = handle_aligned_surfaces(
            section.number, planspace, codespace, parent,
            intent_mode, intent_budgets, expansion_counts,
        )
        if action == ACTION_ABORT:
            return ACTION_ABORT, None, intent_mode
        if action == ACTION_CONTINUE:
            return ACTION_CONTINUE, reproposal_reason, intent_mode
        _write_alignment_surface(planspace, section)
        return "break", None, intent_mode

    intent_mode = handle_misaligned_surfaces(
        section.number, planspace, codespace, parent,
        intent_mode, intent_budgets, expansion_counts,
    )
    return ACTION_CONTINUE, problems, intent_mode


def _dispatch_and_validate_proposal(
    section, planspace: Path, codespace: Path, parent: str,
    proposal_problems: str | None, incoming_notes: str | None,
    proposal_attempt: int,
) -> tuple[str, str | None]:
    """Dispatch a proposal attempt and validate the result.

    Returns (action, intg_result) where action is 'abort', 'continue',
    or 'proceed'.
    """
    integration_proposal = PathRegistry(planspace).proposal(section.number)
    proposal_model = resolve_proposal_model(
        section.number, planspace, proposal_attempt,
    )
    intg_prompt = build_proposal_prompt(
        section, planspace, codespace,
        proposal_problems, incoming_notes,
    )
    if intg_prompt is None:
        return ACTION_ABORT, None

    intg_result = dispatch_proposal(
        section.number, planspace, codespace, parent,
        proposal_model, intg_prompt, integration_proposal,
    )
    if intg_result is None:
        return ACTION_ABORT, None

    signal_action = handle_proposal_signals(
        section.number, planspace, parent, codespace, intg_result,
    )
    if signal_action == ACTION_ABORT:
        return ACTION_ABORT, None
    if signal_action == ACTION_CONTINUE:
        return ACTION_CONTINUE, None

    if not _check_proposal_written(
        section.number, planspace, parent, integration_proposal,
    ):
        return ACTION_ABORT, None

    return "proceed", intg_result


def run_proposal_loop(
    section,
    planspace: Path,
    codespace: Path,
    parent: str,
    cycle_budget: dict,
    incoming_notes: str | None,
) -> str | None:
    """Run the integration proposal loop until aligned or aborted."""
    paths = PathRegistry(planspace)
    integration_proposal = paths.proposal(section.number)
    cycle_budget_path = paths.cycle_budget(section.number)
    triage_result = load_triage_result(section.number, planspace) or {}
    intent_mode = triage_result.get("intent_mode", "lightweight")
    intent_budgets = triage_result.get("budgets", {})
    proposal_problems: str | None = None
    proposal_attempt = 0
    expansion_counts: dict[str, int] = {}

    while True:
        # --- early abort checks ---
        if check_early_abort(section.number, planspace, parent):
            return None

        proposal_attempt += 1

        # --- budget enforcement ---
        budget_result = check_budget_exceeded(
            section.number, planspace, parent,
            proposal_attempt, cycle_budget, cycle_budget_path,
        )
        if budget_result is True:
            return None
        # budget_result is False means resumed; None means not exceeded

        tag = "revise " if proposal_problems else ""
        Services.logger().log(
            f"Section {section.number}: {tag}integration proposal "
            f"(attempt {proposal_attempt})"
        )

        dispatch_action, intg_result = _dispatch_and_validate_proposal(
            section, planspace, codespace, parent,
            proposal_problems, incoming_notes, proposal_attempt,
        )
        if dispatch_action == ACTION_ABORT:
            return None
        if dispatch_action == ACTION_CONTINUE:
            continue

        # --- alignment check ---
        align_check = run_alignment_check(
            section, planspace, codespace, parent,
        )
        if align_check is None:
            return None
        align_result, align_output = align_check

        action, problems, intent_mode = _run_alignment_phase(
            section, planspace, codespace, parent,
            align_result, align_output,
            intent_mode, intent_budgets, expansion_counts,
        )
        if action == ACTION_ABORT:
            return None
        if action == "break":
            break
        # action == ACTION_CONTINUE
        proposal_problems = problems
        if problems is not None:
            _log_misalignment_problems(
                section.number, planspace, parent, problems, proposal_attempt,
            )

    return proposal_problems or ""
