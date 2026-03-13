"""Intent bootstrap pipeline for section-loop runner.

Decomposes the former ``run_intent_bootstrap`` god function into
single-concern steps composed via the pipeline engine.  Alignment
guards and logging are handled by middleware — not inlined.
"""

from __future__ import annotations

from pathlib import Path

from orchestrator.path_registry import PathRegistry
from intent.service.intent_pack_generator import (
    ensure_global_philosophy,
    generate_intent_pack,
)
from intent.service.intent_triager import run_intent_triage
from containers import Services
from signals.service.blocker_manager import _update_blocker_rollup
from intake.service.governance_packet_builder import build_section_governance_packet
from orchestrator.types import Section

from pipeline import AlignmentGuard, Pipeline, PipelineContext, Step


# Module-level callback — monkey-patched by runner before use; default
# routes through the DI container so standalone calls also work.
alignment_changed_pending = lambda planspace: Services.pipeline_control().alignment_changed_pending(planspace)


# -- Step functions (each has exactly ONE concern) -------------------------


def _step_triage(ctx: PipelineContext) -> dict:
    """Run intent triage to determine mode and budgets."""
    paths = ctx.paths
    pf_path = paths.problem_frame(ctx.section.number)
    pf_content = (
        pf_path.read_text(encoding="utf-8").strip()
        if pf_path.exists()
        else ""
    )
    ctx.state["pf_content"] = pf_content

    notes_count = 0
    notes_dir = paths.notes_dir()
    if notes_dir.exists():
        notes_count = len(
            list(notes_dir.glob(f"from-*-to-{ctx.section.number}.md")),
        )

    result = run_intent_triage(
        ctx.section.number,
        ctx.planspace,
        ctx.codespace,
        ctx.parent,
        related_files_count=len(ctx.section.related_files),
        incoming_notes_count=notes_count,
        solve_count=ctx.section.solve_count,
        section_summary=pf_content[:500] if pf_content else "",
    )
    ctx.state["intent_mode"] = result.get("intent_mode", "lightweight")
    ctx.state["intent_budgets"] = result.get("budgets", {})
    return result


def _step_extract_todos(ctx: PipelineContext) -> str:
    """Extract TODO comments from related files and record traceability."""
    paths = ctx.paths
    todos_path = paths.todos(ctx.section.number)
    paths.todos_dir().mkdir(parents=True, exist_ok=True)

    todo_entries = _extract_todos_from_files(
        ctx.codespace, ctx.section.related_files,
    )
    artifact_name = f"section-{ctx.section.number}-todos.md"

    if todo_entries:
        todos_path.write_text(todo_entries, encoding="utf-8")
        Services.logger().log(f"Section {ctx.section.number}: extracted TODOs from related files")
        Services.communicator().record_traceability(
            ctx.planspace, ctx.section.number, artifact_name,
            "related files TODO extraction",
            "in-code microstrategies for alignment",
        )
    elif todos_path.exists():
        todos_path.unlink()
        Services.logger().log(
            f"Section {ctx.section.number}: removed stale TODO extraction "
            "(no TODOs remaining)",
        )
        Services.communicator().record_traceability(
            ctx.planspace, ctx.section.number, artifact_name,
            "related files TODO extraction",
            "in-code microstrategies for alignment",
        )
    else:
        Services.logger().log(f"Section {ctx.section.number}: no TODOs found in related files")

    return todo_entries or ""


def _step_philosophy(ctx: PipelineContext) -> dict:
    """Ensure global philosophy is bootstrapped.

    Returns the philosophy result on success.  Returns ``None`` to
    halt the pipeline when philosophy is blocked (need_decision,
    needs_parent, or unavailable).
    """
    result = ensure_global_philosophy(
        ctx.planspace, ctx.codespace, ctx.parent,
    )

    if result["status"] != "ready":
        blocking_state = result.get("blocking_state")
        sec = ctx.section.number
        if blocking_state == "NEED_DECISION":
            Services.logger().log(
                f"Section {sec}: philosophy bootstrap needs "
                f"user input — {result['detail']}",
            )
            _update_blocker_rollup(ctx.planspace)
            Services.pipeline_control().pause_for_parent(
                ctx.planspace, ctx.parent,
                "pause:need_decision:global:philosophy bootstrap requires user input",
            )
        elif blocking_state == "NEEDS_PARENT":
            Services.logger().log(
                f"Section {sec}: philosophy bootstrap needs "
                f"parent intervention — {result['detail']}",
            )
        else:
            Services.logger().log(
                f"Section {sec}: philosophy unavailable — "
                f"{result['detail']}",
            )
        return None  # halt pipeline

    return result


def _step_governance(ctx: PipelineContext) -> str:
    """Build the section governance packet."""
    pf_content = ctx.state.get("pf_content", "")
    build_section_governance_packet(
        ctx.section.number,
        ctx.planspace,
        ctx.codespace,
        pf_content[:500] if pf_content else "",
    )
    return "ok"


def _step_intent_pack(ctx: PipelineContext) -> str:
    """Generate full intent pack (only in full mode)."""
    generate_intent_pack(
        ctx.section,
        ctx.planspace,
        ctx.codespace,
        ctx.parent,
        incoming_notes=ctx.state.get("incoming_notes", ""),
    )
    Services.logger().log(f"Section {ctx.section.number}: intent bootstrap complete (full mode)")
    return "ok"


def _step_budget(ctx: PipelineContext) -> dict:
    """Merge triage budgets with existing cycle budget and return."""
    paths = ctx.paths
    intent_budgets = ctx.state.get("intent_budgets", {})

    if intent_budgets:
        triage_budget_keys = frozenset(("proposal_max", "implementation_max"))
        cycle_budget_path = paths.cycle_budget(ctx.section.number)
        existing_budget = Services.artifact_io().read_json(cycle_budget_path)
        if existing_budget is not None:
            existing_budget.update({
                key: value
                for key, value in intent_budgets.items()
                if (
                    key.startswith("intent_")
                    or key.startswith("max_new_")
                    or key in triage_budget_keys
                )
            })
            Services.artifact_io().write_json(cycle_budget_path, existing_budget)

    cycle_budget_path = paths.cycle_budget(ctx.section.number)
    cycle_budget = {"proposal_max": 5, "implementation_max": 5}
    loaded_budget = Services.artifact_io().read_json(cycle_budget_path)
    if loaded_budget is not None:
        cycle_budget.update(loaded_budget)

    ctx.state["result"] = cycle_budget
    return cycle_budget


# -- Guards ----------------------------------------------------------------


def _has_related_files(ctx: PipelineContext) -> bool:
    return bool(ctx.section.related_files)


def _is_full_mode(ctx: PipelineContext) -> bool:
    return ctx.state.get("intent_mode") == "full"


# -- Pipeline definition --------------------------------------------------

_STEPS = [
    Step("triage", _step_triage),
    Step("extract-todos", _step_extract_todos, guard=_has_related_files),
    Step("philosophy", _step_philosophy),
    Step("governance", _step_governance),
    Step("intent-pack", _step_intent_pack, guard=_is_full_mode),
    Step("budget", _step_budget),
]


# -- Public entry point (same signature as before) -------------------------


def run_intent_bootstrap(
    section: Section,
    planspace: Path,
    codespace: Path,
    parent: str,
    incoming_notes: str | None,
) -> dict | None:
    """Run intent triage, TODO surfacing, philosophy, and budget assembly."""
    ctx = PipelineContext(
        section=section,
        planspace=planspace,
        codespace=codespace,
        parent=parent,
        policy=Services.policies().load(planspace),
        paths=PathRegistry(planspace),
        state={"incoming_notes": incoming_notes or ""},
    )
    pipe = Pipeline(
        "intent-bootstrap",
        steps=_STEPS,
        middleware=[
            AlignmentGuard(
                alignment_changed_pending,
                after_steps={"philosophy", "intent-pack"},
            ),
        ],
    )
    return pipe.run(ctx)


# -- Helpers ---------------------------------------------------------------


def _extract_todos_from_files(codespace: Path, related_files: list[str]) -> str:
    from implementation.service.microstrategy_decider import (
        _extract_todos_from_files as extract_todos,
    )

    return extract_todos(codespace, related_files)
