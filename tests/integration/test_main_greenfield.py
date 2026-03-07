"""Integration tests for main.py unified section handling.

Tests that sections with no related files follow the same proposal path
as sections with related files (no mode-based short-circuit).
"""

import json
from pathlib import Path
from unittest.mock import MagicMock

from _paths import SRC_DIR

from lib.sections.section_loader import load_sections, parse_related_files

from section_loop.types import Section


class TestNoModeRouting:
    """Sections with no related files use the same path as all sections."""

    def test_no_greenfield_shortcircuit_in_main(self) -> None:
        """main.py must not short-circuit based on project_mode == greenfield."""
        main_path = (SRC_DIR / "scripts" / "section_loop" / "main.py")
        content = main_path.read_text()
        # The old greenfield short-circuit checked project_mode and wrote
        # a blocker signal directly. This should no longer exist.
        assert 'project_mode == "greenfield"' not in content, (
            "main.py must not route based on project_mode == greenfield"
        )


class TestParseRelatedFiles:
    def test_no_related_files_section(self, tmp_path: Path) -> None:
        p = tmp_path / "section-01.md"
        p.write_text("# Section 01\n\nJust a description.\n")
        assert parse_related_files(p) == []


class TestLoadSections:
    def test_load_sections_ignores_non_spec_artifacts(
        self, tmp_path: Path,
    ) -> None:
        sections_dir = tmp_path / "sections"
        sections_dir.mkdir()
        (sections_dir / "section-01.md").write_text(
            "# Section 01\n\n## Related Files\n### src/main.py\n",
            encoding="utf-8",
        )
        (sections_dir / "section-01-proposal-excerpt.md").write_text(
            "excerpt",
            encoding="utf-8",
        )

        loaded = load_sections(sections_dir)

        assert loaded == [
            Section(
                number="01",
                path=sections_dir / "section-01.md",
                related_files=["src/main.py"],
            ),
        ]
