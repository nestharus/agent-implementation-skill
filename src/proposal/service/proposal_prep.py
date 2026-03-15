"""Model selection and prompt construction for the proposal loop.

Extracted from proposal_cycle.py to isolate model policy resolution
and prompt assembly from the main loop orchestration.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from containers import DispatchHelperService, LogService, ModelPolicyService
    from dispatch.prompt.writers import Writers as PromptWriters
    from reconciliation.repository.results import Results

from orchestrator.path_registry import PathRegistry


class ProposalPrep:
    def __init__(
        self,
        logger: LogService,
        policies: ModelPolicyService,
        dispatch_helpers: DispatchHelperService,
        reconciliation_results: Results,
        prompt_writers: PromptWriters,
    ) -> None:
        self._logger = logger
        self._policies = policies
        self._dispatch_helpers = dispatch_helpers
        self._reconciliation_results = reconciliation_results
        self._prompt_writers = prompt_writers

    def resolve_proposal_model(
        self,
        section_number: str,
        planspace: Path,
        proposal_attempt: int,
    ) -> str:
        """Select the proposal model, escalating if stall conditions are met."""
        paths = PathRegistry(planspace)
        policy = self._policies.load(planspace)
        proposal_model = self._policies.resolve(policy, "proposal")
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
            proposal_model = self._policies.resolve(policy, "escalation_model")
            self._logger.log(
                f"Section {section_number}: escalating to "
                f"{proposal_model} (attempt={proposal_attempt}, notes={notes_count})"
            )

        reason = (
            f"attempt={proposal_attempt}, notes={notes_count}"
            if escalated_from
            else "first attempt, default model"
        )
        self._dispatch_helpers.write_model_choice_signal(
            planspace,
            section_number,
            "integration-proposal",
            proposal_model,
            reason,
            escalated_from,
        )
        return proposal_model

    def build_proposal_prompt(
        self,
        section,
        planspace: Path,
        codespace: Path,
        proposal_problems: str | None,
        incoming_notes: str | None,
    ) -> Path | None:
        """Write the proposal prompt and append reconciliation context if needed.

        Returns the prompt path, or None if blocked by template safety.
        """
        intg_prompt = self._prompt_writers.write_integration_proposal_prompt(
            section,
            planspace,
            codespace,
            proposal_problems,
            incoming_notes=incoming_notes,
        )
        if intg_prompt is None:
            self._logger.log(
                f"Section {section.number}: integration proposal prompt "
                f"blocked by template safety — skipping dispatch"
            )
            return None

        paths = PathRegistry(planspace)
        recon_result = self._reconciliation_results.load_result(planspace, section.number)
        if recon_result and recon_result.get("affected"):
            recon_path = paths.reconciliation_result(section.number)
            with intg_prompt.open("a", encoding="utf-8") as handle:
                handle.write(_compose_proposal_text(recon_path))
            self._logger.log(
                f"Section {section.number}: appended reconciliation "
                f"context to proposal prompt"
            )

        return intg_prompt


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
