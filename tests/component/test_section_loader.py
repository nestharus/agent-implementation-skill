from __future__ import annotations

from pathlib import Path

from src.scripts.lib.section_loader import load_sections, parse_related_files


def test_parse_related_files_returns_empty_when_block_missing(tmp_path: Path) -> None:
    section_path = tmp_path / "section-01.md"
    section_path.write_text("# Section 01\n\nJust a description.\n", encoding="utf-8")

    assert parse_related_files(section_path) == []


def test_load_sections_filters_non_spec_markdown_files(tmp_path: Path) -> None:
    sections_dir = tmp_path / "sections"
    sections_dir.mkdir()
    (sections_dir / "section-02.md").write_text(
        "# Section 02\n\n## Related Files\n\n### src/two.py\n",
        encoding="utf-8",
    )
    (sections_dir / "section-10.md").write_text(
        "# Section 10\n\n## Related Files\n\n### src/ten.py\n",
        encoding="utf-8",
    )
    (sections_dir / "section-02-proposal-excerpt.md").write_text(
        "ignore me\n",
        encoding="utf-8",
    )
    (sections_dir / "notes.md").write_text("ignore me\n", encoding="utf-8")

    sections = load_sections(sections_dir)

    assert [section.number for section in sections] == ["02", "10"]
    assert [section.related_files for section in sections] == [
        ["src/two.py"],
        ["src/ten.py"],
    ]
