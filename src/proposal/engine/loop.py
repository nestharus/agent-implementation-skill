from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

from staleness.service.change_tracker import check_pending as alignment_changed_pending
from signals.repository.artifact_io import read_json, read_json_or_default, write_json
from containers import Services
from intent.service.triage import load_triage_result
from orchestrator.path_registry import PathRegistry
from staleness.service.section_alignment import _extract_problems
from intent.service.surfaces import (
    load_combined_intent_surfaces,
    load_surface_registry,
    merge_surfaces_into_registry,
    normalize_surface_ids,
    save_surface_registry,
)
from dispatch.prompt.writers import (
    write_integration_alignment_prompt,
    write_integration_proposal_prompt,
)
from reconciliation.engine.loop import load_reconciliation_result
from signals.service.blockers import (
    _append_open_problem,
    _update_blocker_rollup,
)
from implementation.service.reexplore import _write_alignment_surface
from proposal.service.intent_expansion import run_aligned_expansion, run_misaligned_expansion


DEFINITION_GAP_KINDS = {
    "new_axis",
    "gap",
    "silence",
    "ungrounded_assumption",
}


# ---------------------------------------------------------------------------
# Surface helpers (unchanged)
# ---------------------------------------------------------------------------

def _load_combined_surfaces(section_number: str, planspace: Path) -> dict | None:
    """Load and merge all surfaces that can trigger proposal expansion."""
    return load_combined_intent_surfaces(section_number, planspace)


def _has_definition_gap_surfaces(surfaces: dict | None) -> bool:
    """Return whether any surfaced issue implies definition growth."""
    if not isinstance(surfaces, (dict, Mapping)):
        return False
    return any(
        surface.get("kind") in DEFINITION_GAP_KINDS
        for kind_key in ("problem_surfaces", "philosophy_surfaces")
        for surface in surfaces.get(kind_key, [])
        if isinstance(surface, dict)
    )


def _count_surfaces(surfaces: dict | None) -> int:
    """Count all structured surfaces in a payload."""
    if not isinstance(surfaces, (dict, Mapping)):
        return 0
    return sum(
        len(surfaces.get(kind_key, []))
        for kind_key in ("problem_surfaces", "philosophy_surfaces")
    )


def _persist_surfaces(section_number: str, planspace: Path, surfaces: dict) -> dict:
    """Normalize and persist discovered surfaces into the section registry."""
    registry = load_surface_registry(section_number, planspace)
    surfaces = normalize_surface_ids(surfaces, registry, section_number)
    merge_surfaces_into_registry(registry, surfaces)
    save_surface_registry(section_number, planspace, registry)
    return surfaces


def _write_intent_escalation_signal(
    paths: PathRegistry,
    section_number: str,
    reason: str,
    surface_count: int,
) -> None:
    """Record that lightweight intent escalated after structured discoveries."""
    escalation_signal = {
        "section": section_number,
        "reason": reason,
        "surface_count": surface_count,
    }
    write_json(
        paths.intent_escalation_signal(section_number),
        escalation_signal,
    )


# ---------------------------------------------------------------------------
# Extracted loop concerns
# ---------------------------------------------------------------------------

def _check_early_abort(
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

    if alignment_changed_pending(planspace):
        Services.logger().log(
            f"Section {section_number}: alignment changed — "
            "aborting section to restart Phase 1"
        )
        return True

    return False


def _check_budget_exceeded(
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
    write_json(budget_signal_path, budget_signal)
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
    reloaded = read_json(cycle_budget_path)
    if reloaded is not None:
        cycle_budget.update(reloaded)
    return False


def _resolve_proposal_model(
    section_number: str,
    planspace: Path,
    policy: dict,
    proposal_attempt: int,
    paths: PathRegistry,
) -> str:
    """Select the proposal model, escalating if stall conditions are met."""
    proposal_model = Services.policies().resolve(policy, "proposal")
    notes_count = 0
    notes_dir = paths.notes_dir()
    if notes_dir.exists():
        notes_count = len(list(notes_dir.glob(f"from-*-to-{section_number}.md")))
    escalated_from = None
    triggers = policy.get("escalation_triggers", {})
    max_attempts = triggers.get("max_attempts_before_escalation", 3)
    stall_threshold = triggers.get("stall_count", 2)
    if proposal_attempt >= max_attempts or notes_count >= stall_threshold:
        escalated_from = proposal_model
        proposal_model = Services.policies().resolve(policy, "escalation_model")
        Services.logger().log(
            f"Section {section_number}: escalating to "
            f"{proposal_model} (attempt={proposal_attempt}, notes={notes_count})"
        )

    reason = (
        f"attempt={proposal_attempt}, notes={notes_count}"
        if escalated_from
        else "first attempt, default model"
    )
    Services.dispatch_helpers().write_model_choice_signal(
        planspace,
        section_number,
        "integration-proposal",
        proposal_model,
        reason,
        escalated_from,
    )
    return proposal_model


def _build_proposal_prompt(
    section,
    planspace: Path,
    codespace: Path,
    proposal_problems: str | None,
    incoming_notes: str | None,
    policy: dict,
    paths: PathRegistry,
) -> Path | None:
    """Write the proposal prompt and append reconciliation context if needed.

    Returns the prompt path, or None if blocked by template safety.
    """
    artifacts = paths.artifacts
    intg_prompt = write_integration_proposal_prompt(
        section,
        planspace,
        codespace,
        proposal_problems,
        incoming_notes=incoming_notes,
        model_policy=policy,
    )
    if intg_prompt is None:
        Services.logger().log(
            f"Section {section.number}: integration proposal prompt "
            f"blocked by template safety — skipping dispatch"
        )
        return None

    recon_result = load_reconciliation_result(artifacts, section.number)
    if recon_result and recon_result.get("affected"):
        recon_path = (
            paths.reconciliation_dir()
            / f"section-{section.number}-reconciliation-result.json"
        )
        with intg_prompt.open("a", encoding="utf-8") as handle:
            handle.write(
                "\n## Reconciliation Context\n\n"
                "This section was affected by cross-section "
                "reconciliation during Phase 1b. The reconciliation "
                "analysis found overlapping anchors, contract "
                "conflicts, or shared seams involving this section.\n\n"
                "Read the reconciliation result and adjust your "
                "proposal to account for shared anchors, resolved "
                "conflicts, and seam decisions:\n"
                f"`{recon_path}`\n"
            )
        Services.logger().log(
            f"Section {section.number}: appended reconciliation "
            f"context to proposal prompt"
        )

    return intg_prompt


def _dispatch_proposal(
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
    artifacts = paths.artifacts
    intg_output = artifacts / f"intg-proposal-{section_number}-output.md"
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
    if intg_result == "ALIGNMENT_CHANGED_PENDING":
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


def _handle_proposal_signals(
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

    if signal in ("needs_parent", "out_of_scope"):
        _append_open_problem(planspace, section_number, detail, signal)
        Services.communicator().mailbox_send(
            planspace,
            parent,
            f"open-problem:{section_number}:{signal}:{detail[:200]}",
        )
    if signal == "out_of_scope":
        scope_delta_dir = paths.scope_deltas_dir()
        scope_delta_dir.mkdir(parents=True, exist_ok=True)
        proposal_sig_path = paths.proposal_signal(section_number)
        signal_payload = read_json_or_default(proposal_sig_path, {})
        scope_delta = {
            "delta_id": f"delta-{section_number}-proposal-oos",
            "section": section_number,
            "signal": "out_of_scope",
            "detail": detail,
            "requires_root_reframing": True,
            "signal_path": str(proposal_sig_path),
            "signal_payload": signal_payload,
        }
        write_json(
            scope_delta_dir / f"section-{section_number}-scope-delta.json",
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
    if alignment_changed_pending(planspace):
        return "abort"
    return "continue"


def _run_alignment_check(
    section,
    planspace: Path,
    codespace: Path,
    parent: str,
    policy: dict,
    paths: PathRegistry,
) -> tuple[str, Path] | None:
    """Dispatch the alignment judge and return (result, output_path).

    Returns None if the caller should abort (ALIGNMENT_CHANGED_PENDING).
    """
    section_number = section.number
    artifacts = paths.artifacts
    Services.logger().log(f"Section {section_number}: proposal alignment check")
    align_prompt = write_integration_alignment_prompt(
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
        Services.policies().resolve(policy, "intent_judge")
        if has_intent_artifacts
        else Services.policies().resolve(policy, "alignment")
    )
    align_result = Services.dispatcher().dispatch(
        alignment_model,
        align_prompt,
        align_output,
        planspace,
        parent,
        codespace=codespace,
        section_number=section_number,
        agent_file=alignment_agent_file,
    )
    if align_result == "ALIGNMENT_CHANGED_PENDING":
        return None

    return align_result, align_output


def _handle_alignment_signals(
    section_number: str,
    planspace: Path,
    parent: str,
    codespace: Path,
    align_result: str,
    align_output: Path,
    paths: PathRegistry,
) -> str | None:
    """Check alignment-judge signals for underspec.

    Returns:
        "continue" — underspec handled, caller should retry
        "abort" — caller should return None
        None — no underspec signal, proceed normally
    """
    signal, detail = Services.dispatch_helpers().check_agent_signals(
        align_result,
        signal_path=paths.signals_dir() / f"proposal-align-{section_number}-signal.json",
        output_path=align_output,
        planspace=planspace,
        parent=parent,
        codespace=codespace,
    )
    if signal != "underspec":
        return None

    response = Services.pipeline_control().pause_for_parent(
        planspace,
        parent,
        f"pause:underspec:{section_number}:{detail}",
    )
    if not response.startswith("resume"):
        return "abort"
    payload = response.partition(":")[2].strip()
    if payload:
        Services.cross_section().persist_decision(planspace, section_number, payload)
    if alignment_changed_pending(planspace):
        return "abort"
    return "continue"


def _handle_aligned_surfaces(
    section_number: str,
    planspace: Path,
    codespace: Path,
    parent: str,
    paths: PathRegistry,
    intent_mode: str,
    intent_budgets: dict,
    expansion_counts: dict[str, int],
) -> tuple[str, str, str | None]:
    """Handle surface processing when the proposal is aligned.

    Returns (action, updated_intent_mode, reproposal_reason) where action is:
        "break" — alignment accepted, exit loop
        "continue" — re-propose needed (reproposal_reason has the message)
        "abort" — caller should return None
    """
    surfaces = _load_combined_surfaces(section_number, planspace)
    surface_count = _count_surfaces(surfaces)
    if surface_count:
        if intent_mode != "full":
            _persist_surfaces(section_number, planspace, surfaces)
            Services.logger().log(
                f"Section {section_number}: lightweight mode discovered "
                f"{surface_count} structured surfaces — escalating to "
                "full intent"
            )
            _write_intent_escalation_signal(
                paths,
                section_number,
                "structured_surfaces_on_lightweight",
                surface_count,
            )
            return (
                "continue",
                "full",
                "Lightweight section discovered structured surfaces; "
                "re-propose under full intent mode.",
            )

        if intent_mode == "full":
            action = run_aligned_expansion(
                section_number, planspace, codespace, parent,
                intent_budgets, expansion_counts, surfaces,
                surface_count,
            )
            if action is None:
                return "abort", intent_mode, None
            if action == "continue":
                return (
                    "continue",
                    intent_mode,
                    "Intent expanded; re-propose against "
                    "updated problem/philosophy definitions.",
                )

    Services.logger().log(f"Section {section_number}: integration proposal ALIGNED")
    Services.communicator().mailbox_send(
        planspace,
        parent,
        f"summary:proposal-align:{section_number}:ALIGNED",
    )
    return "break", intent_mode, None


def _handle_misaligned_surfaces(
    section_number: str,
    planspace: Path,
    codespace: Path,
    parent: str,
    paths: PathRegistry,
    intent_mode: str,
    intent_budgets: dict,
    expansion_counts: dict[str, int],
) -> str:
    """Handle surface processing when the proposal is misaligned.

    Returns the (possibly upgraded) intent_mode.
    """
    misaligned_surfaces = _load_combined_surfaces(
        section_number, planspace,
    )
    misaligned_surface_count = _count_surfaces(misaligned_surfaces)
    if not misaligned_surface_count:
        return intent_mode

    misaligned_surfaces = _persist_surfaces(
        section_number,
        planspace,
        misaligned_surfaces,
    )
    Services.logger().log(
        f"Section {section_number}: persisted intent "
        f"surfaces from misaligned pass"
    )
    if intent_mode != "full":
        Services.logger().log(
            f"Section {section_number}: lightweight mode discovered "
            f"{misaligned_surface_count} structured surfaces on "
            "misaligned pass — upgrading to full"
        )
        _write_intent_escalation_signal(
            paths,
            section_number,
            "structured_surfaces_on_lightweight_misaligned",
            misaligned_surface_count,
        )
        intent_mode = "full"

    if intent_mode == "full" and _has_definition_gap_surfaces(
        misaligned_surfaces,
    ):
        run_misaligned_expansion(
            section_number, planspace, codespace, parent,
            intent_budgets, expansion_counts,
        )

    return intent_mode


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

def run_proposal_loop(
    section,
    planspace: Path,
    codespace: Path,
    parent: str,
    policy: dict,
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
        if _check_early_abort(section.number, planspace, parent):
            return None

        proposal_attempt += 1

        # --- budget enforcement ---
        budget_result = _check_budget_exceeded(
            section.number, planspace, parent,
            proposal_attempt, cycle_budget, paths, cycle_budget_path,
        )
        if budget_result is True:
            return None
        # budget_result is False means resumed; None means not exceeded

        tag = "revise " if proposal_problems else ""
        Services.logger().log(
            f"Section {section.number}: {tag}integration proposal "
            f"(attempt {proposal_attempt})"
        )

        # --- model selection ---
        proposal_model = _resolve_proposal_model(
            section.number, planspace, policy, proposal_attempt, paths,
        )

        # --- prompt construction ---
        intg_prompt = _build_proposal_prompt(
            section, planspace, codespace,
            proposal_problems, incoming_notes, policy, paths,
        )
        if intg_prompt is None:
            return None

        # --- proposal dispatch ---
        intg_result = _dispatch_proposal(
            section.number, planspace, codespace, parent,
            proposal_model, intg_prompt, paths, integration_proposal,
        )
        if intg_result is None:
            return None

        # --- proposal signal handling ---
        signal_action = _handle_proposal_signals(
            section.number, planspace, parent, codespace, intg_result, paths,
        )
        if signal_action == "abort":
            return None
        if signal_action == "continue":
            continue

        # --- proposal existence check ---
        if not integration_proposal.exists():
            Services.logger().log(
                f"Section {section.number}: ERROR — integration proposal "
                f"not written"
            )
            Services.communicator().mailbox_send(
                planspace,
                parent,
                f"fail:{section.number}:integration proposal not written",
            )
            return None

        # --- alignment check ---
        align_check = _run_alignment_check(
            section, planspace, codespace, parent, policy, paths,
        )
        if align_check is None:
            return None
        align_result, align_output = align_check

        if align_result.startswith("TIMEOUT:"):
            Services.logger().log(
                f"Section {section.number}: proposal alignment check "
                f"timed out — retrying"
            )
            proposal_problems = "Previous alignment check timed out."
            continue

        problems = _extract_problems(
            align_result,
            output_path=align_output,
            planspace=planspace,
            parent=parent,
            codespace=codespace,
            adjudicator_model=Services.policies().resolve(policy, "adjudicator"),
        )

        # --- alignment signal handling ---
        align_signal = _handle_alignment_signals(
            section.number, planspace, parent, codespace,
            align_result, align_output, paths,
        )
        if align_signal == "abort":
            return None
        if align_signal == "continue":
            continue

        # --- aligned path: surface handling ---
        if problems is None:
            action, intent_mode, reproposal_reason = _handle_aligned_surfaces(
                section.number, planspace, codespace, parent, paths,
                intent_mode, intent_budgets, expansion_counts,
            )
            if action == "abort":
                return None
            if action == "continue":
                proposal_problems = reproposal_reason
                continue
            # action == "break"
            _write_alignment_surface(planspace, section)
            break

        # --- misaligned path: surface handling ---
        intent_mode = _handle_misaligned_surfaces(
            section.number, planspace, codespace, parent, paths,
            intent_mode, intent_budgets, expansion_counts,
        )

        proposal_problems = problems
        short = problems[:200]
        Services.logger().log(
            f"Section {section.number}: integration proposal problems "
            f"(attempt {proposal_attempt}): {short}"
        )
        Services.communicator().mailbox_send(
            planspace,
            parent,
            f"summary:proposal-align:{section.number}:"
            f"PROBLEMS-attempt-{proposal_attempt}:{short}",
        )

    return proposal_problems or ""
