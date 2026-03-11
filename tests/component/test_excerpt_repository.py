from __future__ import annotations

from pathlib import Path

import pytest

from src.proposal.repository.excerpts import (
    exists,
    invalidate_all,
    read,
    write,
)


def test_write_read_and_exists_round_trip(tmp_path: Path) -> None:
    planspace = tmp_path / "planspace"

    excerpt_path = write(planspace, "01", "proposal", "excerpt body")

    assert excerpt_path.name == "section-01-proposal-excerpt.md"
    assert exists(planspace, "01", "proposal") is True
    assert read(planspace, "01", "proposal") == "excerpt body"


def test_read_missing_excerpt_returns_none(tmp_path: Path) -> None:
    assert read(tmp_path / "planspace", "01", "alignment") is None


def test_invalidate_all_deletes_only_excerpts(tmp_path: Path) -> None:
    planspace = tmp_path / "planspace"
    sections = planspace / "artifacts" / "sections"
    sections.mkdir(parents=True)
    write(planspace, "01", "proposal", "proposal")
    write(planspace, "01", "alignment", "alignment")
    (sections / "section-01.md").write_text("spec")

    invalidate_all(planspace)

    assert not exists(planspace, "01", "proposal")
    assert not exists(planspace, "01", "alignment")
    assert (sections / "section-01.md").exists()


def test_unknown_excerpt_type_raises_value_error(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        write(tmp_path / "planspace", "01", "other", "body")
