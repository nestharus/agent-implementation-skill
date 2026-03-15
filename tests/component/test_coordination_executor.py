from __future__ import annotations

from pathlib import Path

import pytest

from conftest import override_dispatcher_and_guard
from containers import Services
from coordination.engine.plan_executor import (
    CoordinationExecutionExit,
    PlanExecutor,
)
from coordination.problem_types import MisalignedProblem
from coordination.prompt.writers import Writers
from coordination.types import BridgeDirective, ProblemGroup
from orchestrator.types import Section
from pipeline.context import DispatchContext
from src.orchestrator.path_registry import PathRegistry


def _planspace(tmp_path: Path) -> Path:
    planspace = tmp_path / "planspace"
    planspace.mkdir()
    PathRegistry(planspace).ensure_artifacts_tree()
    return planspace


def _make_executor() -> PlanExecutor:
    writers = Writers(
        artifact_io=Services.artifact_io(),
        communicator=Services.communicator(),
        logger=Services.logger(),
        prompt_guard=Services.prompt_guard(),
        task_router=Services.task_router(),
    )
    return PlanExecutor(
        artifact_io=Services.artifact_io(),
        communicator=Services.communicator(),
        dispatch_helpers=Services.dispatch_helpers(),
        dispatcher=Services.dispatcher(),
        flow_ingestion=Services.flow_ingestion(),
        hasher=Services.hasher(),
        logger=Services.logger(),
        pipeline_control=Services.pipeline_control(),
        task_router=Services.task_router(),
        writers=writers,
    )


def test_execute_coordination_plan_runs_fix_groups_and_persists_modified_files(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    noop_pipeline_control,
) -> None:
    planspace = _planspace(tmp_path)
    sections_by_num = {
        "01": Section(
            number="01",
            path=planspace / "artifacts" / "section-01.md",
            related_files=["src/a.py"],
        ),
        "02": Section(
            number="02",
            path=planspace / "artifacts" / "section-02.md",
            related_files=["src/b.py"],
        ),
    }

    monkeypatch.setattr(
        PlanExecutor,
        "_dispatch_fix_group",
        lambda self, group, group_index, *args, **kwargs: (group_index, [group[0].files[0]]),
    )

    executor = _make_executor()
    affected_sections = executor.execute_coordination_plan(
        [
            ProblemGroup(
                problems=[MisalignedProblem(section="01", description="", files=["src/a.py"])],
            ),
            ProblemGroup(
                problems=[MisalignedProblem(section="02", description="", files=["src/b.py"])],
            ),
        ],
        sections_by_num,
        DispatchContext(planspace=planspace, codespace=tmp_path / "codespace"),
    )

    assert affected_sections == ["01", "02"]
    assert executor.read_execution_modified_files(planspace) == ["src/a.py", "src/b.py"]


def test_execute_coordination_plan_runs_bridge_and_registers_inputs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    noop_pipeline_control,
    noop_communicator,
) -> None:
    planspace = _planspace(tmp_path)
    notes_dir = planspace / "artifacts" / "notes"
    sections_by_num = {
        "01": Section(
            number="01",
            path=planspace / "artifacts" / "section-01.md",
            related_files=["src/a.py"],
        ),
        "02": Section(
            number="02",
            path=planspace / "artifacts" / "section-02.md",
            related_files=["src/a.py"],
        ),
    }
    calls = {"dispatch": 0}

    def _dispatch_agent(*args, **kwargs):
        calls["dispatch"] += 1
        notes_dir.joinpath("from-bridge-0-to-01.md").write_text("Note for 01", encoding="utf-8")
        notes_dir.joinpath("from-bridge-0-to-02.md").write_text("Note for 02", encoding="utf-8")
        contract_delta = (
            planspace / "artifacts" / "contracts" / "contract-delta-group-0.md"
        )
        contract_delta.parent.mkdir(parents=True, exist_ok=True)
        contract_delta.write_text("delta", encoding="utf-8")
        return "ok"

    monkeypatch.setattr(
        PlanExecutor,
        "_dispatch_fix_group",
        lambda self, group, group_index, *args, **kwargs: (group_index, ["src/a.py"]),
    )
    monkeypatch.setattr(
        Services.hasher(),
        "content_hash",
        lambda payload: "abcdef1234567890",
    )

    with override_dispatcher_and_guard(_dispatch_agent):
        executor = _make_executor()
        affected_sections = executor.execute_coordination_plan(
            [
                ProblemGroup(
                    problems=[
                        MisalignedProblem(section="01", description="", files=["src/a.py"]),
                        MisalignedProblem(section="02", description="", files=["src/a.py"]),
                    ],
                    bridge=BridgeDirective(needed=True, reason="shared seam"),
                ),
            ],
            sections_by_num,
            DispatchContext(planspace=planspace, codespace=tmp_path / "codespace"),
        )

    assert affected_sections == ["01", "02"]
    assert calls["dispatch"] == 1
    assert "Note ID" in (
        notes_dir / "from-bridge-0-to-01.md"
    ).read_text(encoding="utf-8")
    assert (
        planspace / "artifacts" / "inputs" / "section-01" / "contract-delta-group-0.ref"
    ).exists()


def test_execute_coordination_plan_raises_on_fix_group_sentinel(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    noop_pipeline_control,
) -> None:
    planspace = _planspace(tmp_path)
    sections_by_num = {
        "01": Section(number="01", path=planspace / "artifacts" / "section-01.md"),
    }

    monkeypatch.setattr(
        PlanExecutor,
        "_dispatch_fix_group",
        lambda self, *args, **kwargs: (0, None),
    )

    executor = _make_executor()
    with pytest.raises(CoordinationExecutionExit):
        executor.execute_coordination_plan(
            [
                ProblemGroup(
                    problems=[MisalignedProblem(section="01", description="", files=["src/a.py"])],
                ),
            ],
            sections_by_num,
            DispatchContext(planspace=planspace, codespace=tmp_path / "codespace"),
        )
