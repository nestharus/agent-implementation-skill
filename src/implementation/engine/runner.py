from pathlib import Path

from intent.engine import bootstrap as intent_bootstrap_module
from implementation.service.triage_orchestrator import run_impact_triage
from intent.engine.bootstrap import run_intent_bootstrap
from proposal.service.problem_frame_gate import validate_problem_frame
from containers import Services
from orchestrator.path_registry import PathRegistry
from implementation.service.microstrategy import run_microstrategy
from proposal.engine.loop import run_proposal_loop
from proposal.service.readiness_resolver import resolve_readiness
from proposal.service.excerpt_extractor import extract_excerpts
from implementation.engine.loop import run_implementation_loop
from intent.service.recurrence import emit_recurrence_signal
from dispatch.service.tool_registry_manager import (
    handle_tool_friction,
    surface_tool_registry,
    validate_tool_registry_after_implementation,
)

from scan.service.section_notes import (
    post_section_completion,
    read_incoming_notes,
)
from orchestrator.types import ProposalPassResult, Section
from intent.service.loop_bootstrap import ensure_global_philosophy, generate_intent_pack
from intent.service.triage import run_intent_triage
from reconciliation.engine.loop import load_reconciliation_result
from signals.service.blockers import _update_blocker_rollup
from implementation.service.microstrategy_decision import _extract_todos_from_files


# ---------------------------------------------------------------------------
# Private helpers — each encapsulates one concern from the section pipeline
# ---------------------------------------------------------------------------


def _check_recurrence(planspace: Path, section: Section) -> None:
    """Emit a recurrence signal when a section loops (solve_count >= 2)."""
    if section.solve_count >= 2:
        emit_recurrence_signal(planspace, section.number, section.solve_count)


def _read_notes(
    section: Section, planspace: Path, codespace: Path,
) -> list[dict]:
    """Read incoming notes from other sections and log if any arrived."""
    incoming_notes = read_incoming_notes(section, planspace, codespace)
    if incoming_notes:
        Services.logger().log(f"Section {section.number}: received incoming notes from "
            f"other sections")
    return incoming_notes


def _run_impact_triage(
    section: Section,
    planspace: Path,
    codespace: Path,
    parent: str,
    policy: dict,
    incoming_notes: list[dict],
) -> tuple[bool, list[str] | None]:
    """Run impact triage and return (should_continue, early_return_value).

    Returns ``(True, None)`` when the pipeline should continue.
    Returns ``(False, value)`` when the caller should return ``value``.
    """
    triage_status, triage_files = run_impact_triage(
        section,
        planspace,
        codespace,
        parent,
        policy,
        incoming_notes,
    )
    if triage_status == "abort":
        return False, None
    if triage_status == "skip":
        return False, triage_files if triage_files is not None else []
    return True, None


def _surface_tools(
    section: Section,
    paths: PathRegistry,
    artifacts: Path,
    planspace: Path,
    parent: str,
    codespace: Path,
    policy: dict,
) -> int:
    """Surface section-relevant tools from tool registry.

    Returns the pre-implementation tool count for later validation.
    """
    tools_available_path = paths.tools_available(section.number)
    tool_registry_path = paths.tool_registry()
    # Compatibility note: stale surface cleanup still occurs in the extracted
    # helper via tools_available_path.exists() / tools_available_path.unlink().
    return surface_tool_registry(
        section_number=section.number,
        tool_registry_path=tool_registry_path,
        tools_available_path=tools_available_path,
        artifacts=artifacts,
        planspace=planspace,
        parent=parent,
        codespace=codespace,
        policy=policy,
        dispatch_agent=Services.dispatcher().dispatch,
        log=Services.logger().log,
        update_blocker_rollup=_update_blocker_rollup,
    )


def _run_intent_bootstrap_phase(
    section: Section,
    planspace: Path,
    codespace: Path,
    parent: str,
    policy: dict,
    incoming_notes: list[dict],
) -> dict | None:
    """Wire intent bootstrap dependencies and run bootstrap.

    Returns the cycle budget dict, or ``None`` if the section should abort.
    """
    intent_bootstrap_module.run_intent_triage = run_intent_triage
    intent_bootstrap_module.ensure_global_philosophy = ensure_global_philosophy
    intent_bootstrap_module.generate_intent_pack = generate_intent_pack
    intent_bootstrap_module._extract_todos_from_files = _extract_todos_from_files
    intent_bootstrap_module.alignment_changed_pending = Services.pipeline_control().alignment_changed_pending
    return run_intent_bootstrap(
        section,
        planspace,
        codespace,
        parent,
        policy,
        incoming_notes,
    )


def _resolve_readiness_and_route(
    section: Section,
    planspace: Path,
    parent: str,
    pass_mode: str,
    codespace: Path,
) -> list[str] | ProposalPassResult | None:
    """Resolve readiness, route blockers, and return early if not ready.

    Returns a sentinel ``_CONTINUE`` when the caller should proceed to
    implementation steps.  Any other value is the final return for
    ``run_section``.
    """
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
    return _CONTINUE


# Sentinel object used by _resolve_readiness_and_route to signal "proceed
# to implementation steps" without conflicting with any valid return value.
_CONTINUE = object()


# ---------------------------------------------------------------------------
# Implementation-step helpers (from _run_section_implementation_steps)
# ---------------------------------------------------------------------------


def _check_upstream_freshness(
    section: Section,
    planspace: Path,
    artifacts: Path,
) -> bool:
    """Check readiness and reconciliation freshness gates.

    Returns ``True`` if implementation may proceed, ``False`` otherwise.
    """
    readiness = resolve_readiness(planspace, section.number)
    if not readiness.get("ready"):
        Services.logger().log(f"Section {section.number}: implementation steps blocked — "
            f"upstream freshness check failed (execution_ready is false)")
        return False

    recon_result = load_reconciliation_result(artifacts, section.number)
    if recon_result and recon_result.get("affected"):
        Services.logger().log(f"Section {section.number}: implementation steps blocked — "
            f"reconciliation result marks section as affected")
        return False

    return True


def _load_cycle_budget(paths: PathRegistry, section_number: str) -> dict:
    """Load the per-section cycle budget, falling back to defaults."""
    cycle_budget_path = paths.cycle_budget(section_number)
    cycle_budget = {"proposal_max": 5, "implementation_max": 5}
    loaded = Services.artifact_io().read_json(cycle_budget_path)
    if loaded is not None:
        cycle_budget.update(loaded)
    return cycle_budget


def _count_pre_impl_tools(paths: PathRegistry) -> tuple[Path, int]:
    """Read tool registry and return (registry_path, tool_count)."""
    tool_registry_path = paths.tool_registry()
    pre_tool_total = 0
    registry = Services.artifact_io().read_json(tool_registry_path)
    if registry is not None:
        all_tools = (registry if isinstance(registry, list)
                     else registry.get("tools", []))
        pre_tool_total = len(all_tools)
    return tool_registry_path, pre_tool_total


def _run_microstrategy_step(
    section: Section,
    planspace: Path,
    codespace: Path,
    parent: str,
    policy: dict,
    paths: PathRegistry,
) -> bool:
    """Run microstrategy and check for blockers.

    Returns ``True`` if the pipeline should continue, ``False`` to abort.
    """
    microstrategy_result = run_microstrategy(
        section,
        planspace,
        codespace,
        parent,
        policy,
    )
    microstrategy_blocker = paths.microstrategy_blocker_signal(section.number)
    if microstrategy_result is None and microstrategy_blocker.exists():
        return False
    return True


def _validate_tools_post_impl(
    section: Section,
    pre_tool_total: int,
    tool_registry_path: Path,
    artifacts: Path,
    planspace: Path,
    parent: str,
    codespace: Path,
    policy: dict,
    all_sections: list[Section] | None,
) -> None:
    """Validate tool registry after implementation and handle friction."""
    friction_signal_path = validate_tool_registry_after_implementation(
        section_number=section.number,
        pre_tool_total=pre_tool_total,
        tool_registry_path=tool_registry_path,
        artifacts=artifacts,
        planspace=planspace,
        parent=parent,
        codespace=codespace,
        policy=policy,
        dispatch_agent=Services.dispatcher().dispatch,
        log=Services.logger().log,
        update_blocker_rollup=_update_blocker_rollup,
    )

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
        dispatch_agent=Services.dispatcher().dispatch,
        log=Services.logger().log,
        write_consequence_note=Services.cross_section().write_consequence_note,
        update_blocker_rollup=_update_blocker_rollup,
    )


def _run_post_completion(
    section: Section,
    actually_changed: list[str],
    all_sections: list[Section] | None,
    planspace: Path,
    codespace: Path,
    parent: str,
    policy: dict,
) -> None:
    """Run post-completion impact analysis and consequence notes."""
    if actually_changed and all_sections:
        post_section_completion(
            section, actually_changed, all_sections,
            planspace, codespace, parent,
            impact_model=Services.policies().resolve(policy, "impact_analysis"),
            normalizer_model=Services.policies().resolve(policy, "impact_normalizer"),
        )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


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
        Services.logger().log(f"Section {section.number}: implementation pass blocked — "
            f"reconciliation result marks section as affected")
        return None

    readiness = resolve_readiness(planspace, section.number)
    if not readiness.get("ready"):
        Services.logger().log(f"Section {section.number}: implementation pass skipped — "
            f"execution_ready is false")
        return None

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
    policy = Services.policies().load(planspace)

    # Implementation-only mode: skip proposal steps, jump to execution
    if pass_mode == "implementation":
        return _run_implementation_pass(
            planspace, codespace, section, parent,
            all_sections=all_sections,
            artifacts=artifacts, policy=policy,
        )

    # Recurrence signal
    _check_recurrence(planspace, section)

    # Step 0: Read incoming notes from other sections
    incoming_notes = _read_notes(section, planspace, codespace)

    # Step 0c: Impact triage — skip expensive steps if notes are trivial
    should_continue, early_return = _run_impact_triage(
        section, planspace, codespace, parent, policy, incoming_notes,
    )
    if not should_continue:
        return early_return

    # Step 0b: Surface section-relevant tools from tool registry
    # Compatibility note: stale surface cleanup still occurs in the extracted
    # helper via tools_available_path.exists() / tools_available_path.unlink().
    _surface_tools(section, paths, artifacts, planspace, parent, codespace, policy)

    # Step 1: Section setup — extract excerpts from global documents
    if extract_excerpts(section, planspace, codespace, parent, policy) is None:
        return None

    # Step 1a: Problem frame quality gate (enforced)
    if validate_problem_frame(section, planspace, codespace, parent, policy) is None:
        return None

    # Step 1b: Intent bootstrap
    cycle_budget = _run_intent_bootstrap_phase(
        section, planspace, codespace, parent, policy, incoming_notes,
    )
    if cycle_budget is None:
        return None

    # Step 2: Proposal loop
    if run_proposal_loop(
        section, planspace, codespace, parent, policy,
        cycle_budget, incoming_notes,
    ) is None:
        return None

    # Step 2b: Readiness resolution and routing
    readiness_outcome = _resolve_readiness_and_route(
        section, planspace, parent, pass_mode, codespace,
    )
    if readiness_outcome is not _CONTINUE:
        return readiness_outcome

    # Step 3+: Implementation steps
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
    paths = PathRegistry(planspace)

    # Upstream freshness gate
    if not _check_upstream_freshness(section, planspace, artifacts):
        return None

    # Load cycle budget and pre-implementation tool count
    cycle_budget = _load_cycle_budget(paths, section.number)
    tool_registry_path, pre_tool_total = _count_pre_impl_tools(paths)

    # Step 2.5: Generate microstrategy
    if not _run_microstrategy_step(section, planspace, codespace, parent, policy, paths):
        return None

    # Step 3: Strategic implementation
    actually_changed = run_implementation_loop(
        section, planspace, codespace, parent, policy, cycle_budget,
    )
    if actually_changed is None:
        return None

    # Step 3b-3c: Validate tool registry and handle friction
    _validate_tools_post_impl(
        section, pre_tool_total, tool_registry_path, artifacts,
        planspace, parent, codespace, policy, all_sections,
    )

    # Step 4: Post-completion
    _run_post_completion(
        section, actually_changed, all_sections,
        planspace, codespace, parent, policy,
    )

    return actually_changed
