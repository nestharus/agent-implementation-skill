"""Integration tests: Intake/Governance -> Proposal boundary.

Verifies that governance packets built by GovernancePacketBuilder are
threaded into prompt context assembly for proposal prompts.

Uses real filesystem I/O, real packet building — no mocking except
CrossSectionService (returns section summary text).
"""

from __future__ import annotations

import json
from pathlib import Path

from containers import ArtifactIOService, CrossSectionService
from dispatch.prompt.context_builder import ContextBuilder
from intake.service.governance_packet_builder import GovernancePacketBuilder
from orchestrator.path_registry import PathRegistry
from orchestrator.types import Section


class _StubCrossSection(CrossSectionService):
    """Returns a fixed summary for section spec files."""

    def __init__(self, summary: str = "Authentication and session management") -> None:
        self._summary = summary

    def persist_decision(self, *_args, **_kwargs):
        return None

    def extract_section_summary(self, path) -> str:
        return self._summary

    def write_consequence_note(self, *_args, **_kwargs):
        return None


def _make_section(planspace: Path, num: str = "01") -> Section:
    paths = PathRegistry(planspace)
    spec = paths.section_spec(num)
    if not spec.exists():
        spec.write_text(
            f"# Section {num}: Authentication\n\n"
            f"Handle user login and session management.\n"
        )
    return Section(
        number=num,
        path=spec,
        related_files=["src/auth.py"],
    )


def _write_governance_indexes(
    planspace: Path,
    problems: list[dict] | None = None,
    patterns: list[dict] | None = None,
) -> PathRegistry:
    """Write governance JSON indexes directly into planspace."""
    paths = PathRegistry(planspace)
    artifact_io = ArtifactIOService()

    if problems is None:
        problems = [
            {
                "problem_id": "PRB-001",
                "title": "Auth token expiry",
                "status": "open",
                "regions": ["section-01"],
                "solution_surfaces": "session management, token refresh",
            },
        ]
    if patterns is None:
        patterns = [
            {
                "pattern_id": "PAT-001",
                "title": "Retry with backoff",
                "problem_class": "transient failures",
                "regions": ["section-01"],
                "solution_surfaces": "authentication, API calls",
            },
        ]

    artifact_io.write_json(paths.governance_problem_index(), problems)
    artifact_io.write_json(paths.governance_pattern_index(), patterns)
    artifact_io.write_json(paths.governance_profile_index(), [])
    artifact_io.write_json(
        paths.governance_region_profile_map(),
        {"default": "", "overrides": {}},
    )
    artifact_io.write_json(paths.governance_synthesis_cues(), {})
    artifact_io.write_json(
        paths.governance_index_status(),
        {"ok": True, "parse_failures": []},
    )
    return paths


class TestGovernancePacketBuilding:
    """GovernancePacketBuilder produces section-scoped governance packets."""

    def test_packet_written_to_correct_path(self, planspace: Path) -> None:
        paths = _write_governance_indexes(planspace)
        builder = GovernancePacketBuilder(artifact_io=ArtifactIOService())

        result = builder.build_section_governance_packet(
            "01", planspace, section_summary="authentication login",
        )

        assert result is not None
        assert result == paths.governance_packet("01")
        assert result.exists()

    def test_packet_contains_matched_problems(self, planspace: Path) -> None:
        _write_governance_indexes(planspace)
        builder = GovernancePacketBuilder(artifact_io=ArtifactIOService())

        builder.build_section_governance_packet(
            "01", planspace, section_summary="authentication",
        )

        paths = PathRegistry(planspace)
        packet = json.loads(
            paths.governance_packet("01").read_text(encoding="utf-8"),
        )
        assert packet["section"] == "01"
        assert len(packet["candidate_problems"]) >= 1
        assert packet["candidate_problems"][0]["problem_id"] == "PRB-001"

    def test_packet_contains_matched_patterns(self, planspace: Path) -> None:
        _write_governance_indexes(planspace)
        builder = GovernancePacketBuilder(artifact_io=ArtifactIOService())

        builder.build_section_governance_packet(
            "01", planspace, section_summary="authentication",
        )

        paths = PathRegistry(planspace)
        packet = json.loads(
            paths.governance_packet("01").read_text(encoding="utf-8"),
        )
        assert len(packet["candidate_patterns"]) >= 1
        assert packet["candidate_patterns"][0]["pattern_id"] == "PAT-001"

    def test_packet_applicability_state_matched(self, planspace: Path) -> None:
        _write_governance_indexes(planspace)
        builder = GovernancePacketBuilder(artifact_io=ArtifactIOService())

        builder.build_section_governance_packet(
            "01", planspace, section_summary="authentication",
        )

        paths = PathRegistry(planspace)
        packet = json.loads(
            paths.governance_packet("01").read_text(encoding="utf-8"),
        )
        assert packet["applicability_state"] == "matched"

    def test_no_full_archive_on_no_match(self, planspace: Path) -> None:
        """When no problems/patterns match, packet must NOT hydrate full archive."""
        _write_governance_indexes(
            planspace,
            problems=[{
                "problem_id": "PRB-099",
                "title": "Unrelated database issue",
                "regions": ["section-99"],
                "solution_surfaces": "database sharding",
            }],
            patterns=[{
                "pattern_id": "PAT-099",
                "title": "Connection pooling",
                "regions": ["section-99"],
                "solution_surfaces": "database connections",
            }],
        )
        builder = GovernancePacketBuilder(artifact_io=ArtifactIOService())

        builder.build_section_governance_packet(
            "01", planspace, section_summary="authentication login",
        )

        paths = PathRegistry(planspace)
        packet = json.loads(
            paths.governance_packet("01").read_text(encoding="utf-8"),
        )
        # No candidates — section-scoped filtering rejected them
        assert packet["candidate_problems"] == []
        assert packet["candidate_patterns"] == []
        # Archive refs still present for manual lookup
        assert "archive_refs" in packet


class TestGovernanceThreadedIntoPromptContext:
    """Governance packets appear in prompt context assembly."""

    def test_governance_ref_present_when_packet_exists(
        self, planspace: Path, codespace: Path,
    ) -> None:
        """Context builder includes governance_ref when packet file exists."""
        _write_governance_indexes(planspace)
        builder = GovernancePacketBuilder(artifact_io=ArtifactIOService())
        builder.build_section_governance_packet(
            "01", planspace, section_summary="authentication",
        )

        section = _make_section(planspace)
        ctx = ContextBuilder(
            artifact_io=ArtifactIOService(),
            cross_section=_StubCrossSection(),
        ).build_prompt_context(section, planspace, codespace)

        assert "governance_ref" in ctx
        assert "governance" in ctx["governance_ref"].lower()
        # The reference should point to the packet path
        paths = PathRegistry(planspace)
        assert str(paths.governance_packet("01")) in ctx["governance_ref"]

    def test_governance_ref_empty_when_no_packet(
        self, planspace: Path, codespace: Path,
    ) -> None:
        """Context builder returns empty governance_ref when no packet exists."""
        section = _make_section(planspace)
        ctx = ContextBuilder(
            artifact_io=ArtifactIOService(),
            cross_section=_StubCrossSection(),
        ).build_prompt_context(section, planspace, codespace)

        assert ctx["governance_ref"] == ""

    def test_section_scoped_packet_not_full_archive(
        self, planspace: Path, codespace: Path,
    ) -> None:
        """Context only references section-scoped packet, not full index files."""
        _write_governance_indexes(planspace)
        builder = GovernancePacketBuilder(artifact_io=ArtifactIOService())
        builder.build_section_governance_packet(
            "01", planspace, section_summary="authentication",
        )

        section = _make_section(planspace)
        ctx = ContextBuilder(
            artifact_io=ArtifactIOService(),
            cross_section=_StubCrossSection(),
        ).build_prompt_context(section, planspace, codespace)

        governance_ref = ctx["governance_ref"]
        # Should reference the section packet, not the raw indexes
        assert "governance-packet" in governance_ref
        assert "problem-index.json" not in governance_ref
        assert "pattern-index.json" not in governance_ref

    def test_different_sections_get_different_packets(
        self, planspace: Path, codespace: Path,
    ) -> None:
        """Each section gets its own governance packet with scoped content."""
        artifact_io = ArtifactIOService()
        problems = [
            {
                "problem_id": "PRB-001",
                "title": "Auth token issue",
                "regions": ["section-01"],
                "solution_surfaces": "authentication",
            },
            {
                "problem_id": "PRB-002",
                "title": "Payment timeout",
                "regions": ["section-02"],
                "solution_surfaces": "payment processing",
            },
        ]
        _write_governance_indexes(planspace, problems=problems, patterns=[])

        builder = GovernancePacketBuilder(artifact_io=artifact_io)
        builder.build_section_governance_packet(
            "01", planspace, section_summary="authentication",
        )
        builder.build_section_governance_packet(
            "02", planspace, section_summary="payment processing",
        )

        paths = PathRegistry(planspace)
        packet_01 = json.loads(
            paths.governance_packet("01").read_text(encoding="utf-8"),
        )
        packet_02 = json.loads(
            paths.governance_packet("02").read_text(encoding="utf-8"),
        )

        # Section 01 gets PRB-001 only
        ids_01 = {p["problem_id"] for p in packet_01["candidate_problems"]}
        assert "PRB-001" in ids_01
        assert "PRB-002" not in ids_01

        # Section 02 gets PRB-002 only
        ids_02 = {p["problem_id"] for p in packet_02["candidate_problems"]}
        assert "PRB-002" in ids_02
        assert "PRB-001" not in ids_02


class TestGovernanceContextIntegrity:
    """End-to-end: build indexes, build packet, verify in context."""

    def test_end_to_end_governance_in_context(
        self, planspace: Path, codespace: Path,
    ) -> None:
        """Full pipeline: indexes -> packet -> context reference."""
        artifact_io = ArtifactIOService()
        paths = PathRegistry(planspace)

        # Step 1: Write governance indexes
        artifact_io.write_json(paths.governance_problem_index(), [
            {
                "problem_id": "PRB-010",
                "title": "Session fixation",
                "status": "open",
                "regions": ["section-01"],
                "solution_surfaces": "session management, cookies",
            },
        ])
        artifact_io.write_json(paths.governance_pattern_index(), [
            {
                "pattern_id": "PAT-010",
                "title": "Secure cookie defaults",
                "problem_class": "session security",
                "regions": ["section-01"],
                "solution_surfaces": "cookies, session tokens",
            },
        ])
        artifact_io.write_json(paths.governance_profile_index(), [])
        artifact_io.write_json(
            paths.governance_region_profile_map(),
            {"default": "", "overrides": {}},
        )
        artifact_io.write_json(paths.governance_synthesis_cues(), {})
        artifact_io.write_json(
            paths.governance_index_status(),
            {"ok": True, "parse_failures": []},
        )

        # Step 2: Build packet
        builder = GovernancePacketBuilder(artifact_io=artifact_io)
        packet_path = builder.build_section_governance_packet(
            "01", planspace, section_summary="authentication session cookies",
        )
        assert packet_path is not None
        assert packet_path.exists()

        # Step 3: Verify packet content
        packet = json.loads(packet_path.read_text(encoding="utf-8"))
        assert packet["applicability_state"] == "matched"
        problem_ids = {p["problem_id"] for p in packet["candidate_problems"]}
        pattern_ids = {p["pattern_id"] for p in packet["candidate_patterns"]}
        assert "PRB-010" in problem_ids
        assert "PAT-010" in pattern_ids

        # Step 4: Verify context assembly references the packet
        section = _make_section(planspace)
        ctx = ContextBuilder(
            artifact_io=artifact_io,
            cross_section=_StubCrossSection("session management and cookies"),
        ).build_prompt_context(section, planspace, codespace)

        assert str(packet_path) in ctx["governance_ref"]
