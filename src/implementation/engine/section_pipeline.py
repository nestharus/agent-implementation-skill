from pathlib import Path

from intent.engine import intent_initializer as intent_bootstrap_module
from implementation.service.triage_orchestrator import run_impact_triage
from intent.engine.intent_initializer import run_intent_bootstrap
from proposal.service.problem_frame_gate import validate_problem_frame
from containers import Services
from orchestrator.path_registry import PathRegistry
from implementation.service.microstrategy_generator import run_microstrategy
from pipeline.context import DispatchContext
from proposal.engine.proposal_cycle import run_proposal_loop
from proposal.service.readiness_resolver import resolve_readiness
from proposal.service.excerpt_extractor import extract_excerpts
from implementation.engine.implementation_cycle import run_implementation_loop
from intent.service.recurrence_emitter import emit_recurrence_signal
from dispatch.service.tool_surface_writer import surface_tool_registry
from dispatch.service.tool_validator import validate_tool_registry_after_implementation
from dispatch.service.tool_bridge import handle_tool_friction

from coordination.service.completion_handler import (
    post_section_completion,
    read_incoming_notes,
)
from orchestrator.types import ProposalPassResult, Section
from intent.service.intent_pack_generator import ensure_global_philosophy, generate_intent_pack
from intent.service.intent_triager import run_intent_triage
from reconciliation.engine.cross_section_reconciler import load_reconciliation_result
from implementation.service.microstrategy_decider import _extract_todos_from_files
from signals.types import (
    ACTION_ABORT, ACTION_SKIP,
    PASS_MODE_IMPLEMENTATION, PASS_MODE_PROPOSAL,
)


_DEFAULT_PROPOSAL_CYCLE_MAX = 5
_DEFAULT_IMPLEMENTATION_CYCLE_MAX = 5
_RECURRENCE_LOOP_THRESHOLD = 2

# ---------------------------------------------------------------------------
# Private helpers — each encapsulates one concern from the section pipeline
# ---------------------------------------------------------------------------


def _check_recurrence(planspace: Path, section: Section) -> None:
    """Emit a recurrence signal when a section loops excessively."""
    if section.solve_count >= _RECURRENCE_LOOP_THRESHOLD:
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
        incoming_notes,
    )
    if triage_status == ACTION_ABORT:
        return False, None
    if triage_status == ACTION_SKIP:
        return False, triage_files if triage_files is not None else []
    return True, None


def _surface_tools(
    section: Section,
    planspace: Path,
    parent: str,
    codespace: Path,
) -> int:
    """Surface section-relevant tools from tool registry.

    Returns the pre-implementation tool count for later validation.
    """
    return surface_tool_registry(
        section_number=section.number,
        planspace=planspace,
        parent=parent,
        codespace=codespace,
    )


def _run_intent_bootstrap_phase(
    section: Section,
    planspace: Path,
    codespace: Path,
    parent: str,
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
    if pass_mode == PASS_MODE_PROPOSAL:
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
) -> bool:
    """Check readiness and reconciliation freshness gates.

    Returns ``True`` if implementation may proceed, ``False`` otherwise.
    """
    readiness = resolve_readiness(planspace, section.number)
    if not readiness.ready:
        Services.logger().log(f"Section {section.number}: implementation steps blocked — "
            f"upstream freshness check failed (execution_ready is false)")
        return False

    recon_result = load_reconciliation_result(planspace, section.number)
    if recon_result and recon_result.get("affected"):
        Services.logger().log(f"Section {section.number}: implementation steps blocked — "
            f"reconciliation result marks section as affected")
        return False

    return True


def _load_cycle_budget(paths: PathRegistry, section_number: str) -> dict:
    """Load the per-section cycle budget, falling back to defaults."""
    cycle_budget_path = paths.cycle_budget(section_number)
    cycle_budget = {
        "proposal_max": _DEFAULT_PROPOSAL_CYCLE_MAX,
        "implementation_max": _DEFAULT_IMPLEMENTATION_CYCLE_MAX,
    }
    loaded = Services.artifact_io().read_json(cycle_budget_path)
    if loaded is not None:
        cycle_budget.update(loaded)
    return cycle_budget


def _count_pre_impl_tools(paths: PathRegistry) -> int:
    """Read tool registry and return the tool count."""
    tool_registry_path = paths.tool_registry()
    registry = Services.artifact_io().read_json(tool_registry_path)
    if registry is None:
        return 0
    all_tools = (registry if isinstance(registry, list)
                 else registry.get("tools", []))
    return len(all_tools)


def _run_microstrategy_step(
    section: Section,
    planspace: Path,
    codespace: Path,
    parent: str,
) -> bool:
    """Run microstrategy and check for blockers.

    Returns ``True`` if the pipeline should continue, ``False`` to abort.
    """
    microstrategy_result = run_microstrategy(
        section,
        planspace,
        codespace,
        parent,
    )
    microstrategy_blocker = PathRegistry(planspace).microstrategy_blocker_signal(section.number)
    if microstrategy_result is None and microstrategy_blocker.exists():
        return False
    return True


def _validate_tools_post_impl(
    section: Section,
    pre_tool_total: int,
    planspace: Path,
    parent: str,
    codespace: Path,
    all_sections: list[Section] | None,
) -> None:
    """Validate tool registry after implementation and handle friction."""
    validate_tool_registry_after_implementation(
        section_number=section.number,
        pre_tool_total=pre_tool_total,
        planspace=planspace,
        parent=parent,
        codespace=codespace,
    )

    handle_tool_friction(
        section_number=section.number,
        section_path=section.path,
        all_sections=all_sections,
        planspace=planspace,
        parent=parent,
        codespace=codespace,
    )


def _run_post_completion(
    section: Section,
    actually_changed: list[str],
    all_sections: list[Section] | None,
    planspace: Path,
    codespace: Path,
    parent: str,
) -> None:
    """Run post-completion impact analysis and consequence notes."""
    if actually_changed and all_sections:
        post_section_completion(
            section, actually_changed, all_sections,
            planspace, codespace, parent,
        )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def _run_implementation_pass(
    planspace: Path, codespace: Path, section: Section, parent: str,
    *,
    all_sections: list[Section] | None = None,
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
    recon_result = load_reconciliation_result(planspace, section.number)
    if recon_result and recon_result.get("affected"):
        Services.logger().log(f"Section {section.number}: implementation pass blocked — "
            f"reconciliation result marks section as affected")
        return None

    readiness = resolve_readiness(planspace, section.number)
    if not readiness.ready:
        Services.logger().log(f"Section {section.number}: implementation pass skipped — "
            f"execution_ready is false")
        return None

    return _run_section_implementation_steps(
        planspace, codespace, section, parent,
        all_sections=all_sections,
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
    # Implementation-only mode: skip proposal steps, jump to execution
    if pass_mode == PASS_MODE_IMPLEMENTATION:
        return _run_implementation_pass(
            planspace, codespace, section, parent,
            all_sections=all_sections,
        )

    # Recurrence signal
    _check_recurrence(planspace, section)

    # Step 0: Read incoming notes from other sections
    incoming_notes = _read_notes(section, planspace, codespace)

    # Step 0c: Impact triage — skip expensive steps if notes are trivial
    should_continue, early_return = _run_impact_triage(
        section, planspace, codespace, parent, incoming_notes,
    )
    if not should_continue:
        return early_return

    # Step 0b: Surface section-relevant tools from tool registry
    # Compatibility note: stale surface cleanup still occurs in the extracted
    # helper via tools_available_path.exists() / tools_available_path.unlink().
    _surface_tools(section, planspace, parent, codespace)

    # Step 1: Section setup — extract excerpts from global documents
    if extract_excerpts(section, planspace, codespace, parent) is None:
        return None

    # Step 1a: Problem frame quality gate (enforced)
    if validate_problem_frame(section, planspace, codespace, parent) is None:
        return None

    # Step 1b: Intent bootstrap
    cycle_budget = _run_intent_bootstrap_phase(
        section, planspace, codespace, parent, incoming_notes,
    )
    if cycle_budget is None:
        return None

    # Step 2: Proposal loop
    if run_proposal_loop(
        section,
        DispatchContext(planspace=planspace, codespace=codespace, parent=parent),
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
    )


def _run_section_implementation_steps(
    planspace: Path, codespace: Path, section: Section, parent: str,
    *,
    all_sections: list[Section] | None = None,
) -> list[str] | None:
    """Execute microstrategy through post-completion for a section.

    This is the implementation half of the section pipeline, extracted so
    it can be called independently by ``_run_implementation_pass`` (two-pass
    mode) or inline from ``run_section`` (full mode).
    """
    paths = PathRegistry(planspace)

    # Upstream freshness gate
    if not _check_upstream_freshness(section, planspace):
        return None

    # Load cycle budget and pre-implementation tool count
    cycle_budget = _load_cycle_budget(paths, section.number)
    pre_tool_total = _count_pre_impl_tools(paths)

    # Step 2.5: Generate microstrategy
    if not _run_microstrategy_step(section, planspace, codespace, parent):
        return None

    # Step 3: Strategic implementation
    actually_changed = run_implementation_loop(
        section, planspace, codespace, parent, cycle_budget,
    )
    if actually_changed is None:
        return None

    # Step 3b-3c: Validate tool registry and handle friction
    _validate_tools_post_impl(
        section, pre_tool_total,
        planspace, parent, codespace, all_sections,
    )

    # Step 4: Post-completion
    _run_post_completion(
        section, actually_changed, all_sections,
        planspace, codespace, parent,
    )

    return actually_changed
