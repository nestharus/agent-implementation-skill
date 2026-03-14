"""Integration tests for section_engine.py problem-frame gate (P1/R19).

Tests that the problem frame quality gate is enforced:
- Missing problem frame → retry → still missing → needs_parent signal
- Incomplete headings → needs_parent signal
- Complete problem frame → passes validation
"""

import json
from pathlib import Path
from unittest.mock import MagicMock

from staleness.helpers.content_hasher import file_hash
from orchestrator.engine.section_pipeline import run_section
from orchestrator.types import Section


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
    intent_global = planspace / "artifacts" / "intent" / "global"
    intent_global.mkdir(parents=True, exist_ok=True)
    source_path = codespace / "README.md"
    if not source_path.exists():
        source_path.write_text(
            "# Project Notes\n\n"
            "Fail explicitly. Escalate uncertainty before risky changes.\n",
            encoding="utf-8",
        )
    (intent_global / "philosophy.md").write_text(
        "# Operational Philosophy\n\n"
        "## Principles\n\n"
        "### P1: Fail explicitly with context.\n"
        "Grounding: README.\n"
        "Test: silent defaults violate this.\n",
        encoding="utf-8",
    )
    (intent_global / "philosophy-source-map.json").write_text(
        json.dumps({
            "P1": {
                "source_type": "repo_source",
                "source_file": str(source_path),
                "source_section": "Project Notes",
            },
        }),
        encoding="utf-8",
    )
    (intent_global / "philosophy-source-manifest.json").write_text(
        json.dumps({
            "sources": [{
                "path": str(source_path),
                "hash": file_hash(source_path),
                "source_type": "repo_source",
            }],
        }),
        encoding="utf-8",
    )
    return sec


class TestProblemFrameMissing:
    """P1/R19: missing problem frame → retry once → needs_parent signal."""

    def test_missing_after_retry_returns_none(
        self, planspace: Path, codespace: Path, mock_dispatch: MagicMock,
    ) -> None:
        section = _make_section(planspace, codespace)
        mock_dispatch.return_value = ""

        result = run_section(planspace, codespace, section)

        assert result is None

    def test_missing_after_retry_writes_signal(
        self, planspace: Path, codespace: Path, mock_dispatch: MagicMock,
    ) -> None:
        section = _make_section(planspace, codespace)
        mock_dispatch.return_value = ""

        run_section(planspace, codespace, section)

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

        run_section(planspace, codespace, section)

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

        run_section(planspace, codespace, section)

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

        result = run_section(planspace, codespace, section)

        # The gate should have passed — no needs_parent signal about
        # problem frame being missing
        signal_path = (planspace / "artifacts" / "signals"
                       / "setup-01-signal.json")
        if signal_path.exists():
            data = json.loads(signal_path.read_text())
            # Signal should NOT be needs_parent about problem frame
            if data.get("state") == "needs_parent":
                assert "problem frame" not in data.get("detail", "").lower()


class TestProblemFrameEmpty:
    """R68/V3: empty problem frame → needs_parent (no heading gate)."""

    def test_empty_frame_returns_none(
        self, planspace: Path, codespace: Path, mock_dispatch: MagicMock,
    ) -> None:
        section = _make_section(planspace, codespace)
        mock_dispatch.return_value = ""
        # Create an empty problem frame
        pf_path = (planspace / "artifacts" / "sections"
                   / "section-01-problem-frame.md")
        pf_path.write_text("")

        result = run_section(planspace, codespace, section)
        assert result is None

    def test_empty_frame_writes_signal(
        self, planspace: Path, codespace: Path, mock_dispatch: MagicMock,
    ) -> None:
        section = _make_section(planspace, codespace)
        mock_dispatch.return_value = ""
        pf_path = (planspace / "artifacts" / "sections"
                   / "section-01-problem-frame.md")
        pf_path.write_text("   \n  \n  ")  # whitespace-only = empty

        run_section(planspace, codespace, section)

        signal_path = (planspace / "artifacts" / "signals"
                       / "setup-01-signal.json")
        assert signal_path.exists()
        data = json.loads(signal_path.read_text())
        assert data["state"] == "needs_parent"
        assert "empty" in data["detail"].lower()

    def test_nonempty_frame_passes(
        self, planspace: Path, codespace: Path, mock_dispatch: MagicMock,
    ) -> None:
        """A non-empty problem frame should pass the non-empty check.

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
        )
        mock_dispatch.return_value = ""

        result = run_section(planspace, codespace, section)

        # result is None because proposal loop fails, but the non-empty
        # gate should NOT have fired
        signal_path = (planspace / "artifacts" / "signals"
                       / "setup-01-signal.json")
        if signal_path.exists():
            data = json.loads(signal_path.read_text())
            assert "empty" not in data.get("detail", "").lower()
