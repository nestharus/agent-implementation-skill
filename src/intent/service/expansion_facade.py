"""Expansion cycle public wrappers."""

from __future__ import annotations

from pathlib import Path

from intent.engine import expansion_orchestrator as _intent_surface


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
