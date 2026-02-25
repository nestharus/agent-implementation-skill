"""Surface registry: deduplication, tracking, and diminishing returns."""

import json
from pathlib import Path

from ..communication import log
from ..dispatch import read_agent_signal


def load_surface_registry(
    section_number: str, planspace: Path,
) -> dict:
    """Load the persistent surface registry for a section.

    Returns the registry dict or an empty default if missing/malformed.
    """
    registry_path = (
        planspace / "artifacts" / "intent" / "sections"
        / f"section-{section_number}" / "surface-registry.json"
    )
    if not registry_path.exists():
        return {"section": section_number, "next_id": 1, "surfaces": []}

    try:
        data = json.loads(registry_path.read_text(encoding="utf-8"))
        if isinstance(data, dict) and "surfaces" in data:
            return data
    except (json.JSONDecodeError, OSError) as exc:
        log(f"Section {section_number}: surface registry malformed ({exc}) "
            f"— preserving and starting fresh")
        try:
            registry_path.rename(registry_path.with_suffix(".malformed.json"))
        except OSError:
            pass

    return {"section": section_number, "next_id": 1, "surfaces": []}


def save_surface_registry(
    section_number: str, planspace: Path, registry: dict,
) -> None:
    """Write the surface registry back to disk."""
    registry_path = (
        planspace / "artifacts" / "intent" / "sections"
        / f"section-{section_number}" / "surface-registry.json"
    )
    registry_path.parent.mkdir(parents=True, exist_ok=True)
    registry_path.write_text(
        json.dumps(registry, indent=2), encoding="utf-8",
    )


def load_intent_surfaces(
    section_number: str, planspace: Path,
) -> dict | None:
    """Load intent-surfaces-NN.json signal written by intent-judge."""
    signals_dir = planspace / "artifacts" / "signals"
    surfaces_path = signals_dir / f"intent-surfaces-{section_number}.json"
    return read_agent_signal(surfaces_path)


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

    for kind in ("problem_surfaces", "philosophy_surfaces"):
        for surface in surfaces.get(kind, []):
            sid = surface.get("id", "")
            if sid in existing_ids:
                # Update last_seen
                for existing in registry["surfaces"]:
                    if existing["id"] == sid:
                        existing["last_seen"] = {
                            "stage": surfaces.get("stage", "unknown"),
                            "attempt": surfaces.get("attempt", 0),
                        }
                duplicate_ids.append(sid)
            else:
                # Add new surface
                entry = {
                    "id": sid,
                    "kind": surface.get("kind", "unknown"),
                    "axis_id": surface.get("axis_id", ""),
                    "status": "pending",
                    "first_seen": {
                        "stage": surfaces.get("stage", "unknown"),
                        "attempt": surfaces.get("attempt", 0),
                    },
                    "last_seen": {
                        "stage": surfaces.get("stage", "unknown"),
                        "attempt": surfaces.get("attempt", 0),
                    },
                    "notes": surface.get("title", ""),
                }
                registry.setdefault("surfaces", []).append(entry)
                existing_ids.add(sid)
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
            surface["status"] = "discarded"


def surfaces_are_diminishing(
    registry: dict,
    new_surfaces: list[dict],
    duplicate_ids: list[str],
) -> bool:
    """Check if surface discovery has hit diminishing returns.

    Returns True when >60% of newly-reported surfaces are duplicates
    of already-discarded surfaces.
    """
    total = len(new_surfaces) + len(duplicate_ids)
    if total == 0:
        return True  # No surfaces at all — nothing left to discover

    discarded_ids = {
        s["id"] for s in registry.get("surfaces", [])
        if s.get("status") == "discarded"
    }
    discarded_dupes = sum(1 for sid in duplicate_ids if sid in discarded_ids)
    return discarded_dupes / total > 0.6
