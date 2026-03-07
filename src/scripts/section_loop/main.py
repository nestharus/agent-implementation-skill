import subprocess
import sys
from pathlib import Path

from lib.alignment_change_tracker import (
    check_and_clear,
    check_pending as alignment_changed_pending,
)
from lib.coordination_problem_resolver import _collect_outstanding_problems
from lib.implementation_pass import (
    ImplementationPassExit,
    ImplementationPassRestart,
    run_implementation_pass,
)
from lib.path_registry import PathRegistry
from lib.project_mode import resolve_project_mode, write_mode_contract
from lib.proposal_pass import ProposalPassExit, run_proposal_pass
from lib.section_loader import load_sections

from .alignment import (
    _extract_problems,
    _parse_alignment_verdict,
    _run_alignment_check_with_retries,
)
from .communication import (
    AGENT_NAME,
    DB_SH,
    log,
    mailbox_cleanup,
    mailbox_register,
    mailbox_send,
)
from .coordination import (
    MAX_COORDINATION_ROUNDS,
    MIN_COORDINATION_ROUNDS,
    run_global_coordination,
)
from .cross_section import read_incoming_notes
from .dispatch import (
    check_agent_signals,
    dispatch_agent,
    read_model_policy,
)
from lib.strategic_state import build_strategic_state
from .pipeline_control import (
    _section_inputs_hash,
    handle_pending_messages,
    poll_control_messages,
    requeue_changed_sections,
)
from .reconciliation import run_reconciliation
from .section_engine import run_section
from .types import ProposalPassResult, SectionResult


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

        ready_sections = sorted(
            num for num, pr in proposal_results.items()
            if pr.execution_ready
        )
        blocked_sections = sorted(
            num for num, pr in proposal_results.items()
            if not pr.execution_ready
        )

        # -----------------------------------------------------------------
        # Phase 1b: Universal reconciliation
        # -----------------------------------------------------------------
        # Run cross-section reconciliation over all proposal-state
        # artifacts.  Detects overlapping anchors, conflicting contracts,
        # redundant new-section candidates, and shared seams that need
        # substrate work.  Writes per-section result artifacts and, when
        # needed, consolidated scope-delta and substrate-trigger signals.
        recon_summary = run_reconciliation(
            planspace,
            list(proposal_results.values()),
        )
        log(f"Phase 1b reconciliation: "
            f"{recon_summary['conflicts_found']} conflicts, "
            f"{recon_summary['new_sections_proposed']} new-section "
            f"proposals, "
            f"substrate_needed={recon_summary['substrate_needed']}, "
            f"affected sections={recon_summary['sections_affected']}")

        # Sections affected by reconciliation conflicts should not
        # proceed to implementation until the conflicts are addressed.
        # Mark them as not execution-ready so they are excluded from
        # Phase 1c and reported to the parent as blocked.
        reconciliation_blocked: set[str] = set()
        for sec_num in recon_summary.get("sections_affected", []):
            if sec_num in proposal_results:
                pr = proposal_results[sec_num]
                if pr.execution_ready:
                    pr.execution_ready = False
                    pr.needs_reconciliation = True
                    pr.blockers.append({
                        "type": "reconciliation",
                        "description": (
                            f"Section affected by cross-section "
                            f"reconciliation — "
                            f"{recon_summary['conflicts_found']} "
                            f"conflict(s) found"
                        ),
                    })
                    reconciliation_blocked.add(sec_num)
                    log(f"Section {sec_num}: blocked by reconciliation")

        # Recompute ready/blocked lists after reconciliation
        ready_sections = sorted(
            num for num, pr in proposal_results.items()
            if pr.execution_ready
        )
        blocked_sections = sorted(
            num for num, pr in proposal_results.items()
            if not pr.execution_ready
        )
        if reconciliation_blocked:
            log(f"Reconciliation blocked {len(reconciliation_blocked)} "
                f"additional sections: {sorted(reconciliation_blocked)}")
            log(f"Updated proposal summary: {len(ready_sections)} ready, "
                f"{len(blocked_sections)} blocked")

        # -----------------------------------------------------------------
        # Phase 1b.2: Re-proposal pass for reconciliation-affected sections
        # -----------------------------------------------------------------
        # Sections that were affected by reconciliation (marked
        # needs_reconciliation=True) must re-run through the proposal
        # pass so the proposer can incorporate reconciliation findings
        # (overlapping anchors, contract conflicts, shared seams).
        # The reconciliation result artifact is already on disk; the
        # runner will detect it and append it to the proposer's context.
        reproposal_sections = sorted(reconciliation_blocked)
        reproposal_restart_phase1 = False
        if reproposal_sections:
            log(f"=== Phase 1b.2: re-proposal pass for "
                f"{len(reproposal_sections)} reconciliation-affected "
                f"sections ===")

            for sec_num in reproposal_sections:
                # Check for abort or alignment changes before each section
                if handle_pending_messages(planspace, [], set()):
                    log("Aborted by parent during re-proposal pass")
                    mailbox_send(planspace, parent, "fail:aborted")
                    return

                if alignment_changed_pending(planspace):
                    if _check_and_clear_alignment_changed(planspace):
                        log("Alignment changed during re-proposal pass "
                            "— restarting from Phase 1")
                        reproposal_restart_phase1 = True
                        break

                section = sections_by_num[sec_num]
                log(f"=== Section {sec_num} re-proposal pass "
                    f"(reconciliation-affected) ===")

                reproposal_result = run_section(
                    planspace, codespace, section, parent,
                    all_sections=all_sections,
                    pass_mode="proposal",
                )

                # Check if alignment_changed arrived during re-proposal
                if _check_and_clear_alignment_changed(planspace):
                    log("Alignment changed during re-proposal — "
                        "restarting from Phase 1")
                    reproposal_restart_phase1 = True
                    break

                if reproposal_result is None:
                    log(f"Section {sec_num}: paused during re-proposal")
                    continue

                if isinstance(reproposal_result, ProposalPassResult):
                    proposal_results[sec_num] = reproposal_result
                    status = ("ready" if reproposal_result.execution_ready
                              else f"still blocked "
                                   f"({len(reproposal_result.blockers)} "
                                   f"blockers)")
                    log(f"Section {sec_num}: re-proposal complete — "
                        f"{status}")
                    mailbox_send(planspace, parent,
                                 f"reproposal-done:{sec_num}:{status}")

        if reproposal_restart_phase1:
            continue  # outer while True → restart Phase 1

        if reproposal_sections:
            # Recompute ready/blocked after re-proposal
            ready_sections = sorted(
                num for num, pr in proposal_results.items()
                if pr.execution_ready
            )
            blocked_sections = sorted(
                num for num, pr in proposal_results.items()
                if not pr.execution_ready
            )
            log(f"Post-reproposal summary: {len(ready_sections)} ready, "
                f"{len(blocked_sections)} blocked")
            if blocked_sections:
                log(f"Still blocked after re-proposal: {blocked_sections}")

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

        # -------------------------------------------------------------
        # Phase 2: Global coordination loop
        # -------------------------------------------------------------
        # Re-run alignment on ALL sections to get a global snapshot.
        # Sections may have been individually aligned but cross-section
        # changes (shared files modified by later sections) can
        # introduce problems invisible during the initial pass.
        log("=== Phase 2: global coordination ===")
        log("Re-checking alignment across all sections...")

        # Compute input hashes to skip unchanged sections (targeted,
        # not brute-force recheck).
        phase2_hash_dir = paths.phase2_inputs_hashes_dir()
        phase2_hash_dir.mkdir(parents=True, exist_ok=True)

        restart_phase1 = False
        for sec_num, section in sections_by_num.items():
            # Skip sections whose inputs haven't changed since last
            # ALIGNED result (incremental convergence).
            cur_hash = _section_inputs_hash(
                sec_num, planspace, codespace, sections_by_num)
            prev_hash_file = paths.phase2_input_hash(sec_num)
            prev_hash = (prev_hash_file.read_text(encoding="utf-8")
                         .strip() if prev_hash_file.exists() else "")
            prev_result = section_results.get(sec_num)
            if (prev_hash == cur_hash and prev_result
                    and prev_result.aligned):
                log(f"Section {sec_num}: inputs unchanged since "
                    f"ALIGNED — skipping Phase 2 recheck")
                continue
            prev_hash_file.write_text(cur_hash, encoding="utf-8")

            # Poll for control messages before each dispatch
            ctrl = poll_control_messages(planspace, parent, sec_num)
            if ctrl == "alignment_changed":
                log("Alignment changed during Phase 2 — restarting "
                    "from Phase 1")
                restart_phase1 = True
                break

            # Read incoming notes for cross-section awareness
            notes = read_incoming_notes(section, planspace, codespace)
            if notes:
                log(f"Section {sec_num}: has incoming notes for global "
                    f"alignment check")

            # Alignment check with TIMEOUT retry (max 2 retries)
            align_result = _run_alignment_check_with_retries(
                section, planspace, codespace, parent, sec_num,
                output_prefix="global-align",
                model=policy["alignment"],
                adjudicator_model=policy.get("adjudicator", "glm"),
            )
            if align_result == "ALIGNMENT_CHANGED_PENDING":
                # Alignment changed mid-check — let outer loop restart
                restart_phase1 = True
                break
            if align_result == "INVALID_FRAME":
                # Structural failure — alignment prompt frame is wrong.
                # Surface upward, don't continue with broken evaluation.
                log(f"Section {sec_num}: invalid alignment frame — "
                    f"requires parent intervention")
                mailbox_send(
                    planspace, parent,
                    f"fail:invalid_alignment_frame:{sec_num}",
                )
                section_results[sec_num] = SectionResult(
                    section_number=sec_num,
                    aligned=False,
                    problems="invalid alignment frame — requires "
                             "parent intervention",
                    modified_files=section_results.get(
                        sec_num, SectionResult(sec_num)
                    ).modified_files,
                )
                continue
            if align_result is None:
                # All retries timed out
                log(f"Section {sec_num}: global alignment check timed "
                    f"out after retries")
                section_results[sec_num] = SectionResult(
                    section_number=sec_num,
                    aligned=False,
                    problems="alignment check timed out after retries",
                    modified_files=section_results.get(
                        sec_num, SectionResult(sec_num)
                    ).modified_files,
                )
                continue

            global_align_output = (
                paths.artifacts / f"global-align-{sec_num}-output.md"
            )
            problems = _extract_problems(
                align_result, output_path=global_align_output,
                planspace=planspace, parent=parent, codespace=codespace,
                adjudicator_model=policy.get("adjudicator", "glm"),
            )
            main_signal_dir = paths.signals_dir()
            main_signal_dir.mkdir(parents=True, exist_ok=True)
            signal, detail = check_agent_signals(
                align_result,
                signal_path=(main_signal_dir
                             / f"global-align-{sec_num}-signal.json"),
                output_path=global_align_output,
                planspace=planspace, parent=parent, codespace=codespace,
            )

            if problems is None and signal is None:
                section_results[sec_num] = SectionResult(
                    section_number=sec_num,
                    aligned=True,
                    modified_files=section_results.get(
                        sec_num, SectionResult(sec_num)
                    ).modified_files,
                )
            else:
                log(f"Section {sec_num}: global alignment found "
                    f"problems")
                combined_problems = problems or ""
                if signal:
                    combined_problems += (
                        f"\n[signal:{signal}] {detail}"
                        if combined_problems
                        else f"[signal:{signal}] {detail}"
                    )
                section_results[sec_num] = SectionResult(
                    section_number=sec_num,
                    aligned=False,
                    problems=combined_problems or None,
                    modified_files=section_results.get(
                        sec_num, SectionResult(sec_num)
                    ).modified_files,
                )

        if restart_phase1:
            continue  # outer while True → restart Phase 1

        # Check if everything is already aligned
        misaligned = [
            r for r in section_results.values() if not r.aligned
        ]
        if not misaligned:
            # Check for outstanding cross-section problems
            # (unaddressed notes, conflicts) before declaring completion.
            outstanding = _collect_outstanding_problems(
                section_results, sections_by_num, planspace,
            )
            if outstanding:
                outstanding_types = [p["type"] for p in outstanding]
                log(f"{len(outstanding)} outstanding cross-section "
                    f"problems remain (types: {outstanding_types}) — "
                    f"cannot declare completion")
                # Fall through to coordination to address outstanding
            else:
                # Final control-message drain — catch alignment_changed
                # or abort that arrived during the last dispatch.
                ctrl = poll_control_messages(planspace, parent)
                if ctrl == "alignment_changed":
                    log("Alignment changed just before completion — "
                        "restarting from Phase 1")
                    continue  # outer while True → restart Phase 1
                log("=== All sections ALIGNED after initial pass ===")
                build_strategic_state(decisions_dir, section_results, planspace)
                mailbox_send(planspace, parent, "complete")
                return

        # Include outstanding cross-section problems in unresolved count
        # so stall detection works when misaligned=0 but notes exist.
        outstanding_count = len(outstanding) if not misaligned else 0
        if misaligned:
            log(f"{len(misaligned)} sections need coordination: "
                f"{sorted(r.section_number for r in misaligned)}")
        else:
            log(f"All sections aligned but {outstanding_count} "
                f"outstanding cross-section problems need coordination")

        # Run the coordinator loop (adaptive: continues while improving)
        prev_unresolved = len(misaligned) + outstanding_count
        stall_count = 0
        round_num = 0
        while round_num < MAX_COORDINATION_ROUNDS:
            round_num += 1
            # Poll for control messages before each round
            ctrl = poll_control_messages(planspace, parent)
            if ctrl == "alignment_changed":
                log("Alignment changed during coordination — "
                    "restarting from Phase 1")
                restart_phase1 = True
                break

            log(f"=== Coordination round {round_num} "
                f"(prev unresolved: {prev_unresolved}) ===")
            mailbox_send(planspace, parent,
                         f"status:coordination:round-{round_num}")

            all_done = run_global_coordination(
                all_sections, section_results, sections_by_num,
                planspace, codespace, parent,
            )

            # Check if alignment_changed was received during
            # coordination (consumed inside run_global_coordination,
            # which sets the flag file)
            if _check_and_clear_alignment_changed(planspace):
                log("Alignment changed during coordination — "
                    "restarting from Phase 1")
                restart_phase1 = True
                break

            if all_done:
                # Final control-message drain — catch alignment_changed
                # or abort that arrived during the last dispatch.
                ctrl = poll_control_messages(planspace, parent)
                if ctrl == "alignment_changed":
                    log("Alignment changed just before completion — "
                        "restarting from Phase 1")
                    restart_phase1 = True
                    break
                log(f"=== All sections ALIGNED after coordination "
                    f"round {round_num} ===")
                build_strategic_state(decisions_dir, section_results, planspace)
                mailbox_send(planspace, parent, "complete")
                return

            remaining = [
                r for r in section_results.values() if not r.aligned
            ]
            # Include outstanding problems in stall detection so
            # note-only rounds aren't immediately marked as stalled.
            remaining_outstanding = (
                _collect_outstanding_problems(
                    section_results, sections_by_num, planspace,
                ) if not remaining else []
            )
            cur_unresolved = len(remaining) + len(remaining_outstanding)
            log(f"Coordination round {round_num}: "
                f"{cur_unresolved} unresolved "
                f"({len(remaining)} misaligned, "
                f"{len(remaining_outstanding)} outstanding) "
                f"(was {prev_unresolved})")

            # Adaptive termination: stop if not making progress
            if cur_unresolved >= prev_unresolved:
                stall_count += 1
                escalation_threshold = policy.get(
                    "escalation_triggers", {},
                ).get("stall_count", 2)
                if stall_count == escalation_threshold:
                    # Escalation on churn: flag for stronger model on
                    # next round's coordination fixes
                    log(f"Coordination churning ({stall_count} rounds "
                        f"without improvement) — escalating model")
                    escalation_file = (
                        paths.coordination_dir() / "model-escalation.txt"
                    )
                    escalation_file.write_text(
                        policy["escalation_model"], encoding="utf-8")
                    mailbox_send(planspace, parent,
                                 f"escalation:coordination:"
                                 f"round-{round_num}:stall_count="
                                 f"{stall_count}")
                if round_num >= MIN_COORDINATION_ROUNDS and stall_count >= 3:
                    log(f"Coordination stalled ({stall_count} rounds "
                        f"without improvement) — stopping")
                    break
            else:
                stall_count = 0  # reset on progress

            prev_unresolved = cur_unresolved

        if not restart_phase1:
            # Coordination exhausted or stalled — do NOT send "complete".
            remaining = [
                r for r in section_results.values() if not r.aligned
            ]
            if remaining:
                log(f"=== Coordination finished after {round_num} rounds, "
                    f"{len(remaining)} sections still unresolved ===")
                build_strategic_state(decisions_dir, section_results, planspace)
                for r in remaining:
                    summary = (r.problems or "unknown")[:120]
                    log(f"  - Section {r.section_number}: {summary}")
                    mailbox_send(
                        planspace, parent,
                        f"fail:{r.section_number}:"
                        f"coordination_exhausted:{summary}",
                    )
                return  # exhausted — exit

            # All sections aligned but coordination stalled/exhausted.
            # Check for outstanding cross-section problems that didn't
            # manifest as misalignment (upward signaling contract).
            outstanding = _collect_outstanding_problems(
                section_results, sections_by_num, planspace,
            )
            if outstanding:
                log(f"=== Coordination exhausted after {round_num} rounds: "
                    f"all sections aligned but {len(outstanding)} "
                    f"outstanding problems remain ===")
                build_strategic_state(decisions_dir, section_results, planspace)
                # Write structured rollup artifact for parent visibility
                rollup_dir = paths.coordination_dir()
                rollup_dir.mkdir(parents=True, exist_ok=True)
                rollup_path = rollup_dir / "coordination-exhausted.json"
                write_json(
                    rollup_path,
                    [{"type": p["type"],
                      "section": p["section"],
                      "description": p["description"][:200]}
                     for p in outstanding],
                )
                mailbox_send(
                    planspace, parent,
                    f"fail:coordination_exhausted:outstanding:"
                    f"{len(outstanding)}",
                )
                return  # exhausted — exit

        if restart_phase1:
            continue  # outer while True → restart Phase 1

        # If we reach here without restart, we're done
        return


if __name__ == "__main__":
    main()
