from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from dependency_injector import providers

from conftest import make_dispatcher, NoOpChangeTracker, NoOpCommunicator, NoOpPipelineControl
from containers import ArtifactIOService, ChangeTrackerService, FreshnessService, Services
from signals.repository.artifact_io import read_json, write_json
from implementation.engine.implementation_phase import ImplementationPhase
from implementation.repository.roal_index import RoalIndex
from implementation.service.risk_artifacts import RiskArtifacts
from implementation.service.risk_history_recorder import append_risk_history
from proposal.service.readiness_resolver import ReadinessResult
from risk.repository.history import RiskHistory, append_history_entry
from risk.engine.risk_assessor import run_lightweight_risk_check, run_risk_loop
from risk.service.package_builder import PackageBuilder
from risk.repository.serialization import serialize_assessment, serialize_plan
from risk.types import (
    PackageStep,
    PostureProfile,
    RiskAssessment,
    RiskHistoryEntry,
    RiskModifiers,
    RiskPackage,
    RiskPlan,
    RiskType,
    RiskVector,
    StepAssessment,
    StepClass,
    StepDecision,
    StepMitigation,
    UnderstandingInventory,
)
from orchestrator.types import ProposalPassResult, Section


@pytest.fixture(autouse=True)
def _reset_change_tracker():
    """Ensure Services.change_tracker override is reset after each test."""
    yield
    Services.change_tracker.reset_override()


def _make_phase() -> ImplementationPhase:
    """Create an ImplementationPhase with mock services for direct method calls."""
    return ImplementationPhase(
        artifact_io=Services.artifact_io(),
        change_tracker=NoOpChangeTracker(),
        communicator=NoOpCommunicator(),
        logger=Services.logger(),
        pipeline_control=NoOpPipelineControl(),
        risk_assessment=Services.risk_assessment(),
        risk_artifacts=RiskArtifacts(
            artifact_io=Services.artifact_io(),
            freshness=FreshnessService(),
        ),
        roal_index=RoalIndex(artifact_io=Services.artifact_io()),
    )


def _read_roal_input_index(planspace: Path, sec_num: str) -> list[dict]:
    return RoalIndex(artifact_io=ArtifactIOService()).read_roal_input_index(planspace, sec_num)


def run_implementation_pass(proposal_results, sections_by_num, planspace, codespace):
    """Test helper: instantiate ImplementationPhase and call run_implementation_pass."""
    return _make_phase().run_implementation_pass(
        proposal_results, sections_by_num, planspace, codespace,
    )


class _StubChangeTracker(ChangeTrackerService):
    """Test double whose make_alignment_checker returns a configurable callable."""

    def __init__(self, checker_fn):
        self._checker_fn = checker_fn

    def set_flag(self, planspace) -> None:
        pass

    def make_alignment_checker(self):
        return self._checker_fn

    def invalidate_excerpts(self, planspace) -> None:
        pass


def _write_risk_inputs(
    planspace: Path,
    sec_num: str,
    *,
    triage_confidence: str = "medium",
    microstrategy_lines: list[str] | None = None,
    shared_seams: list[str] | None = None,
    freshness_token: str | None = None,
) -> None:
    artifacts = planspace / "artifacts"
    sections = artifacts / "sections"
    proposals = artifacts / "proposals"
    readiness = artifacts / "readiness"
    signals = artifacts / "signals"

    sections.mkdir(parents=True, exist_ok=True)
    proposals.mkdir(parents=True, exist_ok=True)
    readiness.mkdir(parents=True, exist_ok=True)
    signals.mkdir(parents=True, exist_ok=True)

    (sections / f"section-{sec_num}.md").write_text(
        f"# Section {sec_num}\n\nSection spec body.\n",
        encoding="utf-8",
    )
    (sections / f"section-{sec_num}-proposal-excerpt.md").write_text(
        "Proposal excerpt\n",
        encoding="utf-8",
    )
    (sections / f"section-{sec_num}-alignment-excerpt.md").write_text(
        "Alignment excerpt\n",
        encoding="utf-8",
    )
    (sections / f"section-{sec_num}-problem-frame.md").write_text(
        "Problem frame\n",
        encoding="utf-8",
    )
    lines = microstrategy_lines or [
        "Refresh understanding",
        "Implement approved change",
        "Verify results",
    ]
    (proposals / f"section-{sec_num}-microstrategy.md").write_text(
        "".join(f"- {line}\n" for line in lines),
        encoding="utf-8",
    )
    write_json(
        proposals / f"section-{sec_num}-proposal-state.json",
        {
            "resolved_anchors": ["tests::smoke"],
            "unresolved_anchors": [],
            "resolved_contracts": ["src/main.py"],
            "unresolved_contracts": [],
            "research_questions": [],
            "blocking_research_questions": [],
            "user_root_questions": [],
            "new_section_candidates": [],
            "shared_seam_candidates": shared_seams or [],
            "execution_ready": True,
            "readiness_rationale": "ready",
            "problem_ids": [],
            "pattern_ids": [],
            "profile_id": "",
            "pattern_deviations": [],
            "governance_questions": [],
        },
    )
    write_json(
        readiness / f"section-{sec_num}-execution-ready.json",
        {"ready": True, "blockers": [], "rationale": "ready"},
    )
    signal_payload: dict[str, str | int | None] = {
        "intent_mode": "lightweight",
        "confidence": triage_confidence,
        "risk_confidence": triage_confidence,
        "risk_mode": "full" if triage_confidence != "high" else "light",
        "risk_budget_hint": {
            "high": 0,
            "medium": 2,
            "low": 4,
        }[triage_confidence],
        "posture_floor": None,
    }
    if freshness_token is not None:
        signal_payload["freshness_token"] = freshness_token
    write_json(signals / f"intent-triage-{sec_num}.json", signal_payload)
    write_json(artifacts / "tool-registry.json", {"tools": ["pytest"]})
    (artifacts / "codemap.md").write_text("Codemap\n", encoding="utf-8")


def _make_section(
    planspace: Path,
    sec_num: str,
    *,
    related_files: list[str] | None = None,
    solve_count: int = 0,
) -> Section:
    return Section(
        number=sec_num,
        path=planspace / "artifacts" / "sections" / f"section-{sec_num}.md",
        related_files=related_files or ["src/main.py"],
        solve_count=solve_count,
    )


def _assessment_for(
    package: RiskPackage,
    *,
    raw_risks: list[int],
) -> RiskAssessment:
    step_assessments: list[StepAssessment] = []
    for step, raw_risk in zip(package.steps, raw_risks, strict=True):
        dominant_risks = (
            [RiskType.BRUTE_FORCE_REGRESSION]
            if raw_risk >= 60
            else [RiskType.CONTEXT_ROT]
        )
        vector = (
            RiskVector(brute_force_regression=4)
            if raw_risk >= 60
            else RiskVector(context_rot=1)
        )
        step_assessments.append(
            StepAssessment(
                step_id=step.step_id,
                assessment_class=step.assessment_class,
                summary=step.summary,
                prerequisites=list(step.prerequisites),
                risk_vector=vector,
                modifiers=RiskModifiers(
                    blast_radius=3 if raw_risk >= 60 else 1,
                    confidence=0.9,
                ),
                raw_risk=raw_risk,
                dominant_risks=dominant_risks,
            ),
        )

    return RiskAssessment(
        assessment_id=f"assessment-{package.scope}",
        layer=package.layer,
        package_id=package.package_id,
        assessment_scope=package.scope,
        understanding_inventory=UnderstandingInventory(
            confirmed=["proposal reviewed"],
            assumed=[],
            missing=[],
            stale=[],
        ),
        package_raw_risk=max(raw_risks),
        assessment_confidence=0.9,
        dominant_risks=list(step_assessments[-1].dominant_risks),
        step_assessments=step_assessments,
        frontier_candidates=[step.step_id for step in package.steps],
    )


def _plan_for(
    package: RiskPackage,
    assessment_id: str,
    decisions: list[StepMitigation],
) -> RiskPlan:
    return RiskPlan(
        plan_id=f"plan-{package.scope}",
        assessment_id=assessment_id,
        package_id=package.package_id,
        layer=package.layer,
        step_decisions=decisions,
        accepted_frontier=[
            decision.step_id
            for decision in decisions
            if decision.decision == StepDecision.ACCEPT
        ],
        deferred_steps=[
            decision.step_id
            for decision in decisions
            if decision.decision == StepDecision.REJECT_DEFER
        ],
        reopen_steps=[
            decision.step_id
            for decision in decisions
            if decision.decision == StepDecision.REJECT_REOPEN
        ],
    )


def test_full_risk_loop_single_step(
    planspace: Path,
    mock_dispatch: MagicMock,
) -> None:
    _write_risk_inputs(
        planspace,
        "01",
        triage_confidence="medium",
        microstrategy_lines=["Apply the approved change"],
    )
    section = _make_section(planspace, "01")
    package = PackageBuilder(artifact_io=ArtifactIOService()).build_package_from_proposal("section-01", planspace)
    assessment = _assessment_for(package, raw_risks=[20])
    plan = _plan_for(
        package,
        assessment.assessment_id,
        [
            StepMitigation(
                step_id=package.steps[0].step_id,
                decision=StepDecision.ACCEPT,
                posture=PostureProfile.P0_DIRECT,
                mitigations=["bounded single-file edit"],
                residual_risk=20,
                reason="below threshold",
            ),
        ],
    )
    mock_dispatch.side_effect = [
        json.dumps(serialize_assessment(assessment)),
        json.dumps(serialize_plan(plan)),
    ]

    result = _make_phase()._run_risk_review(planspace, section)

    assert result is not None
    assert result.accepted_frontier == [package.steps[0].step_id]
    assert result.deferred_steps == []


def test_full_risk_loop_multi_step_with_defer(
    planspace: Path,
    mock_dispatch: MagicMock,
) -> None:
    _write_risk_inputs(
        planspace,
        "01",
        triage_confidence="medium",
        microstrategy_lines=["Refresh understanding", "Apply the approved change"],
    )
    section = _make_section(planspace, "01", related_files=["src/main.py", "src/utils.py"])
    package = PackageBuilder(artifact_io=ArtifactIOService()).build_package_from_proposal("section-01", planspace)
    assessment = _assessment_for(package, raw_risks=[25, 75])
    plan = _plan_for(
        package,
        assessment.assessment_id,
        [
            StepMitigation(
                step_id=package.steps[0].step_id,
                decision=StepDecision.ACCEPT,
                posture=PostureProfile.P1_LIGHT,
                mitigations=["refresh context first"],
                residual_risk=25,
                reason="frontier step is safe",
            ),
            StepMitigation(
                step_id=package.steps[1].step_id,
                decision=StepDecision.REJECT_DEFER,
                posture=PostureProfile.P4_REOPEN,
                mitigations=["defer until first step lands"],
                residual_risk=75,
                reason="second step remains high risk",
                wait_for=[package.steps[0].step_id],
            ),
        ],
    )
    mock_dispatch.side_effect = [
        json.dumps(serialize_assessment(assessment)),
        json.dumps(serialize_plan(plan)),
    ]

    result = _make_phase()._run_risk_review(planspace, section)

    assert result is not None
    assert result.accepted_frontier == [package.steps[0].step_id]
    assert result.deferred_steps == [package.steps[1].step_id]


def test_risk_loop_fallback_on_parse_failure(
    planspace: Path,
    mock_dispatch: MagicMock,
) -> None:
    _write_risk_inputs(
        planspace,
        "01",
        triage_confidence="medium",
        microstrategy_lines=["Apply the approved change"],
    )
    section = _make_section(planspace, "01")
    package = PackageBuilder(artifact_io=ArtifactIOService()).build_package_from_proposal("section-01", planspace)
    mock_dispatch.return_value = "not valid json"

    result = _make_phase()._run_risk_review(planspace, section)

    assert result is not None
    assert result.accepted_frontier == []
    assert result.reopen_steps == [package.steps[0].step_id]
    assert result.step_decisions[0].posture == PostureProfile.P4_REOPEN


def test_risk_loop_respects_agent_accept_above_threshold(
    planspace: Path,
    mock_dispatch: MagicMock,
) -> None:
    """Agent decisions are authoritative -- residual risk above threshold is accepted.

    Threshold enforcement has been removed; the agent's ACCEPT stands even
    when residual_risk exceeds the class threshold.
    """
    _write_risk_inputs(
        planspace,
        "01",
        triage_confidence="medium",
        microstrategy_lines=["Apply the approved change"],
    )
    section = _make_section(planspace, "01")
    package = PackageBuilder(artifact_io=ArtifactIOService()).build_package_from_proposal("section-01", planspace)
    assessment = _assessment_for(package, raw_risks=[70])
    over_threshold_plan = _plan_for(
        package,
        assessment.assessment_id,
        [
            StepMitigation(
                step_id=package.steps[0].step_id,
                decision=StepDecision.ACCEPT,
                posture=PostureProfile.P3_GUARDED,
                mitigations=["tool agent still wants to proceed"],
                residual_risk=70,
                reason="optimizer accepted despite high residual risk",
            ),
        ],
    )
    mock_dispatch.side_effect = [
        json.dumps(serialize_assessment(assessment)),
        json.dumps(serialize_plan(over_threshold_plan)),
    ]

    result = _make_phase()._run_risk_review(planspace, section)

    assert result is not None
    assert result.accepted_frontier == [package.steps[0].step_id]
    assert result.deferred_steps == []
    assert result.step_decisions[0].decision == StepDecision.ACCEPT


def test_risk_history_accumulates(
    planspace: Path,
    mock_dispatch: MagicMock,
) -> None:
    for sec_num in ("01", "02"):
        _write_risk_inputs(
            planspace,
            sec_num,
            triage_confidence="medium",
            microstrategy_lines=["Apply the approved change"],
        )

    first_section = _make_section(planspace, "01")
    second_section = _make_section(planspace, "02")
    first_package = PackageBuilder(artifact_io=ArtifactIOService()).build_package_from_proposal("section-01", planspace)
    second_package = PackageBuilder(artifact_io=ArtifactIOService()).build_package_from_proposal("section-02", planspace)
    first_assessment = _assessment_for(first_package, raw_risks=[20])
    second_assessment = _assessment_for(second_package, raw_risks=[22])
    first_plan = _plan_for(
        first_package,
        first_assessment.assessment_id,
        [
            StepMitigation(
                step_id=first_package.steps[0].step_id,
                decision=StepDecision.ACCEPT,
                posture=PostureProfile.P1_LIGHT,
                mitigations=["safe change"],
                residual_risk=20,
                reason="below threshold",
            ),
        ],
    )
    second_plan = _plan_for(
        second_package,
        second_assessment.assessment_id,
        [
            StepMitigation(
                step_id=second_package.steps[0].step_id,
                decision=StepDecision.ACCEPT,
                posture=PostureProfile.P1_LIGHT,
                mitigations=["safe follow-up change"],
                residual_risk=22,
                reason="below threshold",
            ),
        ],
    )
    mock_dispatch.side_effect = [
        json.dumps(serialize_assessment(first_assessment)),
        json.dumps(serialize_plan(first_plan)),
        json.dumps(serialize_assessment(second_assessment)),
        json.dumps(serialize_plan(second_plan)),
    ]

    phase = _make_phase()
    first_result = phase._run_risk_review(planspace, first_section)
    second_result = phase._run_risk_review(planspace, second_section)
    assert first_result is not None
    assert second_result is not None

    _aio = ArtifactIOService()
    append_risk_history(planspace, "01", first_result, ["src/main.py"], artifact_io=_aio)
    append_risk_history(planspace, "02", second_result, ["src/utils.py"], artifact_io=_aio)
    history = RiskHistory(artifact_io=ArtifactIOService()).read_history(planspace / "artifacts" / "risk" / "risk-history.jsonl")

    assert len(history) == 2
    assert {entry.package_id for entry in history} == {
        first_package.package_id,
        second_package.package_id,
    }


def test_lightweight_risk_check(
    planspace: Path,
    mock_dispatch: MagicMock,
) -> None:
    _write_risk_inputs(
        planspace,
        "01",
        triage_confidence="medium",
        microstrategy_lines=["Apply the approved change"],
    )
    package = PackageBuilder(artifact_io=ArtifactIOService()).build_package_from_proposal("section-01", planspace)
    assessment = _assessment_for(package, raw_risks=[20])
    plan_payload = _plan_for(
        package,
        assessment.assessment_id,
        [
            StepMitigation(
                step_id=package.steps[0].step_id,
                decision=StepDecision.ACCEPT,
                posture=PostureProfile.P1_LIGHT,
                mitigations=["bounded edit"],
                residual_risk=20,
                reason="optimizer accepted after lightweight assessment",
            ),
        ],
    )
    mock_dispatch.side_effect = [
        json.dumps(serialize_assessment(assessment)),
        json.dumps(serialize_plan(plan_payload)),
    ]

    plan = run_lightweight_risk_check(
        planspace,
        "section-01",
        "implementation",
        package,
    )

    assert plan.accepted_frontier == [package.steps[0].step_id]
    assert plan.deferred_steps == []
    assert [call.kwargs["agent_file"] for call in mock_dispatch.call_args_list] == [
        "risk-assessor.md",
        "execution-optimizer.md",
    ]


def test_full_adaptive_cycle_preserves_agent_posture(
    planspace: Path,
) -> None:
    _write_risk_inputs(
        planspace,
        "01",
        triage_confidence="medium",
        microstrategy_lines=["Apply the approved change"],
    )
    package = RiskPackage(
        package_id="pkg-implementation-section-01",
        layer="implementation",
        scope="section-01",
        origin_problem_id="problem-01",
        origin_source="proposal",
        steps=[
            PackageStep(
                step_id="explore-01",
                assessment_class=StepClass.EXPLORE,
                summary="Refresh context before implementation",
            ),
        ],
    )
    history_path = planspace / "artifacts" / "risk" / "risk-history.jsonl"
    for index in range(2):
        append_history_entry(
            history_path,
            RiskHistoryEntry(
                package_id=f"pkg-prior-{index}",
                step_id=package.steps[0].step_id,
                layer="implementation",
                assessment_class=package.steps[0].assessment_class,
                posture=PostureProfile.P3_GUARDED,
                predicted_risk=65,
                actual_outcome="success",
                dominant_risks=[RiskType.BRUTE_FORCE_REGRESSION],
                blast_radius_band=3,
            ),
        )

    high_assessment = RiskAssessment(
        assessment_id="assessment-high",
        layer="implementation",
        package_id=package.package_id,
        assessment_scope=package.scope,
        understanding_inventory=UnderstandingInventory(
            confirmed=["proposal reviewed"],
            assumed=[],
            missing=[],
            stale=[],
        ),
        package_raw_risk=60,
        assessment_confidence=0.9,
        dominant_risks=[RiskType.BRUTE_FORCE_REGRESSION],
        step_assessments=[
            StepAssessment(
                step_id=package.steps[0].step_id,
                assessment_class=package.steps[0].assessment_class,
                summary=package.steps[0].summary,
                prerequisites=[],
                risk_vector=RiskVector(brute_force_regression=4),
                modifiers=RiskModifiers(blast_radius=3, confidence=0.9),
                raw_risk=60,
                dominant_risks=[RiskType.BRUTE_FORCE_REGRESSION],
            ),
        ],
        frontier_candidates=[package.steps[0].step_id],
    )
    low_assessment = RiskAssessment(
        assessment_id="assessment-low",
        layer="implementation",
        package_id=package.package_id,
        assessment_scope=package.scope,
        understanding_inventory=UnderstandingInventory(
            confirmed=["proposal reviewed"],
            assumed=[],
            missing=[],
            stale=[],
        ),
        package_raw_risk=35,
        assessment_confidence=0.9,
        dominant_risks=[RiskType.BRUTE_FORCE_REGRESSION],
        step_assessments=[
            StepAssessment(
                step_id=package.steps[0].step_id,
                assessment_class=package.steps[0].assessment_class,
                summary=package.steps[0].summary,
                prerequisites=[],
                risk_vector=RiskVector(brute_force_regression=2),
                modifiers=RiskModifiers(blast_radius=3, confidence=0.9),
                raw_risk=35,
                dominant_risks=[RiskType.BRUTE_FORCE_REGRESSION],
            ),
        ],
        frontier_candidates=[package.steps[0].step_id],
    )
    high_plan = _plan_for(
        package,
        high_assessment.assessment_id,
        [
            StepMitigation(
                step_id=package.steps[0].step_id,
                decision=StepDecision.ACCEPT,
                posture=PostureProfile.P3_GUARDED,
                mitigations=["guard the risky edit"],
                residual_risk=60,
                reason="high-risk but still executable",
            ),
        ],
    )
    low_plan = _plan_for(
        package,
        low_assessment.assessment_id,
        [
            StepMitigation(
                step_id=package.steps[0].step_id,
                decision=StepDecision.ACCEPT,
                posture=PostureProfile.P1_LIGHT,
                mitigations=["risk score is now lower"],
                residual_risk=35,
                reason="lower risk after prior success",
            ),
        ],
    )
    responses = iter(
        [
            json.dumps(serialize_assessment(high_assessment)),
            json.dumps(serialize_plan(high_plan)),
            json.dumps(serialize_assessment(low_assessment)),
            json.dumps(serialize_plan(low_plan)),
        ]
    )

    def _dispatch(*_args, **_kwargs) -> str:
        return next(responses)

    Services.dispatcher.override(providers.Object(make_dispatcher(_dispatch)))
    try:
        first_plan = run_risk_loop(
            planspace,
            "section-01",
            "implementation",
            package,
        )
        append_risk_history(planspace, "01", first_plan, ["src/main.py"], artifact_io=ArtifactIOService())
        second_plan = run_risk_loop(
            planspace,
            "section-01",
            "implementation",
            package,
        )
    finally:
        Services.dispatcher.reset_override()

    # Agent posture decisions are authoritative -- no hysteresis override
    assert first_plan.step_decisions[0].posture == PostureProfile.P3_GUARDED
    assert second_plan.step_decisions[0].posture == PostureProfile.P1_LIGHT


def test_reassessment_executes_newly_accepted_frontier_end_to_end(
    planspace: Path,
    codespace: Path,
    monkeypatch,
    noop_pipeline_control,
    noop_communicator,
) -> None:
    _write_risk_inputs(
        planspace,
        "01",
        triage_confidence="medium",
        microstrategy_lines=["Apply the approved change", "Verify the change"],
    )
    section = _make_section(planspace, "01")
    package = PackageBuilder(artifact_io=ArtifactIOService()).build_package_from_proposal("section-01", planspace)
    initial_plan = _plan_for(
        package,
        "assessment-initial",
        [
            StepMitigation(
                step_id=package.steps[0].step_id,
                decision=StepDecision.ACCEPT,
                posture=PostureProfile.P2_STANDARD,
                mitigations=["implement the approved slice"],
                residual_risk=30,
                reason="safe edit",
            ),
            StepMitigation(
                step_id=package.steps[1].step_id,
                decision=StepDecision.REJECT_DEFER,
                posture=PostureProfile.P3_GUARDED,
                mitigations=["wait for implementation outputs"],
                residual_risk=55,
                reason="verify after implementation outputs land",
                wait_for=["modified-file-manifest", "alignment-check-result"],
            ),
        ],
    )
    initial_plan.expected_reassessment_inputs = [
        "modified-file-manifest",
        "alignment-check-result",
    ]
    reassessed_plan = _plan_for(
        package,
        "assessment-reassessed",
        [
            StepMitigation(
                step_id=package.steps[1].step_id,
                decision=StepDecision.ACCEPT,
                posture=PostureProfile.P2_STANDARD,
                mitigations=["verification can now proceed"],
                residual_risk=35,
                reason="required inputs arrived",
            ),
        ],
    )
    reassessment_packages: list[list[str]] = []
    run_calls: list[list[str]] = []

    ct = _StubChangeTracker(lambda *args: False)
    Services.change_tracker.override(providers.Object(ct))
    monkeypatch.setattr("proposal.service.readiness_resolver.ReadinessResolver.resolve_readiness", lambda self, *_args, **_kwargs: ReadinessResult(ready=True))
    monkeypatch.setattr(ImplementationPhase, "_run_risk_review", lambda self, *_args, **_kwargs: initial_plan)
    monkeypatch.setattr("implementation.engine.implementation_phase.append_risk_history", lambda *args, **kwargs: None)
    monkeypatch.setattr("risk.service.package_builder.PackageBuilder.read_package", lambda *_args, **_kwargs: package)
    monkeypatch.setattr("containers.LogService.log_lifecycle", lambda *args, **kwargs: None)

    def _run_section(*_args, **_kwargs) -> list[str]:
        files = (
            ["src/main.py"]
            if not run_calls
            else ["tests/test_main.py"]
        )
        run_calls.append(files)
        (planspace / "artifacts" / "impl-align-01-output.md").write_text(
            '{"frame_ok": true, "aligned": true, "problems": []}',
            encoding="utf-8",
        )
        return files

    def _reassess(self, planspace, scope, layer, package, *args, **kwargs) -> RiskPlan:
        reassessment_packages.append([step.step_id for step in package.steps])
        return reassessed_plan

    monkeypatch.setattr("orchestrator.engine.section_pipeline.SectionPipeline.run_section", _run_section)
    monkeypatch.setattr("containers.RiskAssessmentService.run_risk_loop", _reassess)

    results = run_implementation_pass(
        {"01": ProposalPassResult(section_number="01", execution_ready=True)},
        {"01": section},
        planspace,
        codespace,
    )

    deferred_payload = read_json(
        planspace
        / "artifacts"
        / "inputs"
        / "section-01"
        / "section-01-risk-deferred.json",
    )
    accepted_payload = read_json(
        planspace
        / "artifacts"
        / "inputs"
        / "section-01"
        / "section-01-risk-accepted-steps.json",
    )
    deferred_ref = (
        planspace
        / "artifacts"
        / "inputs"
        / "section-01"
        / "section-01-risk-deferred.ref"
    )

    assert reassessment_packages == [[package.steps[1].step_id]]
    assert run_calls == [["src/main.py"], ["tests/test_main.py"]]
    assert accepted_payload["accepted_steps"] == [package.steps[1].step_id]
    assert deferred_payload is None
    assert not deferred_ref.exists()
    assert results["01"].aligned is True
    assert results["01"].modified_files == ["src/main.py", "tests/test_main.py"]
    assert _read_roal_input_index(planspace, "01") == [
        {
            "kind": "accepted_frontier",
            "path": str(
                planspace
                / "artifacts"
                / "inputs"
                / "section-01"
                / "section-01-risk-accepted-steps.json"
            ),
            "produced_by": "implementation_pass",
        },
    ]
