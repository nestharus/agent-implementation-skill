"""Component tests for scaffold ownership assignment mechanism."""

from __future__ import annotations

import json
from pathlib import Path

from src.containers import ArtifactIOService, Services
from src.coordination.engine.plan_executor import PlanExecutor
from src.coordination.problem_types import MisalignedProblem
from src.coordination.types import (
    BridgeDirective,
    CoordinationStrategy,
    ProblemGroup,
)
from src.orchestrator.path_registry import PathRegistry
from src.pipeline.context import DispatchContext
from src.proposal.service.readiness_resolver import ReadinessResolver


# ---------------------------------------------------------------------------
# PathRegistry accessor
# ---------------------------------------------------------------------------


def test_scaffold_assignments_path(tmp_path: Path) -> None:
    """scaffold_assignments() returns the expected path."""
    paths = PathRegistry(tmp_path)
    expected = (
        tmp_path / "artifacts" / "coordination" / "signals"
        / "scaffold-assignments.json"
    )
    assert paths.scaffold_assignments() == expected


# ---------------------------------------------------------------------------
# PlanExecutor._write_scaffold_assignments
# ---------------------------------------------------------------------------

def _make_executor() -> PlanExecutor:
    """Build a PlanExecutor with minimal real dependencies for unit-style tests."""
    from tests.conftest import (
        MockDispatcher,
        NoOpCommunicator,
        NoOpFlow,
        NoOpPipelineControl,
        WritingGuard,
    )
    from src.coordination.prompt.writers import Writers

    logger = Services.logger()
    artifact_io = Services.artifact_io()
    communicator = NoOpCommunicator()
    dispatch_helpers = Services.dispatch_helpers()
    dispatcher = MockDispatcher()
    hasher = Services.hasher()
    pipeline_control = NoOpPipelineControl()
    task_router = Services.task_router()

    writers = Writers(
        task_router=task_router,
        prompt_guard=WritingGuard(),
        logger=logger,
        communicator=communicator,
        artifact_io=artifact_io,
    )

    return PlanExecutor(
        artifact_io=artifact_io,
        communicator=communicator,
        dispatch_helpers=dispatch_helpers,
        dispatcher=dispatcher,
        flow_ingestion=NoOpFlow(),
        hasher=hasher,
        logger=logger,
        pipeline_control=pipeline_control,
        task_router=task_router,
        writers=writers,
    )


def _make_ctx(planspace: Path) -> DispatchContext:
    """Build a DispatchContext for testing."""
    from tests.conftest import StubPolicies

    return DispatchContext(
        planspace=planspace,
        codespace=planspace,
        _policies=StubPolicies(),
    )


def test_write_scaffold_assignments_writes_signal(planspace: Path) -> None:
    """scaffold_assign groups produce a scaffold-assignments.json signal."""
    paths = PathRegistry(planspace)
    ctx = _make_ctx(planspace)

    groups = [
        ProblemGroup(
            problems=[
                MisalignedProblem(
                    section="01", description="missing config",
                    files=["docker-compose.yml", "backend/app/main.py"],
                ),
                MisalignedProblem(
                    section="02", description="missing db",
                    files=["backend/app/db/session.py"],
                ),
                MisalignedProblem(
                    section="03", description="missing migrations",
                    files=["backend/migrations/env.py"],
                ),
            ],
            strategy=CoordinationStrategy.SCAFFOLD_ASSIGN,
            reason="foundational vacuum",
        ),
    ]

    executor = _make_executor()
    covered = executor._write_scaffold_assignments(groups, ctx)

    assert covered == {"01", "02", "03"}

    signal_path = paths.scaffold_assignments()
    assert signal_path.exists()
    data = json.loads(signal_path.read_text(encoding="utf-8"))
    assert "assignments" in data
    sections = {a["section"] for a in data["assignments"]}
    assert sections == {"01", "02", "03"}

    # Verify file lists
    section_files = {a["section"]: a["files"] for a in data["assignments"]}
    assert "docker-compose.yml" in section_files["01"]
    assert "backend/app/main.py" in section_files["01"]
    assert "backend/app/db/session.py" in section_files["02"]
    assert "backend/migrations/env.py" in section_files["03"]


def test_write_scaffold_assignments_ignores_non_scaffold_groups(
    planspace: Path,
) -> None:
    """Non-scaffold_assign groups produce no signal."""
    paths = PathRegistry(planspace)
    ctx = _make_ctx(planspace)

    groups = [
        ProblemGroup(
            problems=[
                MisalignedProblem(
                    section="01", description="drift",
                    files=["src/config.py"],
                ),
            ],
            strategy=CoordinationStrategy.SEQUENTIAL,
            reason="simple fix",
        ),
    ]

    executor = _make_executor()
    covered = executor._write_scaffold_assignments(groups, ctx)

    assert covered == set()
    assert not paths.scaffold_assignments().exists()


def test_write_scaffold_assignments_deduplicates_files(
    planspace: Path,
) -> None:
    """Duplicate files within a section are not repeated in assignments."""
    paths = PathRegistry(planspace)
    ctx = _make_ctx(planspace)

    groups = [
        ProblemGroup(
            problems=[
                MisalignedProblem(
                    section="01", description="prob A",
                    files=["config.py", "main.py"],
                ),
                MisalignedProblem(
                    section="01", description="prob B",
                    files=["config.py"],  # duplicate
                ),
            ],
            strategy=CoordinationStrategy.SCAFFOLD_ASSIGN,
            reason="foundational vacuum",
        ),
    ]

    executor = _make_executor()
    executor._write_scaffold_assignments(groups, ctx)

    data = json.loads(paths.scaffold_assignments().read_text(encoding="utf-8"))
    section_01 = [a for a in data["assignments"] if a["section"] == "01"]
    assert len(section_01) == 1
    assert section_01[0]["files"] == ["config.py", "main.py"]


# ---------------------------------------------------------------------------
# ReadinessResolver._apply_scaffold_overlay
# ---------------------------------------------------------------------------


def _make_proposal_state(planspace: Path, section: str, **overrides) -> None:
    """Write a ready proposal-state."""
    state = {
        "resolved_anchors": ["a.store"],
        "unresolved_anchors": [],
        "resolved_contracts": ["Proto"],
        "unresolved_contracts": [],
        "research_questions": [],
        "blocking_research_questions": [],
        "user_root_questions": [],
        "new_section_candidates": [],
        "shared_seam_candidates": [],
        "execution_ready": True,
        "readiness_rationale": "ready",
        "problem_ids": [],
        "pattern_ids": [],
        "profile_id": "",
        "pattern_deviations": [],
        "governance_questions": [],
    }
    state.update(overrides)
    proposal = (
        planspace / "artifacts" / "proposals"
        / f"section-{section}-proposal-state.json"
    )
    proposal.parent.mkdir(parents=True, exist_ok=True)
    proposal.write_text(json.dumps(state), encoding="utf-8")


def _write_scaffold_signal(
    planspace: Path, assignments: list[dict],
) -> None:
    """Write a scaffold-assignments.json signal."""
    signal_path = PathRegistry(planspace).scaffold_assignments()
    signal_path.parent.mkdir(parents=True, exist_ok=True)
    signal_path.write_text(
        json.dumps({"assignments": assignments}),
        encoding="utf-8",
    )


def _resolve_readiness(planspace: Path, section: str):
    """Test helper — resolve readiness for a section."""
    return ReadinessResolver(
        artifact_io=ArtifactIOService(),
    ).resolve_readiness(planspace, section)


def test_scaffold_overlay_unblocks_assigned_anchors(tmp_path: Path) -> None:
    """Unresolved anchors assigned to this section via scaffold are filtered."""
    planspace = tmp_path / "planspace"
    planspace.mkdir()
    PathRegistry(planspace).ensure_artifacts_tree()

    _make_proposal_state(
        planspace, "01",
        unresolved_anchors=[
            "docker-compose.yml service definitions",
            "backend/app/main.py entrypoint",
            "unknown_service.py needs investigation",
        ],
        execution_ready=True,
    )
    _write_scaffold_signal(planspace, [
        {"section": "01", "files": [
            "docker-compose.yml", "backend/app/main.py",
        ]},
        {"section": "02", "files": ["backend/app/db/session.py"]},
    ])

    result = _resolve_readiness(planspace, "01")

    # Two anchors resolved by scaffold, one remains -> still blocked
    assert result.ready is False
    descriptions = [b["description"] for b in result.blockers]
    assert any("unknown_service" in d for d in descriptions)
    assert not any("docker-compose" in d for d in descriptions)
    assert not any("main.py" in d for d in descriptions)


def test_scaffold_overlay_all_assigned_unblocks(tmp_path: Path) -> None:
    """When all unresolved anchors are scaffold-assigned, section becomes ready."""
    planspace = tmp_path / "planspace"
    planspace.mkdir()
    PathRegistry(planspace).ensure_artifacts_tree()

    _make_proposal_state(
        planspace, "01",
        unresolved_anchors=[
            "docker-compose.yml service definitions",
            "backend/app/main.py entrypoint",
        ],
        execution_ready=True,
    )
    _write_scaffold_signal(planspace, [
        {"section": "01", "files": [
            "docker-compose.yml", "backend/app/main.py",
        ]},
    ])

    result = _resolve_readiness(planspace, "01")

    assert result.ready is True
    assert result.blockers == []


def test_scaffold_overlay_does_not_affect_other_sections(
    tmp_path: Path,
) -> None:
    """Scaffold assignments for section 02 do not affect section 01."""
    planspace = tmp_path / "planspace"
    planspace.mkdir()
    PathRegistry(planspace).ensure_artifacts_tree()

    _make_proposal_state(
        planspace, "01",
        unresolved_anchors=["docker-compose.yml service definitions"],
        execution_ready=True,
    )
    _write_scaffold_signal(planspace, [
        # Only section 02 owns docker-compose.yml
        {"section": "02", "files": ["docker-compose.yml"]},
    ])

    result = _resolve_readiness(planspace, "01")

    # Section 01 is not the owner, so the anchor remains blocking
    assert result.ready is False
    descriptions = [b["description"] for b in result.blockers]
    assert any("docker-compose" in d for d in descriptions)


def test_scaffold_overlay_noop_when_no_signal(tmp_path: Path) -> None:
    """Missing scaffold signal -> overlay does nothing (fail-open)."""
    planspace = tmp_path / "planspace"
    planspace.mkdir()
    PathRegistry(planspace).ensure_artifacts_tree()

    _make_proposal_state(
        planspace, "01",
        unresolved_anchors=["docker-compose.yml service definitions"],
        execution_ready=True,
    )
    # No scaffold signal written

    result = _resolve_readiness(planspace, "01")

    assert result.ready is False
    assert len(result.blockers) == 1


def test_scaffold_overlay_noop_on_malformed_signal(tmp_path: Path) -> None:
    """Malformed scaffold signal -> overlay does nothing (fail-open)."""
    planspace = tmp_path / "planspace"
    planspace.mkdir()
    PathRegistry(planspace).ensure_artifacts_tree()

    _make_proposal_state(
        planspace, "01",
        unresolved_anchors=["docker-compose.yml service definitions"],
        execution_ready=True,
    )
    signal_path = PathRegistry(planspace).scaffold_assignments()
    signal_path.parent.mkdir(parents=True, exist_ok=True)
    signal_path.write_text("not valid json {{{", encoding="utf-8")

    result = _resolve_readiness(planspace, "01")

    assert result.ready is False
    assert len(result.blockers) == 1


def test_scaffold_overlay_case_insensitive_matching(tmp_path: Path) -> None:
    """Scaffold overlay matches file paths case-insensitively."""
    planspace = tmp_path / "planspace"
    planspace.mkdir()
    PathRegistry(planspace).ensure_artifacts_tree()

    _make_proposal_state(
        planspace, "01",
        unresolved_anchors=[
            "Docker-Compose.yml service definitions",
        ],
        execution_ready=True,
    )
    _write_scaffold_signal(planspace, [
        {"section": "01", "files": ["docker-compose.yml"]},
    ])

    result = _resolve_readiness(planspace, "01")

    assert result.ready is True
    assert result.blockers == []


# ---------------------------------------------------------------------------
# CoordinationStrategy enum
# ---------------------------------------------------------------------------


def test_coordination_strategy_scaffold_assign_value() -> None:
    """SCAFFOLD_ASSIGN has the expected string value."""
    assert CoordinationStrategy.SCAFFOLD_ASSIGN == "scaffold_assign"
    assert str(CoordinationStrategy.SCAFFOLD_ASSIGN) == "scaffold_assign"
