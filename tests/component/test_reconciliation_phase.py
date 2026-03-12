from __future__ import annotations

from pathlib import Path

import pytest
from dependency_injector import providers

from conftest import CapturingCommunicator, CapturingPipelineControl
from containers import Services
from src.reconciliation.engine import phase
from src.reconciliation.engine.phase import (
    ReconciliationPhaseExit,
    run_reconciliation_phase,
)
from orchestrator.types import ProposalPassResult, Section


def _make_section(planspace: Path, number: str) -> Section:
    section_path = planspace / "artifacts" / "sections" / f"section-{number}.md"
    section_path.write_text(f"# Section {number}\n", encoding="utf-8")
    return Section(number=number, path=section_path)


def test_run_reconciliation_phase_reproposes_blocked_sections(
    planspace: Path,
    codespace: Path,
    monkeypatch: pytest.MonkeyPatch,
    noop_pipeline_control,
    capturing_communicator,
) -> None:
    section = _make_section(planspace, "01")
    other = _make_section(planspace, "02")
    proposal_results = {
        "01": ProposalPassResult(section_number="01", execution_ready=True),
        "02": ProposalPassResult(section_number="02", execution_ready=False),
    }

    monkeypatch.setattr(
        phase,
        "run_reconciliation_loop",
        lambda *_args, **_kwargs: {
            "conflicts_found": 1,
            "new_sections_proposed": 0,
            "substrate_needed": False,
            "sections_affected": ["01"],
        },
    )
    monkeypatch.setattr(
        phase,
        "_check_and_clear_alignment_changed",
        lambda *_args, **_kwargs: False,
    )
    monkeypatch.setattr(
        phase,
        "run_section",
        lambda *_args, **_kwargs: ProposalPassResult(
            section_number="01",
            execution_ready=True,
        ),
    )

    result = run_reconciliation_phase(
        proposal_results,
        {"01": section, "02": other},
        [section, other],
        planspace,
        codespace,
        "parent",
        {},
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

    monkeypatch.setattr(
        phase,
        "run_reconciliation_loop",
        lambda *_args, **_kwargs: {
            "conflicts_found": 1,
            "new_sections_proposed": 0,
            "substrate_needed": False,
            "sections_affected": ["01"],
        },
    )
    monkeypatch.setattr(
        phase,
        "_check_and_clear_alignment_changed",
        lambda *_args, **_kwargs: True,
    )

    result = run_reconciliation_phase(
        proposal_results,
        {"01": section},
        [section],
        planspace,
        codespace,
        "parent",
        {},
    )

    assert result.new_section_numbers == []
    assert result.removed_section_numbers == ["01"]
    assert result.alignment_changed is True


def test_run_reconciliation_phase_exits_when_parent_aborts(
    planspace: Path,
    codespace: Path,
    monkeypatch: pytest.MonkeyPatch,
    capturing_pipeline_control,
    capturing_communicator,
) -> None:
    section = _make_section(planspace, "01")
    proposal_results = {
        "01": ProposalPassResult(section_number="01", execution_ready=True),
    }

    capturing_pipeline_control._pending_return = True

    monkeypatch.setattr(
        phase,
        "run_reconciliation_loop",
        lambda *_args, **_kwargs: {
            "conflicts_found": 1,
            "new_sections_proposed": 0,
            "substrate_needed": False,
            "sections_affected": ["01"],
        },
    )

    with pytest.raises(ReconciliationPhaseExit):
        run_reconciliation_phase(
            proposal_results,
            {"01": section},
            [section],
            planspace,
            codespace,
            "parent",
            {},
        )

    assert capturing_communicator.messages == ["fail:aborted"]
