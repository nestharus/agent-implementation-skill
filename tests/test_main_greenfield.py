"""Integration tests for main.py greenfield routing (P2/R19).

Tests that greenfield sections produce standard blocker signals
instead of custom NEEDS_RESEARCH format.
"""

import json
from pathlib import Path
from unittest.mock import MagicMock

from section_loop.main import _run_loop, load_sections, parse_related_files
from section_loop.types import Section, SectionResult


class TestGreenfieldBlockerSignal:
    """P2/R19: greenfield section produces blocker.json with needs_parent."""

    def test_greenfield_section_writes_blocker_json(
        self, planspace: Path, codespace: Path, mock_dispatch: MagicMock,
    ) -> None:
        """A greenfield section with no related files should write a
        standard blocker.json with state=needs_parent."""
        # Create section spec with NO related files
        sec_path = (planspace / "artifacts" / "sections"
                    / "section-01.md")
        sec_path.write_text("# Section 01\n\nNew feature from scratch.\n")

        # Set project mode to greenfield
        mode_path = planspace / "artifacts" / "project-mode.txt"
        mode_path.write_text("greenfield")

        # Create global docs
        global_p = planspace / "artifacts" / "global-proposal.md"
        global_a = planspace / "artifacts" / "global-alignment.md"
        global_p.write_text("# Global Proposal\nAll sections.")
        global_a.write_text("# Global Alignment\nConstraints.")

        sections_dir = planspace / "artifacts" / "sections"

        # Mock dispatch_agent to avoid real LLM calls
        mock_dispatch.return_value = ""

        # Run the loop — it will process the greenfield section and
        # exit when it pauses (returns None from run_section path)
        # We need to catch the return from _run_loop
        _run_loop(
            planspace, codespace, "parent", sections_dir,
            global_p, global_a,
        )

        # Verify blocker signal was written
        signal_dir = planspace / "artifacts" / "signals"
        blocker_path = signal_dir / "section-01-blocker.json"
        assert blocker_path.exists(), (
            "Expected blocker.json for greenfield section"
        )
        data = json.loads(blocker_path.read_text())
        assert data["state"] == "needs_parent"
        assert "greenfield" in data["detail"].lower() or \
               "no related files" in data["detail"].lower()

    def test_greenfield_section_result_uses_needs_parent_prefix(
        self, planspace: Path, codespace: Path, mock_dispatch: MagicMock,
    ) -> None:
        """The SectionResult.problems should use needs_parent: prefix."""
        sec_path = (planspace / "artifacts" / "sections"
                    / "section-01.md")
        sec_path.write_text("# Section 01\n\nNew feature.\n")

        mode_path = planspace / "artifacts" / "project-mode.txt"
        mode_path.write_text("greenfield")

        global_p = planspace / "artifacts" / "global-proposal.md"
        global_a = planspace / "artifacts" / "global-alignment.md"
        global_p.write_text("proposal")
        global_a.write_text("alignment")

        sections_dir = planspace / "artifacts" / "sections"
        mock_dispatch.return_value = ""

        _run_loop(
            planspace, codespace, "parent", sections_dir,
            global_p, global_a,
        )

        # The blocker signal should be the needs_parent type
        signal_dir = planspace / "artifacts" / "signals"
        blocker_path = signal_dir / "section-01-blocker.json"
        assert blocker_path.exists()
        data = json.loads(blocker_path.read_text())
        assert data["state"] == "needs_parent"
        # "needs" field should describe options for the parent
        assert "needs" in data


class TestParseRelatedFiles:
    def test_no_related_files_section(self, tmp_path: Path) -> None:
        p = tmp_path / "section-01.md"
        p.write_text("# Section 01\n\nJust a description.\n")
        assert parse_related_files(p) == []

    def test_extracts_file_paths(self, tmp_path: Path) -> None:
        p = tmp_path / "section-01.md"
        p.write_text(
            "# Section 01\n\nDescription.\n\n"
            "## Related Files\n\n"
            "### src/auth.py\nHandles authentication.\n\n"
            "### src/db.py\nDatabase layer.\n"
        )
        result = parse_related_files(p)
        assert result == ["src/auth.py", "src/db.py"]

    def test_ignores_paths_in_code_fences(self, tmp_path: Path) -> None:
        p = tmp_path / "section-01.md"
        p.write_text(
            "# Section 01\n\n"
            "## Related Files\n\n"
            "### src/real.py\nReal file.\n\n"
            "```\n### src/fake.py\nInside a fence.\n```\n"
        )
        result = parse_related_files(p)
        assert result == ["src/real.py"]
