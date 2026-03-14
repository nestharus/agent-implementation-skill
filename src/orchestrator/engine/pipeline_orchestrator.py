import logging
import sys
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="[%(name)s] %(message)s",
    stream=sys.stderr,
)

from intake.service.assessment_evaluator import promote_debt_signals
from intake.repository.governance_loader import bootstrap_governance_if_missing, build_governance_indexes
from coordination.engine.coordination_controller import run_coordination_loop
from implementation.engine.implementation_phase import (
    ImplementationPassExit,
    ImplementationPassRestart,
    run_implementation_pass,
)
from orchestrator.path_registry import PathRegistry
from scan.service.project_mode import resolve_project_mode, write_mode_contract
from proposal.engine.proposal_phase import ProposalPassExit, run_proposal_pass
from reconciliation.engine.reconciliation_phase import ReconciliationPhaseExit, run_reconciliation_phase
from scan.service.section_loader import load_sections

from _config import AGENT_NAME
from flow.service.task_db_client import init_db

from containers import Services
from signals.types import TRUNCATE_SUMMARY
from orchestrator.engine.strategic_state_builder import build_strategic_state
from orchestrator.types import SectionResult

_MAX_BLOCKERS_IN_SUMMARY = 3


_check_and_clear_alignment_changed = Services.change_tracker().make_alignment_checker()


def main() -> None:
    """Run the section loop orchestrator CLI."""
    import argparse

    parser = argparse.ArgumentParser(
        description="Section loop orchestrator for the implementation pipeline.",
    )
    parser.add_argument("planspace", type=Path,
                        help="Path to the planspace directory")
    parser.add_argument("codespace", type=Path,
                        help="Path to the codespace directory")
    parser.add_argument("--global-proposal", type=Path, required=True,
                        dest="global_proposal",
                        help="Path to the global proposal document")
    parser.add_argument("--global-alignment", type=Path, required=True,
                        dest="global_alignment",
                        help="Path to the global alignment document")
    parser.add_argument("--parent", type=str, default="orchestrator",
                        help="Parent agent mailbox name (default: orchestrator)")

    args = parser.parse_args()

    # Validate paths
    if not args.global_proposal.exists():
        print(f"Error: global proposal not found: {args.global_proposal}")
        sys.exit(1)
    if not args.global_alignment.exists():
        print(f"Error: global alignment not found: {args.global_alignment}")
        sys.exit(1)

    paths = PathRegistry(args.planspace)
    sections_dir = paths.sections_dir()

    # Initialize coordination DB (idempotent) and register
    init_db(paths.run_db())
    Services.communicator().mailbox_register(args.planspace)
    Services.logger().log(f"Registered: {AGENT_NAME} (parent: {args.parent})")

    try:
        _run_loop(args.planspace, args.codespace, args.parent, sections_dir,
                  args.global_proposal, args.global_alignment)
    finally:
        Services.communicator().mailbox_cleanup(args.planspace)
        Services.logger().log("Mailbox cleaned up")


def _record_blocked_sections(
    blocked_sections: list[str],
    proposal_results: dict,
    section_results: dict[str, SectionResult],
) -> None:
    """Record blocked sections as non-aligned results for Phase 2."""
    for sec_num in blocked_sections:
        pr = proposal_results[sec_num]
        blocker_summary = "; ".join(
            b.get("description", "unknown")[:TRUNCATE_SUMMARY]
            for b in pr.blockers[:_MAX_BLOCKERS_IN_SUMMARY]
        ) or "execution not ready"
        section_results.setdefault(sec_num, SectionResult(
            section_number=sec_num,
            aligned=False,
            problems=f"readiness blocked: {blocker_summary}",
        ))


def _run_phase2(
    all_sections: list,
    sections_by_num: dict,
    section_results: dict[str, SectionResult],
    planspace: Path,
    codespace: Path,
    parent: str,
) -> str:
    """Run Phase 2: strategic state, global recheck, and coordination.

    Returns 'restart_phase1', 'done', or a coordination status.
    """
    build_strategic_state(PathRegistry(planspace).decisions_dir(), section_results, planspace)

    promoted = promote_debt_signals(planspace)
    if promoted:
        Services.logger().log(f"Stabilization: promoted {len(promoted)} debt entries to staging")

    phase2_status = Services.section_alignment().run_global_recheck(
        sections_by_num, section_results, planspace, codespace, parent,
    )
    if phase2_status == "restart_phase1":
        return "restart_phase1"

    coordination_status = run_coordination_loop(
        all_sections, section_results, sections_by_num,
        planspace, codespace, parent,
    )
    return coordination_status or "done"


def _run_loop(planspace: Path, codespace: Path, parent: str,
              sections_dir: Path, global_proposal: Path,
              global_alignment: Path) -> None:
    paths = PathRegistry(planspace)
    bootstrap_governance_if_missing(codespace)
    build_governance_indexes(codespace, planspace)
    project_mode, mode_constraints = resolve_project_mode(planspace, parent)
    write_mode_contract(planspace, project_mode, mode_constraints)

    all_sections = load_sections(sections_dir)
    for sec in all_sections:
        sec.global_proposal_path = global_proposal
        sec.global_alignment_path = global_alignment
    sections_by_num = {s.number: s for s in all_sections}
    Services.logger().log(f"Loaded {len(all_sections)} sections")

    while True:
        section_results: dict[str, SectionResult] = {}
        try:
            proposal_results = run_proposal_pass(
                all_sections, sections_by_num, planspace, codespace, parent,
            )
        except ProposalPassExit:
            return

        try:
            reconciliation = run_reconciliation_phase(
                proposal_results, sections_by_num, all_sections,
                planspace, codespace, parent,
            )
        except ReconciliationPhaseExit:
            return

        blocked_sections = reconciliation.removed_section_numbers
        if reconciliation.alignment_changed:
            continue

        try:
            section_results.update(
                run_implementation_pass(
                    proposal_results, sections_by_num,
                    planspace, codespace, parent,
                ),
            )
        except ImplementationPassRestart:
            continue
        except ImplementationPassExit:
            return

        _record_blocked_sections(blocked_sections, proposal_results, section_results)

        implemented_sections = [
            sec_num for sec_num, result in section_results.items()
            if result.aligned
        ]
        Services.logger().log(f"=== Phase 1 complete: {len(implemented_sections)} sections "
            f"implemented, {len(blocked_sections)} blocked ===")

        status = _run_phase2(
            all_sections, sections_by_num, section_results,
            planspace, codespace, parent,
        )
        if status == "restart_phase1":
            continue
        return


if __name__ == "__main__":
    main()
