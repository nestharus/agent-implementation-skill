from __future__ import annotations

from pathlib import Path

import pytest

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
) -> None:
    section = _make_section(planspace, "01")
    other = _make_section(planspace, "02")
    proposal_results = {
        "01": ProposalPassResult(section_number="01", execution_ready=True),
        "02": ProposalPassResult(section_number="02", execution_ready=False),
    }
    messages: list[str] = []

    monkeypatch.setattr(
        phase,
        "run_reconciliation",
        lambda *_args, **_kwargs: {
            "conflicts_found": 1,
            "new_sections_proposed": 0,
            "substrate_needed": False,
            "sections_affected": ["01"],
        },
    )
    monkeypatch.setattr(
        phase,
        "handle_pending_messages",
        lambda *_args, **_kwargs: False,
    )
    monkeypatch.setattr(
        phase,
        "alignment_changed_pending",
        lambda *_args, **_kwargs: False,
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
    monkeypatch.setattr(
        phase,
        "mailbox_send",
        lambda _planspace, _parent, message: messages.append(message),
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
    assert messages == ["reproposal-done:01:ready"]


def test_run_reconciliation_phase_restarts_on_alignment_change(
    planspace: Path,
    codespace: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    section = _make_section(planspace, "01")
    proposal_results = {
        "01": ProposalPassResult(section_number="01", execution_ready=True),
    }

    monkeypatch.setattr(
        phase,
        "run_reconciliation",
        lambda *_args, **_kwargs: {
            "conflicts_found": 1,
            "new_sections_proposed": 0,
            "substrate_needed": False,
            "sections_affected": ["01"],
        },
    )
    monkeypatch.setattr(
        phase,
        "handle_pending_messages",
        lambda *_args, **_kwargs: False,
    )
    monkeypatch.setattr(
        phase,
        "alignment_changed_pending",
        lambda *_args, **_kwargs: True,
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
) -> None:
    section = _make_section(planspace, "01")
    proposal_results = {
        "01": ProposalPassResult(section_number="01", execution_ready=True),
    }
    messages: list[str] = []

    monkeypatch.setattr(
        phase,
        "run_reconciliation",
        lambda *_args, **_kwargs: {
            "conflicts_found": 1,
            "new_sections_proposed": 0,
            "substrate_needed": False,
            "sections_affected": ["01"],
        },
    )
    monkeypatch.setattr(
        phase,
        "handle_pending_messages",
        lambda *_args, **_kwargs: True,
    )
    monkeypatch.setattr(
        phase,
        "mailbox_send",
        lambda _planspace, _parent, message: messages.append(message),
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

    assert messages == ["fail:aborted"]
