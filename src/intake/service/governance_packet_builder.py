"""Governance packet builder for section-scoped advisory context."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from orchestrator.path_registry import PathRegistry

if TYPE_CHECKING:
    from containers import ArtifactIOService

_PROBLEM_FRAME_TRUNCATION = 2000
_MIN_TERM_LENGTH = 2
_MAX_KEYWORDS_IN_BASIS = 3
_MAX_BASIS_PARTS = 5


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
        if len(cleaned) > _MIN_TERM_LENGTH:
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
    3. Records with no regions are treated as ambiguous (PAT-0011) — missing
       applicability metadata does not imply universal applicability.

    Returns (matched_records, applicability_basis).
    """
    summary_terms = _normalize_terms(section_summary) if section_summary else set()
    matched: list[dict] = []
    ambiguous: list[dict] = []
    basis_parts: list[str] = []

    for record in records:
        regions = record.get("regions", [])
        if not isinstance(regions, list):
            regions = []
        region_text = " ".join(str(r) for r in regions).lower()

        # Missing regions: treat as ambiguous per PAT-0011
        if not region_text:
            ambiguous.append(record)
            rec_id = record.get(id_key, "unknown")
            basis_parts.append(f"{rec_id}:no_regions")
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
                basis_parts.append(f"{rec_id}:keyword({','.join(sorted(overlap)[:_MAX_KEYWORDS_IN_BASIS])})")

    # Include ambiguous records in results but mark basis explicitly
    all_matched = matched + ambiguous
    if all_matched:
        if ambiguous and not matched:
            basis = f"ambiguous_only:{';'.join(basis_parts[:_MAX_BASIS_PARTS])}"
        elif ambiguous:
            basis = f"region_match+ambiguous({';'.join(basis_parts[:_MAX_BASIS_PARTS])})"
        else:
            basis = "region_match" if not basis_parts else f"region_match+keyword({';'.join(basis_parts[:_MAX_BASIS_PARTS])})"
        return all_matched, basis

    # No-match: return empty candidates with explicit governance questions
    # PAT-0011 (R108): no-match must NOT hydrate the full archive
    return [], "no_match:no_region_or_keyword_match"


@dataclass
class _GovernanceInputs:
    """All loaded governance data needed to build a section packet."""

    paths: PathRegistry
    all_problems: list[dict]
    all_patterns: list[dict]
    all_profiles: list[dict]
    region_profile_map: dict
    combined_summary: str
    synthesis_ids: set[str]
    index_parse_failures: list[str] = field(default_factory=list)


def _boost_candidates_by_synthesis(
    candidate_problems: list[dict],
    candidate_patterns: list[dict],
    all_problems: list[dict],
    all_patterns: list[dict],
    synthesis_ids: set[str],
    problem_basis: str,
    pattern_basis: str,
) -> tuple[str, str]:
    """Boost candidates whose IDs appear in synthesis cues but were not yet matched.

    Mutates candidate_problems and candidate_patterns in place.
    Returns updated (problem_basis, pattern_basis).
    """
    matched_problem_ids = {r.get("problem_id") for r in candidate_problems}
    for rec in all_problems:
        pid = rec.get("problem_id", "")
        if pid in synthesis_ids and pid not in matched_problem_ids:
            candidate_problems.append(rec)
            problem_basis += f"+synthesis({pid})"
            matched_problem_ids.add(pid)

    matched_pattern_ids = {r.get("pattern_id") for r in candidate_patterns}
    for rec in all_patterns:
        pid = rec.get("pattern_id", "")
        if pid in synthesis_ids and pid not in matched_pattern_ids:
            candidate_patterns.append(rec)
            pattern_basis += f"+synthesis({pid})"
            matched_pattern_ids.add(pid)

    return problem_basis, pattern_basis


def _build_governance_questions(
    section_number: str,
    problem_basis: str,
    pattern_basis: str,
    candidate_problems: list[dict],
    candidate_patterns: list[dict],
    all_problems: list[dict],
    all_patterns: list[dict],
    index_parse_failures: list[str],
) -> list[str]:
    """Generate governance questions based on applicability ambiguity."""
    governance_questions: list[str] = []

    # Parse failure questions (PAT-0008 R108)
    if index_parse_failures:
        governance_questions.append(
            f"Section {section_number}: governance index has "
            f"{len(index_parse_failures)} parse failure(s) — "
            "authoritative governance docs may be corrupt. "
            "Resolve parse errors before trusting governance state."
        )

    problem_ambiguous = "ambiguous" in problem_basis and candidate_problems
    pattern_ambiguous = "ambiguous" in pattern_basis and candidate_patterns

    # No-match questions (PAT-0011 R108): empty candidates with archives
    # present means applicability could not be determined, not that
    # governance doesn't apply
    problem_no_match = "no_match" in problem_basis and not candidate_problems and all_problems
    pattern_no_match = "no_match" in pattern_basis and not candidate_patterns and all_patterns

    if problem_ambiguous or problem_no_match:
        if problem_no_match:
            reason = (
                "no problems matched this section by region or keyword — "
                f"{len(all_problems)} problem(s) exist in the archive"
            )
        elif "no_regions" in problem_basis:
            reason = "some problems have missing applicability metadata"
        else:
            reason = "broad fallback used"
        governance_questions.append(
            f"Section {section_number}: problem applicability is ambiguous — "
            f"{reason}. Which problems apply?"
        )
    if pattern_ambiguous or pattern_no_match:
        if pattern_no_match:
            reason = (
                "no patterns matched this section by region or keyword — "
                f"{len(all_patterns)} pattern(s) exist in the archive"
            )
        elif "no_regions" in pattern_basis:
            reason = "some patterns have missing applicability metadata"
        else:
            reason = "broad fallback used"
        governance_questions.append(
            f"Section {section_number}: pattern applicability is ambiguous — "
            f"{reason}. Which patterns apply?"
        )

    return governance_questions


def _determine_applicability_state(
    candidate_problems: list[dict],
    candidate_patterns: list[dict],
    governing_profile: str,
    all_problems: list[dict],
    all_patterns: list[dict],
    governance_questions: list[str],
    index_parse_failures: list[str],
) -> str:
    """Determine the explicit applicability state for the packet."""
    if index_parse_failures:
        return "ambiguous_applicability"
    if not candidate_problems and not candidate_patterns and not governing_profile:
        if all_problems or all_patterns:
            # Archives exist but nothing matched — ambiguous, not absent
            return "ambiguous_applicability"
        return "no_applicable_governance"
    if governance_questions:
        return "ambiguous_applicability"
    return "matched"


class GovernancePacketBuilder:
    """Builds governance packets for section-scoped advisory context.

    All cross-cutting services are received via constructor injection.
    """

    def __init__(self, artifact_io: ArtifactIOService) -> None:
        self._artifact_io = artifact_io

    def _list_index(self, path: Path) -> list[dict]:
        data = self._artifact_io.read_json(path)
        if isinstance(data, list):
            return [entry for entry in data if isinstance(entry, dict)]
        return []

    def _dict_index(self, path: Path) -> dict:
        data = self._artifact_io.read_json(path)
        if isinstance(data, dict):
            return data
        return {"default": "", "overrides": {}}

    def _load_governance_inputs(
        self,
        paths: PathRegistry,
        section_number: str,
        section_summary: str,
    ) -> _GovernanceInputs:
        """Load all indexes, problem frame, synthesis cues, and combined summary."""
        # Load synthesis cues for additional matching signal (PAT-0011 R109)
        synthesis_cues_path = paths.governance_synthesis_cues()
        synthesis_cues = self._artifact_io.read_json(synthesis_cues_path)
        if not isinstance(synthesis_cues, dict):
            synthesis_cues = {}

        # Load problem-frame text as additional summary signal
        problem_frame_text = ""
        problem_frame_path = paths.problem_frame(section_number)
        if problem_frame_path.exists():
            try:
                problem_frame_text = problem_frame_path.read_text(encoding="utf-8")[:_PROBLEM_FRAME_TRUNCATION]
            except OSError:
                pass
        combined_summary = f"{section_summary} {problem_frame_text}".strip()

        all_problems = self._list_index(paths.governance_problem_index())
        all_patterns = self._list_index(paths.governance_pattern_index())
        all_profiles = self._list_index(paths.governance_profile_index())
        region_profile_map = self._dict_index(paths.governance_region_profile_map())

        # Check for authoritative parse failures (PAT-0008 R108)
        index_status_path = paths.governance_index_status()
        index_parse_failures: list[str] = []
        index_status = self._artifact_io.read_json(index_status_path)
        if isinstance(index_status, dict):
            index_parse_failures = index_status.get("parse_failures", [])
            if not isinstance(index_parse_failures, list):
                index_parse_failures = []

        # Collect synthesis-cue IDs that match the section summary terms
        # PAT-0011 (R109): synthesis cues must be consumed when available
        synthesis_ids: set[str] = set()
        if synthesis_cues and combined_summary:
            summary_terms = _normalize_terms(combined_summary)
            for region_name, ref_ids in synthesis_cues.items():
                region_terms = _normalize_terms(region_name)
                if summary_terms & region_terms:
                    synthesis_ids.update(ref_ids)

        return _GovernanceInputs(
            paths=paths,
            all_problems=all_problems,
            all_patterns=all_patterns,
            all_profiles=all_profiles,
            region_profile_map=region_profile_map,
            combined_summary=combined_summary,
            synthesis_ids=synthesis_ids,
            index_parse_failures=index_parse_failures,
        )

    def build_section_governance_packet(
        self,
        section_number: str,
        planspace: Path,
        section_summary: str = "",
    ) -> Path | None:
        """Build a governance packet for a section.

        The packet contains candidate governance items scoped to the section.
        Full archive references are available via archive_refs for agents that
        need the complete picture.
        """

        paths = PathRegistry(planspace)
        packet_path = paths.governance_packet(section_number)
        inputs = self._load_governance_inputs(paths, section_number, section_summary)

        # Candidate filtering: section-scoped matching
        candidate_problems, problem_basis = _filter_by_regions(
            inputs.all_problems, section_number, "problem_id", inputs.combined_summary,
        )
        candidate_patterns, pattern_basis = _filter_by_regions(
            inputs.all_patterns, section_number, "pattern_id", inputs.combined_summary,
        )

        # Boost candidates via synthesis cues (bounded: no full archive)
        if inputs.synthesis_ids:
            problem_basis, pattern_basis = _boost_candidates_by_synthesis(
                candidate_problems, candidate_patterns,
                inputs.all_problems, inputs.all_patterns,
                inputs.synthesis_ids, problem_basis, pattern_basis,
            )

        governing_profile = _resolve_governing_profile(
            section_number, inputs.region_profile_map,
        )
        # Narrow profile scope: include only governing profile or bounded candidates
        bounded_profiles = [
            p for p in inputs.all_profiles
            if isinstance(p, dict) and p.get("profile_id") == governing_profile
        ] if governing_profile else inputs.all_profiles

        governance_questions = _build_governance_questions(
            section_number, problem_basis, pattern_basis,
            candidate_problems, candidate_patterns,
            inputs.all_problems, inputs.all_patterns, inputs.index_parse_failures,
        )
        applicability_state = _determine_applicability_state(
            candidate_problems, candidate_patterns, governing_profile,
            inputs.all_problems, inputs.all_patterns,
            governance_questions, inputs.index_parse_failures,
        )
        packet = {
            "section": section_number,
            "candidate_problems": candidate_problems,
            "candidate_patterns": candidate_patterns,
            "profiles": bounded_profiles,
            "region_profile_map": inputs.region_profile_map,
            "archive_refs": {
                "problem_index": str(paths.governance_problem_index()),
                "pattern_index": str(paths.governance_pattern_index()),
                "profile_index": str(paths.governance_profile_index()),
            },
            "applicability_basis": {
                "problems": problem_basis,
                "patterns": pattern_basis,
            },
            "applicability_state": applicability_state,
            "governance_questions": governance_questions,
            "governing_profile": governing_profile,
        }

        try:
            self._artifact_io.write_json(packet_path, packet)
        except OSError:
            return None
        return packet_path


# ---------------------------------------------------------------------------
# Backward-compat wrappers — used by tests and callers until they are
# converted to receive GovernancePacketBuilder via constructor injection.
# ---------------------------------------------------------------------------

def _get_builder() -> GovernancePacketBuilder:
    from containers import Services
    return GovernancePacketBuilder(artifact_io=Services.artifact_io())


def build_section_governance_packet(
    section_number: str,
    planspace: Path,
    section_summary: str = "",
) -> Path | None:
    return _get_builder().build_section_governance_packet(
        section_number, planspace, section_summary,
    )
