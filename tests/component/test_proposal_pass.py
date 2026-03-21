from __future__ import annotations

from pathlib import Path

import pytest

from implementation.service.section_reexplorer import SectionReexplorer
from orchestrator.engine.section_pipeline import SectionPipeline
from src.orchestrator.path_registry import PathRegistry
from src.proposal.engine import proposal_phase as proposal_pass
from src.proposal.engine.proposal_phase import ProposalPassExit, run_proposal_pass
from orchestrator.types import ProposalPassResult, Section


def _planspace(tmp_path: Path) -> Path:
    planspace = tmp_path / "planspace"
    planspace.mkdir()
    PathRegistry(planspace).ensure_artifacts_tree()
    return planspace


def test_run_proposal_pass_reexplores_then_records_result(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    noop_pipeline_control,
    noop_change_tracker,
    capturing_communicator,
) -> None:
    planspace = _planspace(tmp_path)
    section_path = planspace / "artifacts" / "section-01.md"
    section_path.write_text("# Section 01\n", encoding="utf-8")
    section = Section(number="01", path=section_path, related_files=[])

    monkeypatch.setattr(
        SectionReexplorer,
        "reexplore_section",
        lambda self, *args, **kwargs: "ok",
    )
    monkeypatch.setattr(
        proposal_pass,
        "parse_related_files",
        lambda path: ["src/app.py"],
    )
    monkeypatch.setattr(
        SectionPipeline,
        "run_section",
        lambda self, *args, **kwargs: ProposalPassResult(
            section_number="01",
            execution_ready=True,
        ),
    )
    monkeypatch.setattr("containers.LogService.log_lifecycle", lambda *args, **kwargs: None)

    results = run_proposal_pass(
        [section],
        {"01": section},
        planspace,
        tmp_path / "codespace",
    )

    assert results["01"].execution_ready is True
    assert section.related_files == ["src/app.py"]
    assert capturing_communicator.messages == ["proposal-done:01:ready"]


def test_run_proposal_pass_raises_on_abort(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capturing_pipeline_control,
    noop_change_tracker,
    capturing_communicator,
) -> None:
    planspace = _planspace(tmp_path)
    section = Section(number="01", path=planspace / "artifacts" / "section-01.md")

    capturing_pipeline_control._pending_return = True

    with pytest.raises(ProposalPassExit):
        run_proposal_pass(
            [section],
            {"01": section},
            planspace,
            tmp_path / "codespace",
        )

    assert capturing_communicator.messages == ["fail:aborted"]


def test_paused_section_continues_to_next(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    noop_pipeline_control,
    noop_change_tracker,
    capturing_communicator,
) -> None:
    """When a section's proposal returns None (paused/aborted), the phase
    records it as blocked and continues processing remaining sections instead
    of raising ProposalPassExit.
    """
    planspace = _planspace(tmp_path)
    codespace = tmp_path / "codespace"
    codespace.mkdir(exist_ok=True)

    sec1_path = planspace / "artifacts" / "sections" / "section-01.md"
    sec1_path.parent.mkdir(parents=True, exist_ok=True)
    sec1_path.write_text("# Section 01\n", encoding="utf-8")
    sec2_path = planspace / "artifacts" / "sections" / "section-02.md"
    sec2_path.write_text("# Section 02\n", encoding="utf-8")

    section1 = Section(number="01", path=sec1_path, related_files=["src/a.py"])
    section2 = Section(number="02", path=sec2_path, related_files=["src/b.py"])

    call_count = {"value": 0}

    def _run_section(self, planspace, codespace, section, *, all_sections=None, pass_mode="full"):
        call_count["value"] += 1
        if section.number == "01":
            return None  # simulate pause/abort
        return ProposalPassResult(
            section_number="02",
            execution_ready=True,
        )

    monkeypatch.setattr(SectionPipeline, "run_section", _run_section)
    monkeypatch.setattr("containers.LogService.log_lifecycle", lambda *args, **kwargs: None)

    results = run_proposal_pass(
        [section1, section2],
        {"01": section1, "02": section2},
        planspace,
        codespace,
    )

    # Section 01 should be recorded as blocked, not raise ProposalPassExit
    assert "01" in results
    assert results["01"].execution_ready is False
    assert results["01"].blockers[0]["type"] == "paused"

    # Section 02 should have been processed successfully
    assert "02" in results
    assert results["02"].execution_ready is True

    # Both sections were dispatched
    assert call_count["value"] == 2

    # Verify messages sent to parent
    paused_msg = [m for m in capturing_communicator.messages if "01" in m and "paused" in m]
    assert len(paused_msg) == 1
    ready_msg = [m for m in capturing_communicator.messages if "02" in m and "ready" in m]
    assert len(ready_msg) == 1
