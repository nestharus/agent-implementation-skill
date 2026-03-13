"""Model selection and prompt construction for the proposal loop.

Extracted from proposal_cycle.py to isolate model policy resolution
and prompt assembly from the main loop orchestration.
"""

from __future__ import annotations

from pathlib import Path

from containers import Services
from orchestrator.path_registry import PathRegistry
from dispatch.prompt.writers import write_integration_proposal_prompt
from reconciliation.engine.cross_section_reconciler import load_reconciliation_result


def resolve_proposal_model(
    section_number: str,
    planspace: Path,
    proposal_attempt: int,
    paths: PathRegistry,
) -> str:
    """Select the proposal model, escalating if stall conditions are met."""
    policy = Services.policies().load(planspace)
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


def _compose_proposal_text(recon_path: Path) -> str:
    """Build the reconciliation context appendix for a proposal prompt."""
    return (
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


def build_proposal_prompt(
    section,
    planspace: Path,
    codespace: Path,
    proposal_problems: str | None,
    incoming_notes: str | None,
    paths: PathRegistry,
) -> Path | None:
    """Write the proposal prompt and append reconciliation context if needed.

    Returns the prompt path, or None if blocked by template safety.
    """
    intg_prompt = write_integration_proposal_prompt(
        section,
        planspace,
        codespace,
        proposal_problems,
        incoming_notes=incoming_notes,
    )
    if intg_prompt is None:
        Services.logger().log(
            f"Section {section.number}: integration proposal prompt "
            f"blocked by template safety — skipping dispatch"
        )
        return None

    recon_result = load_reconciliation_result(planspace, section.number)
    if recon_result and recon_result.get("affected"):
        recon_path = paths.reconciliation_result(section.number)
        with intg_prompt.open("a", encoding="utf-8") as handle:
            handle.write(_compose_proposal_text(recon_path))
        Services.logger().log(
            f"Section {section.number}: appended reconciliation "
            f"context to proposal prompt"
        )

    return intg_prompt
