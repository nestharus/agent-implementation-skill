"""Surface registry: deduplication, tracking, and diminishing returns."""

from __future__ import annotations

from collections.abc import Mapping
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING

from orchestrator.path_registry import PathRegistry

if TYPE_CHECKING:
    from containers import ArtifactIOService, HasherService, LogService, SignalReader


class SurfaceStatus(str, Enum):
    """Status of an expansion surface entry."""

    PENDING = "pending"
    DISCARDED = "discarded"

    def __str__(self) -> str:  # noqa: D105
        return self.value


_FINGERPRINT_LENGTH = 12


class SurfaceRegistry:
    """Surface registry: deduplication, tracking, and diminishing returns."""

    def __init__(
        self,
        artifact_io: ArtifactIOService,
        hasher: HasherService,
        logger: LogService,
        signals: SignalReader,
    ) -> None:
        self._artifact_io = artifact_io
        self._hasher = hasher
        self._logger = logger
        self._signals = signals

    def load_surface_registry(
        self, section_number: str, planspace: Path,
    ) -> dict:
        """Load the persistent surface registry for a section.

        Returns the registry dict or an empty default if missing/malformed.
        """
        registry_path = (
            PathRegistry(planspace).intent_section_dir(section_number)
            / "surface-registry.json"
        )
        if not registry_path.exists():
            return {"section": section_number, "next_id": 1, "surfaces": []}

        data = self._artifact_io.read_json(registry_path)
        if isinstance(data, dict) and "surfaces" in data:
            return data
        if data is not None:
            # Schema mismatch: JSON valid but missing required keys (V6/R53)
            self._logger.log(f"Section {section_number}: surface registry missing 'surfaces' "
                f"key — preserving and starting fresh")
            malformed_path = self._artifact_io.rename_malformed(registry_path)
            if malformed_path is None and registry_path.exists():
                self._logger.log(f"Section {section_number}: failed to rename schema-"
                    "mismatched registry")
        else:
            self._logger.log(f"Section {section_number}: surface registry malformed "
                f"— preserving and starting fresh")

        return {"section": section_number, "next_id": 1, "surfaces": []}

    def save_surface_registry(
        self, section_number: str, planspace: Path, registry: dict,
    ) -> None:
        """Write the surface registry back to disk."""
        registry_path = (
            PathRegistry(planspace).intent_section_dir(section_number)
            / "surface-registry.json"
        )
        self._artifact_io.write_json(registry_path, registry)

    def load_intent_surfaces(
        self, section_number: str, planspace: Path,
    ) -> dict | None:
        """Load intent-surfaces-NN.json signal written by intent-judge."""
        signals_dir = PathRegistry(planspace).signals_dir()
        surfaces_path = signals_dir / f"intent-surfaces-{section_number}.json"
        return self._signals.read(surfaces_path)

    def load_implementation_feedback_surfaces(
        self, section_number: str, planspace: Path,
    ) -> dict | None:
        """Load implementation feedback surfaces for a section."""
        feedback_path = PathRegistry(planspace).impl_feedback_surfaces(section_number)
        return self._signals.read(feedback_path)

    def load_research_derived_surfaces(
        self, section_number: str, planspace: Path,
    ) -> dict | None:
        """Load research-derived surfaces with corruption preservation."""
        research_path = PathRegistry(planspace).research_derived_surfaces(section_number)
        if not research_path.exists():
            return None
        data = self._artifact_io.read_json(research_path)
        if not isinstance(data, dict):
            if data is not None:
                self._artifact_io.rename_malformed(research_path)
            return None
        if (
            "problem_surfaces" not in data
            and "philosophy_surfaces" not in data
        ):
            self._artifact_io.rename_malformed(research_path)
            return None
        return data

    def load_combined_intent_surfaces(
        self, section_number: str, planspace: Path,
    ) -> dict | None:
        """Load and merge all surface sources used by proposal/expansion."""
        surfaces = self.load_intent_surfaces(section_number, planspace)
        surfaces = merge_surface_payloads(
            surfaces,
            self.load_implementation_feedback_surfaces(section_number, planspace),
        )
        surfaces = merge_surface_payloads(
            surfaces,
            self.load_research_derived_surfaces(section_number, planspace),
        )
        return surfaces

    def normalize_surface_ids(
        self, surfaces: dict, registry: dict, section_number: str,
    ) -> dict:
        """Assign stable mechanical IDs to surfaces using registry counter.

        Computes a fingerprint per surface (hash of kind+axis+title+description+evidence)
        and maps to a stable ID via the registry. Duplicate fingerprints reuse existing IDs.
        Rewrites the surfaces dict in-place with IDs filled in.

        Returns the updated surfaces dict.
        """
        # Build fingerprint→id lookup from existing registry entries
        fp_to_id: dict[str, str] = {}
        for entry in registry.get("surfaces", []):
            fp = entry.get("fingerprint", "")
            if fp:
                fp_to_id[fp] = entry["id"]

        next_id = registry.get("next_id", 1)

        for kind_key, prefix in (
            ("problem_surfaces", "P"),
            ("philosophy_surfaces", "F"),
        ):
            for surface in surfaces.get(kind_key, []):
                # Compute fingerprint from content fields
                fp_input = "|".join(
                    str(surface.get(f, "")).strip()
                    for f in ("kind", "axis_id", "title", "description", "evidence")
                )
                fp = self._hasher.content_hash(fp_input)[:_FINGERPRINT_LENGTH]
                surface["_fingerprint"] = fp

                if fp in fp_to_id:
                    surface["id"] = fp_to_id[fp]
                else:
                    new_id = f"{prefix}-{section_number}-{next_id:04d}"
                    surface["id"] = new_id
                    fp_to_id[fp] = new_id
                    next_id += 1

        registry["next_id"] = next_id
        return surfaces


# -- Pure functions (no Services usage) ------------------------------------

def merge_surface_payloads(
    surfaces: dict | None, additional_surfaces: dict | None,
) -> dict | None:
    """Merge problem/philosophy surface lists into a single payload."""
    if not isinstance(additional_surfaces, (dict, Mapping)):
        return surfaces
    if surfaces is None:
        return additional_surfaces

    for kind in ("problem_surfaces", "philosophy_surfaces"):
        existing = list(surfaces.get(kind, []))
        new = list(additional_surfaces.get(kind, []))
        surfaces[kind] = existing + new

    return surfaces


def _seen_stamp(surfaces: dict) -> dict:
    """Build a stage/attempt stamp from the surfaces envelope."""
    return {
        "stage": surfaces.get("stage", "unknown"),
        "attempt": surfaces.get("attempt", 0),
    }


def _build_surface_entry(surface: dict, stamp: dict) -> dict:
    """Build a registry entry from a raw surface dict."""
    return {
        "id": surface.get("id", ""),
        "kind": surface.get("kind", "unknown"),
        "axis_id": surface.get("axis_id", ""),
        "status": SurfaceStatus.PENDING,
        "fingerprint": surface.get("_fingerprint", ""),
        "first_seen": dict(stamp),
        "last_seen": dict(stamp),
        "notes": surface.get("title", ""),
        "description": surface.get("description", ""),
        "evidence": surface.get("evidence", ""),
    }


def merge_surfaces_into_registry(
    registry: dict, surfaces: dict,
) -> tuple[list[dict], list[str]]:
    """Merge newly-discovered surfaces into the registry.

    Returns (new_surfaces, duplicate_ids) where new_surfaces are the
    surfaces that were actually added, and duplicate_ids are IDs of
    surfaces that were already tracked.
    """
    existing_ids = {s["id"] for s in registry.get("surfaces", [])}
    new_surfaces: list[dict] = []
    duplicate_ids: list[str] = []
    stamp = _seen_stamp(surfaces)

    existing_by_id = {s["id"]: s for s in registry.get("surfaces", [])}

    for kind in ("problem_surfaces", "philosophy_surfaces"):
        for surface in surfaces.get(kind, []):
            sid = surface.get("id", "")
            if sid in existing_ids:
                if sid in existing_by_id:
                    existing_by_id[sid]["last_seen"] = dict(stamp)
                duplicate_ids.append(sid)
            else:
                entry = _build_surface_entry(surface, stamp)
                registry.setdefault("surfaces", []).append(entry)
                existing_ids.add(sid)
                existing_by_id[sid] = entry
                new_surfaces.append(entry)

    return new_surfaces, duplicate_ids


def mark_surfaces_applied(
    registry: dict, applied_ids: list[str],
) -> None:
    """Mark surfaces as applied in the registry."""
    applied_set = set(applied_ids)
    for surface in registry.get("surfaces", []):
        if surface["id"] in applied_set:
            surface["status"] = "applied"


def mark_surfaces_discarded(
    registry: dict, discarded_ids: list[str],
) -> None:
    """Mark surfaces as discarded in the registry."""
    discarded_set = set(discarded_ids)
    for surface in registry.get("surfaces", []):
        if surface["id"] in discarded_set:
            surface["status"] = SurfaceStatus.DISCARDED


def find_discarded_recurrences(
    registry: dict,
    duplicate_ids: list[str],
) -> list[dict]:
    """Find discarded surfaces that have resurfaced.

    Returns a list of registry entries for surfaces that were previously
    discarded but have now been re-reported by the intent judge.
    Recurrence is a signal worth adjudicating — it may indicate a real
    problem that was incorrectly discarded, or a false positive.
    """
    discarded_lookup = {
        s["id"]: s for s in registry.get("surfaces", [])
        if s.get("status") == SurfaceStatus.DISCARDED
    }
    return [
        discarded_lookup[sid]
        for sid in duplicate_ids
        if sid in discarded_lookup
    ]


