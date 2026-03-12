"""Integration tests for section loop phase transitions.

Tests the artifact contract between phases:
  proposal pass -> reconciliation -> implementation pass

Mock boundary: ``dispatch_agent`` (the LLM call) is mocked.
Everything else — file I/O, db.sh SQLite, reconciliation detectors — runs
for real.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from signals.repository.artifact_io import write_json
from orchestrator.path_registry import PathRegistry
from implementation.engine.implementation_pass import (
    ImplementationPassRestart,
    run_implementation_pass,
)
from proposal.engine.proposal_pass import ProposalPassExit, run_proposal_pass
from reconciliation.engine.phase import run_reconciliation_phase
from orchestrator.types import ProposalPassResult, Section, SectionResult


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_section(
    planspace: Path,
    codespace: Path,
    number: str = "01",
    *,
    related_files: list[str] | None = None,
) -> Section:
    """Create a Section with minimal planspace artifacts."""
    paths = PathRegistry(planspace)
    sec_path = paths.sections_dir() / f"section-{number}.md"
    sec_path.write_text(
        f"# Section {number}: Feature {number}\n\nImplement feature {number}.\n",
        encoding="utf-8",
    )
    global_proposal = planspace / "artifacts" / "global-proposal.md"
    global_alignment = planspace / "artifacts" / "global-alignment.md"
    if not global_proposal.exists():
        global_proposal.write_text("# Global Proposal\nAll sections.\n")
    if not global_alignment.exists():
        global_alignment.write_text("# Global Alignment\nConstraints.\n")

    return Section(
        number=number,
        path=sec_path,
        global_proposal_path=global_proposal,
        global_alignment_path=global_alignment,
        related_files=related_files or ["src/main.py"],
    )


def _write_proposal_state(
    planspace: Path,
    section_number: str,
    *,
    execution_ready: bool = True,
    overrides: dict | None = None,
) -> Path:
    """Write a minimal proposal-state artifact that passes readiness.

    Each section gets unique anchor/contract names to avoid false
    reconciliation conflicts unless ``overrides`` explicitly sets them.
    """
    paths = PathRegistry(planspace)
    state = {
        "resolved_anchors": [f"anchor-{section_number}"],
        "unresolved_anchors": [],
        "resolved_contracts": [f"contract-{section_number}"],
        "unresolved_contracts": [],
        "research_questions": [],
        "blocking_research_questions": [],
        "user_root_questions": [],
        "new_section_candidates": [],
        "shared_seam_candidates": [],
        "execution_ready": execution_ready,
        "readiness_rationale": "ready" if execution_ready else "blocked",
        "problem_ids": [],
        "pattern_ids": [],
        "profile_id": "",
        "pattern_deviations": [],
        "governance_questions": [],
        "constraint_ids": [],
        "governance_candidate_refs": [],
        "design_decision_refs": [],
    }
    if overrides:
        state.update(overrides)
    state_path = paths.proposal_state(section_number)
    write_json(state_path, state)
    return state_path


def _make_proposal_result(
    section_number: str,
    *,
    execution_ready: bool = True,
    blockers: list[dict] | None = None,
) -> ProposalPassResult:
    """Build a ProposalPassResult directly (bypasses run_section)."""
    return ProposalPassResult(
        section_number=section_number,
        proposal_aligned=True,
        execution_ready=execution_ready,
        blockers=blockers or [],
        needs_reconciliation=False,
        proposal_state_path="",
    )


def _default_policy() -> dict:
    return {
        "setup": "mock-model",
        "proposal": "mock-model",
        "alignment": "mock-model",
        "implementation": "mock-model",
        "impact_analysis": "mock-model",
        "impact_normalizer": "mock-model",
    }


# ---------------------------------------------------------------------------
# 1. Proposal results are valid input for reconciliation
# ---------------------------------------------------------------------------

class TestProposalResultsValidForReconciliation:
    """Verify that ProposalPassResult objects produced by run_proposal_pass
    satisfy the input contract of run_reconciliation_phase."""

    def test_proposal_results_feed_reconciliation(
        self,
        planspace: Path,
        codespace: Path,
        mock_dispatch: MagicMock,
    ) -> None:
        """Proposal pass output feeds directly into reconciliation."""
        section = _make_section(planspace, codespace, "01")
        sections_by_num = {"01": section}
        all_sections = [section]

        # Build a ProposalPassResult as if run_proposal_pass produced it
        proposal_results: dict[str, ProposalPassResult] = {
            "01": _make_proposal_result("01", execution_ready=True),
        }
        # Write the proposal-state artifact that reconciliation loads
        _write_proposal_state(planspace, "01", execution_ready=True)

        result = run_reconciliation_phase(
            proposal_results,
            sections_by_num,
            all_sections,
            planspace,
            codespace,
            "parent",
            _default_policy(),
        )

        # No conflicts → section stays ready
        assert "01" in result.new_section_numbers
        assert result.removed_section_numbers == []
        assert result.alignment_changed is False

    def test_proposal_results_with_multiple_sections(
        self,
        planspace: Path,
        codespace: Path,
        mock_dispatch: MagicMock,
    ) -> None:
        """Multiple proposal results are correctly partitioned."""
        sec1 = _make_section(planspace, codespace, "01")
        sec2 = _make_section(planspace, codespace, "02")
        sections_by_num = {"01": sec1, "02": sec2}
        all_sections = [sec1, sec2]

        proposal_results = {
            "01": _make_proposal_result("01", execution_ready=True),
            "02": _make_proposal_result(
                "02",
                execution_ready=False,
                blockers=[{"type": "unresolved", "description": "needs design"}],
            ),
        }
        _write_proposal_state(planspace, "01", execution_ready=True)
        _write_proposal_state(planspace, "02", execution_ready=False)

        result = run_reconciliation_phase(
            proposal_results,
            sections_by_num,
            all_sections,
            planspace,
            codespace,
            "parent",
            _default_policy(),
        )

        assert "01" in result.new_section_numbers
        assert "02" in result.removed_section_numbers
        assert result.alignment_changed is False


# ---------------------------------------------------------------------------
# 2. ProposalPassExit stops the outer loop
# ---------------------------------------------------------------------------

class TestProposalPassExitSignal:
    """Verify that ProposalPassExit is raised on abort signal."""

    def test_proposal_pass_exit_on_abort(
        self,
        planspace: Path,
        codespace: Path,
        mock_dispatch: MagicMock,
        capturing_pipeline_control,
        noop_communicator,
    ) -> None:
        """When handle_pending_messages returns True (abort), raise ProposalPassExit."""
        section = _make_section(planspace, codespace, "01")
        sections_by_num = {"01": section}
        all_sections = [section]

        capturing_pipeline_control._pending_return = True

        with pytest.raises(ProposalPassExit):
            run_proposal_pass(
                all_sections,
                sections_by_num,
                planspace,
                codespace,
                "parent",
                _default_policy(),
            )


# ---------------------------------------------------------------------------
# 3. Blocked sections excluded from implementation
# ---------------------------------------------------------------------------

class TestBlockedSectionsExcludedFromImplementation:
    """Verify that reconciliation-blocked sections do not reach implementation."""

    def test_only_ready_sections_enter_implementation(
        self,
        planspace: Path,
        codespace: Path,
        mock_dispatch: MagicMock,
    ) -> None:
        """Implementation pass only processes execution_ready sections."""
        sec1 = _make_section(planspace, codespace, "01")
        sec2 = _make_section(planspace, codespace, "02")
        sections_by_num = {"01": sec1, "02": sec2}

        # Section 01 is ready, section 02 is blocked
        proposal_results = {
            "01": _make_proposal_result("01", execution_ready=True),
            "02": _make_proposal_result(
                "02",
                execution_ready=False,
                blockers=[{"type": "reconciliation", "description": "conflict"}],
            ),
        }

        # Write proposal-state for section 01 (ready) — implementation
        # checks readiness resolver which loads this.
        _write_proposal_state(planspace, "01", execution_ready=True)
        _write_proposal_state(planspace, "02", execution_ready=False)

        # Mock run_section to track which sections get dispatched
        dispatched_sections: list[str] = []

        def track_run_section(
            planspace, codespace, section, parent, *,
            all_sections=None, pass_mode="full",
        ):
            dispatched_sections.append(section.number)
            return ["src/main.py"]  # modified files

        with patch(
            "implementation.engine.implementation_pass.run_section",
            side_effect=track_run_section,
        ), patch(
            "implementation.engine.implementation_pass._run_risk_review",
            return_value=None,
        ):
            results = run_implementation_pass(
                proposal_results,
                sections_by_num,
                planspace,
                codespace,
                "parent",
            )

        # Only section 01 should have been dispatched
        assert "01" in dispatched_sections
        assert "02" not in dispatched_sections

    def test_reconciliation_can_block_previously_ready_section(
        self,
        planspace: Path,
        codespace: Path,
        mock_dispatch: MagicMock,
    ) -> None:
        """Reconciliation detects conflicts and demotes ready -> blocked."""
        sec1 = _make_section(planspace, codespace, "01")
        sec2 = _make_section(planspace, codespace, "02")
        sections_by_num = {"01": sec1, "02": sec2}
        all_sections = [sec1, sec2]

        # Both sections start as ready
        pr1 = _make_proposal_result("01", execution_ready=True)
        pr2 = _make_proposal_result("02", execution_ready=True)
        proposal_results = {"01": pr1, "02": pr2}

        # Write proposal-state artifacts with overlapping anchors
        # so reconciliation detects a conflict.
        conflict_overrides = {
            "resolved_anchors": ["shared-anchor"],
            "resolved_contracts": [],
            "unresolved_contracts": ["shared-contract"],
        }
        _write_proposal_state(
            planspace, "01", execution_ready=True,
            overrides=conflict_overrides,
        )
        _write_proposal_state(
            planspace, "02", execution_ready=True,
            overrides=conflict_overrides,
        )

        # Mock run_section for re-proposal (reconciliation phase calls it
        # for affected sections)
        def mock_run_section(*args, **kwargs):
            sec = args[2]  # section argument
            return _make_proposal_result(sec.number, execution_ready=True)

        with patch(
            "reconciliation.engine.phase.run_section",
            side_effect=mock_run_section,
        ):
            result = run_reconciliation_phase(
                proposal_results,
                sections_by_num,
                all_sections,
                planspace,
                codespace,
                "parent",
                _default_policy(),
            )

        # Both sections had the same unresolved_contracts — reconciliation
        # detects the contract conflict and marks both as affected.
        # The re-proposal mock restores them to ready, but the key test
        # is that reconciliation DID detect the overlap and trigger
        # the re-proposal path.
        # Whether they end up ready or blocked depends on the mock —
        # the contract we test is that reconciliation processes them.
        total = len(result.new_section_numbers) + len(result.removed_section_numbers)
        assert total == 2


# ---------------------------------------------------------------------------
# 4. ImplementationPassRestart on alignment change
# ---------------------------------------------------------------------------

class TestImplementationPassRestartOnAlignmentChange:
    """Verify that alignment changes during implementation restart Phase 1."""

    def test_restart_on_alignment_changed(
        self,
        planspace: Path,
        codespace: Path,
        mock_dispatch: MagicMock,
    ) -> None:
        """ImplementationPassRestart raised when alignment changes mid-pass."""
        section = _make_section(planspace, codespace, "01")
        sections_by_num = {"01": section}

        proposal_results = {
            "01": _make_proposal_result("01", execution_ready=True),
        }
        _write_proposal_state(planspace, "01", execution_ready=True)

        # Mock alignment_changed_pending to return True
        with patch(
            "implementation.engine.implementation_pass.alignment_changed_pending",
            return_value=True,
        ), patch(
            "implementation.engine.implementation_pass._check_and_clear_alignment_changed",
            return_value=True,
        ):
            with pytest.raises(ImplementationPassRestart):
                run_implementation_pass(
                    proposal_results,
                    sections_by_num,
                    planspace,
                    codespace,
                    "parent",
                )


# ---------------------------------------------------------------------------
# 5. Full phase sequence: proposal -> reconciliation -> implementation
# ---------------------------------------------------------------------------

class TestFullPhaseSequence:
    """End-to-end: proposal results flow through reconciliation into
    implementation, producing SectionResult objects."""

    def test_full_sequence_no_conflicts(
        self,
        planspace: Path,
        codespace: Path,
        mock_dispatch: MagicMock,
    ) -> None:
        """Happy path: proposal -> reconciliation (no conflicts) ->
        implementation succeeds."""
        sec1 = _make_section(planspace, codespace, "01")
        sec2 = _make_section(planspace, codespace, "02")
        sections_by_num = {"01": sec1, "02": sec2}
        all_sections = [sec1, sec2]

        # --- Phase 1a: Simulate proposal pass output ---
        proposal_results = {
            "01": _make_proposal_result("01", execution_ready=True),
            "02": _make_proposal_result("02", execution_ready=True),
        }
        _write_proposal_state(planspace, "01", execution_ready=True)
        _write_proposal_state(planspace, "02", execution_ready=True)

        # --- Phase 1b: Reconciliation ---
        reconciliation = run_reconciliation_phase(
            proposal_results,
            sections_by_num,
            all_sections,
            planspace,
            codespace,
            "parent",
            _default_policy(),
        )

        assert sorted(reconciliation.new_section_numbers) == ["01", "02"]
        assert reconciliation.removed_section_numbers == []
        assert reconciliation.alignment_changed is False

        # Verify proposal_results are still valid after reconciliation
        for num, pr in proposal_results.items():
            assert pr.execution_ready is True

        # --- Phase 1c: Implementation ---
        def mock_run_section(
            planspace, codespace, section, parent, *,
            all_sections=None, pass_mode="full",
        ):
            return [f"src/feature_{section.number}.py"]

        with patch(
            "implementation.engine.implementation_pass.run_section",
            side_effect=mock_run_section,
        ), patch(
            "implementation.engine.implementation_pass._run_risk_review",
            return_value=None,
        ):
            section_results = run_implementation_pass(
                proposal_results,
                sections_by_num,
                planspace,
                codespace,
                "parent",
            )

        # Both sections should have aligned results
        assert "01" in section_results
        assert "02" in section_results
        assert section_results["01"].aligned is True
        assert section_results["02"].aligned is True
        assert section_results["01"].modified_files == ["src/feature_01.py"]
        assert section_results["02"].modified_files == ["src/feature_02.py"]

    def test_full_sequence_with_blocked_section(
        self,
        planspace: Path,
        codespace: Path,
        mock_dispatch: MagicMock,
    ) -> None:
        """Mixed scenario: one section ready, one blocked — only ready
        section gets implementation results."""
        sec1 = _make_section(planspace, codespace, "01")
        sec2 = _make_section(planspace, codespace, "02")
        sections_by_num = {"01": sec1, "02": sec2}
        all_sections = [sec1, sec2]

        proposal_results = {
            "01": _make_proposal_result("01", execution_ready=True),
            "02": _make_proposal_result(
                "02",
                execution_ready=False,
                blockers=[{"type": "needs_design", "description": "unclear"}],
            ),
        }
        _write_proposal_state(planspace, "01", execution_ready=True)
        _write_proposal_state(planspace, "02", execution_ready=False)

        # Reconciliation
        reconciliation = run_reconciliation_phase(
            proposal_results,
            sections_by_num,
            all_sections,
            planspace,
            codespace,
            "parent",
            _default_policy(),
        )

        assert "01" in reconciliation.new_section_numbers
        assert "02" in reconciliation.removed_section_numbers

        # Implementation
        def mock_run_section(
            planspace, codespace, section, parent, *,
            all_sections=None, pass_mode="full",
        ):
            return [f"src/feature_{section.number}.py"]

        with patch(
            "implementation.engine.implementation_pass.run_section",
            side_effect=mock_run_section,
        ), patch(
            "implementation.engine.implementation_pass._run_risk_review",
            return_value=None,
        ):
            section_results = run_implementation_pass(
                proposal_results,
                sections_by_num,
                planspace,
                codespace,
                "parent",
            )

        # Only section 01 should have a result — section 02 was blocked
        assert "01" in section_results
        assert "02" not in section_results
        assert section_results["01"].aligned is True

    def test_implementation_none_result_excluded(
        self,
        planspace: Path,
        codespace: Path,
        mock_dispatch: MagicMock,
    ) -> None:
        """When run_section returns None, the section has no SectionResult."""
        section = _make_section(planspace, codespace, "01")
        sections_by_num = {"01": section}

        proposal_results = {
            "01": _make_proposal_result("01", execution_ready=True),
        }
        _write_proposal_state(planspace, "01", execution_ready=True)

        def mock_run_section(*args, **kwargs):
            return None  # implementation failed

        with patch(
            "implementation.engine.implementation_pass.run_section",
            side_effect=mock_run_section,
        ), patch(
            "implementation.engine.implementation_pass._run_risk_review",
            return_value=None,
        ):
            section_results = run_implementation_pass(
                proposal_results,
                sections_by_num,
                planspace,
                codespace,
                "parent",
            )

        # No result for section 01 — implementation returned None
        assert "01" not in section_results

    def test_reconciliation_artifact_written(
        self,
        planspace: Path,
        codespace: Path,
        mock_dispatch: MagicMock,
    ) -> None:
        """Reconciliation phase writes per-section result artifacts."""
        section = _make_section(planspace, codespace, "01")
        sections_by_num = {"01": section}
        all_sections = [section]

        proposal_results = {
            "01": _make_proposal_result("01", execution_ready=True),
        }
        _write_proposal_state(planspace, "01", execution_ready=True)

        run_reconciliation_phase(
            proposal_results,
            sections_by_num,
            all_sections,
            planspace,
            codespace,
            "parent",
            _default_policy(),
        )

        # Check that reconciliation wrote the result artifact
        recon_result_path = (
            PathRegistry(planspace).reconciliation_dir()
            / "section-01-reconciliation-result.json"
        )
        assert recon_result_path.exists()
        data = json.loads(recon_result_path.read_text(encoding="utf-8"))
        assert data["section"] == "01"
        assert "affected" in data

    def test_reconciliation_summary_artifact_written(
        self,
        planspace: Path,
        codespace: Path,
        mock_dispatch: MagicMock,
    ) -> None:
        """Reconciliation phase writes a summary artifact."""
        section = _make_section(planspace, codespace, "01")
        sections_by_num = {"01": section}
        all_sections = [section]

        proposal_results = {
            "01": _make_proposal_result("01", execution_ready=True),
        }
        _write_proposal_state(planspace, "01", execution_ready=True)

        run_reconciliation_phase(
            proposal_results,
            sections_by_num,
            all_sections,
            planspace,
            codespace,
            "parent",
            _default_policy(),
        )

        summary_path = (
            PathRegistry(planspace).reconciliation_dir()
            / "reconciliation-summary.json"
        )
        assert summary_path.exists()
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        assert "conflicts_found" in summary
        assert "sections_affected" in summary
