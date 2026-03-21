"""Surface loading, persistence, and loop-level surface action handling.

Extracted from proposal_cycle.py to isolate surface-related logic
from the main proposal loop orchestration.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from containers import ArtifactIOService, Communicator, LogService
    from proposal.service.expansion_handler import ExpansionHandler
    from intent.service.surface_registry import SurfaceRegistry

from orchestrator.path_registry import PathRegistry
from intent.service.surface_registry import (
    merge_surfaces_into_registry,
)
from signals.types import ACTION_ABORT, ACTION_BREAK, ACTION_CONTINUE, INTENT_MODE_FULL


DEFINITION_GAP_KINDS = {
    "new_axis",
    "gap",
    "silence",
    "ungrounded_assumption",
}


# ---------------------------------------------------------------------------
# Surface helpers (pure — no Services usage)
# ---------------------------------------------------------------------------

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


@dataclass(frozen=True)
class SurfaceActionResult:
    """Result of aligned/misaligned surface handling."""

    action: str
    intent_mode: str
    reproposal_reason: str | None = None
    intent_pack_stale: bool = False


@dataclass(frozen=True)
class MisalignedSurfaceResult:
    """Result of misaligned surface handling."""

    intent_mode: str
    intent_pack_stale: bool = False


class SurfaceHandler:
    def __init__(
        self,
        logger: LogService,
        artifact_io: ArtifactIOService,
        communicator: Communicator,
        expansion_handler: ExpansionHandler,
        surface_registry: SurfaceRegistry,
    ) -> None:
        self._logger = logger
        self._artifact_io = artifact_io
        self._communicator = communicator
        self._expansion_handler = expansion_handler
        self._surface_registry = surface_registry

    def _load_combined_surfaces(
        self, section_number: str, planspace: Path,
    ) -> dict | None:
        """Load and merge all surfaces that can trigger proposal expansion."""
        return self._surface_registry.load_combined_intent_surfaces(
            section_number, planspace,
        )

    def _persist_surfaces(
        self, section_number: str, planspace: Path, surfaces: dict,
    ) -> dict:
        """Normalize and persist discovered surfaces into the section registry."""
        registry = self._surface_registry.load_surface_registry(
            section_number, planspace,
        )
        surfaces = self._surface_registry.normalize_surface_ids(
            surfaces, registry, section_number,
        )
        merge_surfaces_into_registry(registry, surfaces)
        self._surface_registry.save_surface_registry(
            section_number, planspace, registry,
        )
        return surfaces

    def _write_intent_escalation_signal(
        self,
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
        self._artifact_io.write_json(
            paths.intent_escalation_signal(section_number),
            escalation_signal,
        )

    def _invalidate_intent_pack_hash(
        self,
        planspace: Path,
        section_number: str,
    ) -> None:
        """Delete the intent pack input hash so the next generation is forced."""
        paths = PathRegistry(planspace)
        hash_file = paths.intent_section_dir(section_number) / "intent-pack-input-hash.txt"
        if hash_file.exists():
            hash_file.unlink()
            self._logger.log(
                f"Section {section_number}: invalidated intent pack hash "
                "for regeneration after mode escalation"
            )

    # ---------------------------------------------------------------------------
    # Surface action handlers (aligned / misaligned paths)
    # ---------------------------------------------------------------------------

    def handle_aligned_surfaces(
        self,
        section_number: str,
        planspace: Path,
        codespace: Path,
        intent_mode: str,
        expansion_counts: dict[str, int],
    ) -> SurfaceActionResult:
        """Handle surface processing when the proposal is aligned.

        Returns a ``SurfaceActionResult`` with action:
            "break" -- alignment accepted, exit loop
            "continue" -- re-propose needed (reproposal_reason has the message)
            "abort" -- caller should return None
        """
        surfaces = self._load_combined_surfaces(section_number, planspace)
        surface_count = _count_surfaces(surfaces)
        if surface_count:
            if intent_mode != INTENT_MODE_FULL:
                self._persist_surfaces(section_number, planspace, surfaces)
                self._logger.log(
                    f"Section {section_number}: lightweight mode discovered "
                    f"{surface_count} structured surfaces — escalating to "
                    "full intent"
                )
                self._write_intent_escalation_signal(
                    planspace,
                    section_number,
                    "structured_surfaces_on_lightweight",
                    surface_count,
                )
                self._invalidate_intent_pack_hash(planspace, section_number)
                return SurfaceActionResult(
                    action=ACTION_CONTINUE,
                    intent_mode=INTENT_MODE_FULL,
                    reproposal_reason=(
                        "Lightweight section discovered structured surfaces; "
                        "re-propose under full intent mode."
                    ),
                    intent_pack_stale=True,
                )

            if intent_mode == INTENT_MODE_FULL:
                action = self._expansion_handler.run_aligned_expansion(
                    section_number, planspace, codespace,
                    expansion_counts,
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

        self._logger.log(f"Section {section_number}: integration proposal ALIGNED")
        self._communicator.log_summary(
            planspace,
            f"summary:proposal-align:{section_number}:ALIGNED",
        )
        return SurfaceActionResult(action=ACTION_BREAK, intent_mode=intent_mode)

    def handle_misaligned_surfaces(
        self,
        section_number: str,
        planspace: Path,
        codespace: Path,
        intent_mode: str,
        expansion_counts: dict[str, int],
    ) -> MisalignedSurfaceResult:
        """Handle surface processing when the proposal is misaligned.

        Returns a ``MisalignedSurfaceResult`` with the (possibly upgraded)
        intent_mode and whether the intent pack needs regeneration.
        """
        misaligned_surfaces = self._load_combined_surfaces(
            section_number, planspace,
        )
        misaligned_surface_count = _count_surfaces(misaligned_surfaces)
        if not misaligned_surface_count:
            return MisalignedSurfaceResult(intent_mode=intent_mode)

        intent_pack_stale = False
        misaligned_surfaces = self._persist_surfaces(
            section_number,
            planspace,
            misaligned_surfaces,
        )
        self._logger.log(
            f"Section {section_number}: persisted intent "
            f"surfaces from misaligned pass"
        )
        if intent_mode != INTENT_MODE_FULL:
            self._logger.log(
                f"Section {section_number}: lightweight mode discovered "
                f"{misaligned_surface_count} structured surfaces on "
                "misaligned pass — upgrading to full"
            )
            self._write_intent_escalation_signal(
                planspace,
                section_number,
                "structured_surfaces_on_lightweight_misaligned",
                misaligned_surface_count,
            )
            self._invalidate_intent_pack_hash(planspace, section_number)
            intent_mode = INTENT_MODE_FULL
            intent_pack_stale = True

        if intent_mode == INTENT_MODE_FULL and _has_definition_gap_surfaces(
            misaligned_surfaces,
        ):
            self._expansion_handler.run_misaligned_expansion(
                section_number, planspace, codespace,
                expansion_counts,
            )

        return MisalignedSurfaceResult(
            intent_mode=intent_mode,
            intent_pack_stale=intent_pack_stale,
        )
