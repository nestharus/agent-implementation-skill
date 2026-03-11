"""Expansion cycle public wrappers."""

from __future__ import annotations

from pathlib import Path

from intent.engine import surface as _intent_surface
from dispatch.service.prompt_safety import write_validated_prompt

from dispatch.engine.section_dispatch import dispatch_agent
from dispatch.service.model_policy import load_model_policy as read_model_policy
from signals.repository.signal_reader import read_agent_signal
from orchestrator.service.pipeline_control import pause_for_parent
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


def _sync_overrides() -> None:
    """Propagate monkeypatched wrapper dependencies into the lib module."""
    _intent_surface.dispatch_agent = dispatch_agent
    _intent_surface.read_agent_signal = read_agent_signal
    _intent_surface.read_model_policy = read_model_policy
    _intent_surface.write_validated_prompt = write_validated_prompt
    _intent_surface.pause_for_parent = pause_for_parent
    _intent_surface.find_discarded_recurrences = find_discarded_recurrences
    _intent_surface.load_intent_surfaces = load_combined_intent_surfaces
    _intent_surface.load_surface_registry = load_surface_registry
    _intent_surface.mark_surfaces_applied = mark_surfaces_applied
    _intent_surface.mark_surfaces_discarded = mark_surfaces_discarded
    _intent_surface.merge_surfaces_into_registry = merge_surfaces_into_registry
    _intent_surface.normalize_surface_ids = normalize_surface_ids
    _intent_surface.save_surface_registry = save_surface_registry


def run_expansion_cycle(
    section_number: str,
    planspace: Path,
    codespace: Path,
    parent: str,
    *,
    budgets: dict | None = None,
) -> dict:
    _sync_overrides()
    return _intent_surface.run_expansion_cycle(
        section_number,
        planspace,
        codespace,
        parent,
        budgets=budgets,
    )


def handle_user_gate(
    section_number: str,
    planspace: Path,
    parent: str,
    delta_result: dict,
) -> str | None:
    _sync_overrides()
    return _intent_surface.handle_user_gate(
        section_number,
        planspace,
        parent,
        delta_result,
    )


def _run_problem_expander(
    section_number: str,
    planspace: Path,
    codespace: Path,
    parent: str,
    policy: dict,
    *,
    pending_surfaces_path: Path | None = None,
    remaining_axis_budget: int = 6,
) -> dict | None:
    _sync_overrides()
    return _intent_surface.run_problem_expander(
        section_number,
        planspace,
        codespace,
        parent,
        policy,
        pending_surfaces_path=pending_surfaces_path,
        remaining_axis_budget=remaining_axis_budget,
    )


def _run_philosophy_expander(
    section_number: str,
    planspace: Path,
    codespace: Path,
    parent: str,
    policy: dict,
    *,
    pending_surfaces_path: Path | None = None,
) -> dict | None:
    _sync_overrides()
    return _intent_surface.run_philosophy_expander(
        section_number,
        planspace,
        codespace,
        parent,
        policy,
        pending_surfaces_path=pending_surfaces_path,
    )


def _adjudicate_recurrence(
    section_number: str,
    planspace: Path,
    codespace: Path,
    parent: str,
    policy: dict,
    recurrences: list[dict],
) -> list[str]:
    _sync_overrides()
    return _intent_surface.adjudicate_recurrence(
        section_number,
        planspace,
        codespace,
        parent,
        policy,
        recurrences,
    )
