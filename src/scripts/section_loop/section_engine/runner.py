import json
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from lib.artifact_io import read_json, read_json_or_default, write_json
from lib.alignment_change_tracker import check_pending as alignment_changed_pending
from lib.excerpt_repository import exists as excerpt_exists
from lib.hash_service import content_hash, file_hash
from lib.note_repository import write_consequence_note
from lib.path_registry import PathRegistry

from ..alignment import (
    _extract_problems,
    _parse_alignment_verdict,
    _run_alignment_check_with_retries,
    collect_modified_files,
)
from ..change_detection import diff_files, snapshot_files
from ..communication import (
    AGENT_NAME,
    DB_SH,
    WORKFLOW_HOME,
    _log_artifact,
    _record_traceability,
    log,
    mailbox_send,
)
from ..cross_section import (
    extract_section_summary,
    persist_decision,
    post_section_completion,
    read_incoming_notes,
)
from ..dispatch import (
    check_agent_signals,
    dispatch_agent,
    read_agent_signal,
    read_model_policy,
    summarize_output,
    write_model_choice_signal,
)
from ..agent_templates import TASK_SUBMISSION_SEMANTICS, validate_dynamic_content
from ..task_ingestion import ingest_and_submit
from ..pipeline_control import (
    handle_pending_messages,
    pause_for_parent,
    poll_control_messages,
)
from ..prompts import (
    agent_mail_instructions,
    signal_instructions,
    write_impl_alignment_prompt,
    write_integration_alignment_prompt,
    write_integration_proposal_prompt,
    write_section_setup_prompt,
    write_strategic_impl_prompt,
)
from ..types import ProposalPassResult, Section
from ..intent import (
    ensure_global_philosophy,
    generate_intent_pack,
    run_intent_triage,
)
from ..intent.expansion import handle_user_gate, run_expansion_cycle
from ..intent.surfaces import (
    load_intent_surfaces,
    load_surface_registry,
    merge_surfaces_into_registry,
    normalize_surface_ids,
    save_surface_registry,
)
from ..intent.triage import load_triage_result
from ..readiness import resolve_readiness
from ..reconciliation import load_reconciliation_result
from lib.proposal_state_repository import load_proposal_state
from lib.reconciliation_queue import queue_reconciliation_request
from .blockers import _append_open_problem, _update_blocker_rollup
from .reexplore import _write_alignment_surface
from .todos import _check_needs_microstrategy, _extract_todos_from_files
from .traceability import _file_sha256, _write_traceability_index


def _write_tool_surface(
    all_tools: list, section_number: str,
    tools_available_path: Path,
) -> int:
    """Filter and write section-relevant tools surface.

    Returns the count of relevant tools written.
    """
    sec_key = f"section-{section_number}"
    relevant_tools = [
        t for t in all_tools
        if t.get("scope") == "cross-section"
        or t.get("created_by") == sec_key
    ]
    if relevant_tools:
        lines = ["# Available Tools\n",
                 "Cross-section and section-local tools:\n"]
        for tool in relevant_tools:
            path = tool.get("path", "unknown")
            desc = tool.get("description", "")
            scope = tool.get("scope", "section-local")
            creator = tool.get("created_by", "unknown")
            status = tool.get("status", "experimental")
            tool_id = tool.get("id", "")
            id_tag = f" id={tool_id}" if tool_id else ""
            lines.append(
                f"- `{path}` [{status}] ({scope}, "
                f"from {creator}{id_tag}): {desc}")
        tools_available_path.write_text(
            "\n".join(lines) + "\n", encoding="utf-8",
        )
    elif tools_available_path.exists():
        tools_available_path.unlink()
    return len(relevant_tools)


def _run_implementation_pass(
    planspace: Path, codespace: Path, section: Section, parent: str,
    *,
    all_sections: list[Section] | None = None,
    artifacts: Path,
    policy: dict,
) -> list[str] | None:
    """Execute implementation for a section whose proposal is already aligned.

    Validates the readiness artifact, then runs microstrategy through
    post-completion.  Returns ``None`` if the section is not execution-ready
    (the caller should not have dispatched implementation in that case, but
    the gate is enforced here as a fail-closed safeguard).

    Also checks whether upstream artifacts (reconciliation result,
    proposal state) have changed since the readiness artifact was last
    written.  If they have, the readiness artifact is stale and the
    section must be re-resolved before implementation can proceed.

    This is the second half of ``run_section`` — extracted so that the
    two-pass orchestrator can call proposal and implementation independently.
    """
    # Fail-closed: if a reconciliation result exists and marks this
    # section as affected, block implementation — the section must go
    # through re-proposal to incorporate reconciliation findings.
    recon_result = load_reconciliation_result(artifacts, section.number)
    if recon_result and recon_result.get("affected"):
        log(f"Section {section.number}: implementation pass blocked — "
            f"reconciliation result marks section as affected")
        return None

    readiness = resolve_readiness(artifacts, section.number)
    if not readiness.get("ready"):
        log(f"Section {section.number}: implementation pass skipped — "
            f"execution_ready is false")
        return None

    # Delegate to run_section in full mode starting from the
    # microstrategy step.  We use a private sentinel to skip re-running
    # the proposal steps.
    return _run_section_implementation_steps(
        planspace, codespace, section, parent,
        all_sections=all_sections,
        artifacts=artifacts, policy=policy,
    )


def run_section(
    planspace: Path, codespace: Path, section: Section, parent: str,
    all_sections: list[Section] | None = None,
    *,
    pass_mode: str = "full",
) -> list[str] | ProposalPassResult | None:
    """Run a section through the strategic flow.

    0. Read incoming notes from other sections (pre-section)
    1. Section setup (once) — extract proposal/alignment excerpts
    2. Integration proposal loop — GPT proposes, Opus checks alignment
    3. Strategic implementation — GPT implements, Opus checks alignment
    4. Post-completion — snapshot, impact analysis, consequence notes

    Parameters
    ----------
    pass_mode:
        ``"full"`` (default) — run the complete pipeline (legacy behavior).
        ``"proposal"`` — run exploration through readiness resolution, then
        stop.  Returns a ``ProposalPassResult``.  No code files are modified.
        ``"implementation"`` — assume proposal is aligned and ready.  Pick
        up from the readiness artifact and run microstrategy through
        post-completion.  Only proceeds if ``execution_ready == true``.

    Returns
    -------
    - ``list[str]`` of modified files on successful implementation
      (``"full"`` or ``"implementation"`` mode).
    - ``ProposalPassResult`` when ``pass_mode="proposal"`` completes.
    - ``None`` if paused/aborted (waiting for parent).
    """
    paths = PathRegistry(planspace)
    artifacts = paths.artifacts
    policy = read_model_policy(planspace)

    # -----------------------------------------------------------------
    # Implementation-only mode: skip proposal steps, jump to execution
    # -----------------------------------------------------------------
    if pass_mode == "implementation":
        return _run_implementation_pass(
            planspace, codespace, section, parent,
            all_sections=all_sections,
            artifacts=artifacts, policy=policy,
        )

    # -----------------------------------------------------------------
    # Recurrence signal: notify coordinator when a section loops
    # -----------------------------------------------------------------
    if section.solve_count >= 2:
        recurrence_signal = {
            "section": section.number,
            "attempt": section.solve_count,
            "recurring": True,
            "escalate_to_coordinator": True,
        }
        recurrence_path = (
            paths.signals_dir() / f"section-{section.number}-recurrence.json"
        )
        write_json(recurrence_path, recurrence_signal)
        log(f"Section {section.number}: recurrence signal written "
            f"(attempt {section.solve_count})")

    # -----------------------------------------------------------------
    # Step 0: Read incoming notes from other sections
    # -----------------------------------------------------------------
    incoming_notes = read_incoming_notes(section, planspace, codespace)
    if incoming_notes:
        log(f"Section {section.number}: received incoming notes from "
            f"other sections")

    # -----------------------------------------------------------------
    # Step 0c: Impact triage — skip expensive steps if notes are trivial
    # -----------------------------------------------------------------
    if incoming_notes and section.solve_count >= 1:
        triage_dir = artifacts / "triage"
        triage_dir.mkdir(parents=True, exist_ok=True)
        triage_prompt_path = triage_dir / f"triage-{section.number}-prompt.md"
        triage_output_path = triage_dir / f"triage-{section.number}-output.md"
        triage_signal_path = (artifacts / "signals"
                              / f"triage-{section.number}.json")

        # Build triage context
        existing_proposal = (artifacts / "proposals"
                             / f"section-{section.number}-integration-proposal.md")
        proposal_ref = ""
        if existing_proposal.exists():
            proposal_ref = f"3. Existing proposal: `{existing_proposal}`"

        last_align = artifacts / f"intg-align-{section.number}-output.md"
        align_ref = ""
        if last_align.exists():
            align_ref = f"4. Last alignment verdict: `{last_align}`"

        # Write incoming notes to artifact file (paths not contents)
        triage_notes_path = (triage_dir
                             / f"triage-{section.number}-incoming-notes.md")
        triage_notes_path.write_text(incoming_notes, encoding="utf-8")

        triage_prompt_path.write_text(f"""# Task: Impact Triage for Section {section.number}

## Context
This section has already been solved once (attempt {section.solve_count}).
New notes/changes arrived from other sections. Determine if they require
re-planning or re-implementation, or if they can be acknowledged without
expensive rework.

## Files to Read
1. Section specification: `{section.path}`
2. Incoming notes: `{triage_notes_path}`
{proposal_ref}
{align_ref}

## Instructions
Classify the impact of these notes on this section:
- `needs_replan`: true if the notes change the problem or strategy
- `needs_code_change`: true if the notes require implementation changes
- Both false if the notes are informational or already handled

For every note you read, you MUST include an acknowledgment entry in the
`acknowledge` array. Each note contains a **Note ID** field — use that ID.

Write a JSON signal to: `{triage_signal_path}`
```json
{{
  "needs_replan": false,
  "needs_code_change": false,
  "acknowledge": [
    {{"note_id": "<note-id-from-note>", "action": "accepted", "reason": "informational; no action required"}}
  ],
  "reasons": ["notes are informational"]
}}
```

Valid actions: "accepted" (resolved/no-op), "rejected" (disagree with note),
"deferred" (will address later).
""", encoding="utf-8")
        _log_artifact(planspace, f"prompt:triage-{section.number}")

        dispatch_agent(
            policy.get("triage", "glm"),
            triage_prompt_path, triage_output_path,
            planspace, parent, codespace=codespace,
            section_number=section.number,
            agent_file="consequence-note-triager.md",
        )

        # Read triage signal
        triage = read_json(triage_signal_path)
        if triage is not None:
            needs_replan = triage.get("needs_replan", True)
            needs_code = triage.get("needs_code_change", True)
            if not needs_replan and not needs_code:
                # Merge triage acknowledgments into note-ack file
                triage_acks = triage.get("acknowledge", [])
                ack_path = (artifacts / "signals"
                            / f"note-ack-{section.number}.json")
                existing_acks: dict = read_json_or_default(
                    ack_path, {"acknowledged": []})
                existing_ids = {
                    e.get("note_id")
                    for e in existing_acks.get("acknowledged", [])
                }
                for ack in triage_acks:
                    nid = ack.get("note_id")
                    if nid and nid not in existing_ids:
                        existing_acks.setdefault(
                            "acknowledged", []).append(ack)
                        existing_ids.add(nid)
                write_json(ack_path, existing_acks)

                # Completeness check: all incoming note IDs must be acked
                incoming_note_ids = set(re.findall(
                    r'\*\*Note ID\*\*:\s*`([^`]+)`',
                    incoming_notes))
                acked_ids = {
                    a.get("note_id") for a in triage_acks
                } | existing_ids
                if (incoming_note_ids
                        and not incoming_note_ids.issubset(acked_ids)):
                    log(f"Section {section.number}: triage did not "
                        f"acknowledge all notes — full processing")
                else:
                    log(f"Section {section.number}: triage says no "
                        f"rework needed — skipping to alignment check")
                    # Jump straight to alignment verification
                    verify_result = (
                        _run_alignment_check_with_retries(
                            section, planspace, codespace, parent,
                            section.number,
                            output_prefix="triage-align",
                            model=policy["alignment"],
                            adjudicator_model=policy.get(
                                "adjudicator", "glm"),
                        ))
                    if verify_result == "ALIGNMENT_CHANGED_PENDING":
                        return None
                    if verify_result:
                        verdict = _parse_alignment_verdict(
                            verify_result)
                        if (verdict is not None
                                and verdict.get("aligned") is True
                                and verdict.get("frame_ok", True)
                                is True):
                            log(f"Section {section.number}: triage + "
                                f"alignment confirms no rework needed")
                            reported = collect_modified_files(
                                planspace, section, codespace)
                            return reported if reported else []

    # -----------------------------------------------------------------
    # Step 0b: Surface section-relevant tools from tool registry
    # -----------------------------------------------------------------
    tools_available_path = (artifacts / "sections"
                            / f"section-{section.number}-tools-available.md")
    tool_registry_path = artifacts / "tool-registry.json"
    pre_tool_total = 0  # Total tool count before implementation
    if tool_registry_path.exists():
        try:
            registry = json.loads(
                tool_registry_path.read_text(encoding="utf-8"),
            )
            all_tools = (registry if isinstance(registry, list)
                         else registry.get("tools", []))
            pre_tool_total = len(all_tools)
            relevant_count = _write_tool_surface(
                all_tools, section.number, tools_available_path,
            )
            if relevant_count:
                log(f"Section {section.number}: {relevant_count} "
                    f"relevant tools (of {len(all_tools)} total)")
            elif tools_available_path.exists():
                log(f"Section {section.number}: removed stale "
                    f"tools-available surface (no relevant tools)")
        except (json.JSONDecodeError, ValueError) as exc:
            # Fail-closed: remove stale surface to prevent agents
            # from reasoning over outdated tool context (R34/V1)
            if tools_available_path.exists():
                tools_available_path.unlink()
                log(f"Section {section.number}: removed stale "
                    f"tools-available surface (malformed registry)")
            # Preserve corrupted registry before repair (V8/R55)
            malformed_path = tool_registry_path.with_suffix(
                ".malformed.json")
            try:
                import shutil
                shutil.copy2(tool_registry_path, malformed_path)
            except OSError:
                pass  # Best-effort preserve
            # Dispatch tool-registrar to attempt repair
            log(f"Section {section.number}: tool-registry.json "
                f"malformed ({exc}) — dispatching repair "
                f"(original preserved as {malformed_path.name})")
            repair_prompt = (
                artifacts
                / f"tool-registry-repair-{section.number}-prompt.md"
            )
            repair_output = (
                artifacts
                / f"tool-registry-repair-{section.number}-output.md"
            )
            repair_prompt.write_text(
                f"# Task: Repair Tool Registry\n\n"
                f"The tool registry at `{tool_registry_path}` contains "
                f"malformed JSON.\n\nError: {exc}\n\n"
                f"Read the file, reconstruct valid JSON preserving all "
                f"tool entries, and write back to the same path.\n",
                encoding="utf-8",
            )
            dispatch_agent(
                policy.get("tool_registrar", "glm"),
                repair_prompt, repair_output,
                planspace, parent, codespace=codespace,
                section_number=section.number,
                agent_file="tool-registrar.md",
            )
            # Re-check after repair
            registry = read_json(tool_registry_path)
            if registry is not None:
                all_tools = (registry if isinstance(registry, list)
                             else registry.get("tools", []))
                pre_tool_total = len(all_tools)
                log(f"Section {section.number}: tool registry "
                    f"repaired ({len(all_tools)} tools)")
                # Rebuild tool surface after successful repair
                relevant_count = _write_tool_surface(
                    all_tools, section.number, tools_available_path,
                )
                if relevant_count:
                    log(f"Section {section.number}: rebuilt tools "
                        f"surface ({relevant_count} relevant tools)")
            else:
                log(f"Section {section.number}: tool registry "
                    f"repair failed — writing blocker signal")
                blocker = {
                    "state": "needs_parent",
                    "detail": (
                        "Tool registry malformed; repair agent "
                        "could not fix it."
                    ),
                    "needs": "Valid tool-registry.json",
                    "why_blocked": (
                        "Cannot safely surface tools with an "
                        "invalid registry."
                    ),
                }
                write_json(
                    artifacts / "signals"
                    / f"section-{section.number}-blocker.json",
                    blocker,
                )
                _update_blocker_rollup(planspace)

    # -----------------------------------------------------------------
    # Step 1: Section setup — extract excerpts from global documents
    # -----------------------------------------------------------------
    proposal_excerpt = (artifacts / "sections"
                        / f"section-{section.number}-proposal-excerpt.md")
    alignment_excerpt = (artifacts / "sections"
                         / f"section-{section.number}-alignment-excerpt.md")

    # Setup loop: runs until excerpts exist. Retries after pause/resume.
    while (
        not excerpt_exists(planspace, section.number, "proposal")
        or not excerpt_exists(planspace, section.number, "alignment")
    ):
        log(f"Section {section.number}: setup — extracting excerpts")
        setup_prompt = write_section_setup_prompt(
            section, planspace, codespace,
            section.global_proposal_path,
            section.global_alignment_path,
        )
        setup_output = artifacts / f"setup-{section.number}-output.md"
        setup_agent = f"setup-{section.number}"
        output = dispatch_agent(policy["setup"], setup_prompt, setup_output,
                                planspace, parent, setup_agent,
                                codespace=codespace,
                                section_number=section.number,
                                agent_file="setup-excerpter.md")
        if output == "ALIGNMENT_CHANGED_PENDING":
            return None
        mailbox_send(planspace, parent,
                     f"summary:setup:{section.number}:"
                     f"{summarize_output(output)}")

        signal_dir = artifacts / "signals"
        signal_dir.mkdir(parents=True, exist_ok=True)
        signal, detail = check_agent_signals(
            output,
            signal_path=signal_dir / f"setup-{section.number}-signal.json",
            output_path=setup_output,
            planspace=planspace, parent=parent, codespace=codespace,
        )
        if signal:
            # Surface needs-parent / out-of-scope as open problems
            if signal in ("needs_parent", "out_of_scope"):
                _append_open_problem(
                    planspace, section.number, detail, signal)
                mailbox_send(planspace, parent,
                             f"open-problem:{section.number}:"
                             f"{signal}:{detail[:200]}")
            if signal == "out_of_scope":
                scope_delta_dir = paths.scope_deltas_dir()
                scope_delta_dir.mkdir(parents=True, exist_ok=True)
                # Load full signal payload for richer coordinator context
                setup_sig_path = (signal_dir
                                  / f"setup-{section.number}-signal.json")
                signal_payload = read_json_or_default(setup_sig_path, {})
                scope_delta = {
                    "delta_id": f"delta-{section.number}-setup-oos",
                    "section": section.number,
                    "signal": "out_of_scope",
                    "detail": detail,
                    "requires_root_reframing": True,
                    "signal_path": str(setup_sig_path),
                    "signal_payload": signal_payload,
                }
                write_json(
                    scope_delta_dir
                    / f"section-{section.number}-scope-delta.json",
                    scope_delta,
                )
            _update_blocker_rollup(planspace)
            response = pause_for_parent(
                planspace, parent,
                f"pause:{signal}:{section.number}:{detail}",
            )
            if not response.startswith("resume"):
                return None
            # Persist resume payload and retry setup
            payload = response.partition(":")[2].strip()
            if payload:
                persist_decision(planspace, section.number, payload)
            if alignment_changed_pending(planspace):
                return None
            continue  # Retry setup with new decisions context

        # Verify excerpts were created
        if (
            not excerpt_exists(planspace, section.number, "proposal")
            or not excerpt_exists(planspace, section.number, "alignment")
        ):
            log(f"Section {section.number}: ERROR — setup failed to create "
                f"excerpt files")
            mailbox_send(planspace, parent,
                         f"fail:{section.number}:setup failed to create "
                         f"excerpt files")
            return None
        break  # Excerpts exist, proceed

    # -----------------------------------------------------------------
    # Step 1a: Problem frame quality gate (enforced)
    # -----------------------------------------------------------------
    problem_frame_path = (artifacts / "sections"
                          / f"section-{section.number}-problem-frame.md")
    if not problem_frame_path.exists():
        # Retry setup once — the agent may have failed to create it
        log(f"Section {section.number}: problem frame missing — retrying "
            f"setup once")
        retry_prompt = write_section_setup_prompt(
            section, planspace, codespace,
            section.global_proposal_path,
            section.global_alignment_path,
        )
        retry_output = artifacts / f"setup-{section.number}-retry-output.md"
        retry_result = dispatch_agent(
            policy["setup"], retry_prompt, retry_output,
            planspace, parent, f"setup-{section.number}-retry",
            codespace=codespace,
            section_number=section.number,
            agent_file="setup-excerpter.md",
        )
        if retry_result == "ALIGNMENT_CHANGED_PENDING":
            return None

    if not problem_frame_path.exists():
        # Still missing after retry — emit needs_parent signal and pause
        log(f"Section {section.number}: problem frame still missing after "
            f"retry — emitting needs_parent signal")
        pf_signal = {
            "state": "needs_parent",
            "detail": (
                f"Setup agent failed to create problem frame for section "
                f"{section.number} after 2 attempts. The pipeline requires "
                f"a problem frame before integration work can begin."
            ),
            "needs": (
                "Parent must either provide a problem frame or resolve "
                "why the setup agent cannot produce one."
            ),
            "why_blocked": (
                "Problem frame is a mandatory gate — without it, the "
                "pipeline cannot validate that the section is solving the "
                "right problem."
            ),
        }
        write_json(
            artifacts / "signals" / f"setup-{section.number}-signal.json",
            pf_signal,
        )
        _update_blocker_rollup(planspace)
        mailbox_send(planspace, parent,
                     f"pause:needs_parent:{section.number}:problem frame "
                     f"missing after retry")
        return None

    # V3/R68: Validate problem frame is non-empty — the agent chooses
    # headings, the script only checks the artifact has content.
    pf_content = problem_frame_path.read_text(encoding="utf-8").strip()
    if not pf_content:
        log(f"Section {section.number}: problem frame is empty")
        pf_signal = {
            "state": "needs_parent",
            "detail": (
                f"Problem frame for section {section.number} exists but "
                f"is empty"
            ),
            "needs": (
                "Parent must ensure the setup agent produces a non-empty "
                "problem frame."
            ),
            "why_blocked": (
                "Empty problem frame cannot validate problem "
                "understanding"
            ),
        }
        write_json(
            artifacts / "signals" / f"setup-{section.number}-signal.json",
            pf_signal,
        )
        _update_blocker_rollup(planspace)
        mailbox_send(planspace, parent,
                     f"pause:needs_parent:{section.number}:problem frame "
                     f"empty")
        return None

    log(f"Section {section.number}: problem frame present and validated")
    # P4: Problem frame hash stability — detect meaningful drift
    pf_hash_path = (artifacts / "signals"
                    / f"section-{section.number}-problem-frame-hash.txt")
    pf_hash_path.parent.mkdir(parents=True, exist_ok=True)
    current_pf_hash = file_hash(problem_frame_path)
    if pf_hash_path.exists():
        prev_pf_hash = pf_hash_path.read_text(encoding="utf-8").strip()
        if prev_pf_hash != current_pf_hash:
            log(f"Section {section.number}: problem frame changed — "
                f"forcing integration proposal re-run")
            # Invalidate existing integration proposal to force re-run
            existing_proposal = (
                artifacts / "proposals"
                / f"section-{section.number}-integration-proposal.md"
            )
            if existing_proposal.exists():
                existing_proposal.unlink()
                log(f"Section {section.number}: invalidated existing "
                    f"integration proposal due to problem frame change")
    pf_hash_path.write_text(current_pf_hash, encoding="utf-8")

    if (
        excerpt_exists(planspace, section.number, "proposal")
        and excerpt_exists(planspace, section.number, "alignment")
    ):
        log(f"Section {section.number}: setup — excerpts ready")
        _record_traceability(
            planspace, section.number,
            f"section-{section.number}-proposal-excerpt.md",
            str(section.global_proposal_path),
            "excerpt extraction from global proposal",
        )
        _record_traceability(
            planspace, section.number,
            f"section-{section.number}-alignment-excerpt.md",
            str(section.global_alignment_path),
            "excerpt extraction from global alignment",
        )
        _write_alignment_surface(planspace, section)

    # -----------------------------------------------------------------
    # Step 1.5a: Intent bootstrap (full mode only)
    # -----------------------------------------------------------------
    intent_mode = "lightweight"
    intent_budgets: dict = {}

    notes_count = 0
    notes_dir_check = artifacts / "notes"
    if notes_dir_check.exists():
        notes_count = len(list(
            notes_dir_check.glob(f"from-*-to-{section.number}.md")))

    triage_result = run_intent_triage(
        section.number, planspace, codespace, parent,
        related_files_count=len(section.related_files),
        incoming_notes_count=notes_count,
        solve_count=section.solve_count,
        section_summary=pf_content[:500] if pf_content else "",
    )
    intent_mode = triage_result.get("intent_mode", "lightweight")
    intent_budgets = triage_result.get("budgets", {})

    # -----------------------------------------------------------------
    # Step 1.5b: Extract TODO blocks from related files (conditional)
    # Must run BEFORE intent pack generation so TODOs (microstrategies)
    # are available as input to the intent pack generator (V5/R53).
    # -----------------------------------------------------------------
    todos_path = (artifacts / "todos"
                  / f"section-{section.number}-todos.md")
    if section.related_files:
        todos_path.parent.mkdir(parents=True, exist_ok=True)
        todo_entries = _extract_todos_from_files(codespace, section.related_files)
        if todo_entries:
            todos_path.write_text(todo_entries, encoding="utf-8")
            log(f"Section {section.number}: extracted TODOs from "
                f"related files")
            _record_traceability(
                planspace, section.number,
                f"section-{section.number}-todos.md",
                "related files TODO extraction",
                "in-code microstrategies for alignment",
            )
        elif todos_path.exists():
            todos_path.unlink()
            log(f"Section {section.number}: removed stale TODO extraction "
                f"(no TODOs remaining)")
            _record_traceability(
                planspace, section.number,
                f"section-{section.number}-todos.md",
                "related files TODO extraction",
                "in-code microstrategies for alignment",
            )
        else:
            log(f"Section {section.number}: no TODOs found in related files")

    # V1/R68: Ensure global philosophy unconditionally — the user's
    # execution philosophy is a project-level invariant, not a
    # complexity-triggered feature. Per-section intent packs remain
    # conditional on triage mode.
    philosophy_result = ensure_global_philosophy(
        planspace, codespace, parent)
    if alignment_changed_pending(planspace):
        return None

    # V1/R75: Philosophy is a project-level invariant. Its absence
    # blocks section execution rather than degrading strategic mode.
    # Solving locally without the root frame increases cycles.
    if philosophy_result is None:
        log(f"Section {section.number}: philosophy unavailable — "
            f"blocking section (project-level invariant)")
        blocker = {
            "section": section.number,
            "blocker": "philosophy_unavailable",
            "reason": (
                "Global philosophy could not be established. "
                "Section execution blocked until resolved."
            ),
        }
        write_json(
            artifacts / "signals"
            / f"philosophy-blocker-{section.number}.json",
            blocker,
        )
        return None

    if intent_mode == "full":
        # Generate per-section intent pack
        generate_intent_pack(
            section, planspace, codespace, parent,
            incoming_notes=incoming_notes or "",
        )
        if alignment_changed_pending(planspace):
            return None

        log(f"Section {section.number}: intent bootstrap complete "
            f"(full mode)")

    if intent_mode == "lightweight":
        log(f"Section {section.number}: lightweight intent mode")

    # Merge intent budgets into cycle budget (V7/R53: include
    # proposal_max and implementation_max alongside intent_ and max_new_)
    if intent_budgets:
        _triage_budget_keys = frozenset(
            ("proposal_max", "implementation_max"))
        cycle_budget_path_ib = (artifacts / "signals"
                                / f"section-{section.number}-cycle-budget.json")
        existing_budget = read_json(cycle_budget_path_ib)
        if existing_budget is not None:
            existing_budget.update({
                k: v for k, v in intent_budgets.items()
                if (k.startswith("intent_") or k.startswith("max_new_")
                    or k in _triage_budget_keys)
            })
            write_json(cycle_budget_path_ib, existing_budget)

    # -----------------------------------------------------------------
    # Step 2: Integration proposal loop
    # -----------------------------------------------------------------
    integration_proposal = (artifacts / "proposals"
                            / f"section-{section.number}-integration-proposal.md")
    proposal_problems: str | None = None
    proposal_attempt = 0

    # Cycle budget: read per-section budget or use defaults
    cycle_budget_path = (artifacts / "signals"
                         / f"section-{section.number}-cycle-budget.json")
    cycle_budget = {"proposal_max": 5, "implementation_max": 5}
    _loaded_budget = read_json(cycle_budget_path)
    if _loaded_budget is not None:
        cycle_budget.update(_loaded_budget)

    while True:
        # Check for pending messages between iterations
        if handle_pending_messages(planspace, [], set()):
            mailbox_send(planspace, parent,
                         f"fail:{section.number}:aborted")
            return None  # abort

        # Bail out if alignment_changed arrived (excerpts deleted)
        if alignment_changed_pending(planspace):
            log(f"Section {section.number}: alignment changed — "
                "aborting section to restart Phase 1")
            return None

        proposal_attempt += 1

        # Cycle budget check for proposal loop
        if proposal_attempt > cycle_budget["proposal_max"]:
            log(f"Section {section.number}: proposal cycle budget exhausted "
                f"({cycle_budget['proposal_max']} attempts)")
            budget_signal = {
                "section": section.number,
                "loop": "proposal",
                "attempts": proposal_attempt - 1,
                "budget": cycle_budget["proposal_max"],
                "escalate": True,
            }
            budget_signal_path = (artifacts / "signals"
                                  / f"section-{section.number}"
                                  f"-proposal-budget-exhausted.json")
            write_json(budget_signal_path, budget_signal)
            mailbox_send(planspace, parent,
                         f"budget-exhausted:{section.number}:proposal:"
                         f"{proposal_attempt - 1}")
            response = pause_for_parent(
                planspace, parent,
                f"pause:budget_exhausted:{section.number}:"
                f"proposal loop exceeded {cycle_budget['proposal_max']} "
                f"attempts",
            )
            if not response.startswith("resume"):
                return None
            # Parent may have raised the budget — re-read
            _reloaded = read_json(cycle_budget_path)
            if _reloaded is not None:
                cycle_budget.update(_reloaded)
        tag = "revise " if proposal_problems else ""
        log(f"Section {section.number}: {tag}integration proposal "
            f"(attempt {proposal_attempt})")

        # 2a: GPT writes integration proposal
        # Adaptive model escalation: escalate on repeated misalignment
        # or heavy cross-section coupling
        proposal_model = policy["proposal"]
        notes_count = 0
        notes_dir = paths.notes_dir()
        if notes_dir.exists():
            notes_count = len(list(
                notes_dir.glob(f"from-*-to-{section.number}.md")))
        escalated_from = None
        triggers = policy.get("escalation_triggers", {})
        max_attempts = triggers.get("max_attempts_before_escalation", 3)
        stall_threshold = triggers.get("stall_count", 2)
        if proposal_attempt >= max_attempts or notes_count >= stall_threshold:
            escalated_from = proposal_model
            proposal_model = policy["escalation_model"]
            log(f"Section {section.number}: escalating to "
                f"{proposal_model} (attempt={proposal_attempt}, "
                f"notes={notes_count})")

        reason = (f"attempt={proposal_attempt}, notes={notes_count}"
                  if escalated_from
                  else "first attempt, default model")
        write_model_choice_signal(
            planspace, section.number, "integration-proposal",
            proposal_model, reason, escalated_from,
        )

        intg_prompt = write_integration_proposal_prompt(
            section, planspace, codespace, proposal_problems,
            incoming_notes=incoming_notes,
            model_policy=policy,
        )
        if intg_prompt is None:
            log(f"Section {section.number}: integration proposal prompt "
                f"blocked by template safety — skipping dispatch")
            return None

        # If a reconciliation result artifact exists for this section,
        # append it to the prompt so the proposer can see overlaps,
        # conflicts, and shared seam decisions from Phase 1b.
        recon_result = load_reconciliation_result(
            artifacts, section.number,
        )
        if recon_result and recon_result.get("affected"):
            recon_path = (
                artifacts / "reconciliation"
                / f"section-{section.number}-reconciliation-result.json"
            )
            with intg_prompt.open("a", encoding="utf-8") as f:
                f.write(
                    f"\n## Reconciliation Context\n\n"
                    f"This section was affected by cross-section "
                    f"reconciliation during Phase 1b. The reconciliation "
                    f"analysis found overlapping anchors, contract "
                    f"conflicts, or shared seams involving this section.\n\n"
                    f"Read the reconciliation result and adjust your "
                    f"proposal to account for shared anchors, resolved "
                    f"conflicts, and seam decisions:\n"
                    f"`{recon_path}`\n"
                )
            log(f"Section {section.number}: appended reconciliation "
                f"context to proposal prompt")

        intg_output = artifacts / f"intg-proposal-{section.number}-output.md"
        intg_agent = f"intg-proposal-{section.number}"
        intg_result = dispatch_agent(
            proposal_model, intg_prompt, intg_output,
            planspace, parent, intg_agent, codespace=codespace,
            section_number=section.number,
            agent_file="integration-proposer.md",
        )
        if intg_result == "ALIGNMENT_CHANGED_PENDING":
            return None
        mailbox_send(planspace, parent,
                     f"summary:proposal:{section.number}:"
                     f"{summarize_output(intg_result)}")

        # Detect timeout explicitly (callers handle, not dispatch_agent)
        if intg_result.startswith("TIMEOUT:"):
            log(f"Section {section.number}: integration proposal agent "
                f"timed out")
            mailbox_send(planspace, parent,
                         f"fail:{section.number}:integration proposal "
                         f"agent timed out")
            return None

        # V6: Submit agent-emitted follow-up work into the queue
        ingest_and_submit(
            planspace,
            db_path=planspace / "run.db",
            submitted_by=f"proposal-{section.number}",
            signal_path=artifacts / "signals"
            / f"task-requests-proposal-{section.number}.json",
            origin_refs=[
                str(artifacts / "proposals"
                    / f"section-{section.number}-integration-proposal.md"),
            ],
        )

        signal_dir = artifacts / "signals"
        signal_dir.mkdir(parents=True, exist_ok=True)
        signal, detail = check_agent_signals(
            intg_result,
            signal_path=signal_dir / f"proposal-{section.number}-signal.json",
            output_path=intg_output,
            planspace=planspace, parent=parent, codespace=codespace,
        )
        if signal:
            # Surface needs-parent / out-of-scope as open problems
            if signal in ("needs_parent", "out_of_scope"):
                _append_open_problem(
                    planspace, section.number, detail, signal)
                mailbox_send(planspace, parent,
                             f"open-problem:{section.number}:"
                             f"{signal}:{detail[:200]}")
            if signal == "out_of_scope":
                scope_delta_dir = paths.scope_deltas_dir()
                scope_delta_dir.mkdir(parents=True, exist_ok=True)
                # Load full signal payload for richer coordinator context
                proposal_sig_path = (signal_dir
                                     / f"proposal-{section.number}-signal.json")
                signal_payload = read_json_or_default(
                    proposal_sig_path, {})
                scope_delta = {
                    "delta_id": f"delta-{section.number}-proposal-oos",
                    "section": section.number,
                    "signal": "out_of_scope",
                    "detail": detail,
                    "requires_root_reframing": True,
                    "signal_path": str(proposal_sig_path),
                    "signal_payload": signal_payload,
                }
                write_json(
                    scope_delta_dir
                    / f"section-{section.number}-scope-delta.json",
                    scope_delta,
                )
            _update_blocker_rollup(planspace)
            response = pause_for_parent(
                planspace, parent,
                f"pause:{signal}:{section.number}:{detail}",
            )
            if not response.startswith("resume"):
                return None
            # Persist resume payload and retry the step
            payload = response.partition(":")[2].strip()
            if payload:
                persist_decision(planspace, section.number, payload)
            # Check if alignment changed during the pause
            if alignment_changed_pending(planspace):
                return None
            continue  # Restart proposal step with new context

        # Verify proposal was written
        if not integration_proposal.exists():
            log(f"Section {section.number}: ERROR — integration proposal "
                f"not written")
            mailbox_send(planspace, parent,
                         f"fail:{section.number}:integration proposal "
                         f"not written")
            return None

        # 2b: Opus checks alignment (intent-judge in full mode)
        log(f"Section {section.number}: proposal alignment check")
        align_prompt = write_integration_alignment_prompt(
            section, planspace, codespace,
        )
        align_output = (artifacts
                        / f"intg-align-{section.number}-output.md")
        # Select agent file: lightweight skips expansion, not
        # judgment — use intent-judge when intent artifacts exist
        # (regardless of mode), else fall back to alignment-judge
        intent_sec_dir = (artifacts / "intent" / "sections"
                          / f"section-{section.number}")
        has_intent_artifacts = (
            intent_sec_dir.exists()
            and (intent_sec_dir / "problem.md").exists()
        )
        alignment_agent_file = (
            "intent-judge.md" if has_intent_artifacts
            else "alignment-judge.md"
        )
        alignment_model = (
            policy.get("intent_judge", policy["alignment"])
            if has_intent_artifacts
            else policy["alignment"]
        )
        # No agent_name → no per-agent monitor for alignment checks
        # (Opus alignment prompts don't include narration instructions,
        # so a monitor would false-positive STALLED after 5 min silence)
        align_result = dispatch_agent(
            alignment_model, align_prompt, align_output,
            planspace, parent, codespace=codespace,
            section_number=section.number,
            agent_file=alignment_agent_file,
        )
        if align_result == "ALIGNMENT_CHANGED_PENDING":
            return None

        # Detect timeout on alignment check
        if align_result.startswith("TIMEOUT:"):
            log(f"Section {section.number}: proposal alignment check "
                f"timed out — retrying")
            proposal_problems = "Previous alignment check timed out."
            continue

        # 2c/2d: Check result
        problems = _extract_problems(
            align_result, output_path=align_output,
            planspace=planspace, parent=parent, codespace=codespace,
            adjudicator_model=policy.get("adjudicator", "glm"),
        )

        signal_dir = artifacts / "signals"
        signal_dir.mkdir(parents=True, exist_ok=True)
        signal, detail = check_agent_signals(
            align_result,
            signal_path=(signal_dir
                         / f"proposal-align-{section.number}-signal.json"),
            output_path=align_output,
            planspace=planspace, parent=parent, codespace=codespace,
        )
        if signal == "underspec":
            response = pause_for_parent(
                planspace, parent,
                f"pause:underspec:{section.number}:{detail}",
            )
            if not response.startswith("resume"):
                return None
            payload = response.partition(":")[2].strip()
            if payload:
                persist_decision(planspace, section.number, payload)
            if alignment_changed_pending(planspace):
                return None
            continue

        if problems is None:
            # ALIGNED — check for intent surfaces before proceeding
            if intent_mode == "full":
                surfaces = load_intent_surfaces(section.number, planspace)
                if surfaces:
                    # Check expansion budget
                    expansion_max = intent_budgets.get(
                        "intent_expansion_max", 2)
                    expansion_count = getattr(
                        run_section, "_expansion_counts", {}
                    ).get(section.number, 0)
                    if expansion_count >= expansion_max:
                        log(f"Section {section.number}: intent expansion "
                            f"budget exhausted ({expansion_count}/"
                            f"{expansion_max}) — pausing for decision")
                        stalled_signal = {
                            "section": section.number,
                            "reason": "expansion budget exhausted",
                            "cycles": expansion_count,
                        }
                        write_json(
                            artifacts / "signals"
                            / f"intent-stalled-{section.number}.json",
                            stalled_signal,
                        )
                        response = pause_for_parent(
                            planspace, parent,
                            f"pause:intent-stalled:{section.number}:"
                            f"expansion budget exhausted "
                            f"({expansion_count}/{expansion_max})",
                        )
                        if not response.startswith("resume"):
                            return None
                    else:
                        log(f"Section {section.number}: surfaces found — "
                            f"running expansion cycle")
                        mailbox_send(
                            planspace, parent,
                            f"summary:intent-expand:{section.number}:"
                            f"cycle-{expansion_count + 1}")

                        delta_result = run_expansion_cycle(
                            section.number, planspace, codespace, parent,
                            budgets=intent_budgets,
                        )

                        # Track expansion count
                        if not hasattr(run_section, "_expansion_counts"):
                            run_section._expansion_counts = {}
                        run_section._expansion_counts[section.number] = (
                            expansion_count + 1
                        )

                        # Handle user gate if needed
                        if delta_result.get("needs_user_input"):
                            gate_response = handle_user_gate(
                                section.number, planspace, parent,
                                delta_result,
                            )
                            if (gate_response
                                    and not gate_response.startswith("resume")):
                                return None
                            from ..cross_section import persist_decision
                            payload = gate_response.partition(":")[2].strip()
                            if payload:
                                persist_decision(
                                    planspace, section.number, payload)
                            if alignment_changed_pending(planspace):
                                return None

                        # If expansion applied changes, re-propose
                        if delta_result.get("restart_required"):
                            proposal_problems = (
                                "Intent expanded; re-propose against "
                                "updated problem/philosophy definitions."
                            )
                            log(f"Section {section.number}: intent "
                                f"expanded — re-proposing")
                            continue  # Re-enter proposal loop

            log(f"Section {section.number}: integration proposal ALIGNED")
            mailbox_send(planspace, parent,
                         f"summary:proposal-align:{section.number}:ALIGNED")
            _write_alignment_surface(planspace, section)
            break

        # V5/R57: Persist intent surfaces even when misaligned.
        # Surfaces are strategic signals that should survive across
        # proposal attempts — merge into registry (no expansion).
        if intent_mode == "full":
            misaligned_surfaces = load_intent_surfaces(
                section.number, planspace)
            if misaligned_surfaces:
                registry = load_surface_registry(
                    section.number, planspace)
                misaligned_surfaces = normalize_surface_ids(
                    misaligned_surfaces, registry, section.number)
                merge_surfaces_into_registry(
                    registry, misaligned_surfaces)
                save_surface_registry(
                    section.number, planspace, registry)
                log(f"Section {section.number}: persisted intent "
                    f"surfaces from misaligned pass")

        # Problems found — feed back into next proposal attempt
        proposal_problems = problems
        short = problems[:200]
        log(f"Section {section.number}: integration proposal problems "
            f"(attempt {proposal_attempt}): {short}")
        mailbox_send(planspace, parent,
                     f"summary:proposal-align:{section.number}:"
                     f"PROBLEMS-attempt-{proposal_attempt}:{short}")

    # -----------------------------------------------------------------
    # Readiness gate — fail-closed check before implementation dispatch
    # -----------------------------------------------------------------
    proposal_state_path = (
        artifacts / "proposals"
        / f"section-{section.number}-proposal-state.json"
    )
    ps = load_proposal_state(proposal_state_path)

    # ---------------------------------------------------------------
    # Publish discoveries unconditionally — readiness decides descent,
    # not whether a discovery becomes durable.
    # ---------------------------------------------------------------

    # new_section_candidates → scope-delta artifacts
    scope_delta_dir = PathRegistry(planspace).scope_deltas_dir()
    for candidate in ps.get("new_section_candidates", []):
        scope_delta_dir.mkdir(parents=True, exist_ok=True)
        cand_text = str(candidate)
        cand_hash = content_hash(cand_text)[:8]
        delta_id = f"delta-{section.number}-candidate-{cand_hash}"
        scope_delta = {
            "delta_id": delta_id,
            "section": section.number,
            "signal": "new_section_candidate",
            "detail": cand_text,
            "requires_root_reframing": False,
            "source": "proposal-state:new_section_candidates",
        }
        delta_path = (scope_delta_dir
                      / f"section-{section.number}"
                        f"-candidate-{cand_hash}-scope-delta.json")
        write_json(delta_path, scope_delta)
        log(f"Section {section.number}: wrote scope-delta for "
            f"new_section_candidate ({cand_hash})")

    # research_questions → durable open-problem artifacts
    for question in ps.get("research_questions", []):
        _append_open_problem(
            planspace, section.number,
            str(question), "proposal-state:research_question",
        )
    rq_list = ps.get("research_questions", [])
    if rq_list:
        open_problems_dir = artifacts / "open-problems"
        open_problems_dir.mkdir(parents=True, exist_ok=True)
        rq_artifact = {
            "section": section.number,
            "research_questions": [str(q) for q in rq_list],
            "source": "proposal-state",
        }
        rq_path = (open_problems_dir
                    / f"section-{section.number}"
                      f"-research-questions.json")
        write_json(rq_path, rq_artifact)
        log(f"Section {section.number}: wrote {len(rq_list)} "
            f"research questions to open-problems artifact")

    # ---------------------------------------------------------------
    # Readiness check
    # ---------------------------------------------------------------
    readiness = resolve_readiness(artifacts, section.number)
    if not readiness.get("ready"):
        blockers = readiness.get("blockers", [])
        rationale = readiness.get("rationale", "unknown")
        log(f"Section {section.number}: execution blocked — "
            f"readiness=false, rationale={rationale}, "
            f"blockers={len(blockers)}")
        for b in blockers:
            log(f"  blocker: {b.get('type')}: {b.get('description')}")
        mailbox_send(planspace, parent,
                     f"fail:{section.number}:readiness gate blocked "
                     f"({rationale})")

        # ---------------------------------------------------------------
        # Route blocker-specific fields to their mechanical consumers.
        # ---------------------------------------------------------------
        signal_dir = artifacts / "signals"
        signal_dir.mkdir(parents=True, exist_ok=True)

        # 1. user_root_questions → NEED_DECISION / NEEDS_PARENT signals
        for i, question in enumerate(ps.get("user_root_questions", [])):
            q_signal = {
                "state": "need_decision",
                "section": section.number,
                "detail": str(question),
                "needs": "User/parent decision on this question",
                "why_blocked": (
                    "Proposal has an unresolved user-root question "
                    "that must be answered before implementation"
                ),
                "source": "proposal-state:user_root_questions",
            }
            sig_path = (signal_dir
                        / f"section-{section.number}"
                          f"-proposal-q{i}-signal.json")
            write_json(sig_path, q_signal)
            log(f"Section {section.number}: emitted NEED_DECISION "
                f"signal for user_root_question[{i}]")

        # 2. shared_seam_candidates → substrate-trigger signals
        for i, seam in enumerate(ps.get("shared_seam_candidates", [])):
            trigger = {
                "section": section.number,
                "seam": str(seam),
                "source": "proposal-state:shared_seam_candidates",
                "trigger_type": "shared_seam",
            }
            trigger_path = (signal_dir
                            / f"substrate-trigger-{section.number}"
                              f"-{i:02d}.json")
            write_json(trigger_path, trigger)
            log(f"Section {section.number}: wrote substrate-trigger "
                f"for shared_seam_candidate[{i}]")

        # Also emit NEEDS_PARENT signals for shared seam candidates
        # so they appear in the blocker rollup
        for i, seam in enumerate(ps.get("shared_seam_candidates", [])):
            seam_signal = {
                "state": "needs_parent",
                "section": section.number,
                "detail": (
                    f"Shared seam candidate requires cross-section "
                    f"substrate work: {str(seam)}"
                ),
                "needs": (
                    "SIS/substrate coordination for shared seam"
                ),
                "why_blocked": (
                    "Shared seam cannot be resolved within a single "
                    "section — requires substrate-level coordination"
                ),
                "source": "proposal-state:shared_seam_candidates",
            }
            sig_path = (signal_dir
                        / f"section-{section.number}"
                          f"-seam-{i}-signal.json")
            write_json(sig_path, seam_signal)

        # 3. unresolved_contracts + unresolved_anchors →
        #    reconciliation queue
        uc = [str(c) for c in ps.get("unresolved_contracts", [])]
        ua = [str(a) for a in ps.get("unresolved_anchors", [])]
        if uc or ua:
            queue_reconciliation_request(
                artifacts, section.number, uc, ua,
            )
            log(f"Section {section.number}: queued reconciliation "
                f"request ({len(uc)} contracts, {len(ua)} anchors)")

        # Update the blocker rollup so all routed items appear in
        # the consolidated needs-input.md
        _update_blocker_rollup(planspace)

        if pass_mode == "proposal":
            return ProposalPassResult(
                section_number=section.number,
                proposal_aligned=True,
                execution_ready=False,
                blockers=blockers,
                needs_reconciliation=bool(uc or ua),
                proposal_state_path=str(proposal_state_path),
            )
        return None

    # -----------------------------------------------------------------
    # Proposal-mode exit: proposal aligned, execution ready — stop here
    # -----------------------------------------------------------------
    if pass_mode == "proposal":
        log(f"Section {section.number}: proposal pass complete — "
            f"execution_ready=true, deferring implementation")
        return ProposalPassResult(
            section_number=section.number,
            proposal_aligned=True,
            execution_ready=True,
            blockers=[],
            needs_reconciliation=False,
            proposal_state_path=str(proposal_state_path),
        )

    return _run_section_implementation_steps(
        planspace, codespace, section, parent,
        all_sections=all_sections,
        artifacts=artifacts, policy=policy,
    )


def _run_section_implementation_steps(
    planspace: Path, codespace: Path, section: Section, parent: str,
    *,
    all_sections: list[Section] | None = None,
    artifacts: Path,
    policy: dict,
) -> list[str] | None:
    """Execute microstrategy through post-completion for a section.

    This is the implementation half of the section pipeline, extracted so
    it can be called independently by ``_run_implementation_pass`` (two-pass
    mode) or inline from ``run_section`` (full mode).
    """
    # -----------------------------------------------------------------
    # Upstream freshness gate — prevent stale implementation dispatch
    # -----------------------------------------------------------------
    # If a reconciliation result or readiness artifact has changed
    # since this implementation was queued, the section may have been
    # reopened.  Re-resolve readiness and block if no longer ready.
    readiness = resolve_readiness(artifacts, section.number)
    if not readiness.get("ready"):
        log(f"Section {section.number}: implementation steps blocked — "
            f"upstream freshness check failed (execution_ready is false)")
        return None

    # A reconciliation result marking this section as affected means
    # cross-section conflicts exist that haven't been incorporated
    # into the proposal.  Block implementation to prevent stale work.
    recon_result = load_reconciliation_result(artifacts, section.number)
    if recon_result and recon_result.get("affected"):
        log(f"Section {section.number}: implementation steps blocked — "
            f"reconciliation result marks section as affected")
        return None

    # -----------------------------------------------------------------
    # Step 2.5: Generate microstrategy (agent-driven decision)
    # -----------------------------------------------------------------
    # The microstrategy decider decides whether a microstrategy is needed
    # by writing a structured JSON signal. The script checks mechanically
    # — no hardcoded file-count thresholds.
    integration_proposal = (artifacts / "proposals"
                            / f"section-{section.number}-integration-proposal.md")
    microstrategy_path = (artifacts / "proposals"
                          / f"section-{section.number}-microstrategy.md")

    # Cycle budget: read per-section budget or use defaults
    cycle_budget_path = (artifacts / "signals"
                         / f"section-{section.number}-cycle-budget.json")
    cycle_budget = {"proposal_max": 5, "implementation_max": 5}
    _loaded_budget = read_json(cycle_budget_path)
    if _loaded_budget is not None:
        cycle_budget.update(_loaded_budget)

    # Tool registry state for post-impl validation
    tool_registry_path = artifacts / "tool-registry.json"
    pre_tool_total = 0
    registry = read_json(tool_registry_path)
    if registry is not None:
        all_tools = (registry if isinstance(registry, list)
                     else registry.get("tools", []))
        pre_tool_total = len(all_tools)
    needs_microstrategy = (
        _check_needs_microstrategy(
            integration_proposal, planspace, section.number, parent,
            codespace=codespace,
            model=policy.get("microstrategy_decider", "glm"),
            escalation_model=policy["escalation_model"])
        and not microstrategy_path.exists()
    )
    if not needs_microstrategy and not microstrategy_path.exists():
        log(f"Section {section.number}: microstrategy decider did not "
            f"request microstrategy — skipping")
    if needs_microstrategy:
        log(f"Section {section.number}: generating microstrategy")
        micro_prompt_path = (artifacts
                             / f"microstrategy-{section.number}-prompt.md")
        micro_output_path = (artifacts
                             / f"microstrategy-{section.number}-output.md")
        integration_proposal = (
            artifacts / "proposals"
            / f"section-{section.number}-integration-proposal.md"
        )
        a_name = f"microstrategy-{section.number}"
        m_name = f"{a_name}-monitor"

        file_list = "\n".join(
            f"- `{codespace / rp}`"
            for rp in section.related_files
        )
        todos_ref = ""
        section_todos = (artifacts / "todos"
                         / f"section-{section.number}-todos.md")
        if section_todos.exists():
            todos_ref = f"\nRead the TODO extraction: `{section_todos}`"

        rendered = f"""# Task: Microstrategy for Section {section.number}

## Context
Read the integration proposal: `{integration_proposal}`
Read the alignment excerpt: `{artifacts / "sections" / f"section-{section.number}-alignment-excerpt.md"}`{todos_ref}

## Related Files
{file_list}

## Instructions

The integration proposal describes the HIGH-LEVEL strategy for this
section. Your job is to produce a MICROSTRATEGY — a tactical per-file
breakdown that an implementation agent can follow directly.

For each file that needs changes, write:
1. **File path** and whether it's new or modified
2. **What changes** — specific functions, classes, or blocks to add/modify
3. **Order** — which file changes depend on which others
4. **Risks** — what could go wrong with this specific change

Write the microstrategy to: `{microstrategy_path}`

Keep it tactical and concrete. The integration proposal already justified
WHY — you're capturing WHAT and WHERE at the file level.

## Task Submission

If you need deeper analysis, submit a task request to:
`{artifacts}/signals/task-requests-micro-{section.number}.json`

Available task types: scan_deep_analyze, scan_explore

Write a single JSON object (legacy format), or use the v2 envelope
format with chain or fanout actions — see your agent file for the full
v2 format reference. {TASK_SUBMISSION_SEMANTICS}
{agent_mail_instructions(planspace, a_name, m_name)}
"""
        # V3: Validate dynamic content — violations block dispatch
        violations = validate_dynamic_content(rendered)
        if violations:
            log(f"  ERROR: prompt {micro_prompt_path.name} blocked — "
                f"template violations: {violations}")
            return None
        micro_prompt_path.write_text(rendered, encoding="utf-8")
        _log_artifact(planspace, f"prompt:microstrategy-{section.number}")

        ctrl = poll_control_messages(planspace, parent,
                                     current_section=section.number)
        if ctrl == "alignment_changed":
            return None
        micro_result = dispatch_agent(
            policy.get("implementation", "gpt-5.4-high"),
            micro_prompt_path, micro_output_path,
            planspace, parent, a_name, codespace=codespace,
            section_number=section.number,
            agent_file="microstrategy-writer.md",
        )
        if micro_result == "ALIGNMENT_CHANGED_PENDING":
            return None

        # V6: Submit agent-emitted follow-up work into the queue
        ingest_and_submit(
            planspace,
            db_path=planspace / "run.db",
            submitted_by=f"microstrategy-{section.number}",
            signal_path=artifacts / "signals"
            / f"task-requests-micro-{section.number}.json",
            origin_refs=[
                str(artifacts / "proposals"
                    / f"section-{section.number}-microstrategy.md"),
            ],
        )

        # -- V2/R43: Verify microstrategy output exists --
        if not microstrategy_path.exists() or microstrategy_path.stat().st_size == 0:
            log(f"Section {section.number}: microstrategy missing after "
                f"dispatch — retrying with escalation model")
            escalation_output = (artifacts
                                 / f"microstrategy-{section.number}-escalation-output.md")
            esc_result = dispatch_agent(
                policy["escalation_model"],
                micro_prompt_path, escalation_output,
                planspace, parent, f"{a_name}-escalation",
                codespace=codespace,
                section_number=section.number,
                agent_file="microstrategy-writer.md",
            )
            if esc_result == "ALIGNMENT_CHANGED_PENDING":
                return None

        if microstrategy_path.exists() and microstrategy_path.stat().st_size > 0:
            log(f"Section {section.number}: microstrategy generated")
        else:
            log(f"Section {section.number}: microstrategy generation "
                f"failed — emitting blocker signal")
            blocker = {
                "state": "NEEDS_PARENT",
                "section": str(section.number),
                "detail": (
                    "Microstrategy generation failed after primary "
                    "+ escalation attempts"
                ),
                "needs": (
                    "Tactical breakdown from upstream or decision "
                    "to proceed without microstrategy"
                ),
            }
            write_json(
                artifacts / "signals"
                / f"microstrategy-blocker-{section.number}.json",
                blocker,
            )
            _record_traceability(
                planspace, section.number,
                f"microstrategy-blocker-{section.number}.json",
                f"section-{section.number}-integration-proposal.md",
                "microstrategy generation failed — blocker emitted",
            )
            mailbox_send(planspace, parent,
                         f"summary:microstrategy:{section.number}:blocked")
            return None
        _record_traceability(
            planspace, section.number,
            f"section-{section.number}-microstrategy.md",
            f"section-{section.number}-integration-proposal.md",
            "tactical breakdown from integration proposal",
        )
        mailbox_send(planspace, parent,
                     f"summary:microstrategy:{section.number}:generated")

    # -----------------------------------------------------------------
    # Step 3: Strategic implementation
    # -----------------------------------------------------------------

    # Snapshot all known files before implementation.
    # Used after alignment to detect real vs. phantom modifications.
    all_known_paths = list(section.related_files)
    pre_hashes = snapshot_files(codespace, all_known_paths)

    impl_problems: str | None = None
    impl_attempt = 0

    while True:
        # Check for pending messages between iterations
        if handle_pending_messages(planspace, [], set()):
            mailbox_send(planspace, parent,
                         f"fail:{section.number}:aborted")
            return None  # abort

        # Bail out if alignment_changed arrived (excerpts deleted)
        if alignment_changed_pending(planspace):
            log(f"Section {section.number}: alignment changed — "
                "aborting section to restart Phase 1")
            return None

        impl_attempt += 1

        # Cycle budget check for implementation loop
        if impl_attempt > cycle_budget["implementation_max"]:
            log(f"Section {section.number}: implementation cycle budget "
                f"exhausted ({cycle_budget['implementation_max']} attempts)")
            budget_signal = {
                "section": section.number,
                "loop": "implementation",
                "attempts": impl_attempt - 1,
                "budget": cycle_budget["implementation_max"],
                "escalate": True,
            }
            budget_signal_path = (artifacts / "signals"
                                  / f"section-{section.number}"
                                  f"-impl-budget-exhausted.json")
            write_json(budget_signal_path, budget_signal)
            mailbox_send(planspace, parent,
                         f"budget-exhausted:{section.number}:implementation:"
                         f"{impl_attempt - 1}")
            response = pause_for_parent(
                planspace, parent,
                f"pause:budget_exhausted:{section.number}:"
                f"implementation loop exceeded "
                f"{cycle_budget['implementation_max']} attempts",
            )
            if not response.startswith("resume"):
                return None
            # Parent may have raised the budget — re-read
            _reloaded = read_json(cycle_budget_path)
            if _reloaded is not None:
                cycle_budget.update(_reloaded)

        tag = "fix " if impl_problems else ""
        log(f"Section {section.number}: {tag}strategic implementation "
            f"(attempt {impl_attempt})")

        # 3a: GPT implements strategically
        impl_prompt = write_strategic_impl_prompt(
            section, planspace, codespace, impl_problems,
            model_policy=policy,
        )
        if impl_prompt is None:
            log(f"Section {section.number}: strategic impl prompt "
                f"blocked by template safety — skipping dispatch")
            return None
        impl_output = artifacts / f"impl-{section.number}-output.md"
        impl_agent = f"impl-{section.number}"
        impl_result = dispatch_agent(
            policy.get("implementation", "gpt-5.4-high"),
            impl_prompt, impl_output,
            planspace, parent, impl_agent, codespace=codespace,
            section_number=section.number,
            agent_file="implementation-strategist.md",
        )
        if impl_result == "ALIGNMENT_CHANGED_PENDING":
            return None
        mailbox_send(planspace, parent,
                     f"summary:impl:{section.number}:"
                     f"{summarize_output(impl_result)}")

        # Detect timeout explicitly
        if impl_result.startswith("TIMEOUT:"):
            log(f"Section {section.number}: implementation agent timed out")
            mailbox_send(planspace, parent,
                         f"fail:{section.number}:implementation agent "
                         f"timed out")
            return None

        # V6: Submit agent-emitted follow-up work into the queue
        ingest_and_submit(
            planspace,
            db_path=planspace / "run.db",
            submitted_by=f"implementation-{section.number}",
            signal_path=artifacts / "signals"
            / f"task-requests-impl-{section.number}.json",
            origin_refs=[
                str(artifacts / f"impl-{section.number}-output.md"),
            ],
        )

        signal_dir = artifacts / "signals"
        signal_dir.mkdir(parents=True, exist_ok=True)
        signal, detail = check_agent_signals(
            impl_result,
            signal_path=signal_dir / f"impl-{section.number}-signal.json",
            output_path=(artifacts
                         / f"impl-{section.number}-output.md"),
            planspace=planspace, parent=parent, codespace=codespace,
        )
        if signal:
            response = pause_for_parent(
                planspace, parent,
                f"pause:{signal}:{section.number}:{detail}",
            )
            if not response.startswith("resume"):
                return None
            # Persist resume payload and retry the step
            payload = response.partition(":")[2].strip()
            if payload:
                persist_decision(planspace, section.number, payload)
            if alignment_changed_pending(planspace):
                return None
            continue  # Restart implementation step with new context

        # 3b: Opus checks implementation alignment
        log(f"Section {section.number}: implementation alignment check")
        impl_align_prompt = write_impl_alignment_prompt(
            section, planspace, codespace,
        )
        impl_align_output = (artifacts
                             / f"impl-align-{section.number}-output.md")
        # No agent_name → no per-agent monitor (same rationale as 2b)
        impl_align_result = dispatch_agent(
            policy["alignment"], impl_align_prompt, impl_align_output,
            planspace, parent, codespace=codespace,
            section_number=section.number,
            agent_file="alignment-judge.md",
        )
        if impl_align_result == "ALIGNMENT_CHANGED_PENDING":
            return None

        # Detect timeout on alignment check
        if impl_align_result.startswith("TIMEOUT:"):
            log(f"Section {section.number}: implementation alignment check "
                f"timed out — retrying")
            impl_problems = "Previous alignment check timed out."
            continue

        # 3c/3d: Check result
        problems = _extract_problems(
            impl_align_result, output_path=impl_align_output,
            planspace=planspace, parent=parent, codespace=codespace,
            adjudicator_model=policy.get("adjudicator", "glm"),
        )

        signal_dir = artifacts / "signals"
        signal_dir.mkdir(parents=True, exist_ok=True)
        signal, detail = check_agent_signals(
            impl_align_result,
            signal_path=(signal_dir
                         / f"impl-align-{section.number}-signal.json"),
            output_path=impl_align_output,
            planspace=planspace, parent=parent, codespace=codespace,
        )
        if signal == "underspec":
            response = pause_for_parent(
                planspace, parent,
                f"pause:underspec:{section.number}:{detail}",
            )
            if not response.startswith("resume"):
                return None
            payload = response.partition(":")[2].strip()
            if payload:
                persist_decision(planspace, section.number, payload)
            if alignment_changed_pending(planspace):
                return None
            continue

        if problems is None:
            # ALIGNED — section complete
            log(f"Section {section.number}: implementation ALIGNED")
            mailbox_send(planspace, parent,
                         f"summary:impl-align:{section.number}:ALIGNED")
            break

        # Problems found — feed back into next implementation attempt
        impl_problems = problems
        short = problems[:200]
        log(f"Section {section.number}: implementation problems "
            f"(attempt {impl_attempt}): {short}")
        mailbox_send(planspace, parent,
                     f"summary:impl-align:{section.number}:"
                     f"PROBLEMS-attempt-{impl_attempt}:{short}")

    # Validate modifications against actual file content changes.
    # Two categories:
    # 1. Snapshotted files (related_files) — verified via content-hash diff
    # 2. Reported-but-not-snapshotted files — trusted as "touched" only if
    #    they exist on disk (avoids inflated counts from empty-hash default)
    reported = collect_modified_files(planspace, section, codespace)
    snapshotted_set = set(section.related_files)
    # Diff snapshotted files (related_files union reported that were snapshotted)
    snapshotted_candidates = sorted(
        snapshotted_set | (set(reported) & set(pre_hashes))
    )
    verified_changed = diff_files(codespace, pre_hashes, snapshotted_candidates)
    # Files reported but NOT in the pre-snapshot — include if they exist
    unsnapshotted_reported = [
        rp for rp in reported
        if rp not in pre_hashes and (codespace / rp).exists()
    ]
    if unsnapshotted_reported:
        log(f"Section {section.number}: {len(unsnapshotted_reported)} "
            f"reported files were outside the pre-snapshot set (trusted)")
    actually_changed = sorted(set(verified_changed) | set(unsnapshotted_reported))
    if len(reported) != len(actually_changed):
        log(f"Section {section.number}: {len(reported)} reported, "
            f"{len(actually_changed)} actually changed (detected via diff)")

    # Record change provenance in traceability chain
    for changed_file in actually_changed:
        _record_traceability(
            planspace, section.number,
            changed_file,
            f"section-{section.number}-integration-proposal.md",
            "implementation change",
        )

    # Write traceability index for this section (P2)
    _write_traceability_index(planspace, section, codespace, actually_changed)

    # Write trace-map artifact (P3: stable TODO ID → problem chain)
    trace_map_dir = artifacts / "trace-map"
    trace_map_dir.mkdir(parents=True, exist_ok=True)
    trace_map_path = trace_map_dir / f"section-{section.number}.json"
    trace_map = {
        "section": section.number,
        "problems": [],
        "strategies": [],
        "todo_ids": [],
        "files": list(actually_changed),
    }
    # Extract problems from problem frame
    pf_path = (artifacts / "sections"
               / f"section-{section.number}-problem-frame.md")
    if pf_path.exists():
        pf_text = pf_path.read_text(encoding="utf-8")
        for line in pf_text.split("\n"):
            stripped = line.strip()
            if stripped.startswith("- ") or stripped.startswith("* "):
                trace_map["problems"].append(stripped[2:])
    # Extract TODO IDs from related files
    for rel_path in section.related_files:
        full = codespace / rel_path
        if not full.exists():
            continue
        try:
            content = full.read_text(encoding="utf-8")
            for match in re.finditer(r'TODO\[([^\]]+)\]', content):
                trace_map["todo_ids"].append({
                    "id": match.group(1),
                    "file": rel_path,
                })
        except (OSError, UnicodeDecodeError):
            continue
    write_json(trace_map_path, trace_map)
    log(f"Section {section.number}: trace-map written to {trace_map_path}")

    # -----------------------------------------------------------------
    # Step 3b: Validate tool registry after implementation
    # -----------------------------------------------------------------
    friction_signal_path = (artifacts / "signals"
                            / f"section-{section.number}-tool-friction.json")
    if tool_registry_path.exists():
        try:
            post_registry = json.loads(
                tool_registry_path.read_text(encoding="utf-8"),
            )
            post_tools = (post_registry if isinstance(post_registry, list)
                          else post_registry.get("tools", []))
            # Check if implementation added new tools
            if len(post_tools) > pre_tool_total:
                log(f"Section {section.number}: new tools registered — "
                    f"dispatching tool-registrar for validation")
                registrar_prompt = (
                    artifacts / f"tool-registrar-{section.number}-prompt.md"
                )
                registrar_prompt.write_text(
                    f"# Validate Tool Registry\n\n"
                    f"Section {section.number} just completed implementation.\n"
                    f"Validate the tool registry at: `{tool_registry_path}`\n\n"
                    f"For each tool entry:\n"
                    f"1. Read the tool file and verify it exists and is "
                    f"legitimate\n"
                    f"2. Verify scope classification is correct\n"
                    f"3. Ensure required fields exist: `id`, `path`, "
                    f"`created_by`, `scope`, `status`, `description`, "
                    f"`registered_at`\n"
                    f"4. If `id` is missing, assign a short kebab-case "
                    f"identifier\n"
                    f"5. If `status` is missing, set to `experimental`\n"
                    f"6. Promote tools to `stable` if they have passing "
                    f"tests or are used by multiple sections\n"
                    f"7. Remove entries for files that don't exist or "
                    f"aren't tools\n"
                    f"8. If any cross-section tools were added, verify "
                    f"they are genuinely reusable\n\n"
                    f"After validation, write a tool digest to: "
                    f"`{artifacts / 'tool-digest.md'}`\n"
                    f"Format: one line per tool grouped by scope "
                    f"(cross-section, section-local, test-only).\n\n"
                    f"Write the validated registry back to the same path.\n\n"
                    f"## Tool Friction Detection\n\n"
                    f"After validation, analyze the capability graph for "
                    f"disconnected tool islands or missing bridges. If you "
                    f"detect friction, write a friction signal to:\n"
                    f"`{friction_signal_path}`\n\n"
                    f"Format: `{{\"friction\": true, \"islands\": [[...]], "
                    f"\"missing_bridge\": \"...\"}}`\n"
                    f"If no friction detected, do NOT write a friction "
                    f"signal file.\n",
                    encoding="utf-8",
                )
                registrar_output = (
                    artifacts / f"tool-registrar-{section.number}-output.md"
                )
                dispatch_agent(
                    policy.get("tool_registrar", "glm"),
                    registrar_prompt, registrar_output,
                    planspace, parent,
                    f"tool-registrar-{section.number}",
                    codespace=codespace,
                    agent_file="tool-registrar.md",
                    section_number=section.number,
                )
        except (json.JSONDecodeError, ValueError) as exc:
            # Preserve corrupted registry before repair (V8/R55)
            malformed_path = tool_registry_path.with_suffix(
                ".malformed.json")
            try:
                import shutil
                shutil.copy2(tool_registry_path, malformed_path)
            except OSError:
                pass  # Best-effort preserve
            # Fail-closed: dispatch repair instead of silently
            # proceeding (R34/V2)
            log(f"Section {section.number}: post-impl registry "
                f"malformed ({exc}) — dispatching repair "
                f"(original preserved as {malformed_path.name})")
            repair_prompt = (
                artifacts
                / f"tool-registry-post-repair-{section.number}-prompt.md"
            )
            repair_output = (
                artifacts
                / f"tool-registry-post-repair-{section.number}-output.md"
            )
            repair_prompt.write_text(
                f"# Task: Repair Tool Registry (Post-Implementation)\n\n"
                f"The tool registry at `{tool_registry_path}` became "
                f"malformed after section {section.number} "
                f"implementation.\n\nError: {exc}\n\n"
                f"Read the file, reconstruct valid JSON preserving all "
                f"tool entries, and write back to the same path.\n",
                encoding="utf-8",
            )
            dispatch_agent(
                policy.get("tool_registrar", "glm"),
                repair_prompt, repair_output,
                planspace, parent, codespace=codespace,
                section_number=section.number,
                agent_file="tool-registrar.md",
            )
            # Verify repair succeeded
            if read_json(tool_registry_path) is not None:
                log(f"Section {section.number}: post-impl tool "
                    f"registry repaired")
            else:
                log(f"Section {section.number}: post-impl tool "
                    f"registry repair failed — writing blocker")
                blocker = {
                    "state": "needs_parent",
                    "detail": (
                        "Tool registry malformed after "
                        "implementation; repair agent could "
                        "not fix it."
                    ),
                    "needs": "Valid tool-registry.json",
                    "why_blocked": (
                        "Malformed registry affects subsequent "
                        "sections' tool surfacing."
                    ),
                }
                write_json(
                    artifacts / "signals"
                    / f"section-{section.number}-post-impl-blocker.json",
                    blocker,
                )
                _update_blocker_rollup(planspace)

    # -----------------------------------------------------------------
    # Step 3c: Detect tooling friction and dispatch bridge-tools agent
    # -----------------------------------------------------------------
    tool_friction_detected = False
    if friction_signal_path.exists():
        friction = read_json(friction_signal_path)
        if friction is not None:
            tool_friction_detected = friction.get("friction", False)
        else:
            # File existed but was corrupt — treat as friction
            # detected (fail closed). read_json already preserved
            # the malformed file.
            tool_friction_detected = True

    if tool_friction_detected and tool_registry_path.exists():
        log(f"Section {section.number}: tooling friction detected — "
            f"dispatching bridge-tools agent")
        bridge_tools_prompt = (
            artifacts / f"bridge-tools-{section.number}-prompt.md")
        bridge_tools_output = (
            artifacts / f"bridge-tools-{section.number}-output.md")
        bridge_signal_path = (
            artifacts / "signals"
            / f"section-{section.number}-tool-bridge.json")
        default_proposal_path = (
            artifacts / "proposals"
            / f"section-{section.number}-tool-bridge.md")
        bridge_tools_prompt.write_text(f"""# Task: Bridge Tool Islands for Section {section.number}

## Context
Section {section.number} has signaled tooling friction — tools don't compose
cleanly or a needed tool doesn't exist.

## Files to Read
1. Tool registry: `{tool_registry_path}`
2. Section specification: `{section.path}`
3. Integration proposal: `{artifacts / "proposals" / f"section-{section.number}-integration-proposal.md"}`

## Instructions
Analyze the tool registry and section needs. Either:
(a) Propose a new tool that bridges the gap
(b) Propose a composition pattern connecting existing tools

Write your proposal to: `{default_proposal_path}`
Update the tool registry if new tools are proposed.

## Structured Signal (Required)
Write a structured signal to: `{bridge_signal_path}`
with JSON:
```json
{{
  "status": "bridged"|"no_action"|"needs_parent",
  "proposal_path": "...",
  "notes": "...",
  "targets": ["03", "07"],
  "broadcast": false,
  "note_markdown": "..."
}}
```

- `targets` (optional): section numbers that need this bridge info
- `broadcast` (optional): if true, all sections receive a note
- `note_markdown` (optional): summary for target sections
""", encoding="utf-8")

        # Part 4: Hash tool registry before bridge dispatch
        pre_bridge_registry_hash = ""
        if tool_registry_path.exists():
            pre_bridge_registry_hash = file_hash(tool_registry_path)

        dispatch_agent(
            policy.get("bridge_tools", "gpt-5.4-high"),
            bridge_tools_prompt,
            bridge_tools_output,
            planspace, parent,
            f"bridge-tools-{section.number}",
            codespace=codespace,
            agent_file="bridge-tools.md",
            section_number=section.number,
        )

        # -- V1/R43: Verify bridge-tools output --
        bridge_valid = False
        bridge_data = read_json(bridge_signal_path)
        if bridge_data is not None:
            if bridge_data.get("status") in (
                "bridged", "no_action", "needs_parent",
            ):
                proposal_path = Path(bridge_data.get(
                    "proposal_path", str(default_proposal_path)))
                if (bridge_data["status"] == "no_action"
                        or proposal_path.exists()):
                    bridge_valid = True

        if not bridge_valid:
            log(f"Section {section.number}: bridge signal missing or "
                f"invalid — retrying with escalation model")
            escalation_output = (
                artifacts
                / f"bridge-tools-{section.number}-escalation-output.md")
            dispatch_agent(
                policy["escalation_model"],
                bridge_tools_prompt,
                escalation_output,
                planspace, parent,
                f"bridge-tools-{section.number}-escalation",
                codespace=codespace,
                agent_file="bridge-tools.md",
                section_number=section.number,
            )
            # Re-check after escalation
            bridge_data = read_json(bridge_signal_path)
            if bridge_data is not None:
                if bridge_data.get("status") in (
                    "bridged", "no_action", "needs_parent",
                ):
                    proposal_path = Path(bridge_data.get(
                        "proposal_path", str(default_proposal_path)))
                    if (bridge_data["status"] == "no_action"
                            or proposal_path.exists()):
                        bridge_valid = True

        # -- R44/V1: Wire bridge outputs into downstream channels --
        if bridge_valid:
            # Part 1: Write .ref input for downstream reasoning
            bridge_proposal = bridge_data.get(
                "proposal_path", str(default_proposal_path))
            inputs_dir = artifacts / "inputs" / f"section-{section.number}"
            inputs_dir.mkdir(parents=True, exist_ok=True)
            ref_file = inputs_dir / "tool-bridge.ref"
            ref_file.write_text(str(bridge_proposal), encoding="utf-8")
            log(f"Section {section.number}: bridge proposal registered "
                f"as input ref")

            # Part 2: Cross-section note routing
            targets = bridge_data.get("targets", [])
            broadcast = bridge_data.get("broadcast", False)
            note_md = bridge_data.get("note_markdown", "")
            if note_md and (targets or broadcast):
                if broadcast and all_sections:
                    # All sections except self
                    targets = [s.number for s in all_sections
                               if s.number != section.number]
                for target in targets:
                    write_consequence_note(
                        planspace,
                        f"bridge-{section.number}",
                        str(target),
                        f"# Bridge Note from Section {section.number}\n\n"
                        f"{note_md}\n\n"
                        f"See full proposal: `{bridge_proposal}`\n",
                    )
                if targets:
                    log(f"Section {section.number}: bridge notes routed "
                        f"to {len(targets)} section(s)")

            # Part 4: Regenerate tool digest if bridge modified registry
            post_bridge_registry_hash = ""
            if tool_registry_path.exists():
                post_bridge_registry_hash = file_hash(tool_registry_path)
            if (post_bridge_registry_hash
                    and pre_bridge_registry_hash
                    != post_bridge_registry_hash):
                log(f"Section {section.number}: tool registry modified "
                    f"by bridge-tools — regenerating digest")
                digest_prompt = (
                    artifacts
                    / f"tool-digest-regen-{section.number}-prompt.md")
                digest_output = (
                    artifacts
                    / f"tool-digest-regen-{section.number}-output.md")
                digest_prompt.write_text(
                    f"# Task: Regenerate Tool Digest\n\n"
                    f"The tool registry at `{tool_registry_path}` was "
                    f"modified by bridge-tools for section "
                    f"{section.number}.\n\n"
                    f"Read the registry and write an updated tool digest "
                    f"to: `{artifacts / 'tool-digest.md'}`\n\n"
                    f"Format: one line per tool grouped by scope "
                    f"(cross-section, section-local, test-only).\n",
                    encoding="utf-8",
                )
                dispatch_agent(
                    policy.get("tool_registrar", "glm"),
                    digest_prompt, digest_output,
                    planspace, parent,
                    f"tool-digest-regen-{section.number}",
                    codespace=codespace,
                    section_number=section.number,
                    agent_file="tool-registrar.md",
                )
        else:
            # Part 3: Bridge failed after escalation — write blocker
            log(f"Section {section.number}: bridge-tools dispatch "
                f"failed after escalation — writing failure artifact")
            failure_artifact = (
                artifacts / "signals"
                / f"section-{section.number}-bridge-tools-failure.json")
            write_json(failure_artifact, {
                "section": section.number,
                "status": "failed",
                "reason": "bridge-tools agent did not produce valid "
                          "signal after primary + escalation dispatch",
            })
            # Also write a structured blocker for rollup
            write_json(
                artifacts / "signals"
                / f"section-{section.number}-post-impl-blocker.json",
                {
                    "state": "needs_parent",
                    "detail": (
                        "Bridge-tools agent failed to produce valid output "
                        "after primary + escalation dispatch. Tool friction "
                        "remains unresolved."
                    ),
                    "needs": "Manual review of tool composition gaps",
                    "why_blocked": (
                        f"See failure details: "
                        f"{failure_artifact}"
                    ),
                },
            )
            _update_blocker_rollup(planspace)

        # Acknowledge friction signal so it doesn't re-fire
        try:
            write_json(friction_signal_path, {
                "friction": False,
                "status": "handled",
            })
        except OSError:
            log(f"Section {section.number}: could not acknowledge "
                f"friction signal — file write failed")

    # -----------------------------------------------------------------
    # Step 4: Post-completion — snapshots, impact analysis, notes
    # -----------------------------------------------------------------
    if actually_changed and all_sections:
        post_section_completion(
            section, actually_changed, all_sections,
            planspace, codespace, parent,
            impact_model=policy.get("impact_analysis", "glm"),
            normalizer_model=policy.get("impact_normalizer", "glm"),
        )

    return actually_changed
