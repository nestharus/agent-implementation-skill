"""Reconciliation and re-proposal phase helpers for the section loop."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from containers import Services
from reconciliation.engine.cross_section_reconciler import run_reconciliation_loop
from implementation.engine.section_pipeline import run_section
from orchestrator.types import ProposalPassResult, Section
from signals.types import PASS_MODE_PROPOSAL


@dataclass(frozen=True)
class ReconciliationResult:
    """Structured result from ``run_reconciliation_phase``."""

    new_section_numbers: list[str] = field(default_factory=list)
    removed_section_numbers: list[str] = field(default_factory=list)
    alignment_changed: bool = False


class ReconciliationPhaseExit(Exception):
    """Raised when the reconciliation phase should stop the outer loop."""


def _partition_sections(
    proposal_results: dict[str, ProposalPassResult],
) -> tuple[list[str], list[str]]:
    ready = sorted(
        num for num, pr in proposal_results.items() if pr.execution_ready
    )
    blocked = sorted(
        num for num, pr in proposal_results.items() if not pr.execution_ready
    )
    return ready, blocked


def _apply_reconciliation_blocks(
    proposal_results: dict[str, ProposalPassResult],
    recon_summary: dict,
) -> set[str]:
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
    return reconciliation_blocked


def _run_reproposal_loop(
    reproposal_sections: list[str],
    proposal_results: dict[str, ProposalPassResult],
    sections_by_num: dict[str, Section],
    all_sections: list[Section],
    planspace: Path,
    codespace: Path,
    parent: str,
) -> bool:
    for sec_num in reproposal_sections:
        if Services.pipeline_control().handle_pending_messages(planspace, [], set()):
            Services.logger().log("Aborted by parent during re-proposal pass")
            Services.communicator().mailbox_send(planspace, parent, "fail:aborted")
            raise ReconciliationPhaseExit

        if Services.pipeline_control().check_alignment_and_return(
            planspace, _check_and_clear_alignment_changed,
        ):
            Services.logger().log("Alignment changed during re-proposal pass — restarting from Phase 1")
            return True

        section = sections_by_num[sec_num]
        Services.logger().log(f"=== Section {sec_num} re-proposal pass (reconciliation-affected) ===")

        reproposal_result = run_section(
            planspace,
            codespace,
            section,
            parent,
            all_sections=all_sections,
            pass_mode=PASS_MODE_PROPOSAL,
        )

        if Services.pipeline_control().check_alignment_and_return(
            planspace, _check_and_clear_alignment_changed,
        ):
            Services.logger().log("Alignment changed during re-proposal — restarting from Phase 1")
            return True

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

    return False


def run_reconciliation_phase(
    proposal_results: dict[str, ProposalPassResult],
    sections_by_num: dict[str, Section],
    all_sections: list[Section],
    planspace: Path,
    codespace: Path,
    parent: str,
) -> ReconciliationResult:
    """Run reconciliation blocking and any required re-proposal passes."""

    ready_sections, blocked_sections = _partition_sections(proposal_results)

    recon_summary = run_reconciliation_loop(
        planspace,
        list(proposal_results.values()),
    )
    Services.logger().log(
        f"Phase 1b reconciliation: {recon_summary['conflicts_found']} conflicts, "
        f"{recon_summary['new_sections_proposed']} new-section proposals, "
        f"substrate_needed={recon_summary['substrate_needed']}, "
        f"affected sections={recon_summary['sections_affected']}",
    )

    reconciliation_blocked = _apply_reconciliation_blocks(proposal_results, recon_summary)

    ready_sections, blocked_sections = _partition_sections(proposal_results)
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

        restart_phase1 = _run_reproposal_loop(
            reproposal_sections,
            proposal_results,
            sections_by_num,
            all_sections,
            planspace,
            codespace,
            parent,
        )

    if reproposal_sections:
        ready_sections, blocked_sections = _partition_sections(proposal_results)
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


_check_and_clear_alignment_changed = Services.change_tracker().make_alignment_checker()
