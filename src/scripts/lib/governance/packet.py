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


def build_section_governance_packet(
    section_number: str,
    planspace: Path,
    codespace: Path,
    section_summary: str = "",
) -> Path | None:
    """Build a governance packet for a section."""
    del codespace, section_summary

    paths = PathRegistry(planspace)
    packet_path = paths.governance_packet(section_number)
    packet = {
        "section": section_number,
        "problems": _list_index(paths.governance_problem_index()),
        "patterns": _list_index(paths.governance_pattern_index()),
        "profiles": _list_index(paths.governance_profile_index()),
        "region_profile_map": _dict_index(paths.governance_region_profile_map()),
    }
    packet["governing_profile"] = _resolve_governing_profile(
        section_number,
        packet["region_profile_map"],
    )

    try:
        write_json(packet_path, packet)
    except OSError:
        return None
    return packet_path

