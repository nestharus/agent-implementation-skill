from __future__ import annotations

import json
from pathlib import Path

from orchestrator.types import Section

from src.orchestrator.service.section_decisions import (
    build_section_number_map,
    extract_section_summary,
    normalize_section_number,
    persist_decision,
    read_decisions,
)


def test_extract_section_summary_prefers_yaml_frontmatter(tmp_path: Path) -> None:
    section_path = tmp_path / "section.md"
    section_path.write_text(
        "---\ntitle: Auth\nsummary: Handle user authentication\n---\n# Section\n",
        encoding="utf-8",
    )

    assert extract_section_summary(section_path) == "Handle user authentication"


def test_extract_section_summary_falls_back_to_first_content_line(
    tmp_path: Path,
) -> None:
    section_path = tmp_path / "section.md"
    section_path.write_text("# Section 01\n\nHandle user login.\n", encoding="utf-8")

    assert extract_section_summary(section_path) == "Handle user login."


def test_persist_decision_writes_json_and_prose(tmp_path: Path) -> None:
    planspace = tmp_path / "planspace"

    persist_decision(planspace, "01", "Use JWT tokens")

    decisions_dir = planspace / "artifacts" / "decisions"
    prose = (decisions_dir / "section-01.md").read_text(encoding="utf-8")
    payload = json.loads((decisions_dir / "section-01.json").read_text(encoding="utf-8"))

    assert "Use JWT tokens" in prose
    assert payload[0]["proposal_summary"] == "Use JWT tokens"
    assert read_decisions(planspace, "01") == prose


def test_section_number_helpers_normalize_to_canonical_form(tmp_path: Path) -> None:
    sections = [
        Section(number="01", path=tmp_path / "section-01.md"),
        Section(number="10", path=tmp_path / "section-10.md"),
    ]

    sec_map = build_section_number_map(sections)

    assert sec_map == {1: "01", 10: "10"}
    assert normalize_section_number("1", sec_map) == "01"
    assert normalize_section_number("abc", sec_map) == "abc"
