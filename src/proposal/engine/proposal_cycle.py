from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from containers import Services
from dispatch.types import DispatchResult, DispatchStatus
from intent.service.intent_triager import load_triage_result
from orchestrator.path_registry import PathRegistry
from pipeline.context import DispatchContext
from implementation.service.section_reexplorer import write_alignment_surface
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


@dataclass(frozen=True)
class AlignmentPhaseResult:
    """Result of the alignment evaluation phase."""

    action: str
    problems: str | None = None
    intent_mode: str = ""


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
    align_result: DispatchResult,
    align_output: Path,
    ctx: DispatchContext,
) -> tuple[str | None, bool]:
    """Extract problems from alignment result, handling timeout.

    Returns (problems, is_timeout).  When is_timeout is True, problems
    holds the timeout retry message.
    """
    if align_result.status is DispatchStatus.TIMEOUT:
        return "Previous alignment check timed out.", True

    problems = Services.section_alignment().extract_problems(
        align_result.output,
        output_path=align_output,
        planspace=ctx.planspace,
        parent=ctx.parent,
        codespace=ctx.codespace,
        adjudicator_model=ctx.resolve_model("adjudicator"),
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
    ctx: DispatchContext,
    align_result: DispatchResult,
    align_output: Path,
    intent_mode: str,
    intent_budgets: dict,
    expansion_counts: dict[str, int],
) -> AlignmentPhaseResult:
    """Evaluate alignment and handle surfaces.

    Returns an ``AlignmentPhaseResult`` with action:
    - ``'abort'`` to exit loop returning None,
    - ``'continue'`` to retry with problems,
    - ``'break'`` to exit loop with success.
    """
    problems, is_timeout = _evaluate_alignment(
        align_result, align_output, ctx,
    )
    if is_timeout:
        Services.logger().log(
            f"Section {section.number}: proposal alignment check "
            f"timed out — retrying"
        )
        return AlignmentPhaseResult(ACTION_CONTINUE, problems, intent_mode)

    align_signal = handle_alignment_signals(
        section.number, ctx.planspace, ctx.parent,
    )
    if align_signal == ACTION_ABORT:
        return AlignmentPhaseResult(ACTION_ABORT, intent_mode=intent_mode)
    if align_signal == ACTION_CONTINUE:
        return AlignmentPhaseResult(ACTION_CONTINUE, intent_mode=intent_mode)

    if problems is None:
        surface_result = handle_aligned_surfaces(
            section.number, ctx.planspace, ctx.codespace, ctx.parent,
            intent_mode, intent_budgets, expansion_counts,
        )
        if surface_result.action == ACTION_ABORT:
            return AlignmentPhaseResult(ACTION_ABORT, intent_mode=surface_result.intent_mode)
        if surface_result.action == ACTION_CONTINUE:
            return AlignmentPhaseResult(
                ACTION_CONTINUE, surface_result.reproposal_reason,
                surface_result.intent_mode,
            )
        write_alignment_surface(ctx.planspace, section)
        return AlignmentPhaseResult("break", intent_mode=surface_result.intent_mode)

    intent_mode = handle_misaligned_surfaces(
        section.number, ctx.planspace, ctx.codespace, ctx.parent,
        intent_mode, intent_budgets, expansion_counts,
    )
    return AlignmentPhaseResult(ACTION_CONTINUE, problems, intent_mode)


def _dispatch_and_validate_proposal(
    section, ctx: DispatchContext,
    proposal_problems: str | None, incoming_notes: str | None,
    proposal_attempt: int,
) -> tuple[str, str | None]:
    """Dispatch a proposal attempt and validate the result.

    Returns (action, intg_result) where action is 'abort', 'continue',
    or 'proceed'.
    """
    integration_proposal = PathRegistry(ctx.planspace).proposal(section.number)
    proposal_model = resolve_proposal_model(
        section.number, ctx.planspace, proposal_attempt,
    )
    intg_prompt = build_proposal_prompt(
        section, ctx.planspace, ctx.codespace,
        proposal_problems, incoming_notes,
    )
    if intg_prompt is None:
        return ACTION_ABORT, None

    intg_result = dispatch_proposal(
        section.number, ctx.planspace, ctx.codespace, ctx.parent,
        proposal_model, intg_prompt, integration_proposal,
    )
    if intg_result is None:
        return ACTION_ABORT, None

    signal_action = handle_proposal_signals(
        section.number, ctx.planspace, ctx.parent,
    )
    if signal_action == ACTION_ABORT:
        return ACTION_ABORT, None
    if signal_action == ACTION_CONTINUE:
        return ACTION_CONTINUE, None

    if not _check_proposal_written(
        section.number, ctx.planspace, ctx.parent, integration_proposal,
    ):
        return ACTION_ABORT, None

    return "proceed", intg_result


def run_proposal_loop(
    section,
    ctx: DispatchContext,
    cycle_budget: dict,
    incoming_notes: str | None,
) -> str | None:
    """Run the integration proposal loop until aligned or aborted."""
    paths = PathRegistry(ctx.planspace)
    cycle_budget_path = paths.cycle_budget(section.number)
    triage_result = load_triage_result(section.number, ctx.planspace) or {}
    intent_mode = triage_result.get("intent_mode", "lightweight")
    intent_budgets = triage_result.get("budgets", {})
    proposal_problems: str | None = None
    proposal_attempt = 0
    expansion_counts: dict[str, int] = {}

    while True:
        # --- early abort checks ---
        if check_early_abort(section.number, ctx.planspace, ctx.parent):
            return None

        proposal_attempt += 1

        # --- budget enforcement ---
        budget_result = check_budget_exceeded(
            section.number, ctx.planspace, ctx.parent,
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

        dispatch_action, _ = _dispatch_and_validate_proposal(
            section, ctx,
            proposal_problems, incoming_notes, proposal_attempt,
        )
        if dispatch_action == ACTION_ABORT:
            return None
        if dispatch_action == ACTION_CONTINUE:
            continue

        # --- alignment check ---
        align_check = run_alignment_check(
            section, ctx.planspace, ctx.codespace, ctx.parent,
        )
        if align_check is None:
            return None
        align_result, align_output = align_check

        phase = _run_alignment_phase(
            section, ctx,
            align_result, align_output,
            intent_mode, intent_budgets, expansion_counts,
        )
        intent_mode = phase.intent_mode
        if phase.action == ACTION_ABORT:
            return None
        if phase.action == "break":
            break
        # action == ACTION_CONTINUE
        proposal_problems = phase.problems
        if phase.problems is not None:
            _log_misalignment_problems(
                section.number, ctx.planspace, ctx.parent,
                phase.problems, proposal_attempt,
            )

    return proposal_problems or ""
