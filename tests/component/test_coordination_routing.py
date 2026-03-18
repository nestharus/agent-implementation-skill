"""Component tests for specialized coordination routing.

Covers the four new CoordinationStrategy variants and their dispatch paths
through PlanExecutor.execute_coordination_plan.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from conftest import (
    MockDispatcher,
    NoOpCommunicator,
    NoOpFlow,
    NoOpPipelineControl,
    WritingGuard,
)
from containers import Services
from coordination.engine.plan_executor import PlanExecutor
from coordination.problem_types import MisalignedProblem
from coordination.prompt.writers import Writers
from coordination.types import (
    BridgeDirective,
    CoordinationStrategy,
    ProblemGroup,
)
from orchestrator.path_registry import PathRegistry
from orchestrator.types import Section
from pipeline.context import DispatchContext


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _planspace(tmp_path: Path) -> Path:
    planspace = tmp_path / "planspace"
    planspace.mkdir()
    PathRegistry(planspace).ensure_artifacts_tree()
    return planspace


class CapturingFlow(NoOpFlow):
    """Test double that records submit_chain calls."""

    def __init__(self) -> None:
        self.chain_calls: list[tuple] = []

    def submit_chain(self, env, steps, **kwargs):
        self.chain_calls.append((env, steps, kwargs))
        return [1]


def _make_executor(
    *,
    dispatcher=None,
    flow_ingestion=None,
    communicator=None,
) -> PlanExecutor:
    logger = Services.logger()
    artifact_io = Services.artifact_io()
    comm = communicator or NoOpCommunicator()
    dispatch_helpers = Services.dispatch_helpers()
    disp = dispatcher or MockDispatcher()
    hasher = Services.hasher()
    pipeline_control = NoOpPipelineControl()
    task_router = Services.task_router()
    flow = flow_ingestion or NoOpFlow()

    writers = Writers(
        task_router=task_router,
        prompt_guard=WritingGuard(),
        logger=logger,
        communicator=comm,
        artifact_io=artifact_io,
    )

    return PlanExecutor(
        artifact_io=artifact_io,
        communicator=comm,
        dispatch_helpers=dispatch_helpers,
        dispatcher=disp,
        flow_ingestion=flow,
        hasher=hasher,
        logger=logger,
        pipeline_control=pipeline_control,
        task_router=task_router,
        writers=writers,
    )


def _make_ctx(tmp_path: Path, planspace: Path) -> DispatchContext:
    from conftest import StubPolicies

    codespace = tmp_path / "codespace"
    codespace.mkdir(exist_ok=True)
    return DispatchContext(
        planspace=planspace,
        codespace=codespace,
        _policies=StubPolicies(),
    )


def _sections(*nums: str) -> dict[str, Section]:
    return {
        n: Section(number=n, path=Path(f"/fake/section-{n}.md"))
        for n in nums
    }


# ---------------------------------------------------------------------------
# CoordinationStrategy enum values
# ---------------------------------------------------------------------------


def test_scaffold_create_strategy_value() -> None:
    assert CoordinationStrategy.SCAFFOLD_CREATE == "scaffold_create"
    assert str(CoordinationStrategy.SCAFFOLD_CREATE) == "scaffold_create"


def test_seam_repair_strategy_value() -> None:
    assert CoordinationStrategy.SEAM_REPAIR == "seam_repair"
    assert str(CoordinationStrategy.SEAM_REPAIR) == "seam_repair"


def test_spec_ambiguity_strategy_value() -> None:
    assert CoordinationStrategy.SPEC_AMBIGUITY == "spec_ambiguity"
    assert str(CoordinationStrategy.SPEC_AMBIGUITY) == "spec_ambiguity"


def test_research_needed_strategy_value() -> None:
    assert CoordinationStrategy.RESEARCH_NEEDED == "research_needed"
    assert str(CoordinationStrategy.RESEARCH_NEEDED) == "research_needed"


# ---------------------------------------------------------------------------
# SCAFFOLD_CREATE routing
# ---------------------------------------------------------------------------


def test_scaffold_create_dispatches_scaffolder_agent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """SCAFFOLD_CREATE groups dispatch the scaffolder, not the fixer."""
    planspace = _planspace(tmp_path)
    ctx = _make_ctx(tmp_path, planspace)

    # Monkeypatch _dispatch_scaffold_group to record calls.
    scaffold_calls: list[int] = []
    fix_calls: list[int] = []

    def _fake_scaffold(self, group, gid, ctx_):
        scaffold_calls.append(gid)
        return gid, ["stub/file.py"]

    def _fake_fix(self, group, gid, ctx_, default_fix_model=""):
        fix_calls.append(gid)
        return gid, ["real/file.py"]

    monkeypatch.setattr(PlanExecutor, "_dispatch_scaffold_group", _fake_scaffold)
    monkeypatch.setattr(PlanExecutor, "_dispatch_fix_group", _fake_fix)

    executor = _make_executor()
    affected = executor.execute_coordination_plan(
        [
            ProblemGroup(
                problems=[MisalignedProblem(section="01", description="missing infra", files=["config.py"])],
                strategy=CoordinationStrategy.SCAFFOLD_CREATE,
            ),
        ],
        _sections("01"),
        ctx,
    )

    assert scaffold_calls == [0]
    assert fix_calls == []
    assert "01" in affected


# ---------------------------------------------------------------------------
# SEAM_REPAIR routing
# ---------------------------------------------------------------------------


def test_seam_repair_dispatches_fixer(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """SEAM_REPAIR groups go through the existing fixer dispatch path."""
    planspace = _planspace(tmp_path)
    ctx = _make_ctx(tmp_path, planspace)

    fix_calls: list[int] = []
    scaffold_calls: list[int] = []

    def _fake_fix(self, group, gid, ctx_, default_fix_model=""):
        fix_calls.append(gid)
        return gid, ["src/api.py"]

    def _fake_scaffold(self, group, gid, ctx_):
        scaffold_calls.append(gid)
        return gid, []

    monkeypatch.setattr(PlanExecutor, "_dispatch_fix_group", _fake_fix)
    monkeypatch.setattr(PlanExecutor, "_dispatch_scaffold_group", _fake_scaffold)

    executor = _make_executor()
    affected = executor.execute_coordination_plan(
        [
            ProblemGroup(
                problems=[
                    MisalignedProblem(section="01", description="interface mismatch", files=["src/api.py"]),
                    MisalignedProblem(section="02", description="wrong contract", files=["src/api.py"]),
                ],
                strategy=CoordinationStrategy.SEAM_REPAIR,
                bridge=BridgeDirective(needed=False),
            ),
        ],
        _sections("01", "02"),
        ctx,
    )

    assert fix_calls == [0]
    assert scaffold_calls == []
    assert "01" in affected
    assert "02" in affected


# ---------------------------------------------------------------------------
# SPEC_AMBIGUITY routing
# ---------------------------------------------------------------------------


def test_spec_ambiguity_writes_needs_parent_and_skips_dispatch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """SPEC_AMBIGUITY writes a blocker signal and does NOT dispatch any agent."""
    planspace = _planspace(tmp_path)
    ctx = _make_ctx(tmp_path, planspace)

    fix_calls: list[int] = []
    scaffold_calls: list[int] = []

    def _fake_fix(self, group, gid, ctx_, default_fix_model=""):
        fix_calls.append(gid)
        return gid, []

    def _fake_scaffold(self, group, gid, ctx_):
        scaffold_calls.append(gid)
        return gid, []

    monkeypatch.setattr(PlanExecutor, "_dispatch_fix_group", _fake_fix)
    monkeypatch.setattr(PlanExecutor, "_dispatch_scaffold_group", _fake_scaffold)

    executor = _make_executor()
    affected = executor.execute_coordination_plan(
        [
            ProblemGroup(
                problems=[MisalignedProblem(section="03", description="spec contradicts itself", files=["spec.py"])],
                strategy=CoordinationStrategy.SPEC_AMBIGUITY,
            ),
        ],
        _sections("03"),
        ctx,
    )

    # No agent dispatch happened.
    assert fix_calls == []
    assert scaffold_calls == []

    # Blocker signal was written.
    blocker_path = PathRegistry(planspace).signals_dir() / "blocker-spec-ambiguity-0.json"
    assert blocker_path.exists()
    data = json.loads(blocker_path.read_text(encoding="utf-8"))
    assert data["state"] == "needs_parent"
    assert "spec contradicts itself" in data["why_blocked"]

    assert "03" in affected


def test_spec_ambiguity_mixed_with_fixable_group(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When one group is SPEC_AMBIGUITY and another is SEQUENTIAL, only
    the SEQUENTIAL group dispatches."""
    planspace = _planspace(tmp_path)
    ctx = _make_ctx(tmp_path, planspace)

    fix_calls: list[int] = []

    def _fake_fix(self, group, gid, ctx_, default_fix_model=""):
        fix_calls.append(gid)
        return gid, ["src/a.py"]

    monkeypatch.setattr(PlanExecutor, "_dispatch_fix_group", _fake_fix)

    executor = _make_executor()
    affected = executor.execute_coordination_plan(
        [
            ProblemGroup(
                problems=[MisalignedProblem(section="01", description="ambiguous", files=["a.py"])],
                strategy=CoordinationStrategy.SPEC_AMBIGUITY,
            ),
            ProblemGroup(
                problems=[MisalignedProblem(section="02", description="fixable", files=["src/a.py"])],
                strategy=CoordinationStrategy.SEQUENTIAL,
            ),
        ],
        _sections("01", "02"),
        ctx,
    )

    # Only group 1 was dispatched.
    assert fix_calls == [1]
    assert "01" in affected
    assert "02" in affected


# ---------------------------------------------------------------------------
# RESEARCH_NEEDED routing
# ---------------------------------------------------------------------------


def test_research_needed_submits_explore_task_and_skips_dispatch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """RESEARCH_NEEDED submits a scan.explore task and skips agent dispatch."""
    planspace = _planspace(tmp_path)
    ctx = _make_ctx(tmp_path, planspace)

    fix_calls: list[int] = []

    def _fake_fix(self, group, gid, ctx_, default_fix_model=""):
        fix_calls.append(gid)
        return gid, []

    monkeypatch.setattr(PlanExecutor, "_dispatch_fix_group", _fake_fix)

    flow = CapturingFlow()
    executor = _make_executor(flow_ingestion=flow)
    affected = executor.execute_coordination_plan(
        [
            ProblemGroup(
                problems=[MisalignedProblem(section="04", description="need more info", files=["lib.py"])],
                strategy=CoordinationStrategy.RESEARCH_NEEDED,
            ),
        ],
        _sections("04"),
        ctx,
    )

    assert fix_calls == []
    assert "04" in affected

    # A scan.explore task was submitted.
    assert len(flow.chain_calls) == 1
    env, steps, _ = flow.chain_calls[0]
    assert len(steps) == 1
    assert steps[0].task_type == "scan.explore"
    assert "coord-group-0" in steps[0].concern_scope

    # Exploration prompt was written.
    explore_prompt = PathRegistry(planspace).coordination_dir() / "research-explore-0-prompt.md"
    assert explore_prompt.exists()
    content = explore_prompt.read_text(encoding="utf-8")
    assert "need more info" in content


# ---------------------------------------------------------------------------
# _dispatch_scaffold_group (direct unit test)
# ---------------------------------------------------------------------------


def test_dispatch_scaffold_group_calls_scaffolder_route(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """_dispatch_scaffold_group dispatches via coordination.scaffold route."""
    planspace = _planspace(tmp_path)
    ctx = _make_ctx(tmp_path, planspace)

    dispatch_agent_files: list[str] = []
    disp = MockDispatcher()

    def _capturing_dispatch(*args, **kwargs):
        if "agent_file" in kwargs:
            dispatch_agent_files.append(str(kwargs["agent_file"]))
        return ""

    disp.mock.side_effect = _capturing_dispatch

    executor = _make_executor(dispatcher=disp)
    group_id, modified = executor._dispatch_scaffold_group(
        [MisalignedProblem(section="01", description="missing stub", files=["app/init.py"])],
        0,
        ctx,
    )

    assert group_id == 0
    assert isinstance(modified, list)
    # Verify the scaffolder agent was used (the route resolves to scaffolder.md).
    assert disp.mock.called
    call_kwargs = disp.mock.call_args
    if call_kwargs.kwargs.get("agent_file"):
        assert "scaffolder" in str(call_kwargs.kwargs["agent_file"])


# ---------------------------------------------------------------------------
# _handle_spec_ambiguity_group (direct unit test)
# ---------------------------------------------------------------------------


def test_handle_spec_ambiguity_group_returns_sections(tmp_path: Path) -> None:
    planspace = _planspace(tmp_path)
    ctx = _make_ctx(tmp_path, planspace)

    executor = _make_executor()
    sections = executor._handle_spec_ambiguity_group(
        [
            MisalignedProblem(section="01", description="contradictory", files=["a.py"]),
            MisalignedProblem(section="03", description="underspecified", files=["b.py"]),
        ],
        5,
        ctx,
    )

    assert sections == {"01", "03"}
    blocker = PathRegistry(planspace).signals_dir() / "blocker-spec-ambiguity-5.json"
    assert blocker.exists()


# ---------------------------------------------------------------------------
# _handle_research_needed_group (direct unit test)
# ---------------------------------------------------------------------------


def test_handle_research_needed_group_submits_task(tmp_path: Path) -> None:
    planspace = _planspace(tmp_path)
    ctx = _make_ctx(tmp_path, planspace)

    flow = CapturingFlow()
    executor = _make_executor(flow_ingestion=flow)
    sections = executor._handle_research_needed_group(
        [MisalignedProblem(section="02", description="unclear", files=["x.py"])],
        3,
        ctx,
    )

    assert sections == {"02"}
    assert len(flow.chain_calls) == 1


# ---------------------------------------------------------------------------
# Route registration
# ---------------------------------------------------------------------------


def test_scaffold_route_registered() -> None:
    """coordination.scaffold route is registered and points to scaffolder.md."""
    task_router = Services.task_router()
    agent_path = task_router.agent_for("coordination.scaffold")
    assert agent_path is not None
    assert "scaffolder" in str(agent_path)
