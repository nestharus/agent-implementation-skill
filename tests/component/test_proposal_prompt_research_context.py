from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, call

import pytest

from containers import Services
from dispatch.prompt.writers import Writers as PromptWriters
from orchestrator.engine.section_pipeline import SectionPipeline
from orchestrator.path_registry import PathRegistry
from orchestrator.types import ProposalPassResult, Section
from proposal.engine.readiness_gate import GateResult
from signals.types import PASS_MODE_PROPOSAL


def _make_writers() -> PromptWriters:
    return PromptWriters(
        task_router=Services.task_router(),
        prompt_guard=Services.prompt_guard(),
        logger=Services.logger(),
        communicator=Services.communicator(),
        section_alignment=Services.section_alignment(),
        artifact_io=Services.artifact_io(),
        cross_section=Services.cross_section(),
        config=Services.config(),
    )

def _section(planspace: Path, number: str = "01") -> Section:
    section_path = planspace / "artifacts" / "sections" / f"section-{number}.md"
    section_path.write_text(
        f"# Section {number}\n\nResearch-aware prompt test section.\n",
        encoding="utf-8",
    )
    return Section(number=number, path=section_path, related_files=["src/main.py"])

def _write_common_section_artifacts(planspace: Path, number: str = "01") -> None:
    sections_dir = planspace / "artifacts" / "sections"
    proposals_dir = planspace / "artifacts" / "proposals"
    (sections_dir / f"section-{number}-proposal-excerpt.md").write_text(
        "proposal excerpt\n",
        encoding="utf-8",
    )
    (sections_dir / f"section-{number}-alignment-excerpt.md").write_text(
        "alignment excerpt\n",
        encoding="utf-8",
    )
    (proposals_dir / f"section-{number}-integration-proposal.md").write_text(
        "aligned proposal\n",
        encoding="utf-8",
    )

def _write_research_artifacts(planspace: Path, number: str = "01") -> tuple[Path, Path]:
    research_dir = (
        planspace / "artifacts" / "research" / "sections" / f"section-{number}"
    )
    research_dir.mkdir(parents=True, exist_ok=True)
    addendum = research_dir / "proposal-addendum.md"
    dossier = research_dir / "dossier.md"
    addendum.write_text("domain-specific constraints\n", encoding="utf-8")
    dossier.write_text("background findings\n", encoding="utf-8")
    return addendum, dossier

@pytest.fixture(autouse=True)
def _prompt_writer_isolation(monkeypatch: pytest.MonkeyPatch,
    noop_communicator) -> None:
    monkeypatch.setattr(
        "dispatch.prompt.writers.ContextSidecar.materialize_context_sidecar",
        lambda *_args, **_kwargs: None,
    )

def test_write_integration_proposal_prompt_includes_research_refs_when_present(
    planspace: Path,
    codespace: Path,
) -> None:
    section = _section(planspace)
    _write_common_section_artifacts(planspace)
    addendum, dossier = _write_research_artifacts(planspace)

    prompt_path = _make_writers().write_integration_proposal_prompt(section, planspace, codespace)
    prompt = prompt_path.read_text(encoding="utf-8")

    assert "Research addendum (domain knowledge)" in prompt
    assert str(addendum) in prompt
    assert "Research dossier (full findings)" in prompt
    assert str(dossier) in prompt
    assert (
        "Available task types for this role: scan.explore, signals.impact_analysis, "
        "proposal.integration, research.plan"
    ) in prompt

def test_write_integration_proposal_prompt_omits_research_refs_when_absent(
    planspace: Path,
    codespace: Path,
) -> None:
    section = _section(planspace)
    _write_common_section_artifacts(planspace)

    prompt_path = _make_writers().write_integration_proposal_prompt(section, planspace, codespace)
    prompt = prompt_path.read_text(encoding="utf-8")

    assert "Research addendum (domain knowledge)" not in prompt
    assert "Research dossier (full findings)" not in prompt

def test_write_strategic_impl_prompt_includes_research_refs_when_present(
    planspace: Path,
    codespace: Path,
) -> None:
    section = _section(planspace)
    _write_common_section_artifacts(planspace)
    addendum, dossier = _write_research_artifacts(planspace)

    prompt_path = _make_writers().write_strategic_impl_prompt(section, planspace, codespace)
    prompt = prompt_path.read_text(encoding="utf-8")

    assert "Research addendum (domain constraints)" in prompt
    assert str(addendum) in prompt
    assert "Research dossier (background knowledge)" in prompt
    assert str(dossier) in prompt


# ---------------------------------------------------------------------------
# Section pipeline: dossier-aware proposal re-run
# ---------------------------------------------------------------------------


def test_section_pipeline_reruns_proposal_when_dossier_arrives(
    planspace: Path,
    codespace: Path,
) -> None:
    """When a research dossier appears between the proposal loop and the
    readiness gate, the section pipeline should re-run the proposal loop
    so the rebuilt prompt includes the dossier."""

    section = _section(planspace)
    paths = PathRegistry(planspace)
    dossier_path = paths.research_dossier(section.number)

    # Mock collaborators
    mock_logger = MagicMock()
    mock_artifact_io = MagicMock()
    mock_pipeline_control = MagicMock()
    mock_proposal_cycle = MagicMock()
    mock_readiness_gate = MagicMock()

    # First proposal loop succeeds (returns non-None)
    mock_proposal_cycle.run_proposal_loop.return_value = ""

    # First readiness check: blocked.  Simulate a dossier appearing
    # between the proposal build and the readiness evaluation by writing
    # the dossier as a side-effect of the first resolve_and_route call.
    first_call = True

    def _resolve_side_effect(sec, ps, mode, codespace=None):
        nonlocal first_call
        if first_call:
            first_call = False
            # Simulate research completing: write the dossier
            dossier_path.parent.mkdir(parents=True, exist_ok=True)
            dossier_path.write_text("research findings\n", encoding="utf-8")
            return GateResult(
                ready=False,
                blockers=[{"type": "research_pending"}],
                proposal_pass_result=ProposalPassResult(
                    section_number=section.number,
                    execution_ready=False,
                    blockers=[{"type": "research_pending"}],
                ),
            )
        # Second call (after re-run): ready
        return GateResult(
            ready=True,
            blockers=[],
            proposal_pass_result=ProposalPassResult(
                section_number=section.number,
                execution_ready=True,
            ),
        )

    mock_readiness_gate.resolve_and_route.side_effect = _resolve_side_effect

    pipeline = SectionPipeline(
        logger=mock_logger,
        artifact_io=mock_artifact_io,
        pipeline_control=mock_pipeline_control,
        proposal_cycle=mock_proposal_cycle,
        readiness_gate=mock_readiness_gate,
    )

    result = pipeline.run_section(
        planspace, codespace, section,
        pass_mode=PASS_MODE_PROPOSAL,
    )

    # The proposal loop should have been called twice:
    # once initially, once after dossier appeared.
    assert mock_proposal_cycle.run_proposal_loop.call_count == 2
    # Readiness gate should have been called twice as well.
    assert mock_readiness_gate.resolve_and_route.call_count == 2
    # The log message about the re-run should have been emitted.
    log_messages = [str(c) for c in mock_logger.log.call_args_list]
    assert any("research dossier" in msg and "re-running" in msg for msg in log_messages)


def test_section_pipeline_no_rerun_when_dossier_existed_before_proposal(
    planspace: Path,
    codespace: Path,
) -> None:
    """When the dossier already existed before the proposal loop ran,
    the pipeline should NOT re-run (the prompt already included it)."""

    section = _section(planspace)
    paths = PathRegistry(planspace)

    # Write dossier BEFORE the pipeline runs
    dossier_path = paths.research_dossier(section.number)
    dossier_path.parent.mkdir(parents=True, exist_ok=True)
    dossier_path.write_text("pre-existing research\n", encoding="utf-8")

    mock_logger = MagicMock()
    mock_proposal_cycle = MagicMock()
    mock_readiness_gate = MagicMock()

    mock_proposal_cycle.run_proposal_loop.return_value = ""
    mock_readiness_gate.resolve_and_route.return_value = GateResult(
        ready=False,
        blockers=[{"type": "other_blocker"}],
        proposal_pass_result=ProposalPassResult(
            section_number=section.number,
            execution_ready=False,
            blockers=[{"type": "other_blocker"}],
        ),
    )

    pipeline = SectionPipeline(
        logger=mock_logger,
        artifact_io=MagicMock(),
        pipeline_control=MagicMock(),
        proposal_cycle=mock_proposal_cycle,
        readiness_gate=mock_readiness_gate,
    )

    result = pipeline.run_section(
        planspace, codespace, section,
        pass_mode=PASS_MODE_PROPOSAL,
    )

    # Proposal loop should only run once — no re-run since dossier
    # existed before the proposal was built.
    assert mock_proposal_cycle.run_proposal_loop.call_count == 1
    assert mock_readiness_gate.resolve_and_route.call_count == 1
