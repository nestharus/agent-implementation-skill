"""Governance packet builder for section-scoped advisory context."""

from __future__ import annotations

from pathlib import Path

from lib.core.artifact_io import read_json, write_json
from lib.core.path_registry import PathRegistry


def _list_index(path: Path) -> list[dict]:
    data = read_json(path)
    if isinstance(data, list):
        return [entry for entry in data if isinstance(entry, dict)]
    return []


def _dict_index(path: Path) -> dict:
    data = read_json(path)
    if isinstance(data, dict):
        return data
    return {"default": "", "overrides": {}}


def _resolve_governing_profile(
    section_number: str,
    region_profile_map: dict,
) -> str:
    overrides = region_profile_map.get("overrides", {})
    if not isinstance(overrides, dict):
        overrides = {}

    for key in (
        f"section-{section_number}",
        section_number,
        f"Section {section_number}",
    ):
        profile = overrides.get(key)
        if isinstance(profile, str) and profile.strip():
            return profile.strip()

    default_profile = region_profile_map.get("default", "")
    return default_profile if isinstance(default_profile, str) else ""


def _filter_by_regions(
    records: list[dict],
    section_number: str,
    id_key: str,
) -> list[dict]:
    """Return records whose regions mention this section, or all if no match."""
    matched: list[dict] = []
    for record in records:
        regions = record.get("regions", [])
        if not isinstance(regions, list):
            regions = []
        region_text = " ".join(str(r) for r in regions).lower()
        if not region_text or f"section-{section_number}" in region_text:
            matched.append(record)
    # If filtering yields nothing, return full candidate set (fail-closed:
    # prefer over-inclusion to silent omission).
    return matched if matched else records


def build_section_governance_packet(
    section_number: str,
    planspace: Path,
    codespace: Path,
    section_summary: str = "",
) -> Path | None:
    """Build a governance packet for a section.

    The packet contains candidate governance items scoped to the section.
    Full archive references are available via archive_refs for agents that
    need the complete picture.
    """
    del codespace, section_summary

    paths = PathRegistry(planspace)
    packet_path = paths.governance_packet(section_number)

    all_problems = _list_index(paths.governance_problem_index())
    all_patterns = _list_index(paths.governance_pattern_index())
    all_profiles = _list_index(paths.governance_profile_index())
    region_profile_map = _dict_index(paths.governance_region_profile_map())

    # Candidate filtering: prefer section-matched items, fall back to all
    candidate_problems = _filter_by_regions(
        all_problems, section_number, "problem_id",
    )
    candidate_patterns = _filter_by_regions(
        all_patterns, section_number, "pattern_id",
    )

    packet = {
        "section": section_number,
        "candidate_problems": candidate_problems,
        "candidate_patterns": candidate_patterns,
        "profiles": all_profiles,
        "region_profile_map": region_profile_map,
        "archive_refs": {
            "problem_index": str(paths.governance_problem_index()),
            "pattern_index": str(paths.governance_pattern_index()),
            "profile_index": str(paths.governance_profile_index()),
        },
        "governance_questions": [],
    }
    packet["governing_profile"] = _resolve_governing_profile(
        section_number,
        region_profile_map,
    )

    try:
        write_json(packet_path, packet)
    except OSError:
        return None
    return packet_path
