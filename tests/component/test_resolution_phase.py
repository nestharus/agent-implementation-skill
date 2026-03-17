"""Component tests for the blocker resolution phase.

Covers:
- ReadinessBlockerProblem serialization
- collect_readiness_blocker_problems (blocked produce problems, ready skipped, paused skipped)
- ResolutionPhase (no blocked sections = skip, resolution unblocks, stall detection)
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from containers import ArtifactIOService, Services
from coordination.problem_types import ReadinessBlockerProblem
from coordination.service.problem_resolver import ProblemResolver
from coordination.engine.resolution_phase import (
    MAX_RESOLUTION_ROUNDS,
    ResolutionPhase,
)
from orchestrator.types import ProposalPassResult, Section


# ---------------------------------------------------------------------------
# ReadinessBlockerProblem serialization
# ---------------------------------------------------------------------------


def test_readiness_blocker_problem_type_field() -> None:
    """type field is always 'readiness_blocker' and not settable via init."""
    p = ReadinessBlockerProblem(
        section="01",
        description="missing config",
        blocker_type="governance_deviation",
        needs="pattern delta resolution",
    )
    assert p.type == "readiness_blocker"
    assert p.blocker_type == "governance_deviation"
    assert p.needs == "pattern delta resolution"


def test_readiness_blocker_problem_to_dict() -> None:
    """to_dict() round-trips all fields."""
    p = ReadinessBlockerProblem(
        section="03",
        description="unresolved anchor",
        files=["src/db.py"],
        blocker_type="unresolved_anchor",
        needs="anchor resolution",
    )
    d = p.to_dict()
    assert d["type"] == "readiness_blocker"
    assert d["section"] == "03"
    assert d["description"] == "unresolved anchor"
    assert d["files"] == ["src/db.py"]
    assert d["blocker_type"] == "unresolved_anchor"
    assert d["needs"] == "anchor resolution"


def test_readiness_blocker_problem_defaults() -> None:
    """Default values for optional fields."""
    p = ReadinessBlockerProblem(section="01", description="test")
    assert p.blocker_type == ""
    assert p.needs == ""
    assert p.files == []


# ---------------------------------------------------------------------------
# collect_readiness_blocker_problems
# ---------------------------------------------------------------------------


def _make_resolver() -> ProblemResolver:
    return ProblemResolver(
        artifact_io=Services.artifact_io(),
        communicator=Services.communicator(),
        logger=Services.logger(),
        signals=Services.signals(),
    )


def _make_section(number: str) -> Section:
    return Section(
        number=number,
        path=Path(f"/tmp/section-{number}.md"),
        related_files=[f"src/mod_{number}.py"],
    )


def test_collect_readiness_blocker_problems_blocked_sections(
    noop_communicator,
) -> None:
    """Blocked sections with non-paused blockers produce ReadinessBlockerProblems."""
    sections_by_num = {"01": _make_section("01"), "02": _make_section("02")}
    proposal_results = {
        "01": ProposalPassResult(
            section_number="01",
            execution_ready=False,
            blockers=[
                {
                    "type": "governance_deviation",
                    "description": "unresolved pattern deviation",
                    "needs": "pattern delta resolution",
                },
            ],
        ),
        "02": ProposalPassResult(
            section_number="02",
            execution_ready=False,
            blockers=[
                {
                    "type": "unresolved_anchor",
                    "description": "anchor not resolved",
                    "needs": "anchor resolution",
                },
                {
                    "type": "shared_seam",
                    "description": "shared seam candidate",
                },
            ],
        ),
    }

    resolver = _make_resolver()
    problems = resolver.collect_readiness_blocker_problems(
        proposal_results, sections_by_num,
    )

    assert len(problems) == 3
    assert all(p.type == "readiness_blocker" for p in problems)
    sec_01_problems = [p for p in problems if p.section == "01"]
    assert len(sec_01_problems) == 1
    assert sec_01_problems[0].blocker_type == "governance_deviation"
    assert sec_01_problems[0].needs == "pattern delta resolution"
    assert sec_01_problems[0].files == ["src/mod_01.py"]


def test_collect_readiness_blocker_problems_ready_sections_skipped(
    noop_communicator,
) -> None:
    """Sections that are execution_ready produce no problems."""
    sections_by_num = {"01": _make_section("01")}
    proposal_results = {
        "01": ProposalPassResult(
            section_number="01",
            execution_ready=True,
            blockers=[],
        ),
    }

    resolver = _make_resolver()
    problems = resolver.collect_readiness_blocker_problems(
        proposal_results, sections_by_num,
    )

    assert problems == []


def test_collect_readiness_blocker_problems_paused_skipped(
    noop_communicator,
) -> None:
    """Blockers with type 'paused' are skipped."""
    sections_by_num = {"01": _make_section("01")}
    proposal_results = {
        "01": ProposalPassResult(
            section_number="01",
            execution_ready=False,
            blockers=[
                {
                    "type": "paused",
                    "description": "Section 01 proposal paused or aborted",
                },
                {
                    "type": "governance_deviation",
                    "description": "real blocker",
                    "needs": "resolution",
                },
            ],
        ),
    }

    resolver = _make_resolver()
    problems = resolver.collect_readiness_blocker_problems(
        proposal_results, sections_by_num,
    )

    assert len(problems) == 1
    assert problems[0].blocker_type == "governance_deviation"


def test_collect_readiness_blocker_problems_empty_blockers(
    noop_communicator,
) -> None:
    """Blocked section with empty blockers list produces no problems."""
    sections_by_num = {"01": _make_section("01")}
    proposal_results = {
        "01": ProposalPassResult(
            section_number="01",
            execution_ready=False,
            blockers=[],
        ),
    }

    resolver = _make_resolver()
    problems = resolver.collect_readiness_blocker_problems(
        proposal_results, sections_by_num,
    )

    assert problems == []


# ---------------------------------------------------------------------------
# ResolutionPhase
# ---------------------------------------------------------------------------


def _make_resolution_phase(
    global_coordinator=None,
    readiness_resolver=None,
) -> ResolutionPhase:
    if global_coordinator is None:
        global_coordinator = MagicMock()
    if readiness_resolver is None:
        readiness_resolver = MagicMock()
    return ResolutionPhase(
        global_coordinator=global_coordinator,
        readiness_resolver=readiness_resolver,
        logger=Services.logger(),
        policies=Services.policies(),
        communicator=Services.communicator(),
    )


def test_resolution_phase_no_blocked_sections(
    planspace: Path,
    codespace: Path,
    noop_communicator,
    noop_pipeline_control,
) -> None:
    """No blocked sections means skip — returns empty list immediately."""
    from pipeline.context import DispatchContext
    ctx = DispatchContext(
        planspace=planspace, codespace=codespace,
        _policies=Services.policies(),
    )

    mock_coordinator = MagicMock()
    phase = _make_resolution_phase(global_coordinator=mock_coordinator)

    result = phase.run_resolution_phase(
        proposal_results={},
        blocked_sections=[],
        sections_by_num={},
        ctx=ctx,
    )

    assert result == []
    mock_coordinator.run_blocker_resolution.assert_not_called()


def test_resolution_phase_unblocks_section(
    planspace: Path,
    codespace: Path,
    noop_communicator,
    noop_pipeline_control,
) -> None:
    """Resolution phase unblocks a section and updates proposal_results."""
    from pipeline.context import DispatchContext
    ctx = DispatchContext(
        planspace=planspace, codespace=codespace,
        _policies=Services.policies(),
    )

    mock_coordinator = MagicMock()
    mock_coordinator.run_blocker_resolution.return_value = ["01"]

    phase = _make_resolution_phase(global_coordinator=mock_coordinator)

    proposal_results = {
        "01": ProposalPassResult(
            section_number="01",
            execution_ready=False,
            blockers=[{"type": "test_blocker", "description": "blocked"}],
        ),
        "02": ProposalPassResult(
            section_number="02",
            execution_ready=False,
            blockers=[{"type": "test_blocker", "description": "blocked"}],
        ),
    }

    result = phase.run_resolution_phase(
        proposal_results=proposal_results,
        blocked_sections=["01", "02"],
        sections_by_num={
            "01": _make_section("01"),
            "02": _make_section("02"),
        },
        ctx=ctx,
    )

    # Section 01 was unblocked
    assert proposal_results["01"].execution_ready is True
    assert proposal_results["01"].blockers == []
    # Section 02 remains blocked
    assert "02" in result
    assert "01" not in result


def test_resolution_phase_stall_detection(
    planspace: Path,
    codespace: Path,
    noop_communicator,
    noop_pipeline_control,
) -> None:
    """Stall detection stops the loop when no progress is made."""
    from pipeline.context import DispatchContext
    ctx = DispatchContext(
        planspace=planspace, codespace=codespace,
        _policies=Services.policies(),
    )

    mock_coordinator = MagicMock()
    # Never unblocks anything
    mock_coordinator.run_blocker_resolution.return_value = []

    phase = _make_resolution_phase(global_coordinator=mock_coordinator)

    result = phase.run_resolution_phase(
        proposal_results={
            "01": ProposalPassResult(
                section_number="01",
                execution_ready=False,
                blockers=[{"type": "test", "description": "stuck"}],
            ),
        },
        blocked_sections=["01"],
        sections_by_num={"01": _make_section("01")},
        ctx=ctx,
    )

    # Section should remain blocked
    assert result == ["01"]
    # Should not exceed MAX_RESOLUTION_ROUNDS
    assert mock_coordinator.run_blocker_resolution.call_count <= MAX_RESOLUTION_ROUNDS
