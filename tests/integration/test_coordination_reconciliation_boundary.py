"""Integration tests for the Coordination -> Reconciliation boundary.

Tests the artifact handoff when coordination detects outstanding problems
(scope deltas, consequence notes) and the reconciliation system detects
cross-section conflicts and identifies affected sections.

Chain under test:
  proposal-state artifacts (seeded) -> CrossSectionReconciler ->
  detectors (anchor overlaps, contract conflicts, new-section candidates,
  shared seams) -> per-section result artifacts + scope-delta artifacts +
  substrate-trigger artifacts -> Results.was_section_affected

Mock boundary: ``dispatch_agent`` (the LLM call) is mocked via
``mock_dispatch``.  Everything else — file I/O, detectors,
PathRegistry — runs for real.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from containers import Services
from orchestrator.path_registry import PathRegistry
from orchestrator.types import ProposalPassResult, Section
from proposal.repository.state import ProposalState
from reconciliation.engine.cross_section_reconciler import CrossSectionReconciler
from reconciliation.repository.queue import Queue
from reconciliation.repository.results import Results
from reconciliation.service.adjudicator import Adjudicator
from signals.repository.artifact_io import write_json


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_reconciler() -> CrossSectionReconciler:
    """Build a CrossSectionReconciler with real services from the container."""
    return CrossSectionReconciler(
        artifact_io=Services.artifact_io(),
        results=Results(
            artifact_io=Services.artifact_io(),
            hasher=Services.hasher(),
        ),
        queue=Queue(artifact_io=Services.artifact_io()),
        adjudicator=Adjudicator(
            artifact_io=Services.artifact_io(),
            prompt_guard=Services.prompt_guard(),
            policies=Services.policies(),
            dispatcher=Services.dispatcher(),
            task_router=Services.task_router(),
        ),
    )


def _make_results_repo() -> Results:
    """Build a Results repository with real services."""
    return Results(
        artifact_io=Services.artifact_io(),
        hasher=Services.hasher(),
    )


def _make_queue() -> Queue:
    """Build a reconciliation Queue with real services."""
    return Queue(artifact_io=Services.artifact_io())


def _write_proposal_state(
    planspace: Path,
    section_number: str,
    *,
    overrides: dict | None = None,
) -> Path:
    """Write a full proposal-state artifact.

    Each section gets unique anchor/contract names by default to avoid
    false conflicts unless ``overrides`` explicitly sets them.
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
        "execution_ready": True,
        "readiness_rationale": "ready",
        "problem_ids": [],
        "pattern_ids": [],
        "profile_id": "",
        "pattern_deviations": [],
        "governance_questions": [],
    }
    if overrides:
        state.update(overrides)
    state_path = paths.proposal_state(section_number)
    write_json(state_path, state)
    return state_path


def _make_proposal_result(section_number: str) -> ProposalPassResult:
    """Build a minimal ProposalPassResult (execution_ready=True)."""
    return ProposalPassResult(
        section_number=section_number,
        proposal_aligned=True,
        execution_ready=True,
        blockers=[],
        needs_reconciliation=False,
        proposal_state_path="",
    )


# ---------------------------------------------------------------------------
# 1. Anchor overlap detection across proposal-state artifacts
# ---------------------------------------------------------------------------

class TestAnchorOverlapDetection:
    """When two sections claim the same anchor, reconciliation marks both
    as affected and writes per-section result artifacts."""

    def test_shared_anchor_marks_both_sections_affected(
        self,
        planspace: Path,
        codespace: Path,
        mock_dispatch: MagicMock,
    ) -> None:
        """Overlapping resolved anchors produce anchor_overlap findings."""
        _write_proposal_state(planspace, "01", overrides={
            "resolved_anchors": ["shared-anchor", "unique-01"],
        })
        _write_proposal_state(planspace, "02", overrides={
            "resolved_anchors": ["shared-anchor", "unique-02"],
        })

        reconciler = _make_reconciler()
        summary = reconciler.run_reconciliation_loop(
            planspace,
            [_make_proposal_result("01"), _make_proposal_result("02")],
        )

        assert summary["anchor_overlaps"] >= 1
        assert "01" in summary["sections_affected"]
        assert "02" in summary["sections_affected"]

    def test_anchor_overlap_result_artifacts_written(
        self,
        planspace: Path,
        codespace: Path,
        mock_dispatch: MagicMock,
    ) -> None:
        """Per-section reconciliation-result files are written to disk."""
        _write_proposal_state(planspace, "01", overrides={
            "resolved_anchors": ["overlapping-anchor"],
        })
        _write_proposal_state(planspace, "02", overrides={
            "resolved_anchors": ["overlapping-anchor"],
        })

        reconciler = _make_reconciler()
        reconciler.run_reconciliation_loop(
            planspace,
            [_make_proposal_result("01"), _make_proposal_result("02")],
        )

        results_repo = _make_results_repo()
        for sec in ("01", "02"):
            result = results_repo.load_result(planspace, sec)
            assert result is not None, f"Missing reconciliation result for section {sec}"
            assert result["section"] == sec
            assert result["affected"] is True
            assert len(result["anchor_overlaps"]) >= 1

    def test_unresolved_anchor_overlap_also_detected(
        self,
        planspace: Path,
        codespace: Path,
        mock_dispatch: MagicMock,
    ) -> None:
        """Unresolved anchors shared across sections also trigger detection."""
        _write_proposal_state(planspace, "01", overrides={
            "resolved_anchors": [],
            "unresolved_anchors": ["pending-anchor"],
        })
        _write_proposal_state(planspace, "02", overrides={
            "resolved_anchors": [],
            "unresolved_anchors": ["pending-anchor"],
        })

        reconciler = _make_reconciler()
        summary = reconciler.run_reconciliation_loop(
            planspace,
            [_make_proposal_result("01"), _make_proposal_result("02")],
        )

        assert summary["anchor_overlaps"] >= 1
        assert set(summary["sections_affected"]) >= {"01", "02"}


# ---------------------------------------------------------------------------
# 2. Contract conflict detection
# ---------------------------------------------------------------------------

class TestContractConflictDetection:
    """When one section resolves a contract and another leaves it unresolved,
    reconciliation detects a contract conflict."""

    def test_mixed_resolved_unresolved_contract_conflict(
        self,
        planspace: Path,
        codespace: Path,
        mock_dispatch: MagicMock,
    ) -> None:
        """Resolved-in-one + unresolved-in-another triggers conflict."""
        _write_proposal_state(planspace, "01", overrides={
            "resolved_contracts": ["auth-api"],
            "unresolved_contracts": [],
        })
        _write_proposal_state(planspace, "02", overrides={
            "resolved_contracts": [],
            "unresolved_contracts": ["auth-api"],
        })

        reconciler = _make_reconciler()
        summary = reconciler.run_reconciliation_loop(
            planspace,
            [_make_proposal_result("01"), _make_proposal_result("02")],
        )

        assert summary["contract_conflicts"] >= 1
        assert summary["conflicts_found"] >= 1
        assert "01" in summary["sections_affected"]
        assert "02" in summary["sections_affected"]

    def test_multiple_unresolved_same_contract(
        self,
        planspace: Path,
        codespace: Path,
        mock_dispatch: MagicMock,
    ) -> None:
        """Two sections both leaving the same contract unresolved is a conflict."""
        _write_proposal_state(planspace, "01", overrides={
            "resolved_contracts": [],
            "unresolved_contracts": ["database-schema"],
        })
        _write_proposal_state(planspace, "02", overrides={
            "resolved_contracts": [],
            "unresolved_contracts": ["database-schema"],
        })

        reconciler = _make_reconciler()
        summary = reconciler.run_reconciliation_loop(
            planspace,
            [_make_proposal_result("01"), _make_proposal_result("02")],
        )

        assert summary["contract_conflicts"] >= 1

        results_repo = _make_results_repo()
        for sec in ("01", "02"):
            result = results_repo.load_result(planspace, sec)
            assert result is not None
            assert result["affected"] is True
            assert len(result["contract_conflicts"]) >= 1

    def test_contract_conflict_result_contains_details(
        self,
        planspace: Path,
        codespace: Path,
        mock_dispatch: MagicMock,
    ) -> None:
        """Reconciliation result artifact carries conflict metadata."""
        _write_proposal_state(planspace, "01", overrides={
            "resolved_contracts": ["payment-api"],
            "unresolved_contracts": [],
        })
        _write_proposal_state(planspace, "02", overrides={
            "resolved_contracts": [],
            "unresolved_contracts": ["payment-api"],
        })

        reconciler = _make_reconciler()
        reconciler.run_reconciliation_loop(
            planspace,
            [_make_proposal_result("01"), _make_proposal_result("02")],
        )

        results_repo = _make_results_repo()
        result_01 = results_repo.load_result(planspace, "01")
        assert result_01 is not None
        conflict = result_01["contract_conflicts"][0]
        assert conflict["contract"] == "payment-api"
        assert "01" in conflict["sections"]
        assert "02" in conflict["sections"]


# ---------------------------------------------------------------------------
# 3. Reconciliation request queue merges into proposal states
# ---------------------------------------------------------------------------

class TestReconciliationRequestQueueMerge:
    """When coordination queues reconciliation requests (extra unresolved
    contracts/anchors), the reconciler merges them into loaded states
    before running detection."""

    def test_queued_contracts_surface_as_conflicts(
        self,
        planspace: Path,
        codespace: Path,
        mock_dispatch: MagicMock,
    ) -> None:
        """A queued reconciliation request injects extra unresolved contracts."""
        # Section 01 has "data-layer" resolved normally
        _write_proposal_state(planspace, "01", overrides={
            "resolved_contracts": ["data-layer"],
        })
        # Section 02 has no knowledge of "data-layer" in proposal state
        _write_proposal_state(planspace, "02")

        # Queue a reconciliation request adding "data-layer" as unresolved for 02
        queue = _make_queue()
        queue.queue_reconciliation_request(
            planspace,
            section_number="02",
            unresolved_contracts=["data-layer"],
            unresolved_anchors=[],
        )

        reconciler = _make_reconciler()
        summary = reconciler.run_reconciliation_loop(
            planspace,
            [_make_proposal_result("01"), _make_proposal_result("02")],
        )

        # The merge injects "data-layer" as unresolved in 02.
        # Detector sees 01 resolved + 02 unresolved -> conflict
        assert summary["contract_conflicts"] >= 1
        assert summary["conflicts_found"] >= 1

    def test_queued_anchors_surface_as_overlaps(
        self,
        planspace: Path,
        codespace: Path,
        mock_dispatch: MagicMock,
    ) -> None:
        """A queued reconciliation request injects extra unresolved anchors."""
        _write_proposal_state(planspace, "01", overrides={
            "resolved_anchors": ["config-anchor"],
        })
        _write_proposal_state(planspace, "02")

        queue = _make_queue()
        queue.queue_reconciliation_request(
            planspace,
            section_number="02",
            unresolved_contracts=[],
            unresolved_anchors=["config-anchor"],
        )

        reconciler = _make_reconciler()
        summary = reconciler.run_reconciliation_loop(
            planspace,
            [_make_proposal_result("01"), _make_proposal_result("02")],
        )

        assert summary["anchor_overlaps"] >= 1
        assert "01" in summary["sections_affected"]
        assert "02" in summary["sections_affected"]


# ---------------------------------------------------------------------------
# 4. New-section candidate consolidation
# ---------------------------------------------------------------------------

class TestNewSectionCandidateConsolidation:
    """When multiple sections propose the same new-section candidate,
    reconciliation consolidates them and writes scope-delta artifacts."""

    def test_exact_match_candidates_consolidated(
        self,
        planspace: Path,
        codespace: Path,
        mock_dispatch: MagicMock,
    ) -> None:
        """Two sections proposing the same title produce a consolidated entry."""
        _write_proposal_state(planspace, "01", overrides={
            "new_section_candidates": [{"title": "logging infrastructure"}],
        })
        _write_proposal_state(planspace, "02", overrides={
            "new_section_candidates": [{"title": "logging infrastructure"}],
        })

        reconciler = _make_reconciler()
        summary = reconciler.run_reconciliation_loop(
            planspace,
            [_make_proposal_result("01"), _make_proposal_result("02")],
        )

        assert summary["new_sections_proposed"] >= 1
        assert "01" in summary["sections_affected"]
        assert "02" in summary["sections_affected"]

    def test_consolidated_candidate_writes_scope_delta(
        self,
        planspace: Path,
        codespace: Path,
        mock_dispatch: MagicMock,
    ) -> None:
        """Consolidated new-section candidates produce scope-delta artifacts."""
        _write_proposal_state(planspace, "01", overrides={
            "new_section_candidates": [{"title": "caching layer"}],
        })
        _write_proposal_state(planspace, "02", overrides={
            "new_section_candidates": [{"title": "caching layer"}],
        })

        reconciler = _make_reconciler()
        reconciler.run_reconciliation_loop(
            planspace,
            [_make_proposal_result("01"), _make_proposal_result("02")],
        )

        # Verify a scope-delta artifact was written
        scope_deltas_dir = PathRegistry(planspace).scope_deltas_dir()
        delta_files = list(scope_deltas_dir.glob("reconciliation-*.json"))
        assert len(delta_files) >= 1

        delta = json.loads(delta_files[0].read_text(encoding="utf-8"))
        assert delta["source"] == "reconciliation"
        assert "01" in delta["source_sections"]
        assert "02" in delta["source_sections"]

    def test_non_overlapping_candidates_not_consolidated(
        self,
        planspace: Path,
        codespace: Path,
        mock_dispatch: MagicMock,
    ) -> None:
        """Different new-section titles are not auto-consolidated."""
        _write_proposal_state(planspace, "01", overrides={
            "new_section_candidates": [{"title": "monitoring"}],
        })
        _write_proposal_state(planspace, "02", overrides={
            "new_section_candidates": [{"title": "telemetry"}],
        })

        reconciler = _make_reconciler()
        summary = reconciler.run_reconciliation_loop(
            planspace,
            [_make_proposal_result("01"), _make_proposal_result("02")],
        )

        # No exact-match consolidation (adjudicator mock returns empty)
        assert summary["new_sections_proposed"] == 0


# ---------------------------------------------------------------------------
# 5. Shared seam detection and substrate triggers
# ---------------------------------------------------------------------------

class TestSharedSeamDetection:
    """When multiple sections declare the same shared seam candidate,
    reconciliation aggregates them and writes substrate-trigger artifacts."""

    def test_shared_seam_across_sections_triggers_substrate(
        self,
        planspace: Path,
        codespace: Path,
        mock_dispatch: MagicMock,
    ) -> None:
        """Two sections sharing a seam candidate produces substrate_needed."""
        _write_proposal_state(planspace, "01", overrides={
            "shared_seam_candidates": ["event-bus"],
        })
        _write_proposal_state(planspace, "02", overrides={
            "shared_seam_candidates": ["event-bus"],
        })

        reconciler = _make_reconciler()
        summary = reconciler.run_reconciliation_loop(
            planspace,
            [_make_proposal_result("01"), _make_proposal_result("02")],
        )

        assert summary["shared_seams"] >= 1
        assert summary["substrate_seams"] >= 1
        assert summary["substrate_needed"] is True

    def test_substrate_trigger_artifact_written(
        self,
        planspace: Path,
        codespace: Path,
        mock_dispatch: MagicMock,
    ) -> None:
        """Shared seams produce a substrate-trigger signal file."""
        _write_proposal_state(planspace, "01", overrides={
            "shared_seam_candidates": ["message-queue"],
        })
        _write_proposal_state(planspace, "02", overrides={
            "shared_seam_candidates": ["message-queue"],
        })

        reconciler = _make_reconciler()
        reconciler.run_reconciliation_loop(
            planspace,
            [_make_proposal_result("01"), _make_proposal_result("02")],
        )

        signals_dir = PathRegistry(planspace).signals_dir()
        trigger_files = list(signals_dir.glob("substrate-trigger-reconciliation-*.json"))
        assert len(trigger_files) >= 1

        trigger = json.loads(trigger_files[0].read_text(encoding="utf-8"))
        assert trigger["source"] == "reconciliation"
        assert trigger["trigger_type"] == "shared_seam_reconciliation"
        assert "01" in trigger["sections"]
        assert "02" in trigger["sections"]

    def test_single_section_seam_no_substrate(
        self,
        planspace: Path,
        codespace: Path,
        mock_dispatch: MagicMock,
    ) -> None:
        """A seam claimed by only one section does not trigger substrate."""
        _write_proposal_state(planspace, "01", overrides={
            "shared_seam_candidates": ["solo-seam"],
        })
        _write_proposal_state(planspace, "02")

        reconciler = _make_reconciler()
        summary = reconciler.run_reconciliation_loop(
            planspace,
            [_make_proposal_result("01"), _make_proposal_result("02")],
        )

        assert summary["substrate_needed"] is False
        assert summary["substrate_seams"] == 0


# ---------------------------------------------------------------------------
# 6. Results.was_section_affected round-trip
# ---------------------------------------------------------------------------

class TestResultsWasSectionAffected:
    """Verify the downstream consumption path: after reconciliation runs,
    Results.was_section_affected returns the correct boolean."""

    def test_affected_section_returns_true(
        self,
        planspace: Path,
        codespace: Path,
        mock_dispatch: MagicMock,
    ) -> None:
        _write_proposal_state(planspace, "01", overrides={
            "resolved_anchors": ["shared"],
        })
        _write_proposal_state(planspace, "02", overrides={
            "resolved_anchors": ["shared"],
        })

        reconciler = _make_reconciler()
        reconciler.run_reconciliation_loop(
            planspace,
            [_make_proposal_result("01"), _make_proposal_result("02")],
        )

        results_repo = _make_results_repo()
        assert results_repo.was_section_affected(planspace, "01") is True
        assert results_repo.was_section_affected(planspace, "02") is True

    def test_unaffected_section_returns_false(
        self,
        planspace: Path,
        codespace: Path,
        mock_dispatch: MagicMock,
    ) -> None:
        _write_proposal_state(planspace, "01")
        _write_proposal_state(planspace, "02")

        reconciler = _make_reconciler()
        reconciler.run_reconciliation_loop(
            planspace,
            [_make_proposal_result("01"), _make_proposal_result("02")],
        )

        results_repo = _make_results_repo()
        assert results_repo.was_section_affected(planspace, "01") is False
        assert results_repo.was_section_affected(planspace, "02") is False

    def test_missing_result_returns_false(
        self,
        planspace: Path,
        codespace: Path,
        mock_dispatch: MagicMock,
    ) -> None:
        """Before reconciliation runs, no result file -> False."""
        results_repo = _make_results_repo()
        assert results_repo.was_section_affected(planspace, "99") is False


# ---------------------------------------------------------------------------
# 7. Reconciliation summary artifact integrity
# ---------------------------------------------------------------------------

class TestReconciliationSummaryArtifact:
    """The summary artifact captures aggregate counts and section lists."""

    def test_summary_artifact_all_fields_present(
        self,
        planspace: Path,
        codespace: Path,
        mock_dispatch: MagicMock,
    ) -> None:
        _write_proposal_state(planspace, "01", overrides={
            "resolved_anchors": ["overlap-a"],
            "unresolved_contracts": ["conflict-c"],
        })
        _write_proposal_state(planspace, "02", overrides={
            "resolved_anchors": ["overlap-a"],
            "unresolved_contracts": ["conflict-c"],
        })

        reconciler = _make_reconciler()
        summary = reconciler.run_reconciliation_loop(
            planspace,
            [_make_proposal_result("01"), _make_proposal_result("02")],
        )

        # Verify return value
        assert "sections_affected" in summary
        assert "new_sections_proposed" in summary
        assert "substrate_needed" in summary
        assert "conflicts_found" in summary
        assert "anchor_overlaps" in summary
        assert "contract_conflicts" in summary
        assert "shared_seams" in summary
        assert "substrate_seams" in summary

        # Verify on-disk artifact matches return value
        summary_path = PathRegistry(planspace).reconciliation_summary()
        assert summary_path.exists()
        disk_summary = json.loads(summary_path.read_text(encoding="utf-8"))
        assert disk_summary == summary

    def test_summary_with_three_sections_partial_overlap(
        self,
        planspace: Path,
        codespace: Path,
        mock_dispatch: MagicMock,
    ) -> None:
        """Only sections involved in an overlap are listed as affected."""
        _write_proposal_state(planspace, "01", overrides={
            "resolved_anchors": ["anchor-x"],
        })
        _write_proposal_state(planspace, "02", overrides={
            "resolved_anchors": ["anchor-x"],
        })
        # Section 03 has no overlap with the others
        _write_proposal_state(planspace, "03")

        reconciler = _make_reconciler()
        summary = reconciler.run_reconciliation_loop(
            planspace,
            [
                _make_proposal_result("01"),
                _make_proposal_result("02"),
                _make_proposal_result("03"),
            ],
        )

        assert "01" in summary["sections_affected"]
        assert "02" in summary["sections_affected"]
        assert "03" not in summary["sections_affected"]

        # Section 03 result exists but is not affected
        results_repo = _make_results_repo()
        result_03 = results_repo.load_result(planspace, "03")
        assert result_03 is not None
        assert result_03["affected"] is False


# ---------------------------------------------------------------------------
# 8. Combined multi-signal scenario
# ---------------------------------------------------------------------------

class TestCombinedMultiSignalScenario:
    """Exercise multiple conflict types simultaneously to ensure independent
    detection and combined affected-section tracking."""

    def test_anchor_and_contract_conflicts_combine(
        self,
        planspace: Path,
        codespace: Path,
        mock_dispatch: MagicMock,
    ) -> None:
        """Anchor overlap + contract conflict across different section pairs."""
        _write_proposal_state(planspace, "01", overrides={
            "resolved_anchors": ["shared-anchor"],
            "resolved_contracts": ["api-v2"],
        })
        _write_proposal_state(planspace, "02", overrides={
            "resolved_anchors": ["shared-anchor"],
            "unresolved_contracts": ["api-v2"],
        })
        _write_proposal_state(planspace, "03")

        reconciler = _make_reconciler()
        summary = reconciler.run_reconciliation_loop(
            planspace,
            [
                _make_proposal_result("01"),
                _make_proposal_result("02"),
                _make_proposal_result("03"),
            ],
        )

        assert summary["anchor_overlaps"] >= 1
        assert summary["contract_conflicts"] >= 1
        # conflicts_found = anchor_overlaps + contract_conflicts
        assert summary["conflicts_found"] >= 2
        assert "01" in summary["sections_affected"]
        assert "02" in summary["sections_affected"]
        assert "03" not in summary["sections_affected"]

    def test_all_conflict_types_with_seams(
        self,
        planspace: Path,
        codespace: Path,
        mock_dispatch: MagicMock,
    ) -> None:
        """Anchors, contracts, new-section candidates, and seams combined."""
        _write_proposal_state(planspace, "01", overrides={
            "resolved_anchors": ["overlap-anchor"],
            "unresolved_contracts": ["shared-contract"],
            "new_section_candidates": [{"title": "shared feature"}],
            "shared_seam_candidates": ["event-bus"],
        })
        _write_proposal_state(planspace, "02", overrides={
            "resolved_anchors": ["overlap-anchor"],
            "unresolved_contracts": ["shared-contract"],
            "new_section_candidates": [{"title": "shared feature"}],
            "shared_seam_candidates": ["event-bus"],
        })

        reconciler = _make_reconciler()
        summary = reconciler.run_reconciliation_loop(
            planspace,
            [_make_proposal_result("01"), _make_proposal_result("02")],
        )

        assert summary["anchor_overlaps"] >= 1
        assert summary["contract_conflicts"] >= 1
        assert summary["new_sections_proposed"] >= 1
        assert summary["shared_seams"] >= 1
        assert summary["substrate_seams"] >= 1
        assert summary["substrate_needed"] is True

        # Verify all artifact types written
        paths = PathRegistry(planspace)
        scope_deltas = list(paths.scope_deltas_dir().glob("reconciliation-*.json"))
        assert len(scope_deltas) >= 1
        triggers = list(paths.signals_dir().glob("substrate-trigger-reconciliation-*.json"))
        assert len(triggers) >= 1
        assert paths.reconciliation_summary().exists()

        # Both sections affected
        results_repo = _make_results_repo()
        assert results_repo.was_section_affected(planspace, "01") is True
        assert results_repo.was_section_affected(planspace, "02") is True
