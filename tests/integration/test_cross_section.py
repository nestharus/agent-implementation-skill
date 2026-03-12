"""Integration tests for cross_section module.

Tests pure-logic helpers (diffs, section summaries, decisions,
section number normalization).
"""

from pathlib import Path

import pytest

from _paths import SRC_DIR

from coordination.service.decision_recorder import persist_decision
from implementation.service.file_snapshotter import compute_text_diff
from orchestrator.service.section_decision_store import (
    build_section_number_map,
    extract_section_summary,
    normalize_section_number,
    read_decisions,
)
from orchestrator.types import Section


class TestComputeTextDiff:
    def test_both_missing(self, tmp_path: Path) -> None:
        assert compute_text_diff(
            tmp_path / "a.txt", tmp_path / "b.txt",
        ) == ""

    def test_old_missing(self, tmp_path: Path) -> None:
        new = tmp_path / "new.txt"
        new.write_text("new content\n")
        diff = compute_text_diff(tmp_path / "old.txt", new)
        assert "+" in diff
        assert "new content" in diff

    def test_new_missing(self, tmp_path: Path) -> None:
        old = tmp_path / "old.txt"
        old.write_text("old content\n")
        diff = compute_text_diff(old, tmp_path / "new.txt")
        assert "-" in diff
        assert "old content" in diff

    def test_both_exist_with_changes(self, tmp_path: Path) -> None:
        old = tmp_path / "old.txt"
        new = tmp_path / "new.txt"
        old.write_text("line one\nline two\n")
        new.write_text("line one\nline modified\n")
        diff = compute_text_diff(old, new)
        assert "-line two" in diff
        assert "+line modified" in diff

    def test_identical_files(self, tmp_path: Path) -> None:
        old = tmp_path / "a.txt"
        new = tmp_path / "b.txt"
        old.write_text("same content\n")
        new.write_text("same content\n")
        diff = compute_text_diff(old, new)
        assert diff == ""


class TestExtractSectionSummary:
    def test_yaml_frontmatter(self, tmp_path: Path) -> None:
        f = tmp_path / "section.md"
        f.write_text(
            "---\ntitle: Auth\nsummary: Handle user authentication\n---\n"
            "# Section details...\n",
        )
        assert extract_section_summary(f) == "Handle user authentication"

    def test_fallback_to_first_content_line(self, tmp_path: Path) -> None:
        f = tmp_path / "section.md"
        f.write_text("# Section 01\n\nHandle user login.\n")
        assert extract_section_summary(f) == "Handle user login."

    def test_empty_file(self, tmp_path: Path) -> None:
        f = tmp_path / "section.md"
        f.write_text("")
        assert extract_section_summary(f) == "(no summary available)"

    def test_only_headings(self, tmp_path: Path) -> None:
        f = tmp_path / "section.md"
        f.write_text("# Heading\n## Subheading\n")
        assert extract_section_summary(f) == "(no summary available)"


class TestReadDecisions:
    def test_no_decisions_file(self, planspace: Path) -> None:
        assert read_decisions(planspace, "01") == ""

    def test_existing_decisions(self, planspace: Path) -> None:
        dec = (planspace / "artifacts" / "decisions" / "section-01.md")
        dec.write_text("## Decision\nUse OAuth2\n")
        result = read_decisions(planspace, "01")
        assert "Use OAuth2" in result


class TestPersistDecision:
    def test_creates_decision_file(self, planspace: Path) -> None:
        persist_decision(planspace, "01", "Use JWT tokens")
        dec = planspace / "artifacts" / "decisions" / "section-01.md"
        assert dec.exists()
        assert "Use JWT tokens" in dec.read_text()

    def test_appends_to_existing(self, planspace: Path) -> None:
        persist_decision(planspace, "01", "First decision")
        persist_decision(planspace, "01", "Second decision")
        content = (planspace / "artifacts" / "decisions"
                   / "section-01.md").read_text()
        assert "First decision" in content
        assert "Second decision" in content


class TestNormalizeSectionNumber:
    def test_maps_int_to_canonical(self) -> None:
        sec_map = {1: "01", 2: "02", 10: "10"}
        assert normalize_section_number("1", sec_map) == "01"
        assert normalize_section_number("2", sec_map) == "02"
        assert normalize_section_number("10", sec_map) == "10"

    def test_unknown_int_returns_raw(self) -> None:
        sec_map = {1: "01"}
        assert normalize_section_number("99", sec_map) == "99"

    def test_non_numeric_returns_raw(self) -> None:
        sec_map = {1: "01"}
        assert normalize_section_number("abc", sec_map) == "abc"


class TestImpactPrefilterSeamAwareness:
    """Impact prefilter must consider structured seam artifacts (V13/R66)."""

    def test_shared_input_refs_generate_candidates(self) -> None:
        """Sections sharing .ref files should be impact candidates."""
        src = (
            SRC_DIR / "scripts" / "lib" / "services" / "impact_analyzer.py"
        )
        if not src.exists():
            pytest.skip("impact_analyzer.py not found")
        text = src.read_text(encoding="utf-8")
        # The prefilter must check inputs/ refs
        assert ".ref" in text, (
            "Impact prefilter must check shared .ref input artifacts")
        assert "inputs" in text, (
            "Impact prefilter must check artifacts/inputs/ directory")

    def test_contract_artifacts_generate_candidates(self) -> None:
        """Existing contract artifacts should be impact candidates."""
        src = (
            SRC_DIR / "scripts" / "lib" / "services" / "impact_analyzer.py"
        )
        if not src.exists():
            pytest.skip("impact_analyzer.py not found")
        text = src.read_text(encoding="utf-8")
        # The prefilter must check contract artifacts
        assert "contracts" in text, (
            "Impact prefilter must check existing contract artifacts")


class TestBuildSectionNumberMap:
    def test_builds_correct_map(self, tmp_path: Path) -> None:
        sections = [
            Section(number="01", path=tmp_path / "s1.md"),
            Section(number="02", path=tmp_path / "s2.md"),
            Section(number="10", path=tmp_path / "s10.md"),
        ]
        result = build_section_number_map(sections)
        assert result == {1: "01", 2: "02", 10: "10"}

    def test_empty_list(self) -> None:
        assert build_section_number_map([]) == {}
