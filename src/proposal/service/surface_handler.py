"""Surface loading, persistence, and loop-level surface action handling.

Extracted from proposal_cycle.py to isolate surface-related logic
from the main proposal loop orchestration.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from containers import Services
from orchestrator.path_registry import PathRegistry
from intent.service.surface_registry import (
    load_combined_intent_surfaces,
    load_surface_registry,
    merge_surfaces_into_registry,
    normalize_surface_ids,
    save_surface_registry,
)
from proposal.service.expansion_handler import run_aligned_expansion, run_misaligned_expansion
from signals.types import ACTION_ABORT, ACTION_CONTINUE, INTENT_MODE_FULL


DEFINITION_GAP_KINDS = {
    "new_axis",
    "gap",
    "silence",
    "ungrounded_assumption",
}


# ---------------------------------------------------------------------------
# Surface helpers
# ---------------------------------------------------------------------------

def _load_combined_surfaces(section_number: str, planspace: Path) -> dict | None:
    """Load and merge all surfaces that can trigger proposal expansion."""
    return load_combined_intent_surfaces(section_number, planspace)


def _has_definition_gap_surfaces(surfaces: dict | None) -> bool:
    """Return whether any surfaced issue implies definition growth."""
    if not isinstance(surfaces, (dict, Mapping)):
        return False
    return any(
        surface.get("kind") in DEFINITION_GAP_KINDS
        for kind_key in ("problem_surfaces", "philosophy_surfaces")
        for surface in surfaces.get(kind_key, [])
        if isinstance(surface, dict)
    )


def _count_surfaces(surfaces: dict | None) -> int:
    """Count all structured surfaces in a payload."""
    if not isinstance(surfaces, (dict, Mapping)):
        return 0
    return sum(
        len(surfaces.get(kind_key, []))
        for kind_key in ("problem_surfaces", "philosophy_surfaces")
    )


def _persist_surfaces(section_number: str, planspace: Path, surfaces: dict) -> dict:
    """Normalize and persist discovered surfaces into the section registry."""
    registry = load_surface_registry(section_number, planspace)
    surfaces = normalize_surface_ids(surfaces, registry, section_number)
    merge_surfaces_into_registry(registry, surfaces)
    save_surface_registry(section_number, planspace, registry)
    return surfaces


def _write_intent_escalation_signal(
    planspace: Path,
    section_number: str,
    reason: str,
    surface_count: int,
) -> None:
    """Record that lightweight intent escalated after structured discoveries."""
    escalation_signal = {
        "section": section_number,
        "reason": reason,
        "surface_count": surface_count,
    }
    paths = PathRegistry(planspace)
    Services.artifact_io().write_json(
        paths.intent_escalation_signal(section_number),
        escalation_signal,
    )


@dataclass(frozen=True)
class SurfaceActionResult:
    """Result of aligned/misaligned surface handling."""

    action: str
    intent_mode: str
    reproposal_reason: str | None = None


# ---------------------------------------------------------------------------
# Surface action handlers (aligned / misaligned paths)
# ---------------------------------------------------------------------------

def handle_aligned_surfaces(
    section_number: str,
    planspace: Path,
    codespace: Path,
    parent: str,
    intent_mode: str,
    intent_budgets: dict,
    expansion_counts: dict[str, int],
) -> SurfaceActionResult:
    """Handle surface processing when the proposal is aligned.

    Returns a ``SurfaceActionResult`` with action:
        "break" — alignment accepted, exit loop
        "continue" — re-propose needed (reproposal_reason has the message)
        "abort" — caller should return None
    """
    surfaces = _load_combined_surfaces(section_number, planspace)
    surface_count = _count_surfaces(surfaces)
    if surface_count:
        if intent_mode != INTENT_MODE_FULL:
            _persist_surfaces(section_number, planspace, surfaces)
            Services.logger().log(
                f"Section {section_number}: lightweight mode discovered "
                f"{surface_count} structured surfaces — escalating to "
                "full intent"
            )
            _write_intent_escalation_signal(
                planspace,
                section_number,
                "structured_surfaces_on_lightweight",
                surface_count,
            )
            return SurfaceActionResult(
                action=ACTION_CONTINUE,
                intent_mode=INTENT_MODE_FULL,
                reproposal_reason=(
                    "Lightweight section discovered structured surfaces; "
                    "re-propose under full intent mode."
                ),
            )

        if intent_mode == INTENT_MODE_FULL:
            action = run_aligned_expansion(
                section_number, planspace, codespace, parent,
                intent_budgets, expansion_counts,
            )
            if action is None:
                return SurfaceActionResult(action=ACTION_ABORT, intent_mode=intent_mode)
            if action == ACTION_CONTINUE:
                return SurfaceActionResult(
                    action=ACTION_CONTINUE,
                    intent_mode=intent_mode,
                    reproposal_reason=(
                        "Intent expanded; re-propose against "
                        "updated problem/philosophy definitions."
                    ),
                )

    Services.logger().log(f"Section {section_number}: integration proposal ALIGNED")
    Services.communicator().mailbox_send(
        planspace,
        parent,
        f"summary:proposal-align:{section_number}:ALIGNED",
    )
    return SurfaceActionResult(action="break", intent_mode=intent_mode)


def handle_misaligned_surfaces(
    section_number: str,
    planspace: Path,
    codespace: Path,
    parent: str,
    intent_mode: str,
    intent_budgets: dict,
    expansion_counts: dict[str, int],
) -> str:
    """Handle surface processing when the proposal is misaligned.

    Returns the (possibly upgraded) intent_mode.
    """
    misaligned_surfaces = _load_combined_surfaces(
        section_number, planspace,
    )
    misaligned_surface_count = _count_surfaces(misaligned_surfaces)
    if not misaligned_surface_count:
        return intent_mode

    misaligned_surfaces = _persist_surfaces(
        section_number,
        planspace,
        misaligned_surfaces,
    )
    Services.logger().log(
        f"Section {section_number}: persisted intent "
        f"surfaces from misaligned pass"
    )
    if intent_mode != INTENT_MODE_FULL:
        Services.logger().log(
            f"Section {section_number}: lightweight mode discovered "
            f"{misaligned_surface_count} structured surfaces on "
            "misaligned pass — upgrading to full"
        )
        _write_intent_escalation_signal(
            planspace,
            section_number,
            "structured_surfaces_on_lightweight_misaligned",
            misaligned_surface_count,
        )
        intent_mode = INTENT_MODE_FULL

    if intent_mode == INTENT_MODE_FULL and _has_definition_gap_surfaces(
        misaligned_surfaces,
    ):
        run_misaligned_expansion(
            section_number, planspace, codespace, parent,
            intent_budgets, expansion_counts,
        )

    return intent_mode
