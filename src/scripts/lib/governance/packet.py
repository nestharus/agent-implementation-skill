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


def _normalize_terms(text: str) -> set[str]:
    """Extract lowercased multi-word terms from text for keyword matching."""
    words = text.lower().split()
    terms: set[str] = set()
    for word in words:
        cleaned = word.strip(".,;:()[]{}\"'`")
        if len(cleaned) > 2:
            terms.add(cleaned)
    return terms


def _filter_by_regions(
    records: list[dict],
    section_number: str,
    id_key: str,
    section_summary: str = "",
) -> tuple[list[dict], str]:
    """Return records whose regions match this section, with applicability basis.

    Uses multiple signals:
    1. Direct section-number match in regions text
    2. Keyword overlap between section_summary and regions/solution_surfaces
    3. Records with no regions are always included (universal applicability)

    Returns (matched_records, applicability_basis).
    """
    summary_terms = _normalize_terms(section_summary) if section_summary else set()
    matched: list[dict] = []
    basis_parts: list[str] = []

    for record in records:
        regions = record.get("regions", [])
        if not isinstance(regions, list):
            regions = []
        region_text = " ".join(str(r) for r in regions).lower()

        # Universal: no region specified
        if not region_text:
            matched.append(record)
            continue

        # Direct section-number match
        if f"section-{section_number}" in region_text:
            matched.append(record)
            continue

        # Keyword overlap with section summary
        if summary_terms:
            region_terms = _normalize_terms(region_text)
            solution_text = str(record.get("solution_surfaces", ""))
            region_terms |= _normalize_terms(solution_text)
            overlap = summary_terms & region_terms
            if overlap:
                matched.append(record)
                rec_id = record.get(id_key, "unknown")
                basis_parts.append(f"{rec_id}:keyword({','.join(sorted(overlap)[:3])})")

    if matched:
        basis = "region_match" if not basis_parts else f"region_match+keyword({';'.join(basis_parts[:5])})"
        return matched, basis

    # Fail-closed: broad fallback with explicit reason
    return records, "broad_fallback:no_region_or_keyword_match"


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
    del codespace  # reserved for future codespace-aware filtering

    # Load problem-frame text as additional summary signal
    problem_frame_text = ""
    problem_frame_path = (
        PathRegistry(planspace).problem_frame(section_number)
    )
    if problem_frame_path.exists():
        try:
            problem_frame_text = problem_frame_path.read_text(encoding="utf-8")[:2000]
        except OSError:
            pass
    combined_summary = f"{section_summary} {problem_frame_text}".strip()

    paths = PathRegistry(planspace)
    packet_path = paths.governance_packet(section_number)

    all_problems = _list_index(paths.governance_problem_index())
    all_patterns = _list_index(paths.governance_pattern_index())
    all_profiles = _list_index(paths.governance_profile_index())
    region_profile_map = _dict_index(paths.governance_region_profile_map())

    # Candidate filtering: prefer section-matched items, fall back to all
    candidate_problems, problem_basis = _filter_by_regions(
        all_problems, section_number, "problem_id", combined_summary,
    )
    candidate_patterns, pattern_basis = _filter_by_regions(
        all_patterns, section_number, "pattern_id", combined_summary,
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
        "applicability_basis": {
            "problems": problem_basis,
            "patterns": pattern_basis,
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
