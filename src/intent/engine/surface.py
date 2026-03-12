"""Intent surface orchestration helpers."""

from __future__ import annotations

from pathlib import Path

from signals.repository.artifact_io import write_json
from orchestrator.path_registry import PathRegistry
from containers import Services
from intent.service.surfaces import (
    find_discarded_recurrences,
    load_combined_intent_surfaces,
    load_surface_registry,
    mark_surfaces_applied,
    mark_surfaces_discarded,
    merge_surfaces_into_registry,
    normalize_surface_ids,
    save_surface_registry,
)
from intent.service.expanders import (
    adjudicate_recurrence,
    run_philosophy_expander,
    run_problem_expander,
)


def build_pending_surface_payload(worklist: list[dict], surfaces: dict) -> dict:
    """Build the budgeted pending-surface payload for expanders."""
    budgeted_ids = {surface["id"] for surface in worklist}
    judge_problem = {
        surface.get("id"): surface
        for surface in surfaces.get("problem_surfaces", [])
    }
    judge_philosophy = {
        surface.get("id"): surface
        for surface in surfaces.get("philosophy_surfaces", [])
    }
    problem_surfaces: list[dict] = []
    philosophy_surfaces: list[dict] = []

    for entry in worklist:
        surface_id = entry["id"]
        if surface_id in judge_problem:
            if surface_id in budgeted_ids:
                problem_surfaces.append(judge_problem[surface_id])
        elif surface_id in judge_philosophy:
            if surface_id in budgeted_ids:
                philosophy_surfaces.append(judge_philosophy[surface_id])
        elif surface_id.startswith("P-"):
            problem_surfaces.append({
                "id": surface_id,
                "kind": entry.get("kind", ""),
                "axis_id": entry.get("axis_id", ""),
                "title": entry.get("notes", ""),
                "description": entry.get("description", ""),
                "evidence": entry.get("evidence", ""),
            })
        elif surface_id.startswith("F-"):
            philosophy_surfaces.append({
                "id": surface_id,
                "kind": entry.get("kind", ""),
                "axis_id": entry.get("axis_id", ""),
                "title": entry.get("notes", ""),
                "description": entry.get("description", ""),
                "evidence": entry.get("evidence", ""),
            })

    return {
        "problem_surfaces": problem_surfaces,
        "philosophy_surfaces": philosophy_surfaces,
    }


def run_expansion_cycle(
    section_number: str,
    planspace: Path,
    codespace: Path,
    parent: str,
    *,
    budgets: dict | None = None,
) -> dict:
    """Run one expansion cycle: validate surfaces and expand definitions."""
    policy = Services.policies().load(planspace)
    paths = PathRegistry(planspace)
    budget_config = budgets or {}
    no_work = {
        "restart_required": False,
        "needs_user_input": False,
        "expansion_applied": False,
        "surfaces_found": 0,
    }

    surfaces = load_combined_intent_surfaces(section_number, planspace)
    if not surfaces:
        return no_work

    registry = load_surface_registry(section_number, planspace)
    surfaces = normalize_surface_ids(surfaces, registry, section_number)

    new_surfaces, duplicate_ids = merge_surfaces_into_registry(
        registry,
        surfaces,
    )

    surfaces_path = paths.intent_surfaces_signal(section_number)
    write_json(surfaces_path, surfaces)

    if not new_surfaces:
        recurrences = find_discarded_recurrences(registry, duplicate_ids)
        if recurrences:
            reopened = adjudicate_recurrence(
                section_number,
                planspace,
                codespace,
                parent,
                policy,
                recurrences,
            )
            if reopened:
                for surface_id in reopened:
                    for entry in registry.get("surfaces", []):
                        if entry["id"] == surface_id:
                            entry["status"] = "pending"

    worklist = [
        surface for surface in registry.get("surfaces", [])
        if surface.get("status") == "pending"
    ]

    if not worklist:
        save_surface_registry(section_number, planspace, registry)
        return no_work

    max_surfaces = budget_config.get("max_new_surfaces_per_cycle", 8)
    if len(worklist) > max_surfaces:
        Services.logger().log(f"Section {section_number}: {len(worklist)} pending surfaces "
            f"exceeds budget of {max_surfaces} — processing oldest "
            f"{max_surfaces}")
        worklist = worklist[:max_surfaces]

    budgeted_surfaces = build_pending_surface_payload(worklist, surfaces)
    pending_surfaces_path = (
        paths.signals_dir() / f"intent-surfaces-pending-{section_number}.json"
    )
    write_json(pending_surfaces_path, budgeted_surfaces)

    axes_added = registry.get("axes_added_so_far", 0)
    max_axes = budget_config.get("max_new_axes_total", 6)
    remaining_axis_budget = max(0, max_axes - axes_added)

    delta = {
        "section": section_number,
        "applied": {
            "problem_definition_updated": False,
            "problem_rubric_updated": False,
            "philosophy_updated": False,
        },
        "discarded_surface_ids": [],
        "applied_surface_ids": [],
        "new_axes": [],
        "restart_required": False,
        "needs_user_input": False,
    }

    if budgeted_surfaces["problem_surfaces"]:
        problem_delta = run_problem_expander(
            section_number,
            planspace,
            codespace,
            parent,
            policy,
            pending_surfaces_path=pending_surfaces_path,
            remaining_axis_budget=remaining_axis_budget,
        )
        if problem_delta:
            proposed_axes = problem_delta.get("new_axes", [])
            if len(proposed_axes) > remaining_axis_budget:
                Services.logger().log(f"Section {section_number}: expander proposed "
                    f"{len(proposed_axes)} new axes (budget advisory: "
                    f"{remaining_axis_budget}) — accepting all")
            delta["applied"]["problem_definition_updated"] = (
                problem_delta.get("applied", {})
                .get("problem_definition_updated", False)
            )
            delta["applied"]["problem_rubric_updated"] = (
                problem_delta.get("applied", {})
                .get("problem_rubric_updated", False)
            )
            delta["applied_surface_ids"].extend(
                problem_delta.get("applied_surface_ids", []),
            )
            delta["discarded_surface_ids"].extend(
                problem_delta.get("discarded_surface_ids", []),
            )
            delta["new_axes"].extend(proposed_axes)
            if problem_delta.get("restart_required"):
                delta["restart_required"] = True
                delta["restart_reason"] = problem_delta.get(
                    "restart_reason",
                    "Problem definition expanded",
                )

    if budgeted_surfaces["philosophy_surfaces"]:
        philosophy_delta = run_philosophy_expander(
            section_number,
            planspace,
            codespace,
            parent,
            policy,
            pending_surfaces_path=pending_surfaces_path,
        )
        if philosophy_delta:
            delta["applied"]["philosophy_updated"] = (
                philosophy_delta.get("applied", {})
                .get("philosophy_updated", False)
            )
            delta["applied_surface_ids"].extend(
                philosophy_delta.get("applied_surface_ids", []),
            )
            delta["discarded_surface_ids"].extend(
                philosophy_delta.get("discarded_surface_ids", []),
            )
            if philosophy_delta.get("needs_user_input"):
                delta["needs_user_input"] = True
                delta["user_input_kind"] = "philosophy"
                delta["user_input_path"] = str(
                    paths.philosophy_decisions()
                )
                delta["restart_required"] = True

    mark_surfaces_applied(registry, delta["applied_surface_ids"])
    mark_surfaces_discarded(registry, delta["discarded_surface_ids"])
    registry["axes_added_so_far"] = axes_added + len(delta["new_axes"])
    save_surface_registry(section_number, planspace, registry)

    delta_path = paths.intent_delta_signal(section_number)
    write_json(delta_path, delta)

    expansion_applied = (
        delta["applied"]["problem_definition_updated"]
        or delta["applied"]["problem_rubric_updated"]
        or delta["applied"]["philosophy_updated"]
    )
    return {
        "restart_required": delta["restart_required"],
        "needs_user_input": delta.get("needs_user_input", False),
        "user_input_path": delta.get("user_input_path", ""),
        "expansion_applied": expansion_applied,
        "surfaces_found": len(worklist),
    }


def handle_user_gate(
    section_number: str,
    planspace: Path,
    parent: str,
    delta_result: dict,
) -> str | None:
    """Handle user gate pause if expansion needs a decision."""
    if not delta_result.get("needs_user_input"):
        return None

    paths = PathRegistry(planspace)
    signals_dir = paths.signals_dir()
    signals_dir.mkdir(parents=True, exist_ok=True)

    gate_kind = delta_result.get("user_input_kind", "unknown")
    input_path = delta_result.get(
        "user_input_path",
        "philosophy-decisions.md",
    )

    gate_messages = {
        "philosophy": {
            "detail": (
                f"Philosophy tension requires user direction: "
                f"see {input_path}"
            ),
            "needs": "User chooses stance for philosophical tension",
            "why_blocked": (
                "Cannot decide which principle to optimize "
                "without user priority"
            ),
            "pause_summary": "Philosophy tension requires user direction",
        },
        "axis_budget": {
            "detail": f"Axis budget exceeded — see {input_path}",
            "needs": "Decide which axes to accept within budget",
            "why_blocked": "Proposed axes exceed remaining axis budget",
            "pause_summary": "Axis budget exceeded",
        },
    }
    message = gate_messages.get(gate_kind, {
        "detail": f"User decision required: see {input_path}",
        "needs": "User direction needed",
        "why_blocked": f"Gate type: {gate_kind}",
        "pause_summary": f"{gate_kind} requires user direction",
    })

    blocker_path = (
        signals_dir / f"intent-expand-{section_number}-signal.json"
    )
    if not blocker_path.exists():
        write_json(blocker_path, {
            "state": "NEED_DECISION",
            "detail": message["detail"],
            "needs": message["needs"],
            "why_blocked": message["why_blocked"],
        })

    return Services.pipeline_control().pause_for_parent(
        planspace,
        parent,
        f"pause:need_decision:{section_number}:{message['pause_summary']}",
    )
