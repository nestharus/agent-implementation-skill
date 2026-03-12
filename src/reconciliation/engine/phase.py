"""Reconciliation and re-proposal phase helpers for the section loop."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from staleness.service.change_tracker import (
    check_pending as alignment_changed_pending,
    make_alignment_checker,
)
from containers import Services
from signals.service.communication import AGENT_NAME, DB_SH
from reconciliation.engine.loop import run_reconciliation
from implementation.engine.runner import run_section
from orchestrator.types import ProposalPassResult, Section


@dataclass(frozen=True)
class ReconciliationResult:
    """Structured result from ``run_reconciliation_phase``."""

    new_section_numbers: list[str] = field(default_factory=list)
    removed_section_numbers: list[str] = field(default_factory=list)
    alignment_changed: bool = False


class ReconciliationPhaseExit(Exception):
    """Raised when the reconciliation phase should stop the outer loop."""


def run_reconciliation_phase(
    proposal_results: dict[str, ProposalPassResult],
    sections_by_num: dict[str, Section],
    all_sections: list[Section],
    planspace: Path,
    codespace: Path,
    parent: str,
    policy: dict,
) -> ReconciliationResult:
    """Run reconciliation blocking and any required re-proposal passes."""
    del policy

    ready_sections = sorted(
        num for num, proposal_result in proposal_results.items()
        if proposal_result.execution_ready
    )
    blocked_sections = sorted(
        num for num, proposal_result in proposal_results.items()
        if not proposal_result.execution_ready
    )

    recon_summary = run_reconciliation(
        planspace,
        list(proposal_results.values()),
    )
    Services.logger().log(
        f"Phase 1b reconciliation: {recon_summary['conflicts_found']} conflicts, "
        f"{recon_summary['new_sections_proposed']} new-section proposals, "
        f"substrate_needed={recon_summary['substrate_needed']}, "
        f"affected sections={recon_summary['sections_affected']}",
    )

    reconciliation_blocked: set[str] = set()
    for sec_num in recon_summary.get("sections_affected", []):
        if sec_num in proposal_results:
            proposal_result = proposal_results[sec_num]
            if proposal_result.execution_ready:
                proposal_result.execution_ready = False
                proposal_result.needs_reconciliation = True
                proposal_result.blockers.append({
                    "type": "reconciliation",
                    "description": (
                        "Section affected by cross-section reconciliation — "
                        f"{recon_summary['conflicts_found']} conflict(s) found"
                    ),
                })
                reconciliation_blocked.add(sec_num)
                Services.logger().log(f"Section {sec_num}: blocked by reconciliation")

    ready_sections = sorted(
        num for num, proposal_result in proposal_results.items()
        if proposal_result.execution_ready
    )
    blocked_sections = sorted(
        num for num, proposal_result in proposal_results.items()
        if not proposal_result.execution_ready
    )
    if reconciliation_blocked:
        Services.logger().log(
            f"Reconciliation blocked {len(reconciliation_blocked)} additional "
            f"sections: {sorted(reconciliation_blocked)}",
        )
        Services.logger().log(
            f"Updated proposal summary: {len(ready_sections)} ready, "
            f"{len(blocked_sections)} blocked",
        )

    reproposal_sections = sorted(reconciliation_blocked)
    restart_phase1 = False
    if reproposal_sections:
        Services.logger().log(
            f"=== Phase 1b.2: re-proposal pass for {len(reproposal_sections)} "
            "reconciliation-affected sections ===",
        )

        for sec_num in reproposal_sections:
            if Services.pipeline_control().handle_pending_messages(planspace, [], set()):
                Services.logger().log("Aborted by parent during re-proposal pass")
                Services.communicator().mailbox_send(planspace, parent, "fail:aborted")
                raise ReconciliationPhaseExit

            if alignment_changed_pending(planspace):
                if _check_and_clear_alignment_changed(planspace):
                    Services.logger().log("Alignment changed during re-proposal pass — restarting from Phase 1")
                    restart_phase1 = True
                    break

            section = sections_by_num[sec_num]
            Services.logger().log(f"=== Section {sec_num} re-proposal pass (reconciliation-affected) ===")

            reproposal_result = run_section(
                planspace,
                codespace,
                section,
                parent,
                all_sections=all_sections,
                pass_mode="proposal",
            )

            if _check_and_clear_alignment_changed(planspace):
                Services.logger().log("Alignment changed during re-proposal — restarting from Phase 1")
                restart_phase1 = True
                break

            if reproposal_result is None:
                Services.logger().log(f"Section {sec_num}: paused during re-proposal")
                continue

            if isinstance(reproposal_result, ProposalPassResult):
                proposal_results[sec_num] = reproposal_result
                status = (
                    "ready"
                    if reproposal_result.execution_ready
                    else f"still blocked ({len(reproposal_result.blockers)} blockers)"
                )
                Services.logger().log(f"Section {sec_num}: re-proposal complete — {status}")
                Services.communicator().mailbox_send(planspace, parent, f"reproposal-done:{sec_num}:{status}")

    if reproposal_sections:
        ready_sections = sorted(
            num for num, proposal_result in proposal_results.items()
            if proposal_result.execution_ready
        )
        blocked_sections = sorted(
            num for num, proposal_result in proposal_results.items()
            if not proposal_result.execution_ready
        )
        Services.logger().log(
            f"Post-reproposal summary: {len(ready_sections)} ready, "
            f"{len(blocked_sections)} blocked",
        )
        if blocked_sections:
            Services.logger().log(f"Still blocked after re-proposal: {blocked_sections}")

    return ReconciliationResult(
        new_section_numbers=ready_sections,
        removed_section_numbers=blocked_sections,
        alignment_changed=restart_phase1,
    )


_check_and_clear_alignment_changed = make_alignment_checker(DB_SH, AGENT_NAME)
