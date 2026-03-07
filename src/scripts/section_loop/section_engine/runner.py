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
from lib.microstrategy_orchestrator import run_microstrategy
from lib.proposal_loop import run_proposal_loop
from lib.readiness_gate import resolve_and_route
from lib.readiness_resolver import resolve_readiness
from lib.excerpt_extractor import extract_excerpts
from lib.implementation_loop import run_implementation_loop
from lib.recurrence_emitter import emit_recurrence_signal
from lib.tool_surface import (
    handle_tool_friction,
    surface_tool_registry,
    validate_tool_registry_after_implementation,
)

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
from ..reconciliation import load_reconciliation_result
from lib.proposal_state_repository import load_proposal_state
from lib.reconciliation_queue import queue_reconciliation_request
from .blockers import _append_open_problem, _update_blocker_rollup
from .reexplore import _write_alignment_surface
from .todos import _check_needs_microstrategy, _extract_todos_from_files
from .traceability import _file_sha256, _write_traceability_index


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
        emit_recurrence_signal(planspace, section.number, section.solve_count)

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
    # Compatibility note: stale surface cleanup still occurs in the extracted
    # helper via tools_available_path.exists() / tools_available_path.unlink().
    pre_tool_total = surface_tool_registry(
        section_number=section.number,
        tool_registry_path=tool_registry_path,
        tools_available_path=tools_available_path,
        artifacts=artifacts,
        planspace=planspace,
        parent=parent,
        codespace=codespace,
        policy=policy,
        dispatch_agent=dispatch_agent,
        log=log,
        update_blocker_rollup=_update_blocker_rollup,
    )

    # -----------------------------------------------------------------
    # Step 1: Section setup — extract excerpts from global documents
    # -----------------------------------------------------------------
    if extract_excerpts(section, planspace, codespace, parent, policy) is None:
        return None

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

    # Cycle budget: read per-section budget or use defaults
    cycle_budget_path = (artifacts / "signals"
                         / f"section-{section.number}-cycle-budget.json")
    cycle_budget = {"proposal_max": 5, "implementation_max": 5}
    _loaded_budget = read_json(cycle_budget_path)
    if _loaded_budget is not None:
        cycle_budget.update(_loaded_budget)

    if run_proposal_loop(
        section,
        planspace,
        codespace,
        parent,
        policy,
        cycle_budget,
        incoming_notes,
    ) is None:
        return None

    readiness_result = resolve_and_route(section, planspace, parent, pass_mode)
    if not readiness_result.ready:
        return readiness_result.proposal_pass_result
    if pass_mode == "proposal":
        return readiness_result.proposal_pass_result

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
    microstrategy_result = run_microstrategy(
        section,
        planspace,
        codespace,
        parent,
        policy,
    )
    microstrategy_blocker = (
        artifacts / "signals" / f"microstrategy-blocker-{section.number}.json"
    )
    if microstrategy_result is None and microstrategy_blocker.exists():
        return None

    # -----------------------------------------------------------------
    # Step 3: Strategic implementation
    # -----------------------------------------------------------------
    actually_changed = run_implementation_loop(
        section,
        planspace,
        codespace,
        parent,
        policy,
        cycle_budget,
    )
    if actually_changed is None:
        return None

    # -----------------------------------------------------------------
    # Step 3b: Validate tool registry after implementation
    # -----------------------------------------------------------------
    friction_signal_path = validate_tool_registry_after_implementation(
        section_number=section.number,
        pre_tool_total=pre_tool_total,
        tool_registry_path=tool_registry_path,
        artifacts=artifacts,
        planspace=planspace,
        parent=parent,
        codespace=codespace,
        policy=policy,
        dispatch_agent=dispatch_agent,
        log=log,
        update_blocker_rollup=_update_blocker_rollup,
    )

    # -----------------------------------------------------------------
    # Step 3c: Detect tooling friction and dispatch bridge-tools agent
    # -----------------------------------------------------------------
    handle_tool_friction(
        section_number=section.number,
        section_path=section.path,
        all_sections=all_sections,
        artifacts=artifacts,
        tool_registry_path=tool_registry_path,
        friction_signal_path=friction_signal_path,
        planspace=planspace,
        parent=parent,
        codespace=codespace,
        policy=policy,
        dispatch_agent=dispatch_agent,
        log=log,
        write_consequence_note=write_consequence_note,
        update_blocker_rollup=_update_blocker_rollup,
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
