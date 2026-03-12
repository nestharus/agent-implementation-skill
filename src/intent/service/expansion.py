"""Expansion cycle public wrappers."""

from __future__ import annotations

from pathlib import Path

from intent.engine import surface as _intent_surface


def run_expansion_cycle(
    section_number: str,
    planspace: Path,
    codespace: Path,
    parent: str,
    *,
    budgets: dict | None = None,
) -> dict:
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
    return _intent_surface.adjudicate_recurrence(
        section_number,
        planspace,
        codespace,
        parent,
        policy,
        recurrences,
    )
