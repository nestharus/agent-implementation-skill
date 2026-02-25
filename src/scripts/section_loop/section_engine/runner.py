import hashlib
import json
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path

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
from ..pipeline_control import (
    alignment_changed_pending,
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
from ..types import Section
from .blockers import _append_open_problem, _update_blocker_rollup
from .reexplore import _write_alignment_surface
from .todos import _check_needs_microstrategy, _extract_todos_from_files
from .traceability import _file_sha256, _write_traceability_index


def run_section(
    planspace: Path, codespace: Path, section: Section, parent: str,
    all_sections: list[Section] | None = None,
) -> list[str] | None:
    """Run a section through the strategic flow.

    0. Read incoming notes from other sections (pre-section)
    1. Section setup (once) — extract proposal/alignment excerpts
    2. Integration proposal loop — GPT proposes, Opus checks alignment
    3. Strategic implementation — GPT implements, Opus checks alignment
    4. Post-completion — snapshot, impact analysis, consequence notes

    Returns modified files on success, or None if paused (waiting for
    parent to handle underspec/decision/dependency and send resume).
    """
    artifacts = planspace / "artifacts"
    policy = read_model_policy(planspace)

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
        recurrence_path = (planspace / "artifacts" / "signals"
                           / f"section-{section.number}-recurrence.json")
        recurrence_path.parent.mkdir(parents=True, exist_ok=True)
        recurrence_path.write_text(
            json.dumps(recurrence_signal, indent=2), encoding="utf-8")
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
        )

        # Read triage signal
        if triage_signal_path.exists():
            try:
                triage = json.loads(
                    triage_signal_path.read_text(encoding="utf-8"))
                needs_replan = triage.get("needs_replan", True)
                needs_code = triage.get("needs_code_change", True)
                if not needs_replan and not needs_code:
                    # Merge triage acknowledgments into note-ack file
                    triage_acks = triage.get("acknowledge", [])
                    ack_path = (artifacts / "signals"
                                / f"note-ack-{section.number}.json")
                    existing_acks: dict = {"acknowledged": []}
                    if ack_path.exists():
                        try:
                            existing_acks = json.loads(
                                ack_path.read_text(encoding="utf-8"))
                        except (json.JSONDecodeError, OSError) as exc:
                            # Preserve corrupted file for diagnosis
                            malformed_path = ack_path.with_suffix(
                                ".malformed.json")
                            try:
                                ack_path.rename(malformed_path)
                            except OSError:
                                pass  # Best-effort preserve
                            log(
                                f"Section {section.number}: note-ack "
                                f"file malformed ({exc}) — preserved "
                                f"as {malformed_path.name}, starting "
                                f"fresh"
                            )
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
                    ack_path.parent.mkdir(parents=True, exist_ok=True)
                    ack_path.write_text(
                        json.dumps(existing_acks, indent=2),
                        encoding="utf-8")

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
            except (json.JSONDecodeError, OSError):
                pass  # Fall through to full processing

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
            # Filter to section-relevant: cross-section tools + tools
            # created by this section (section-local from other sections
            # are not surfaced)
            sec_key = f"section-{section.number}"
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
                log(f"Section {section.number}: {len(relevant_tools)} "
                    f"relevant tools (of {len(all_tools)} total)")
            elif tools_available_path.exists():
                # No relevant tools — remove stale surface to prevent
                # agents from reasoning over outdated tool context.
                tools_available_path.unlink()
                log(f"Section {section.number}: removed stale "
                    f"tools-available surface (no relevant tools)")
        except (json.JSONDecodeError, ValueError) as exc:
            # Fail-closed: remove stale surface to prevent agents
            # from reasoning over outdated tool context (R34/V1)
            if tools_available_path.exists():
                tools_available_path.unlink()
                log(f"Section {section.number}: removed stale "
                    f"tools-available surface (malformed registry)")
            # Dispatch tool-registrar to attempt repair
            log(f"Section {section.number}: tool-registry.json "
                f"malformed ({exc}) — dispatching repair")
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
            )
            # Re-check after repair
            try:
                registry = json.loads(
                    tool_registry_path.read_text(encoding="utf-8"))
                all_tools = (registry if isinstance(registry, list)
                             else registry.get("tools", []))
                pre_tool_total = len(all_tools)
                log(f"Section {section.number}: tool registry "
                    f"repaired ({len(all_tools)} tools)")
            except (json.JSONDecodeError, ValueError):
                log(f"Section {section.number}: tool registry "
                    f"repair failed — writing blocker signal")
                signal_dir = artifacts / "signals"
                signal_dir.mkdir(parents=True, exist_ok=True)
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
                (signal_dir
                 / f"section-{section.number}-blocker.json"
                 ).write_text(
                    json.dumps(blocker, indent=2),
                    encoding="utf-8",
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
    while not proposal_excerpt.exists() or not alignment_excerpt.exists():
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
                scope_delta_dir = planspace / "artifacts" / "scope-deltas"
                scope_delta_dir.mkdir(parents=True, exist_ok=True)
                # Load full signal payload for richer coordinator context
                signal_payload = {}
                setup_sig_path = (signal_dir
                                  / f"setup-{section.number}-signal.json")
                if setup_sig_path.exists():
                    try:
                        signal_payload = json.loads(
                            setup_sig_path.read_text(encoding="utf-8"))
                    except (json.JSONDecodeError, OSError) as exc:
                        log(f"Section {section.number}: WARNING — "
                            f"malformed setup signal ({exc}), scope-delta "
                            f"will lack payload enrichment")
                scope_delta = {
                    "section": section.number,
                    "signal": "out_of_scope",
                    "detail": detail,
                    "requires_root_reframing": True,
                    "signal_path": str(setup_sig_path),
                    "signal_payload": signal_payload,
                }
                (scope_delta_dir
                 / f"section-{section.number}-scope-delta.json"
                 ).write_text(
                    json.dumps(scope_delta, indent=2), encoding="utf-8")
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
        if not proposal_excerpt.exists() or not alignment_excerpt.exists():
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
        signal_dir = artifacts / "signals"
        signal_dir.mkdir(parents=True, exist_ok=True)
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
        (signal_dir / f"setup-{section.number}-signal.json").write_text(
            json.dumps(pf_signal, indent=2), encoding="utf-8")
        _update_blocker_rollup(planspace)
        mailbox_send(planspace, parent,
                     f"pause:needs_parent:{section.number}:problem frame "
                     f"missing after retry")
        return None

    # Validate problem frame structure (required headings only, no semantics)
    pf_content = problem_frame_path.read_text(encoding="utf-8")
    required_headings = [
        "Problem Statement",
        "Evidence",
        "Constraints",
        "Success Criteria",
        "Out of Scope",
    ]
    missing_headings = [
        h for h in required_headings
        if h.lower() not in pf_content.lower()
    ]
    if missing_headings:
        log(f"Section {section.number}: problem frame missing required "
            f"headings: {missing_headings}")
        signal_dir = artifacts / "signals"
        signal_dir.mkdir(parents=True, exist_ok=True)
        pf_signal = {
            "state": "needs_parent",
            "detail": (
                f"Problem frame for section {section.number} is missing "
                f"required headings: {missing_headings}"
            ),
            "needs": (
                "Parent must ensure the setup agent produces a complete "
                "problem frame with all required sections."
            ),
            "why_blocked": (
                "Incomplete problem frame cannot validate problem "
                "understanding — missing: "
                + ", ".join(missing_headings)
            ),
        }
        (signal_dir / f"setup-{section.number}-signal.json").write_text(
            json.dumps(pf_signal, indent=2), encoding="utf-8")
        _update_blocker_rollup(planspace)
        mailbox_send(planspace, parent,
                     f"pause:needs_parent:{section.number}:problem frame "
                     f"incomplete — missing {missing_headings}")
        return None

    log(f"Section {section.number}: problem frame present and validated")
    # P4: Problem frame hash stability — detect meaningful drift
    pf_hash_path = (artifacts / "signals"
                    / f"section-{section.number}-problem-frame-hash.txt")
    pf_hash_path.parent.mkdir(parents=True, exist_ok=True)
    current_pf_hash = hashlib.sha256(
        problem_frame_path.read_bytes()).hexdigest()
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

    if proposal_excerpt.exists() and alignment_excerpt.exists():
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
    # Step 1.5: Extract TODO blocks from related files (conditional)
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
    if cycle_budget_path.exists():
        try:
            cycle_budget.update(
                json.loads(cycle_budget_path.read_text(encoding="utf-8")))
        except (json.JSONDecodeError, OSError):
            pass  # Use defaults

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
            budget_signal_path.parent.mkdir(parents=True, exist_ok=True)
            budget_signal_path.write_text(
                json.dumps(budget_signal, indent=2), encoding="utf-8")
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
            if cycle_budget_path.exists():
                try:
                    cycle_budget.update(json.loads(
                        cycle_budget_path.read_text(encoding="utf-8")))
                except (json.JSONDecodeError, OSError) as exc:
                    log(f"Section {section.number}: WARNING — "
                        f"malformed cycle-budget.json ({exc}), "
                        f"keeping previous budget")
        tag = "revise " if proposal_problems else ""
        log(f"Section {section.number}: {tag}integration proposal "
            f"(attempt {proposal_attempt})")

        # 2a: GPT writes integration proposal
        # Adaptive model escalation: escalate on repeated misalignment
        # or heavy cross-section coupling
        proposal_model = policy["proposal"]
        notes_count = 0
        notes_dir = planspace / "artifacts" / "notes"
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
                scope_delta_dir = planspace / "artifacts" / "scope-deltas"
                scope_delta_dir.mkdir(parents=True, exist_ok=True)
                # Load full signal payload for richer coordinator context
                signal_payload = {}
                proposal_sig_path = (signal_dir
                                     / f"proposal-{section.number}-signal.json")
                if proposal_sig_path.exists():
                    try:
                        signal_payload = json.loads(
                            proposal_sig_path.read_text(encoding="utf-8"))
                    except (json.JSONDecodeError, OSError) as exc:
                        log(f"Section {section.number}: WARNING — "
                            f"malformed proposal signal ({exc}), "
                            f"scope-delta will lack payload enrichment")
                scope_delta = {
                    "section": section.number,
                    "signal": "out_of_scope",
                    "detail": detail,
                    "requires_root_reframing": True,
                    "signal_path": str(proposal_sig_path),
                    "signal_payload": signal_payload,
                }
                (scope_delta_dir
                 / f"section-{section.number}-scope-delta.json"
                 ).write_text(
                    json.dumps(scope_delta, indent=2), encoding="utf-8")
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

        # 2b: Opus checks alignment
        log(f"Section {section.number}: proposal alignment check")
        align_prompt = write_integration_alignment_prompt(
            section, planspace, codespace,
        )
        align_output = (artifacts
                        / f"intg-align-{section.number}-output.md")
        # No agent_name → no per-agent monitor for alignment checks
        # (Opus alignment prompts don't include narration instructions,
        # so a monitor would false-positive STALLED after 5 min silence)
        align_result = dispatch_agent(
            policy["alignment"], align_prompt, align_output,
            planspace, parent, codespace=codespace,
            section_number=section.number,
            agent_file="alignment-judge.md",
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
            # ALIGNED — proceed to implementation
            log(f"Section {section.number}: integration proposal ALIGNED")
            mailbox_send(planspace, parent,
                         f"summary:proposal-align:{section.number}:ALIGNED")
            _write_alignment_surface(planspace, section)
            break

        # Problems found — feed back into next proposal attempt
        proposal_problems = problems
        short = problems[:200]
        log(f"Section {section.number}: integration proposal problems "
            f"(attempt {proposal_attempt}): {short}")
        mailbox_send(planspace, parent,
                     f"summary:proposal-align:{section.number}:"
                     f"PROBLEMS-attempt-{proposal_attempt}:{short}")

    # -----------------------------------------------------------------
    # Step 2.5: Generate microstrategy (agent-driven decision)
    # -----------------------------------------------------------------
    # The integration proposer decides whether a microstrategy is needed
    # by including "needs_microstrategy: true" in its output. The script
    # checks mechanically — no hardcoded file-count thresholds.
    microstrategy_path = (artifacts / "proposals"
                          / f"section-{section.number}-microstrategy.md")
    needs_microstrategy = (
        _check_needs_microstrategy(
            integration_proposal, planspace, section.number, parent,
            codespace=codespace,
            model=policy.get("microstrategy_decider", "glm"),
            escalation_model=policy["escalation_model"])
        and not microstrategy_path.exists()
    )
    if not needs_microstrategy and not microstrategy_path.exists():
        log(f"Section {section.number}: integration proposer did not "
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

        micro_prompt_path.write_text(f"""# Task: Microstrategy for Section {section.number}

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
{agent_mail_instructions(planspace, a_name, m_name)}
""", encoding="utf-8")
        _log_artifact(planspace, f"prompt:microstrategy-{section.number}")

        ctrl = poll_control_messages(planspace, parent,
                                     current_section=section.number)
        if ctrl == "alignment_changed":
            return None
        micro_result = dispatch_agent(
            policy.get("implementation", "gpt-5.3-codex-high"),
            micro_prompt_path, micro_output_path,
            planspace, parent, a_name, codespace=codespace,
            section_number=section.number,
            agent_file="microstrategy-writer.md",
        )
        if micro_result == "ALIGNMENT_CHANGED_PENDING":
            return None
        log(f"Section {section.number}: microstrategy generated")
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
            budget_signal_path.parent.mkdir(parents=True, exist_ok=True)
            budget_signal_path.write_text(
                json.dumps(budget_signal, indent=2), encoding="utf-8")
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
            if cycle_budget_path.exists():
                try:
                    cycle_budget.update(json.loads(
                        cycle_budget_path.read_text(encoding="utf-8")))
                except (json.JSONDecodeError, OSError) as exc:
                    log(f"Section {section.number}: WARNING — "
                        f"malformed cycle-budget.json ({exc}), "
                        f"keeping previous budget")

        tag = "fix " if impl_problems else ""
        log(f"Section {section.number}: {tag}strategic implementation "
            f"(attempt {impl_attempt})")

        # 3a: GPT implements strategically
        impl_prompt = write_strategic_impl_prompt(
            section, planspace, codespace, impl_problems,
            model_policy=policy,
        )
        impl_output = artifacts / f"impl-{section.number}-output.md"
        impl_agent = f"impl-{section.number}"
        impl_result = dispatch_agent(
            policy.get("implementation", "gpt-5.3-codex-high"),
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
    trace_map_path.write_text(
        json.dumps(trace_map, indent=2), encoding="utf-8")
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
            # Fail-closed: dispatch repair instead of silently
            # proceeding (R34/V2)
            log(f"Section {section.number}: post-impl registry "
                f"malformed ({exc}) — dispatching repair")
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
            )
            # Verify repair succeeded
            try:
                json.loads(
                    tool_registry_path.read_text(encoding="utf-8"))
                log(f"Section {section.number}: post-impl tool "
                    f"registry repaired")
            except (json.JSONDecodeError, ValueError):
                log(f"Section {section.number}: post-impl tool "
                    f"registry repair failed — writing blocker")
                signal_dir = artifacts / "signals"
                signal_dir.mkdir(parents=True, exist_ok=True)
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
                (signal_dir
                 / f"section-{section.number}-post-impl-blocker.json"
                 ).write_text(
                    json.dumps(blocker, indent=2),
                    encoding="utf-8",
                )
                _update_blocker_rollup(planspace)

    # -----------------------------------------------------------------
    # Step 3c: Detect tooling friction and dispatch bridge-tools agent
    # -----------------------------------------------------------------
    tool_friction_detected = False
    if friction_signal_path.exists():
        try:
            friction = json.loads(
                friction_signal_path.read_text(encoding="utf-8"))
            tool_friction_detected = friction.get("friction", False)
        except (json.JSONDecodeError, OSError):
            # File existence is the signal attempt — treat malformed
            # JSON as friction detected (fail closed).
            log(f"Section {section.number}: friction signal file exists "
                f"but failed to parse — treating as friction detected")
            tool_friction_detected = True

    if tool_friction_detected and tool_registry_path.exists():
        log(f"Section {section.number}: tooling friction detected — "
            f"dispatching bridge-tools agent")
        bridge_tools_prompt = (
            artifacts / f"bridge-tools-{section.number}-prompt.md")
        bridge_tools_output = (
            artifacts / f"bridge-tools-{section.number}-output.md")
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

Write your proposal to: `{artifacts / "proposals" / f"section-{section.number}-tool-bridge.md"}`
Update the tool registry if new tools are proposed.
""", encoding="utf-8")
        dispatch_agent(
            policy.get("bridge_tools", "gpt-5.3-codex-high"),
            bridge_tools_prompt,
            bridge_tools_output,
            planspace, parent,
            f"bridge-tools-{section.number}",
            codespace=codespace,
            agent_file="bridge-tools.md",
            section_number=section.number,
        )

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
