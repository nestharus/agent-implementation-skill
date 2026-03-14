from __future__ import annotations

from pathlib import Path

from src.orchestrator.path_registry import PathRegistry
from src.coordination.repository.notes import (
    read_incoming_notes,
    write_consequence_note,
)


def test_read_incoming_notes_returns_sorted_note_records(tmp_path: Path) -> None:
    planspace = tmp_path / "planspace"
    planspace.mkdir()
    PathRegistry(planspace).ensure_artifacts_tree()
    notes_dir = planspace / "artifacts" / "notes"
    (notes_dir / "from-02-to-01.md").write_text("second")
    (notes_dir / "from-01-to-01.md").write_text("first")

    notes = read_incoming_notes(planspace, "01")

    assert [note["source"] for note in notes] == ["01", "02"]
    assert [note["content"] for note in notes] == ["first", "second"]


def test_write_consequence_note_creates_expected_path(tmp_path: Path) -> None:
    planspace = tmp_path / "planspace"
    planspace.mkdir()
    PathRegistry(planspace).ensure_artifacts_tree()

    note_path = write_consequence_note(
        planspace,
        "bridge-03",
        "07",
        "note body",
    )

    assert note_path.name == "from-bridge-03-to-07.md"
    assert note_path.read_text(encoding="utf-8") == "note body"
