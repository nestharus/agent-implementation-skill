"""Integration tests for main.py unified section handling.

Tests that sections with no related files follow the same proposal path
as sections with related files (no mode-based short-circuit).
"""

import json
from pathlib import Path
from unittest.mock import MagicMock

from _paths import SRC_DIR

from section_loop.main import load_sections, parse_related_files


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
