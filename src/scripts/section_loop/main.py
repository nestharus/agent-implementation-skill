import subprocess
import sys
from pathlib import Path

from lib.services.alignment_change_tracker import check_and_clear
from lib.pipelines.coordination_loop import run_coordination_loop
from lib.pipelines.global_alignment_recheck import run_global_alignment_recheck
from lib.pipelines.implementation_pass import (
    ImplementationPassExit,
    ImplementationPassRestart,
    run_implementation_pass,
)
from lib.core.path_registry import PathRegistry
from lib.sections.project_mode import resolve_project_mode, write_mode_contract
from lib.pipelines.proposal_pass import ProposalPassExit, run_proposal_pass
from lib.pipelines.reconciliation_phase import ReconciliationPhaseExit, run_reconciliation_phase
from lib.sections.section_loader import load_sections

from .communication import (
    AGENT_NAME,
    DB_SH,
    log,
    mailbox_cleanup,
    mailbox_register,
)
from .dispatch import dispatch_agent, read_model_policy
from lib.repositories.strategic_state import build_strategic_state
from .types import SectionResult


def _check_and_clear_alignment_changed(planspace: Path) -> bool:
    return check_and_clear(planspace, db_sh=DB_SH, agent_name=AGENT_NAME)


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
    subprocess.run(  # noqa: S603
        ["bash", str(DB_SH), "init", str(paths.run_db())],  # noqa: S607
        check=True, capture_output=True, text=True,
    )
    mailbox_register(args.planspace)
    log(f"Registered: {AGENT_NAME} (parent: {args.parent})")

    try:
        _run_loop(args.planspace, args.codespace, args.parent, sections_dir,
                  args.global_proposal, args.global_alignment)
    finally:
        mailbox_cleanup(args.planspace)
        log("Mailbox cleaned up")


def _run_loop(planspace: Path, codespace: Path, parent: str,
              sections_dir: Path, global_proposal: Path,
              global_alignment: Path) -> None:
    paths = PathRegistry(planspace)
    project_mode, mode_constraints = resolve_project_mode(planspace, parent)
    write_mode_contract(planspace, project_mode, mode_constraints)

    # Load sections and build cross-reference map
    all_sections = load_sections(sections_dir)

    # Attach global document paths to each section
    for sec in all_sections:
        sec.global_proposal_path = global_proposal
        sec.global_alignment_path = global_alignment

    sections_by_num = {s.number: s for s in all_sections}

    log(f"Loaded {len(all_sections)} sections")

    # Read model policy once — used for re-exploration dispatch.
    policy = read_model_policy(planspace)

    # Outer loop: alignment_changed during Phase 2 restarts from Phase 1.
    # Each iteration runs Phase 1 (per-section) then Phase 2 (global).
    # The loop exits on: complete, fail, abort, or exhaustion.
    while True:

        # -----------------------------------------------------------------
        # Phase 1a: Proposal pass — all sections
        # -----------------------------------------------------------------
        # Run every section through exploration, proposal, alignment, and
        # readiness resolution.  No code files are modified.  Each section
        # yields a ProposalPassResult with readiness disposition.
        section_results: dict[str, SectionResult] = {}
        try:
            proposal_results = run_proposal_pass(
                all_sections,
                sections_by_num,
                planspace,
                codespace,
                parent,
                policy,
            )
        except ProposalPassExit:
            return

        try:
            ready_sections, blocked_sections, reproposal_restart_phase1 = (
                run_reconciliation_phase(
                    proposal_results,
                    sections_by_num,
                    all_sections,
                    planspace,
                    codespace,
                    parent,
                    policy,
                )
            )
        except ReconciliationPhaseExit:
            return

        if reproposal_restart_phase1:
            continue  # outer while True → restart Phase 1

        # -----------------------------------------------------------------
        # Phase 1c: Implementation pass — execution-ready sections only
        # -----------------------------------------------------------------
        try:
            section_results.update(
                run_implementation_pass(
                    proposal_results,
                    sections_by_num,
                    planspace,
                    codespace,
                    parent,
                ),
            )
        except ImplementationPassRestart:
            continue
        except ImplementationPassExit:
            return

        # Record blocked sections (proposal aligned but not
        # execution-ready) as non-aligned results for Phase 2 to handle
        for sec_num in blocked_sections:
            pr = proposal_results[sec_num]
            blocker_summary = "; ".join(
                b.get("description", "unknown")[:80]
                for b in pr.blockers[:3]
            ) or "execution not ready"
            section_results.setdefault(sec_num, SectionResult(
                section_number=sec_num,
                aligned=False,
                problems=f"readiness blocked: {blocker_summary}",
            ))

        implemented_sections = [
            sec_num for sec_num, result in section_results.items()
            if result.aligned
        ]
        log(f"=== Phase 1 complete: {len(implemented_sections)} sections "
            f"implemented, {len(blocked_sections)} blocked ===")

        # Write strategic state snapshot after Phase 1
        decisions_dir = paths.decisions_dir()
        build_strategic_state(decisions_dir, section_results, planspace)

        phase2_status = run_global_alignment_recheck(
            sections_by_num,
            section_results,
            planspace,
            codespace,
            parent,
            policy,
        )
        if phase2_status == "restart_phase1":
            continue  # outer while True → restart Phase 1

        coordination_status = run_coordination_loop(
            all_sections,
            section_results,
            sections_by_num,
            planspace,
            codespace,
            parent,
            policy,
        )
        if coordination_status == "restart_phase1":
            continue  # outer while True → restart Phase 1

        if coordination_status in {"complete", "exhausted", "stalled"}:
            return

        return


if __name__ == "__main__":
    main()
