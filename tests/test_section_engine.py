"""Integration tests for section_engine.py problem-frame gate (P1/R19).

Tests that the problem frame quality gate is enforced:
- Missing problem frame → retry → still missing → needs_parent signal
- Incomplete headings → needs_parent signal
- Complete problem frame → passes validation
"""

import json
from pathlib import Path
from unittest.mock import MagicMock

from section_loop.section_engine import run_section
from section_loop.types import Section


def _make_section(planspace: Path, codespace: Path) -> Section:
    """Create a minimal section with excerpts already in place."""
    sec = Section(
        number="01",
        path=planspace / "artifacts" / "sections" / "section-01.md",
        related_files=["src/main.py"],
    )
    sec.global_proposal_path = planspace / "artifacts" / "global-proposal.md"
    sec.global_alignment_path = planspace / "artifacts" / "global-alignment.md"
    sec.global_proposal_path.write_text("# Global Proposal\nAll sections...")
    sec.global_alignment_path.write_text("# Global Alignment\nConstraints...")
    sec.path.write_text("# Section 01\n\nAuthentication logic.\n")
    # Pre-create excerpts so setup loop is skipped
    sections_dir = planspace / "artifacts" / "sections"
    (sections_dir / "section-01-proposal-excerpt.md").write_text("excerpt")
    (sections_dir / "section-01-alignment-excerpt.md").write_text("excerpt")
    return sec


class TestProblemFrameMissing:
    """P1/R19: missing problem frame → retry once → needs_parent signal."""

    def test_missing_after_retry_returns_none(
        self, planspace: Path, codespace: Path, mock_dispatch: MagicMock,
    ) -> None:
        section = _make_section(planspace, codespace)
        mock_dispatch.return_value = ""

        result = run_section(planspace, codespace, section, "parent")

        assert result is None

    def test_missing_after_retry_writes_signal(
        self, planspace: Path, codespace: Path, mock_dispatch: MagicMock,
    ) -> None:
        section = _make_section(planspace, codespace)
        mock_dispatch.return_value = ""

        run_section(planspace, codespace, section, "parent")

        signal_path = (planspace / "artifacts" / "signals"
                       / "setup-01-signal.json")
        assert signal_path.exists()
        data = json.loads(signal_path.read_text())
        assert data["state"] == "needs_parent"
        assert "problem frame" in data["detail"].lower()

    def test_missing_after_retry_updates_blocker_rollup(
        self, planspace: Path, codespace: Path, mock_dispatch: MagicMock,
    ) -> None:
        section = _make_section(planspace, codespace)
        mock_dispatch.return_value = ""

        run_section(planspace, codespace, section, "parent")

        rollup_path = (planspace / "artifacts" / "decisions"
                       / "needs-input.md")
        assert rollup_path.exists()
        content = rollup_path.read_text()
        assert "NEEDS_PARENT" in content

    def test_retry_dispatches_with_setup_excerpter(
        self, planspace: Path, codespace: Path, mock_dispatch: MagicMock,
    ) -> None:
        """The retry should dispatch with agent_file=setup-excerpter.md."""
        section = _make_section(planspace, codespace)
        mock_dispatch.return_value = ""

        run_section(planspace, codespace, section, "parent")

        # The retry dispatch should use agent_file="setup-excerpter.md"
        retry_calls = [
            c for c in mock_dispatch.call_args_list
            if c.kwargs.get("agent_file") == "setup-excerpter.md"
        ]
        assert len(retry_calls) >= 1

    def test_retry_creates_problem_frame_passes_gate(
        self, planspace: Path, codespace: Path, mock_dispatch: MagicMock,
    ) -> None:
        """If the retry creates the problem frame, the gate passes.

        The first dispatch creates the problem frame file (simulating the
        setup-excerpter succeeding on retry). Subsequent dispatches return
        "" so the proposal loop exits fast at "proposal not written".
        We verify the gate didn't fire needs_parent about the problem frame.
        """
        section = _make_section(planspace, codespace)
        pf_path = (planspace / "artifacts" / "sections"
                   / "section-01-problem-frame.md")

        call_count = 0

        def side_effects(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            # First call is the retry setup — create problem frame
            if call_count == 1:
                pf_path.write_text(
                    "# Problem Frame\n\n"
                    "## Problem Statement\nAuth is broken.\n\n"
                    "## Evidence\n- From proposal.\n\n"
                    "## Constraints\n- OAuth2.\n\n"
                    "## Success Criteria\n- Login works.\n\n"
                    "## Out of Scope\n- SSO.\n"
                )
            return ""

        mock_dispatch.side_effect = side_effects

        # Do NOT pre-create integration proposal — let the proposal loop
        # fail fast at "proposal not written" (returns None).

        result = run_section(planspace, codespace, section, "parent")

        # The gate should have passed — no needs_parent signal about
        # problem frame being missing
        signal_path = (planspace / "artifacts" / "signals"
                       / "setup-01-signal.json")
        if signal_path.exists():
            data = json.loads(signal_path.read_text())
            # Signal should NOT be needs_parent about problem frame
            if data.get("state") == "needs_parent":
                assert "problem frame" not in data.get("detail", "").lower()


class TestProblemFrameIncompleteHeadings:
    """P1/R19: problem frame missing required headings → needs_parent."""

    def test_missing_headings_returns_none(
        self, planspace: Path, codespace: Path, mock_dispatch: MagicMock,
    ) -> None:
        section = _make_section(planspace, codespace)
        mock_dispatch.return_value = ""
        # Create a problem frame missing some required headings
        pf_path = (planspace / "artifacts" / "sections"
                   / "section-01-problem-frame.md")
        pf_path.write_text(
            "# Problem Frame\n\n"
            "## Problem Statement\nAuth is broken.\n\n"
            "## Evidence\n- Missing auth module.\n"
            # Missing: Constraints, Success Criteria, Out of Scope
        )

        result = run_section(planspace, codespace, section, "parent")
        assert result is None

    def test_missing_headings_writes_signal(
        self, planspace: Path, codespace: Path, mock_dispatch: MagicMock,
    ) -> None:
        section = _make_section(planspace, codespace)
        mock_dispatch.return_value = ""
        pf_path = (planspace / "artifacts" / "sections"
                   / "section-01-problem-frame.md")
        pf_path.write_text(
            "# Problem Frame\n\n"
            "## Problem Statement\nAuth is broken.\n\n"
            "## Evidence\n- Missing.\n"
        )

        run_section(planspace, codespace, section, "parent")

        signal_path = (planspace / "artifacts" / "signals"
                       / "setup-01-signal.json")
        assert signal_path.exists()
        data = json.loads(signal_path.read_text())
        assert data["state"] == "needs_parent"
        assert "headings" in data["detail"].lower()

    def test_all_headings_present_passes(
        self, planspace: Path, codespace: Path, mock_dispatch: MagicMock,
    ) -> None:
        """A complete problem frame should pass the heading check.

        The mock returns "" so the proposal loop exits fast at
        "integration proposal not written" — but the gate check is before
        the proposal loop, so we can verify the gate didn't fire.
        """
        section = _make_section(planspace, codespace)
        pf_path = (planspace / "artifacts" / "sections"
                   / "section-01-problem-frame.md")
        pf_path.write_text(
            "# Problem Frame\n\n"
            "## Problem Statement\nAuth is broken.\n\n"
            "## Evidence\n- From proposal: auth module missing.\n\n"
            "## Constraints\n- Must use OAuth2.\n\n"
            "## Success Criteria\n- Login works.\n\n"
            "## Out of Scope\n- SSO integration.\n"
        )
        mock_dispatch.return_value = ""
        # Do NOT pre-create integration proposal — let the proposal loop
        # fail fast at "proposal not written" (returns None).
        # The heading gate is checked before the proposal loop.

        result = run_section(planspace, codespace, section, "parent")

        # result is None because proposal loop fails, but the heading
        # gate should NOT have fired — check no signal about headings
        signal_path = (planspace / "artifacts" / "signals"
                       / "setup-01-signal.json")
        if signal_path.exists():
            data = json.loads(signal_path.read_text())
            assert "headings" not in data.get("detail", "").lower()
