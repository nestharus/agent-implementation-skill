import json
import re
import subprocess
import sys
from pathlib import Path

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
from .coordination.problems import _collect_outstanding_problems
from .decisions import build_strategic_state
from .cross_section import read_incoming_notes
from .dispatch import (
    check_agent_signals,
    dispatch_agent,
    read_model_policy,
)
from .pipeline_control import (
    _check_and_clear_alignment_changed,
    _section_inputs_hash,
    alignment_changed_pending,
    handle_pending_messages,
    pause_for_parent,
    poll_control_messages,
    requeue_changed_sections,
)
from .reconciliation import run_reconciliation
from .section_engine import _reexplore_section, run_section
from .types import ProposalPassResult, Section, SectionResult


def parse_related_files(section_path: Path) -> list[str]:
    """Extract file paths from ## Related Files / ### <path> entries.

    Delegates to the unified block-scoped, code-fence-safe parser
    in ``scan.related_files`` (R33/P9).
    """
    from scan.related_files import extract_related_files

    return extract_related_files(section_path.read_text(encoding="utf-8"))


def load_sections(sections_dir: Path) -> list[Section]:
    """Load all section files and their related file maps.

    Only matches files named ``section-<number>.md`` (the actual spec
    files). Excerpt artifacts like ``section-01-proposal-excerpt.md`` are
    explicitly excluded so they are never mistaken for section specs.
    """
    sections = []
    for path in sorted(sections_dir.glob("section-*.md")):
        m = re.match(r'^section-(\d+)\.md$', path.name)
        if not m:
            continue
        related = parse_related_files(path)
        sections.append(Section(number=m.group(1), path=path,
                                related_files=related))
    return sections


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

    sections_dir = args.planspace / "artifacts" / "sections"

    # Initialize coordination DB (idempotent) and register
    subprocess.run(  # noqa: S603
        ["bash", str(DB_SH), "init", str(args.planspace / "run.db")],  # noqa: S607
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
    # Project mode (greenfield vs brownfield) is determined by the
    # codemap agent during Stage 3 scan.sh. The mode file is written
    # to artifacts/project-mode.txt by the codemap agent — not by
    # hardcoded script logic. If neither file exists, fail closed and
    # pause for parent rather than silently assuming brownfield.
    # Read project mode from structured JSON (preferred) or text fallback
    mode_json_path = planspace / "artifacts" / "signals" / "project-mode.json"
    mode_txt_path = planspace / "artifacts" / "project-mode.txt"
    project_mode = "unknown"
    mode_constraints: list[str] = []
    mode_source = "default"
    if mode_json_path.exists():
        try:
            mode_data = json.loads(
                mode_json_path.read_text(encoding="utf-8"))
            project_mode = mode_data.get("mode", "unknown")
            mode_constraints = mode_data.get("constraints", [])
            mode_source = "JSON signal"
        except (json.JSONDecodeError, OSError) as exc:
            try:
                mode_json_path.rename(
                    mode_json_path.with_suffix(".malformed.json"))
            except OSError:
                pass  # Best-effort preserve
            log(f"project-mode.json malformed ({exc}) — "
                "preserved as .malformed.json, trying text fallback")
            if mode_txt_path.exists():
                project_mode = mode_txt_path.read_text(
                    encoding="utf-8").strip()
                mode_source = "text (JSON malformed)"
            else:
                log("No text fallback — pausing for parent (fail-closed)")
                pause_for_parent(
                    planspace, parent,
                    "pause:needs_parent:project-mode-malformed — "
                    "JSON parse failed and no text fallback exists")
                mode_source = "default (post-resume)"
    elif mode_txt_path.exists():
        project_mode = mode_txt_path.read_text(encoding="utf-8").strip()
        mode_source = "text"
    else:
        # Fail closed: no project-mode signal from scan stage.
        log("No project-mode signal found — pausing for parent "
            "(fail-closed)")
        pause_for_parent(
            planspace, parent,
            "pause:needs_parent:project-mode-missing — "
            "scan stage did not write project-mode signal")
        mode_source = "default (post-resume)"
    # After any pause-for-parent, re-read in case parent provided mode
    if mode_source.startswith("default (post-resume)"):
        if mode_json_path.exists():
            try:
                mode_data = json.loads(
                    mode_json_path.read_text(encoding="utf-8"))
                project_mode = mode_data.get("mode", "unknown")
                mode_constraints = mode_data.get("constraints", [])
                mode_source = "JSON signal (post-resume)"
            except (json.JSONDecodeError, OSError) as exc:
                try:
                    mode_json_path.rename(
                        mode_json_path.with_suffix(".malformed.json"))
                except OSError:
                    pass  # Best-effort preserve
                log(f"project-mode.json malformed after resume ({exc}) "
                    "— preserved as .malformed.json, trying text fallback")
                if mode_txt_path.exists():
                    project_mode = mode_txt_path.read_text(
                        encoding="utf-8").strip()
                    mode_source = "text (post-resume)"
        elif mode_txt_path.exists():
            project_mode = mode_txt_path.read_text(
                encoding="utf-8").strip()
            mode_source = "text (post-resume)"
    log(f"Project mode: {project_mode} (from {mode_source})")

    # Write formalized mode contract (telemetry only — mode does NOT
    # choose planning paths or output shapes).
    mode_contract_path = planspace / "artifacts" / "mode-contract.json"
    mode_contract = {
        "mode": project_mode,
        "constraints": mode_constraints,
        "expected_outputs": [
            "integration proposals", "code changes", "alignment checks",
        ],
    }
    mode_contract_path.write_text(
        json.dumps(mode_contract, indent=2) + "\n", encoding="utf-8")

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
        proposal_results: dict[str, ProposalPassResult] = {}
        queue = [s.number for s in all_sections]
        completed: set[str] = set()

        while queue:
            # Check for abort or alignment changes before each section
            if handle_pending_messages(planspace, queue, completed):
                log("Aborted by parent")
                mailbox_send(planspace, parent, "fail:aborted")
                return

            # If alignment_changed flag is already pending (set by
            # handle_pending_messages above or a prior run_section),
            # skip directly to the _check_and_clear below instead of
            # wasting an Opus setup call.
            if alignment_changed_pending(planspace):  # noqa: SIM102
                # Clear the flag and requeue only sections whose inputs
                # actually changed (targeted, not brute-force requeue).
                if _check_and_clear_alignment_changed(planspace):
                    requeue_changed_sections(
                        completed, queue, sections_by_num,
                        planspace, codespace)
                    continue

            sec_num = queue.pop(0)

            if sec_num in completed:
                continue

            section = sections_by_num[sec_num]
            section.solve_count += 1
            log(f"=== Section {sec_num} proposal pass "
                f"({len(queue)} remaining) "
                f"[round {section.solve_count}] ===")
            # Emit section lifecycle start event for QA monitor rule A6
            subprocess.run(  # noqa: S603
                ["bash", str(DB_SH), "log", str(planspace / "run.db"),  # noqa: S607
                 "lifecycle", f"start:section:{sec_num}",
                 f"round {section.solve_count}",
                 "--agent", AGENT_NAME],
                capture_output=True, text=True,
            )

            if not section.related_files:
                # No related files — dispatch re-explorer to investigate
                # regardless of project mode.  The re-explorer determines
                # whether files were missed and may append them to the
                # section spec.
                log(f"Section {sec_num}: no related files — dispatching "
                    f"re-explorer agent")
                reexplore_result = _reexplore_section(
                    section, planspace, codespace, parent,
                    model=policy["setup"],
                )
                if reexplore_result == "ALIGNMENT_CHANGED_PENDING":
                    if _check_and_clear_alignment_changed(planspace):
                        requeue_changed_sections(
                            completed, queue, sections_by_num,
                            planspace, codespace,
                            current_section=sec_num)
                    continue

                # Re-parse related files (agent may have appended them)
                section.related_files = parse_related_files(section.path)
                if section.related_files:
                    log(f"Section {sec_num}: re-explorer found "
                        f"{len(section.related_files)} files — continuing")
                else:
                    log(f"Section {sec_num}: re-explorer found no files "
                        f"— continuing with unresolved related_files")

            # Run proposal pass only — no code modification
            proposal_result = run_section(
                planspace, codespace, section, parent,
                all_sections=all_sections,
                pass_mode="proposal",
            )

            # Check if alignment_changed arrived during run_section
            # (via handle_pending_messages or pause_for_parent)
            if _check_and_clear_alignment_changed(planspace):
                requeue_changed_sections(
                    completed, queue, sections_by_num,
                    planspace, codespace,
                    current_section=sec_num)
                continue

            if proposal_result is None:
                # Section was paused and parent told us to stop
                log(f"Section {sec_num}: paused during proposal, exiting")
                subprocess.run(  # noqa: S603
                    ["bash", str(DB_SH), "log",  # noqa: S607
                     str(planspace / "run.db"),
                     "lifecycle", f"end:section:{sec_num}", "failed",
                     "--agent", AGENT_NAME],
                    capture_output=True, text=True,
                )
                return

            completed.add(sec_num)
            if isinstance(proposal_result, ProposalPassResult):
                proposal_results[sec_num] = proposal_result
                status = ("ready" if proposal_result.execution_ready
                          else f"blocked ({len(proposal_result.blockers)} "
                               f"blockers)")
                mailbox_send(planspace, parent,
                             f"proposal-done:{sec_num}:{status}")
                log(f"Section {sec_num}: proposal pass complete — "
                    f"{status}")
            else:
                # Defensive: run_section in proposal mode should always
                # return ProposalPassResult or None, but handle gracefully
                log(f"Section {sec_num}: unexpected proposal result type "
                    f"— treating as failed")

            subprocess.run(  # noqa: S603
                ["bash", str(DB_SH), "log",  # noqa: S607
                 str(planspace / "run.db"),
                 "lifecycle", f"end:section:{sec_num}",
                 "proposal-done",
                 "--agent", AGENT_NAME],
                capture_output=True, text=True,
            )

        log(f"=== Phase 1a complete: {len(completed)} sections "
            f"proposed ===")

        ready_sections = sorted(
            num for num, pr in proposal_results.items()
            if pr.execution_ready
        )
        blocked_sections = sorted(
            num for num, pr in proposal_results.items()
            if not pr.execution_ready
        )
        log(f"Proposal summary: {len(ready_sections)} ready, "
            f"{len(blocked_sections)} blocked")
        if blocked_sections:
            log(f"Blocked sections: {blocked_sections}")

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
        impl_completed: set[str] = set()
        impl_restart_phase1 = False

        for sec_num in ready_sections:
            # Check for abort or alignment changes before each section
            if handle_pending_messages(planspace, [], impl_completed):
                log("Aborted by parent during implementation pass")
                mailbox_send(planspace, parent, "fail:aborted")
                return

            if alignment_changed_pending(planspace):
                if _check_and_clear_alignment_changed(planspace):
                    log("Alignment changed during implementation pass "
                        "— restarting from Phase 1")
                    impl_restart_phase1 = True
                    break

            section = sections_by_num[sec_num]
            log(f"=== Section {sec_num} implementation pass ===")
            subprocess.run(  # noqa: S603
                ["bash", str(DB_SH), "log", str(planspace / "run.db"),  # noqa: S607
                 "lifecycle", f"start:section:{sec_num}:impl",
                 f"round {section.solve_count}",
                 "--agent", AGENT_NAME],
                capture_output=True, text=True,
            )

            modified_files = run_section(
                planspace, codespace, section, parent,
                all_sections=all_sections,
                pass_mode="implementation",
            )

            # Check if alignment_changed arrived during implementation
            if _check_and_clear_alignment_changed(planspace):
                log("Alignment changed during implementation — "
                    "restarting from Phase 1")
                impl_restart_phase1 = True
                break

            if modified_files is None:
                # Section was paused or not ready
                log(f"Section {sec_num}: implementation returned None")
                subprocess.run(  # noqa: S603
                    ["bash", str(DB_SH), "log",  # noqa: S607
                     str(planspace / "run.db"),
                     "lifecycle", f"end:section:{sec_num}:impl",
                     "failed",
                     "--agent", AGENT_NAME],
                    capture_output=True, text=True,
                )
                continue

            impl_completed.add(sec_num)
            mailbox_send(planspace, parent,
                         f"done:{sec_num}:{len(modified_files)} files "
                         f"modified")

            # Record result — section passed its internal alignment
            # loop, so it's initially ALIGNED. The coordinator may find
            # cross-section issues later.
            section_results[sec_num] = SectionResult(
                section_number=sec_num,
                aligned=True,
                modified_files=modified_files,
            )

            # Persist baseline hash for targeted requeue (P5).
            baseline_hash_dir = (planspace / "artifacts"
                                 / "section-inputs-hashes")
            baseline_hash_dir.mkdir(parents=True, exist_ok=True)
            (baseline_hash_dir / f"{sec_num}.hash").write_text(
                _section_inputs_hash(
                    sec_num, planspace, codespace, sections_by_num),
                encoding="utf-8")

            # Save input hash for incremental Phase 2 checks
            p2hd = planspace / "artifacts" / "phase2-inputs-hashes"
            p2hd.mkdir(parents=True, exist_ok=True)
            (p2hd / f"{sec_num}.hash").write_text(
                _section_inputs_hash(
                    sec_num, planspace, codespace, sections_by_num),
                encoding="utf-8")

            log(f"Section {sec_num}: implementation done")
            subprocess.run(  # noqa: S603
                ["bash", str(DB_SH), "log",  # noqa: S607
                 str(planspace / "run.db"),
                 "lifecycle", f"end:section:{sec_num}:impl", "done",
                 "--agent", AGENT_NAME],
                capture_output=True, text=True,
            )

        if impl_restart_phase1:
            continue  # outer while True → restart Phase 1

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

        log(f"=== Phase 1 complete: {len(impl_completed)} sections "
            f"implemented, {len(blocked_sections)} blocked ===")

        # Write strategic state snapshot after Phase 1
        decisions_dir = planspace / "artifacts" / "decisions"
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
        phase2_hash_dir = (planspace / "artifacts"
                           / "phase2-inputs-hashes")
        phase2_hash_dir.mkdir(parents=True, exist_ok=True)

        restart_phase1 = False
        for sec_num, section in sections_by_num.items():
            # Skip sections whose inputs haven't changed since last
            # ALIGNED result (incremental convergence).
            cur_hash = _section_inputs_hash(
                sec_num, planspace, codespace, sections_by_num)
            prev_hash_file = phase2_hash_dir / f"{sec_num}.hash"
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

            global_align_output = (planspace / "artifacts"
                                   / f"global-align-{sec_num}-output.md")
            problems = _extract_problems(
                align_result, output_path=global_align_output,
                planspace=planspace, parent=parent, codespace=codespace,
                adjudicator_model=policy.get("adjudicator", "glm"),
            )
            main_signal_dir = (planspace / "artifacts" / "signals")
            main_signal_dir.mkdir(parents=True, exist_ok=True)
            signal, detail = check_agent_signals(
                align_result,
                signal_path=(main_signal_dir
                             / f"global-align-{sec_num}-signal.json"),
                output_path=(planspace / "artifacts"
                             / f"global-align-{sec_num}-output.md"),
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
                        planspace / "artifacts" / "coordination"
                        / "model-escalation.txt"
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
                rollup_dir = planspace / "artifacts" / "coordination"
                rollup_dir.mkdir(parents=True, exist_ok=True)
                rollup_path = rollup_dir / "coordination-exhausted.json"
                rollup_path.write_text(json.dumps(
                    [{"type": p["type"],
                      "section": p["section"],
                      "description": p["description"][:200]}
                     for p in outstanding],
                    indent=2), encoding="utf-8")
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
