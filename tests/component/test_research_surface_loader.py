from __future__ import annotations

import json
from pathlib import Path

from src.intent.service.surface_registry import load_research_derived_surfaces


def test_load_research_derived_surfaces_preserves_schema_mismatch(
    tmp_path: Path,
) -> None:
    planspace = tmp_path / "planspace"
    research_path = (
        planspace
        / "artifacts"
        / "research"
        / "sections"
        / "section-01"
        / "research-derived-surfaces.json"
    )
    research_path.parent.mkdir(parents=True, exist_ok=True)
    research_path.write_text(json.dumps({"stage": "research"}), encoding="utf-8")

    assert load_research_derived_surfaces("01", planspace) is None
    assert research_path.with_suffix(".malformed.json").exists()


def test_load_research_derived_surfaces_accepts_expected_shape(
    tmp_path: Path,
) -> None:
    planspace = tmp_path / "planspace"
    research_path = (
        planspace
        / "artifacts"
        / "research"
        / "sections"
        / "section-01"
        / "research-derived-surfaces.json"
    )
    research_path.parent.mkdir(parents=True, exist_ok=True)
    research_path.write_text(
        json.dumps({"problem_surfaces": [], "philosophy_surfaces": []}),
        encoding="utf-8",
    )

    assert load_research_derived_surfaces("01", planspace) == {
        "problem_surfaces": [],
        "philosophy_surfaces": [],
    }
