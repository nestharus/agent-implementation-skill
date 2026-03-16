"""Intent surface orchestration helpers."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from orchestrator.path_registry import PathRegistry
from orchestrator.types import PauseType
from intent.service.surface_registry import (
    SurfaceStatus,
    find_discarded_recurrences,
    mark_surfaces_applied,
    mark_surfaces_discarded,
    merge_surfaces_into_registry,
)
from signals.types import BLOCKING_NEED_DECISION

if TYPE_CHECKING:
    from containers import ArtifactIOService, LogService, PipelineControlService
    from intent.service.expanders import Expanders
    from intent.service.surface_registry import SurfaceRegistry


# -- Pure helpers (no Services usage) --------------------------------------

def _surface_from_entry(entry: dict) -> dict:
    """Build a surface dict from a worklist entry."""
    return {
        "id": entry["id"],
        "kind": entry.get("kind", ""),
        "axis_id": entry.get("axis_id", ""),
        "title": entry.get("notes", ""),
        "description": entry.get("description", ""),
        "evidence": entry.get("evidence", ""),
    }


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
        if surface_id not in budgeted_ids:
            continue
        if surface_id in judge_problem:
            problem_surfaces.append(judge_problem[surface_id])
        elif surface_id in judge_philosophy:
            philosophy_surfaces.append(judge_philosophy[surface_id])
        elif surface_id.startswith("P-"):
            problem_surfaces.append(_surface_from_entry(entry))
        elif surface_id.startswith("F-"):
            philosophy_surfaces.append(_surface_from_entry(entry))

    return {
        "problem_surfaces": problem_surfaces,
        "philosophy_surfaces": philosophy_surfaces,
    }


class ExpansionOrchestrator:
    """Intent surface orchestration."""

    def __init__(
        self,
        artifact_io: ArtifactIOService,
        expanders: Expanders,
        logger: LogService,
        pipeline_control: PipelineControlService,
        surface_registry: SurfaceRegistry,
    ) -> None:
        self._artifact_io = artifact_io
        self._expanders = expanders
        self._logger = logger
        self._pipeline_control = pipeline_control
        self._surface_registry = surface_registry

    def _reopen_recurring_surfaces(
        self,
        registry: dict,
        duplicate_ids: list[str],
        section_number: str,
        planspace: Path,
        codespace: Path,
    ) -> None:
        recurrences = find_discarded_recurrences(registry, duplicate_ids)
        if not recurrences:
            return
        reopened = self._expanders.adjudicate_recurrence(
            section_number, planspace, codespace, recurrences,
        )
        if reopened:
            for surface_id in reopened:
                for entry in registry.get("surfaces", []):
                    if entry["id"] == surface_id:
                        entry["status"] = SurfaceStatus.PENDING

    def _apply_problem_expansion(
        self,
        delta: dict,
        section_number: str,
        planspace: Path,
        codespace: Path,
        pending_surfaces_path: Path,
        remaining_axis_budget: int,
    ) -> None:
        problem_delta = self._expanders.run_problem_expander(
            section_number, planspace, codespace,
            pending_surfaces_path=pending_surfaces_path,
            remaining_axis_budget=remaining_axis_budget,
        )
        if not problem_delta:
            return
        proposed_axes = problem_delta.get("new_axes", [])
        if len(proposed_axes) > remaining_axis_budget:
            self._logger.log(
                f"Section {section_number}: expander proposed "
                f"{len(proposed_axes)} new axes (budget advisory: "
                f"{remaining_axis_budget}) — accepting all"
            )
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
                "restart_reason", "Problem definition expanded",
            )

    def _apply_philosophy_expansion(
        self,
        delta: dict,
        paths: PathRegistry,
        section_number: str,
        codespace: Path,
        pending_surfaces_path: Path,
    ) -> None:
        philosophy_delta = self._expanders.run_philosophy_expander(
            section_number, paths.planspace, codespace,
            pending_surfaces_path=pending_surfaces_path,
        )
        if not philosophy_delta:
            return
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
            delta["user_input_path"] = str(paths.philosophy_decisions())
            delta["restart_required"] = True

    def _finalize_expansion(
        self,
        registry, delta, axes_added, section_number, paths, worklist,
    ):
        mark_surfaces_applied(registry, delta["applied_surface_ids"])
        mark_surfaces_discarded(registry, delta["discarded_surface_ids"])
        registry["axes_added_so_far"] = axes_added + len(delta["new_axes"])
        self._surface_registry.save_surface_registry(
            section_number, paths.planspace, registry,
        )

        delta_path = paths.intent_delta_signal(section_number)
        self._artifact_io.write_json(delta_path, delta)

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

    def run_expansion_cycle(
        self,
        section_number: str,
        planspace: Path,
        codespace: Path,
        *,
        budgets: dict | None = None,
    ) -> dict:
        """Run one expansion cycle: validate surfaces and expand definitions."""
        paths = PathRegistry(planspace)
        budget_config = budgets or {}
        no_work = {
            "restart_required": False,
            "needs_user_input": False,
            "expansion_applied": False,
            "surfaces_found": 0,
        }

        surfaces = self._surface_registry.load_combined_intent_surfaces(
            section_number, planspace,
        )
        if not surfaces:
            return no_work

        registry = self._surface_registry.load_surface_registry(
            section_number, planspace,
        )
        surfaces = self._surface_registry.normalize_surface_ids(
            surfaces, registry, section_number,
        )
        new_surfaces, duplicate_ids = merge_surfaces_into_registry(registry, surfaces)

        surfaces_path = paths.intent_surfaces_signal(section_number)
        self._artifact_io.write_json(surfaces_path, surfaces)

        if not new_surfaces:
            self._reopen_recurring_surfaces(
                registry, duplicate_ids, section_number, planspace, codespace,
            )

        worklist = [
            surface for surface in registry.get("surfaces", [])
            if surface.get("status") == SurfaceStatus.PENDING
        ]
        if not worklist:
            self._surface_registry.save_surface_registry(
                section_number, planspace, registry,
            )
            return no_work

        budgeted_surfaces = build_pending_surface_payload(worklist, surfaces)
        pending_surfaces_path = (
            paths.signals_dir() / f"intent-surfaces-pending-{section_number}.json"
        )
        self._artifact_io.write_json(pending_surfaces_path, budgeted_surfaces)

        axes_added = registry.get("axes_added_so_far", 0)
        remaining_axis_budget = len(worklist) - axes_added

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
            self._apply_problem_expansion(
                delta, section_number, planspace, codespace,
                pending_surfaces_path, remaining_axis_budget,
            )

        if budgeted_surfaces["philosophy_surfaces"]:
            self._apply_philosophy_expansion(
                delta, paths, section_number, codespace,
                pending_surfaces_path,
            )

        return self._finalize_expansion(
            registry, delta, axes_added, section_number, paths, worklist,
        )

    def handle_user_gate(
        self,
        section_number: str,
        planspace: Path,
        delta_result: dict,
    ) -> str | None:
        """Handle user gate pause if expansion needs a decision."""
        if not delta_result.get("needs_user_input"):
            return None

        paths = PathRegistry(planspace)
        signals_dir = paths.signals_dir()

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
            self._artifact_io.write_json(blocker_path, {
                "state": BLOCKING_NEED_DECISION,
                "detail": message["detail"],
                "needs": message["needs"],
                "why_blocked": message["why_blocked"],
            })

        return self._pipeline_control.pause_for_parent(
            planspace,
            f"pause:{PauseType.NEED_DECISION}:{section_number}:{message['pause_summary']}",
        )
