from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from dependency_injector import providers

from containers import Services
from coordination.engine import coordination_controller as loop
from coordination.engine.coordination_controller import CoordinationController
from coordination.engine.global_coordinator import (
    MIN_COORDINATION_ROUNDS,
)
from coordination.problem_types import UnaddressedNoteProblem
from coordination.service.problem_resolver import ProblemResolver
from orchestrator.types import Section, SectionResult
from pipeline.context import DispatchContext
from tests.conftest import StubPolicies


def _make_section(planspace: Path, number: str) -> Section:
    section_path = planspace / "artifacts" / "sections" / f"section-{number}.md"
    section_path.write_text(f"# Section {number}\n", encoding="utf-8")
    return Section(number=number, path=section_path)


def _make_controller(
    *,
    problem_resolver=None,
    global_coordinator=None,
) -> CoordinationController:
    """Build a CoordinationController with stub/mock dependencies."""
    if problem_resolver is None:
        problem_resolver = ProblemResolver(
            artifact_io=Services.artifact_io(),
            communicator=Services.communicator(),
            logger=Services.logger(),
            signals=Services.signals(),
        )
    if global_coordinator is None:
        global_coordinator = MagicMock()
        global_coordinator.run_global_coordination = MagicMock(return_value=True)

    return CoordinationController(
        artifact_io=Services.artifact_io(),
        change_tracker=Services.change_tracker(),
        communicator=Services.communicator(),
        global_coordinator=global_coordinator,
        logger=Services.logger(),
        pipeline_control=Services.pipeline_control(),
        policies=Services.policies(),
        problem_resolver=problem_resolver,
    )


def test_run_coordination_loop_completes_when_everything_is_aligned(
    planspace: Path,
    noop_pipeline_control,
    capturing_communicator,
) -> None:
    section = _make_section(planspace, "01")

    mock_resolver = MagicMock(spec=ProblemResolver)
    mock_resolver.collect_outstanding_problems = MagicMock(return_value=[])

    mock_coordinator = MagicMock()

    ctrl = _make_controller(
        problem_resolver=mock_resolver,
        global_coordinator=mock_coordinator,
    )
    status = ctrl.run_coordination_loop(
        {"01": SectionResult(section_number="01", aligned=True)},
        {"01": section},
        DispatchContext(planspace=planspace, codespace=planspace, _policies=Services.policies()),
    )

    assert status == "complete"
    assert capturing_communicator.messages == ["complete"]
    mock_coordinator.run_global_coordination.assert_not_called()


def test_run_coordination_loop_restarts_when_control_message_arrives(
    planspace: Path,
    capturing_pipeline_control,
) -> None:
    section = _make_section(planspace, "01")

    capturing_pipeline_control._poll_return = "alignment_changed"

    ctrl = _make_controller()
    status = ctrl.run_coordination_loop(
        {"01": SectionResult(section_number="01", aligned=False, problems="x")},
        {"01": section},
        DispatchContext(planspace=planspace, codespace=planspace, _policies=Services.policies()),
    )

    assert status == "restart_phase1"


def test_run_coordination_loop_stalls_and_reports_remaining_sections(
    planspace: Path,
    monkeypatch: pytest.MonkeyPatch,
    noop_pipeline_control,
    capturing_communicator,
    noop_change_tracker,
) -> None:
    section = _make_section(planspace, "01")

    monkeypatch.setattr(loop, "MIN_COORDINATION_ROUNDS", 1)

    mock_coordinator = MagicMock()
    mock_coordinator.run_global_coordination = MagicMock(return_value=False)

    Services.policies.override(providers.Object(StubPolicies({
        "escalation_model": "stronger-model",
        "escalation_triggers": {"stall_count": 2},
    })))
    try:
        ctrl = _make_controller(global_coordinator=mock_coordinator)
        status = ctrl.run_coordination_loop(
            {"01": SectionResult(section_number="01", aligned=False, problems="still broken")},
            {"01": section},
            DispatchContext(planspace=planspace, codespace=planspace, _policies=Services.policies()),
        )
    finally:
        Services.policies.reset_override()

    assert status == "stalled"
    assert (planspace / "artifacts" / "coordination" / "model-escalation.txt").read_text(
        encoding="utf-8",
    ) == "stronger-model"
    messages = capturing_communicator.messages
    assert any(message.startswith("status:coordination:round-") for message in messages)
    assert "escalation:coordination:round-2:stall_count=2" in messages
    assert "fail:01:coordination_exhausted:still broken" in messages


def test_run_coordination_loop_reports_outstanding_rollup_when_aligned(
    planspace: Path,
    monkeypatch: pytest.MonkeyPatch,
    noop_pipeline_control,
    capturing_communicator,
    noop_change_tracker,
) -> None:
    section = _make_section(planspace, "01")
    outstanding = [
        UnaddressedNoteProblem(section="01", description="note pending"),
    ]

    monkeypatch.setattr(loop, "MIN_COORDINATION_ROUNDS", 1)

    mock_resolver = MagicMock(spec=ProblemResolver)
    mock_resolver.collect_outstanding_problems = MagicMock(return_value=outstanding)

    mock_coordinator = MagicMock()
    mock_coordinator.run_global_coordination = MagicMock(return_value=False)

    ctrl = _make_controller(
        problem_resolver=mock_resolver,
        global_coordinator=mock_coordinator,
    )
    status = ctrl.run_coordination_loop(
        {"01": SectionResult(section_number="01", aligned=True)},
        {"01": section},
        DispatchContext(planspace=planspace, codespace=planspace, _policies=Services.policies()),
    )

    assert status == "stalled"
    rollup = json.loads(
        (
            planspace / "artifacts" / "coordination" / "coordination-exhausted.json"
        ).read_text(encoding="utf-8"),
    )
    assert rollup == [
        {
            "type": "unaddressed_note",
            "section": "01",
            "description": "note pending",
        },
    ]
    assert capturing_communicator.messages[-1] == "fail:coordination_exhausted:outstanding:1"


def test_run_coordination_loop_enters_coordination_for_root_reframing_delta(
    planspace: Path,
    monkeypatch: pytest.MonkeyPatch,
    noop_pipeline_control,
    capturing_communicator,
    noop_change_tracker,
) -> None:
    section = _make_section(planspace, "01")
    section.related_files = ["src/main.py"]
    scope_dir = planspace / "artifacts" / "scope-deltas"
    scope_dir.mkdir(parents=True, exist_ok=True)
    (scope_dir / "section-01-scope-delta.json").write_text(
        json.dumps(
            {
                "delta_id": "delta-01",
                "title": "Shared API reframe",
                "source": "proposal",
                "source_sections": ["01"],
                "requires_root_reframing": True,
            },
        ),
        encoding="utf-8",
    )
    coordination_calls: list[bool] = []

    mock_coordinator = MagicMock()
    mock_coordinator.run_global_coordination = MagicMock(
        side_effect=lambda *_args, **_kwargs: coordination_calls.append(True) or True,
    )

    ctrl = _make_controller(global_coordinator=mock_coordinator)
    status = ctrl.run_coordination_loop(
        {"01": SectionResult(section_number="01", aligned=True)},
        {"01": section},
        DispatchContext(planspace=planspace, codespace=planspace, _policies=Services.policies()),
    )

    assert status == "complete"
    assert coordination_calls == [True]
    assert capturing_communicator.messages == ["status:coordination:round-1", "complete"]
