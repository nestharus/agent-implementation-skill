"""Component tests for ROAL risk package building."""

from __future__ import annotations

import json
from pathlib import Path

from containers import ArtifactIOService, Services
from orchestrator.path_registry import PathRegistry
from proposal.repository.state import ProposalState
from risk.service.package_builder import (
    PackageBuilder,
    _materialize_steps,
    _positional_assessment_class,
    build_package,
    refresh_package,
)
from risk.service.readiness_risk_bridge import (
    ReadinessRiskBridge,
    build_readiness_risk_package,
)
from risk.types import PackageStep, StepClass


def _builder() -> PackageBuilder:
    return PackageBuilder(artifact_io=ArtifactIOService())


def test_build_package_creates_expected_structure() -> None:
    steps = [
        PackageStep(
            step_id="explore-01",
            assessment_class=StepClass.EXPLORE,
            summary="Refresh understanding",
        ),
        PackageStep(
            step_id="edit-02",
            assessment_class=StepClass.EDIT,
            summary="Apply change",
            prerequisites=["explore-01"],
        ),
    ]

    package = build_package(
        scope="section-03",
        layer="implementation",
        problem_id="problem-03",
        source="proposal",
        steps=steps,
    )

    assert package.package_id == "pkg-implementation-section-03"
    assert package.scope == "section-03"
    assert package.origin_problem_id == "problem-03"
    assert package.steps == steps


def test_build_package_from_proposal_with_minimal_proposal(tmp_path: Path) -> None:
    artifacts = tmp_path / "artifacts"
    sections = artifacts / "sections"
    proposals = artifacts / "proposals"
    readiness = artifacts / "readiness"
    sections.mkdir(parents=True)
    proposals.mkdir(parents=True)
    readiness.mkdir(parents=True)

    (sections / "section-03-proposal-excerpt.md").write_text(
        "# Proposal\nImplement cache invalidation\n",
        encoding="utf-8",
    )
    (sections / "section-03-problem-frame.md").write_text(
        "Problem frame details\n",
        encoding="utf-8",
    )
    (proposals / "section-03-proposal-state.json").write_text(
        json.dumps(
            {
                "resolved_anchors": ["cache.invalidate"],
                "unresolved_anchors": [],
                "resolved_contracts": ["CacheStore"],
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
        ),
        encoding="utf-8",
    )
    (readiness / "section-03-execution-ready.json").write_text(
        json.dumps({"ready": True, "blockers": [], "rationale": "ready"}),
        encoding="utf-8",
    )

    package = _builder().build_package_from_proposal("section-03", tmp_path)

    assert package.layer == "implementation"
    assert [step.step_id for step in package.steps] == [
        "explore-01",
        "edit-02",
        "verify-03",
    ]
    assert package.steps[1].mutation_surface == ["CacheStore"]
    assert package.steps[2].verification_surface == ["cache.invalidate"]


def test_build_package_from_proposal_with_microstrategy(tmp_path: Path) -> None:
    artifacts = tmp_path / "artifacts"
    proposals = artifacts / "proposals"
    proposals.mkdir(parents=True)

    (proposals / "section-03-microstrategy.md").write_text(
        "# Inspect current behavior\n"
        "- Coordinate with section 04\n"
        "- Verify final behavior\n",
        encoding="utf-8",
    )

    package = _builder().build_package_from_proposal("section-03", tmp_path)

    assert [step.summary for step in package.steps] == [
        "Inspect current behavior",
        "Coordinate with section 04",
        "Verify final behavior",
    ]
    assert [step.step_id for step in package.steps] == [
        "explore-01",
        "edit-02",
        "verify-03",
    ]


def test_build_package_from_proposal_consumes_typed_microstrategy_steps(
    tmp_path: Path,
) -> None:
    artifacts = tmp_path / "artifacts"
    proposals = artifacts / "proposals"
    proposals.mkdir(parents=True)
    (proposals / "section-03-microstrategy.md").write_text(
        "```json\n"
        "[\n"
        '  {"summary": "Refresh local context", "assessment_class": "explore"},\n'
        '  {"summary": "Stabilize stale readiness inputs", "assessment_class": "stabilize"},\n'
        '  {"summary": "Resolve shared seam with section 04", "assessment_class": "coordinate"},\n'
        '  {"summary": "Run final checks", "assessment_class": "verify"}\n'
        "]\n"
        "```\n",
        encoding="utf-8",
    )

    package = _builder().build_package_from_proposal("section-03", tmp_path)

    assert [step.summary for step in package.steps] == [
        "Refresh local context",
        "Stabilize stale readiness inputs",
        "Resolve shared seam with section 04",
        "Run final checks",
    ]
    assert [step.assessment_class for step in package.steps] == [
        StepClass.EXPLORE,
        StepClass.STABILIZE,
        StepClass.COORDINATE,
        StepClass.VERIFY,
    ]
    assert [step.step_id for step in package.steps] == [
        "explore-01",
        "stabilize-02",
        "coordinate-03",
        "verify-04",
    ]


def test_build_package_from_empty_proposal_uses_generic_defaults(
    tmp_path: Path,
) -> None:
    package = _builder().build_package_from_proposal("section-03", tmp_path)

    assert [step.step_id for step in package.steps] == [
        "explore-01",
        "edit-02",
        "verify-03",
    ]
    assert package.steps[0].summary == "Refresh understanding and constraints"
    assert package.steps[1].summary == "Implement the approved change slice"
    assert package.steps[2].summary == "Verify alignment and execution results"


def test_positional_assessment_class_uses_edit_for_single_step() -> None:
    assert _positional_assessment_class(index=1, total=1) == StepClass.EDIT


def test_positional_assessment_class_uses_position_based_defaults_for_multi_step() -> None:
    assert [
        _positional_assessment_class(index=1, total=4),
        _positional_assessment_class(index=2, total=4),
        _positional_assessment_class(index=3, total=4),
        _positional_assessment_class(index=4, total=4),
    ] == [
        StepClass.EXPLORE,
        StepClass.EDIT,
        StepClass.EDIT,
        StepClass.VERIFY,
    ]


def test_materialize_steps_uses_typed_assessment_classes_when_present() -> None:
    steps = _materialize_steps(
        step_summaries=[
            "Refresh context",
            "Stabilize stale readiness inputs",
            "Resolve seam with section 04",
            "Verify final behavior",
        ],
        proposal_state=ProposalState(),
        assessment_classes={
            2: "stabilize",
            3: "coordinate",
        },
    )

    assert [step.assessment_class for step in steps] == [
        StepClass.EXPLORE,
        StepClass.STABILIZE,
        StepClass.COORDINATE,
        StepClass.VERIFY,
    ]


def test_materialize_steps_uses_positional_fallback_without_assessment_classes() -> None:
    steps = _materialize_steps(
        step_summaries=[
            "Refresh context",
            "Apply change",
            "Verify final behavior",
        ],
        proposal_state=ProposalState(),
    )

    assert [step.assessment_class for step in steps] == [
        StepClass.EXPLORE,
        StepClass.EDIT,
        StepClass.VERIFY,
    ]


def test_materialize_steps_invalid_assessment_class_falls_back_to_positional() -> None:
    steps = _materialize_steps(
        step_summaries=[
            "Refresh context",
            "Middle step",
            "Verify final behavior",
        ],
        proposal_state=ProposalState(),
        assessment_classes={2: "unknown"},
    )

    assert [step.assessment_class for step in steps] == [
        StepClass.EXPLORE,
        StepClass.EDIT,
        StepClass.VERIFY,
    ]


def test_refresh_package_removes_completed_steps() -> None:
    package = build_package(
        scope="section-03",
        layer="implementation",
        problem_id="problem-03",
        source="proposal",
        steps=[
            PackageStep(
                step_id="explore-01",
                assessment_class=StepClass.EXPLORE,
                summary="Refresh understanding",
            ),
            PackageStep(
                step_id="edit-02",
                assessment_class=StepClass.EDIT,
                summary="Apply change",
                prerequisites=["explore-01"],
            ),
            PackageStep(
                step_id="verify-03",
                assessment_class=StepClass.VERIFY,
                summary="Verify change",
                prerequisites=["edit-02"],
            ),
        ],
    )

    refreshed = refresh_package(
        package,
        completed_steps=["explore-01"],
        new_evidence={},
    )

    assert [step.step_id for step in refreshed.steps] == ["edit-02", "verify-03"]
    assert refreshed.steps[0].prerequisites == []
    assert refreshed.steps[1].prerequisites == ["edit-02"]


def test_write_and_read_package_round_trip(tmp_path: Path) -> None:
    b = _builder()
    registry = PathRegistry(tmp_path)
    package = build_package(
        scope="section-03",
        layer="implementation",
        problem_id="problem-03",
        source="proposal",
        steps=[
            PackageStep(
                step_id="explore-01",
                assessment_class=StepClass.EXPLORE,
                summary="Refresh understanding",
            )
        ],
    )

    path = b.write_package(registry, package)
    restored = b.read_package(registry, "section-03")

    assert path == tmp_path / "artifacts" / "risk" / "section-03-risk-package.json"
    assert restored == package


# ---------------------------------------------------------------------------
# ReadinessRiskBridge tests
# ---------------------------------------------------------------------------


def test_build_readiness_risk_package_converts_blockers_to_steps() -> None:
    blockers = [
        {"type": "unresolved_contracts", "description": "CacheProtocol"},
        {"type": "blocking_research_questions", "description": "Retry budget?"},
        {"type": "user_root_questions", "description": "Choose retry policy"},
    ]

    package = build_readiness_risk_package("03", blockers)

    assert package is not None
    assert package.package_id == "pkg-readiness-section-03"
    assert package.layer == "readiness"
    assert package.scope == "section-03"
    assert package.origin_source == "readiness-gate"
    assert len(package.steps) == 3
    assert package.steps[0].assessment_class == StepClass.COORDINATE
    assert package.steps[1].assessment_class == StepClass.EXPLORE
    assert package.steps[2].assessment_class == StepClass.STABILIZE
    assert all("blocker" in step.summary.lower() for step in package.steps)


def test_build_readiness_risk_package_returns_none_for_empty_blockers() -> None:
    assert build_readiness_risk_package("03", []) is None


def test_build_readiness_risk_package_deduplicates_blockers() -> None:
    blockers = [
        {"type": "unresolved_contracts", "description": "CacheProtocol"},
        {"type": "unresolved_contracts", "description": "CacheProtocol"},
    ]

    package = build_readiness_risk_package("03", blockers)

    assert package is not None
    assert len(package.steps) == 1


def test_build_readiness_risk_package_handles_governance_blocker_shape() -> None:
    """Governance blockers use state/detail rather than type/description."""
    blockers = [
        {"state": "governance_deviation", "detail": "pattern mismatch"},
    ]

    package = build_readiness_risk_package("03", blockers)

    assert package is not None
    assert len(package.steps) == 1
    assert "pattern mismatch" in package.steps[0].summary


def test_readiness_risk_bridge_persists_artifact(tmp_path: Path) -> None:
    planspace = tmp_path / "planspace"
    planspace.mkdir()
    PathRegistry(planspace).ensure_artifacts_tree()

    bridge = ReadinessRiskBridge(
        artifact_io=ArtifactIOService(),
        logger=Services.logger(),
    )
    blockers = [
        {"type": "unresolved_contracts", "description": "CacheProtocol"},
        {"type": "user_root_questions", "description": "Choose retry policy"},
    ]

    path = bridge.persist_readiness_risk("03", blockers, planspace)

    assert path is not None
    assert path.exists()
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["package_id"] == "pkg-readiness-section-03"
    assert data["layer"] == "readiness"
    assert len(data["steps"]) == 2


def test_readiness_risk_bridge_returns_none_for_no_blockers(tmp_path: Path) -> None:
    planspace = tmp_path / "planspace"
    planspace.mkdir()
    PathRegistry(planspace).ensure_artifacts_tree()

    bridge = ReadinessRiskBridge(
        artifact_io=ArtifactIOService(),
        logger=Services.logger(),
    )

    assert bridge.persist_readiness_risk("03", [], planspace) is None
    risk_path = planspace / "artifacts" / "risk" / "section-03-readiness-risk.json"
    assert not risk_path.exists()
