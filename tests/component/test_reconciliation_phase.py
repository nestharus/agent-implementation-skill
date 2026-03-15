from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from dependency_injector import providers

from conftest import CapturingCommunicator, CapturingPipelineControl, NoOpChangeTracker
from containers import ArtifactIOService, ChangeTrackerService, Services
from reconciliation.engine.reconciliation_phase import (
    ReconciliationPhase,
    ReconciliationPhaseExit,
)
from orchestrator.types import ProposalPassResult, Section


class _AlignmentChangedTracker(ChangeTrackerService):
    """Change tracker whose alignment checker always returns True."""

    def set_flag(self, planspace) -> None:
        pass

    def make_alignment_checker(self):
        return lambda _planspace: True

    def invalidate_excerpts(self, planspace) -> None:
        pass


def _make_section(planspace: Path, number: str) -> Section:
    section_path = planspace / "artifacts" / "sections" / f"section-{number}.md"
    section_path.write_text(f"# Section {number}\n", encoding="utf-8")
    return Section(number=number, path=section_path)


def _make_phase(cross_section_reconciler=None) -> ReconciliationPhase:
    """Construct a ReconciliationPhase, optionally with a mock reconciler."""
    if cross_section_reconciler is None:
        cross_section_reconciler = MagicMock()
    with patch("orchestrator.engine.section_pipeline.SectionPipeline"):
        return ReconciliationPhase(
            logger=Services.logger(),
            artifact_io=ArtifactIOService(),
            pipeline_control=Services.pipeline_control(),
            change_tracker=Services.change_tracker(),
            cross_section_reconciler=cross_section_reconciler,
        )


def test_run_reconciliation_phase_reproposes_blocked_sections(
    planspace: Path,
    codespace: Path,
    monkeypatch: pytest.MonkeyPatch,
    noop_pipeline_control,
    noop_change_tracker,
    capturing_communicator,
) -> None:
    section = _make_section(planspace, "01")
    other = _make_section(planspace, "02")
    proposal_results = {
        "01": ProposalPassResult(section_number="01", execution_ready=True),
        "02": ProposalPassResult(section_number="02", execution_ready=False),
    }

    mock_reconciler = MagicMock()
    mock_reconciler.run_reconciliation_loop.return_value = {
        "conflicts_found": 1,
        "new_sections_proposed": 0,
        "substrate_needed": False,
        "sections_affected": ["01"],
    }

    phase = _make_phase(mock_reconciler)
    phase._section_pipeline = MagicMock()
    phase._section_pipeline.run_section.return_value = ProposalPassResult(
        section_number="01",
        execution_ready=True,
    )

    result = phase.run_reconciliation_phase(
        proposal_results,
        {"01": section, "02": other},
        [section, other],
        planspace,
        codespace,
    )

    assert result.new_section_numbers == ["01"]
    assert result.removed_section_numbers == ["02"]
    assert result.alignment_changed is False
    assert capturing_communicator.messages == ["reproposal-done:01:ready"]


def test_run_reconciliation_phase_restarts_on_alignment_change(
    planspace: Path,
    codespace: Path,
    monkeypatch: pytest.MonkeyPatch,
    capturing_pipeline_control,
    noop_communicator,
) -> None:
    section = _make_section(planspace, "01")
    proposal_results = {
        "01": ProposalPassResult(section_number="01", execution_ready=True),
    }

    capturing_pipeline_control._alignment_changed_return = True

    # Provide a change tracker whose alignment checker returns True
    ct = _AlignmentChangedTracker()
    Services.change_tracker.override(providers.Object(ct))

    mock_reconciler = MagicMock()
    mock_reconciler.run_reconciliation_loop.return_value = {
        "conflicts_found": 1,
        "new_sections_proposed": 0,
        "substrate_needed": False,
        "sections_affected": ["01"],
    }

    try:
        result = _make_phase(mock_reconciler).run_reconciliation_phase(
            proposal_results,
            {"01": section},
            [section],
            planspace,
            codespace,
        )
    finally:
        Services.change_tracker.reset_override()

    assert result.new_section_numbers == []
    assert result.removed_section_numbers == ["01"]
    assert result.alignment_changed is True


def test_run_reconciliation_phase_exits_when_parent_aborts(
    planspace: Path,
    codespace: Path,
    monkeypatch: pytest.MonkeyPatch,
    capturing_pipeline_control,
    noop_change_tracker,
    capturing_communicator,
) -> None:
    section = _make_section(planspace, "01")
    proposal_results = {
        "01": ProposalPassResult(section_number="01", execution_ready=True),
    }

    capturing_pipeline_control._pending_return = True

    mock_reconciler = MagicMock()
    mock_reconciler.run_reconciliation_loop.return_value = {
        "conflicts_found": 1,
        "new_sections_proposed": 0,
        "substrate_needed": False,
        "sections_affected": ["01"],
    }

    with pytest.raises(ReconciliationPhaseExit):
        _make_phase(mock_reconciler).run_reconciliation_phase(
            proposal_results,
            {"01": section},
            [section],
            planspace,
            codespace,
        )

    assert capturing_communicator.messages == ["fail:aborted"]
