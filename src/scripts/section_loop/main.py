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
from .cross_section import read_incoming_notes
from .dispatch import (
    check_agent_signals,
    dispatch_agent,
    read_agent_signal,
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
from .section_engine import _reexplore_section, run_section
from .types import Section, SectionResult


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
    project_mode = "brownfield"
    mode_constraints: list[str] = []
    mode_source = "default"
    if mode_json_path.exists():
        try:
            mode_data = json.loads(
                mode_json_path.read_text(encoding="utf-8"))
            project_mode = mode_data.get("mode", "brownfield")
            mode_constraints = mode_data.get("constraints", [])
            mode_source = "JSON signal"
        except (json.JSONDecodeError, OSError):
            log("project-mode.json exists but failed to parse — "
                "trying text fallback")
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
                project_mode = mode_data.get("mode", "brownfield")
                mode_constraints = mode_data.get("constraints", [])
                mode_source = "JSON signal (post-resume)"
            except (json.JSONDecodeError, OSError):
                if mode_txt_path.exists():
                    project_mode = mode_txt_path.read_text(
                        encoding="utf-8").strip()
                    mode_source = "text (post-resume)"
        elif mode_txt_path.exists():
            project_mode = mode_txt_path.read_text(
                encoding="utf-8").strip()
            mode_source = "text (post-resume)"
    log(f"Project mode: {project_mode} (from {mode_source})")

    # Write formalized mode contract
    mode_contract_path = planspace / "artifacts" / "mode-contract.json"
    mode_contract = {
        "mode": project_mode,
        "constraints": mode_constraints or (
            ["no code exists, research only"]
            if project_mode == "greenfield"
            else ["integrate with existing code"]
        ),
        "expected_outputs": (
            ["research memo", "prototype plan", "new sections"]
            if project_mode == "greenfield"
            else ["integration proposals", "code changes", "alignment checks"]
        ),
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

    # Route sections based on project mode
    if project_mode == "greenfield":
        log("Greenfield mode: sections without related files will use "
            "research-first template")
        # In greenfield mode, sections without files go directly to research
        # rather than being treated as anomalies.
        # Additionally, greenfield sections require a seed-code decision
        # before dispatching to the implementation strategist (see guard
        # inside the per-section loop below).

    log(f"Loaded {len(all_sections)} sections")

    # Read model policy once — used for re-exploration dispatch.
    policy = read_model_policy(planspace)

    # Outer loop: alignment_changed during Phase 2 restarts from Phase 1.
    # Each iteration runs Phase 1 (per-section) then Phase 2 (global).
    # The loop exits on: complete, fail, abort, or exhaustion.
    while True:

        # -----------------------------------------------------------------
        # Phase 1: Initial pass through all sections
        # -----------------------------------------------------------------
        section_results: dict[str, SectionResult] = {}
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
            log(f"=== Section {sec_num} ({len(queue)} remaining) "
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
                if project_mode == "greenfield":
                    # In greenfield mode, missing files is expected — skip
                    # the re-explorer overhead and go directly to research.
                    log(f"Section {sec_num}: no related files in greenfield "
                        f"mode — skipping re-explorer, marking NEEDS_RESEARCH")
                    section_mode = "greenfield"
                    mode_path = (planspace / "artifacts" / "sections"
                                 / f"section-{section.number}-mode.txt")
                    mode_path.parent.mkdir(parents=True, exist_ok=True)
                    mode_path.write_text(section_mode, encoding="utf-8")
                else:
                    # Agent-driven re-exploration: dispatch an Opus agent to
                    # investigate why the section has no files and determine
                    # whether it's greenfield, brownfield-missed, or hybrid.
                    log(f"Section {sec_num}: no related files — dispatching "
                        f"re-explorer agent")
                    reexplore_result = _reexplore_section(
                        section, planspace, codespace, parent,
                        model=policy["setup"],
                        exploration_model=policy["exploration"],
                    )
                    if reexplore_result == "ALIGNMENT_CHANGED_PENDING":
                        if _check_and_clear_alignment_changed(planspace):
                            requeue_changed_sections(
                                completed, queue, sections_by_num,
                                planspace, codespace,
                                current_section=sec_num)
                        continue
                    # Read section mode from structured JSON signal (not
                    # substring matching). The re-explorer agent writes
                    # signals/section-mode.json per the signal protocol.
                    signal_dir = (planspace / "artifacts" / "signals")
                    signal_dir.mkdir(parents=True, exist_ok=True)
                    mode_signal_path = (
                        signal_dir
                        / f"section-{section.number}-mode.json")
                    mode_signal = read_agent_signal(
                        mode_signal_path,
                        expected_fields=["mode"])
                    if mode_signal:
                        section_mode = mode_signal["mode"]
                    else:
                        # Fail closed: agent didn't write structured
                        # signal. Pause for parent rather than guessing.
                        log(f"Section {sec_num}: no structured mode signal "
                            f"found — pausing for parent (fail-closed)")
                        pause_for_parent(
                            planspace, parent,
                            f"pause:needs_parent:{sec_num}:missing mode "
                            f"signal — re-explorer did not write "
                            f"section-{sec_num}-mode.json")
                        section_mode = "brownfield"
                    mode_path = (planspace / "artifacts" / "sections"
                                 / f"section-{section.number}-mode.txt")
                    mode_path.parent.mkdir(parents=True, exist_ok=True)
                    mode_path.write_text(section_mode, encoding="utf-8")
                    log(f"Section {sec_num}: mode = {section_mode}")

                    # Re-parse related files (agent may have appended them)
                    section.related_files = parse_related_files(section.path)
                if not section.related_files:
                    # Still no files — agent declared greenfield or
                    # couldn't find matches. Greenfield is NOT aligned:
                    # it implies research obligations, not completion.
                    # Emit NEEDS_RESEARCH signal and mark as non-aligned
                    # so the coordinator treats it as a top-priority
                    # open problem.
                    log(f"Section {sec_num}: re-explorer found no files "
                        f"(greenfield — NEEDS_RESEARCH)")
                    completed.add(sec_num)

                    # Emit standard structured blocker signal (5-state
                    # protocol) so coordination can route mechanically.
                    signal_dir = planspace / "artifacts" / "signals"
                    signal_dir.mkdir(parents=True, exist_ok=True)
                    blocker_signal = {
                        "state": "needs_parent",
                        "section": sec_num,
                        "detail": (
                            f"Greenfield/no related files: section "
                            f"{sec_num} has no existing code to integrate "
                            f"with. Requires research/seed decision and "
                            f"possibly new sections."
                        ),
                        "needs": (
                            "Parent must decide: (a) provide seed code "
                            "and related files, (b) reframe as research "
                            "section, or (c) add new sections."
                        ),
                        "why_blocked": (
                            "No existing code to integrate with — cannot "
                            "produce integration proposal or implementation "
                            "without research or seed decision."
                        ),
                    }
                    (signal_dir
                     / f"section-{sec_num}-blocker.json"
                     ).write_text(
                        json.dumps(blocker_signal, indent=2),
                        encoding="utf-8")

                    section_results[sec_num] = SectionResult(
                        section_number=sec_num, aligned=False,
                        problems=(
                            f"needs_parent:greenfield — section "
                            f"{sec_num} requires research/seed decision"
                        ),
                    )
                    mailbox_send(
                        planspace, parent,
                        f"pause:needs_parent:{sec_num}:greenfield section "
                        f"needs research — no existing code to "
                        f"integrate with")
                    subprocess.run(  # noqa: S603
                        ["bash", str(DB_SH), "log",  # noqa: S607
                         str(planspace / "run.db"),
                         "lifecycle", f"end:section:{sec_num}",
                         "needs_parent (greenfield)",
                         "--agent", AGENT_NAME],
                        capture_output=True, text=True,
                    )
                    continue
                log(f"Section {sec_num}: re-explorer found "
                    f"{len(section.related_files)} files — continuing")

            # Run the section
            modified_files = run_section(
                planspace, codespace, section, parent,
                all_sections=all_sections,
            )

            # Check if alignment_changed arrived during run_section
            # (via handle_pending_messages or pause_for_parent)
            if _check_and_clear_alignment_changed(planspace):
                requeue_changed_sections(
                    completed, queue, sections_by_num,
                    planspace, codespace,
                    current_section=sec_num)
                continue

            if modified_files is None:
                # Section was paused and parent told us to stop
                log(f"Section {sec_num}: paused, exiting")
                subprocess.run(  # noqa: S603
                    ["bash", str(DB_SH), "log",  # noqa: S607
                     str(planspace / "run.db"),
                     "lifecycle", f"end:section:{sec_num}", "failed",
                     "--agent", AGENT_NAME],
                    capture_output=True, text=True,
                )
                return

            completed.add(sec_num)
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
            # Without this, the first alignment-change triggers
            # requeue-all because prev="" for every section.
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

            log(f"Section {sec_num}: done")
            subprocess.run(  # noqa: S603
                ["bash", str(DB_SH), "log",  # noqa: S607
                 str(planspace / "run.db"),
                 "lifecycle", f"end:section:{sec_num}", "done",
                 "--agent", AGENT_NAME],
                capture_output=True, text=True,
            )

        log(f"=== Phase 1 complete: {len(completed)} sections "
            f"processed ===")

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
            if not section.related_files:
                continue

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
