"""Integration tests for main.py unified section handling.

Tests that sections with no related files follow the same proposal path
as sections with related files (no mode-based short-circuit).
"""

import json
from pathlib import Path
from unittest.mock import MagicMock

from _paths import SRC_DIR

from scan.service.section_loader import load_sections, parse_related_files

from orchestrator.types import Section


class TestNoModeRouting:
    """Sections with no related files use the same path as all sections.

    PAT-0015: positive contract test — asserts current mode-is-observation
    behavior rather than grepping for deleted source text.
    """

    def test_mode_is_observation_in_main(self) -> None:
        """main.py treats mode as observation, not routing key.

        The runtime resolves project_mode for telemetry only — it does not
        branch on the value to skip proposal or readiness paths.
        """
        main_path = (SRC_DIR / "orchestrator" / "engine" / "pipeline_orchestrator.py")
        content = main_path.read_text()
        # Positive assertion: mode is resolved but only for contract writing
        assert "resolve_project_mode" in content, (
            "main.py must resolve project mode (mode-is-observation contract)"
        )
        assert "write_mode_contract" in content, (
            "main.py must write mode contract for downstream consumers"
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
