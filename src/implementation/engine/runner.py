import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from intent.engine import bootstrap as intent_bootstrap_module
from signals.repository.artifact_io import read_json, write_json
from staleness.service.change_tracker import check_pending as alignment_changed_pending
from staleness.helpers.hashing import content_hash
from implementation.service.impact_triage import run_impact_triage
from intent.engine.bootstrap import run_intent_bootstrap
from proposal.service.problem_frame_gate import validate_problem_frame
from coordination.repository.notes import write_consequence_note
from dispatch.service.model_policy import resolve
from orchestrator.path_registry import PathRegistry
from implementation.service.microstrategy import run_microstrategy
from proposal.engine.loop import run_proposal_loop
from proposal.service.readiness_resolver import resolve_readiness
from proposal.service.excerpt_extractor import extract_excerpts
from implementation.engine.loop import run_implementation_loop
from intent.service.recurrence import emit_recurrence_signal
from dispatch.service.tool_surface import (
    handle_tool_friction,
    surface_tool_registry,
    validate_tool_registry_after_implementation,
)

from staleness.service.section_alignment import (
    _extract_problems,
    _run_alignment_check_with_retries,
)
from staleness.helpers.detection import diff_files, snapshot_files
from signals.service.communication import (
    AGENT_NAME,
    DB_SH,
    log,
    mailbox_send,
)
from coordination.service.cross_section import (
    extract_section_summary,
    persist_decision,
    post_section_completion,
    read_incoming_notes,
)
from dispatch.engine.section_dispatch import (
    check_agent_signals,
    dispatch_agent,
    read_agent_signal,
    read_model_policy,
    summarize_output,
    write_model_choice_signal,
)
from dispatch.prompt.template import TASK_SUBMISSION_SEMANTICS
from dispatch.service.prompt_safety import validate_dynamic_content
from flow.service.section_ingestion import ingest_and_submit
from orchestrator.service.pipeline_control import (
    handle_pending_messages,
    pause_for_parent,
    poll_control_messages,
)
from dispatch.prompt.writers import (
    agent_mail_instructions,
    signal_instructions,
    write_impl_alignment_prompt,
    write_integration_alignment_prompt,
    write_integration_proposal_prompt,
    write_strategic_impl_prompt,
)
from orchestrator.types import ProposalPassResult, Section
from intent.service.loop_bootstrap import ensure_global_philosophy, generate_intent_pack
from intent.service.triage import run_intent_triage
from intent.service.expansion import handle_user_gate, run_expansion_cycle
from intent.service.surfaces import (
    load_intent_surfaces,
    load_surface_registry,
    merge_surfaces_into_registry,
    normalize_surface_ids,
    save_surface_registry,
)
from reconciliation.engine.loop import load_reconciliation_result
from proposal.repository.state import load_proposal_state
from reconciliation.repository.queue import queue_reconciliation_request
from signals.service.blockers import _update_blocker_rollup
from implementation.service.todos import _extract_todos_from_files
from implementation.service.traceability import _file_sha256, _write_traceability_index


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

    readiness = resolve_readiness(planspace, section.number)
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
    2. Integration proposal loop — proposer writes, alignment judge checks
    3. Strategic implementation — implementor writes, alignment judge checks
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
    triage_status, triage_files = run_impact_triage(
        section,
        planspace,
        codespace,
        parent,
        policy,
        incoming_notes,
    )
    if triage_status == "abort":
        return None
    if triage_status == "skip":
        return triage_files if triage_files is not None else []

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
    if validate_problem_frame(
        section,
        planspace,
        codespace,
        parent,
        policy,
    ) is None:
        return None
    intent_bootstrap_module.run_intent_triage = run_intent_triage
    intent_bootstrap_module.ensure_global_philosophy = ensure_global_philosophy
    intent_bootstrap_module.generate_intent_pack = generate_intent_pack
    intent_bootstrap_module._extract_todos_from_files = _extract_todos_from_files
    intent_bootstrap_module.alignment_changed_pending = alignment_changed_pending
    cycle_budget = run_intent_bootstrap(
        section,
        planspace,
        codespace,
        parent,
        policy,
        incoming_notes,
    )
    if cycle_budget is None:
        return None

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

    from proposal.engine.readiness_gate import resolve_and_route  # noqa: E402 — lazy to break circular import

    readiness_result = resolve_and_route(
        section,
        planspace,
        parent,
        pass_mode,
        codespace=codespace,
    )
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
    readiness = resolve_readiness(planspace, section.number)
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
            impact_model=resolve(policy, "impact_analysis"),
            normalizer_model=resolve(policy, "impact_normalizer"),
        )

    return actually_changed
