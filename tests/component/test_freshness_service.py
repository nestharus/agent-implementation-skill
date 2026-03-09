from __future__ import annotations

from pathlib import Path

from src.scripts.lib.services.freshness_service import compute_section_freshness


def test_compute_section_freshness_is_stable_for_same_inputs(
    tmp_path: Path,
) -> None:
    planspace = tmp_path / "planspace"
    sections_dir = planspace / "artifacts" / "sections"
    proposals_dir = planspace / "artifacts" / "proposals"
    notes_dir = planspace / "artifacts" / "notes"
    sections_dir.mkdir(parents=True)
    proposals_dir.mkdir(parents=True)
    notes_dir.mkdir(parents=True)

    (sections_dir / "section-01.md").write_text("spec", encoding="utf-8")
    (sections_dir / "section-01-alignment-excerpt.md").write_text(
        "alignment", encoding="utf-8"
    )
    (proposals_dir / "section-01-integration-proposal.md").write_text(
        "proposal", encoding="utf-8"
    )
    (notes_dir / "from-02-to-01.md").write_text("note", encoding="utf-8")

    token_a = compute_section_freshness(planspace, "01")
    token_b = compute_section_freshness(planspace, "01")

    assert token_a == token_b
    assert len(token_a) == 16


def test_compute_section_freshness_changes_when_load_bearing_artifact_changes(
    tmp_path: Path,
) -> None:
    planspace = tmp_path / "planspace"
    decisions_dir = planspace / "artifacts" / "decisions"
    decisions_dir.mkdir(parents=True)
    decision_path = decisions_dir / "section-02.md"
    decision_path.write_text("first", encoding="utf-8")

    before = compute_section_freshness(planspace, "02")
    decision_path.write_text("second", encoding="utf-8")
    after = compute_section_freshness(planspace, "02")

    assert before != after


def test_compute_section_freshness_changes_when_research_dossier_added(
    tmp_path: Path,
) -> None:
    planspace = tmp_path / "planspace"
    dossier_path = (
        planspace
        / "artifacts"
        / "research"
        / "sections"
        / "section-03"
        / "dossier.md"
    )
    dossier_path.parent.mkdir(parents=True, exist_ok=True)

    before = compute_section_freshness(planspace, "03")
    dossier_path.write_text("research dossier", encoding="utf-8")
    after = compute_section_freshness(planspace, "03")

    assert before != after


def test_compute_section_freshness_changes_when_impl_feedback_added(
    tmp_path: Path,
) -> None:
    planspace = tmp_path / "planspace"
    feedback_path = (
        planspace
        / "artifacts"
        / "signals"
        / "impl-feedback-surfaces-04.json"
    )
    feedback_path.parent.mkdir(parents=True, exist_ok=True)

    before = compute_section_freshness(planspace, "04")
    feedback_path.write_text('{"problem_surfaces":[]}', encoding="utf-8")
    after = compute_section_freshness(planspace, "04")

    assert before != after


def test_compute_section_freshness_changes_when_research_derived_added(
    tmp_path: Path,
) -> None:
    planspace = tmp_path / "planspace"
    derived_path = (
        planspace
        / "artifacts"
        / "research"
        / "sections"
        / "section-05"
        / "research-derived-surfaces.json"
    )
    derived_path.parent.mkdir(parents=True, exist_ok=True)

    before = compute_section_freshness(planspace, "05")
    derived_path.write_text('{"philosophy_surfaces":[]}', encoding="utf-8")
    after = compute_section_freshness(planspace, "05")

    assert before != after


def test_compute_section_freshness_changes_when_research_artifact_removed(
    tmp_path: Path,
) -> None:
    planspace = tmp_path / "planspace"
    addendum_path = (
        planspace
        / "artifacts"
        / "research"
        / "sections"
        / "section-06"
        / "proposal-addendum.md"
    )
    addendum_path.parent.mkdir(parents=True, exist_ok=True)
    addendum_path.write_text("addendum", encoding="utf-8")

    before = compute_section_freshness(planspace, "06")
    addendum_path.unlink()
    after = compute_section_freshness(planspace, "06")

    assert before != after
