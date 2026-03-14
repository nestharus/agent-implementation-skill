from __future__ import annotations

import json
from pathlib import Path

import pytest
from dependency_injector import providers

from containers import Services
from coordination.engine import coordination_controller as loop
from coordination.engine.coordination_controller import run_coordination_loop
from coordination.problem_types import UnaddressedNoteProblem
from orchestrator.types import Section, SectionResult
from pipeline.context import DispatchContext
from tests.conftest import StubPolicies


def _make_section(planspace: Path, number: str) -> Section:
    section_path = planspace / "artifacts" / "sections" / f"section-{number}.md"
    section_path.write_text(f"# Section {number}\n", encoding="utf-8")
    return Section(number=number, path=section_path)


def test_run_coordination_loop_completes_when_everything_is_aligned(
    planspace: Path,
    monkeypatch: pytest.MonkeyPatch,
    noop_pipeline_control,
    capturing_communicator,
) -> None:
    section = _make_section(planspace, "01")
    snapshots: list[int] = []

    monkeypatch.setattr(
        loop,
        "collect_outstanding_problems",
        lambda *_args, **_kwargs: [],
    )
    monkeypatch.setattr(
        loop,
        "build_strategic_state",
        lambda _decisions_dir, section_results, _planspace: snapshots.append(
            len(section_results),
        ),
    )
    monkeypatch.setattr(
        loop,
        "run_global_coordination",
        lambda *_args, **_kwargs: pytest.fail("coordination should not run"),
    )

    status = run_coordination_loop(
        {"01": SectionResult(section_number="01", aligned=True)},
        {"01": section},
        DispatchContext(planspace=planspace, codespace=planspace, parent="parent"),
    )

    assert status == "complete"
    assert snapshots == [1]
    assert capturing_communicator.messages == ["complete"]


def test_run_coordination_loop_restarts_when_control_message_arrives(
    planspace: Path,
    capturing_pipeline_control,
) -> None:
    section = _make_section(planspace, "01")

    capturing_pipeline_control._poll_return = "alignment_changed"

    status = run_coordination_loop(
        {"01": SectionResult(section_number="01", aligned=False, problems="x")},
        {"01": section},
        DispatchContext(planspace=planspace, codespace=planspace, parent="parent"),
    )

    assert status == "restart_phase1"


def test_run_coordination_loop_stalls_and_reports_remaining_sections(
    planspace: Path,
    monkeypatch: pytest.MonkeyPatch,
    noop_pipeline_control,
    capturing_communicator,
) -> None:
    section = _make_section(planspace, "01")
    snapshots: list[int] = []

    monkeypatch.setattr(loop, "MAX_COORDINATION_ROUNDS", 5)
    monkeypatch.setattr(loop, "MIN_COORDINATION_ROUNDS", 1)
    monkeypatch.setattr(
        loop,
        "run_global_coordination",
        lambda *_args, **_kwargs: False,
    )
    monkeypatch.setattr(
        loop,
        "_check_and_clear_alignment_changed",
        lambda *_args, **_kwargs: False,
    )
    monkeypatch.setattr(
        loop,
        "build_strategic_state",
        lambda _decisions_dir, section_results, _planspace: snapshots.append(
            len(section_results),
        ),
    )

    Services.policies.override(providers.Object(StubPolicies({
        "escalation_model": "stronger-model",
        "escalation_triggers": {"stall_count": 2},
    })))
    try:
        status = run_coordination_loop(
            {"01": SectionResult(section_number="01", aligned=False, problems="still broken")},
            {"01": section},
            DispatchContext(planspace=planspace, codespace=planspace, parent="parent"),
        )
    finally:
        Services.policies.reset_override()

    assert status == "stalled"
    assert snapshots == [1]
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
) -> None:
    section = _make_section(planspace, "01")
    snapshots: list[int] = []
    outstanding = [
        UnaddressedNoteProblem(section="01", description="note pending"),
    ]
    calls = iter([outstanding, outstanding, outstanding])

    monkeypatch.setattr(loop, "MAX_COORDINATION_ROUNDS", 1)
    monkeypatch.setattr(loop, "MIN_COORDINATION_ROUNDS", 1)
    monkeypatch.setattr(
        loop,
        "collect_outstanding_problems",
        lambda *_args, **_kwargs: next(calls),
    )
    monkeypatch.setattr(
        loop,
        "run_global_coordination",
        lambda *_args, **_kwargs: False,
    )
    monkeypatch.setattr(
        loop,
        "_check_and_clear_alignment_changed",
        lambda *_args, **_kwargs: False,
    )
    monkeypatch.setattr(
        loop,
        "build_strategic_state",
        lambda _decisions_dir, section_results, _planspace: snapshots.append(
            len(section_results),
        ),
    )

    status = run_coordination_loop(
        {"01": SectionResult(section_number="01", aligned=True)},
        {"01": section},
        DispatchContext(planspace=planspace, codespace=planspace, parent="parent"),
    )

    assert status == "exhausted"
    assert snapshots == [1]
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
    snapshots: list[int] = []
    coordination_calls: list[bool] = []

    monkeypatch.setattr(
        loop,
        "run_global_coordination",
        lambda *_args, **_kwargs: coordination_calls.append(True) or True,
    )
    monkeypatch.setattr(
        loop,
        "_check_and_clear_alignment_changed",
        lambda *_args, **_kwargs: False,
    )
    monkeypatch.setattr(
        loop,
        "build_strategic_state",
        lambda _decisions_dir, section_results, _planspace: snapshots.append(
            len(section_results),
        ),
    )

    status = run_coordination_loop(
        {"01": SectionResult(section_number="01", aligned=True)},
        {"01": section},
        DispatchContext(planspace=planspace, codespace=planspace, parent="parent"),
    )

    assert status == "complete"
    assert coordination_calls == [True]
    assert snapshots == [1]
    assert capturing_communicator.messages == ["status:coordination:round-1", "complete"]
