"""Component tests for ROAL loop orchestration."""

from __future__ import annotations

import json
from pathlib import Path

from lib.risk.history import append_history_entry
from lib.risk.loop import (
    build_optimization_prompt,
    build_risk_assessment_prompt,
    parse_risk_assessment,
    parse_risk_plan,
    run_lightweight_risk_check,
    run_risk_loop,
)
from lib.risk.serialization import serialize_assessment, serialize_plan
from lib.risk.types import (
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


def test_build_risk_assessment_prompt_includes_expected_context(tmp_path: Path) -> None:
    package = _package()
    _write_artifacts(tmp_path)
    append_history_entry(
        tmp_path / "artifacts" / "risk" / "risk-history.jsonl",
        RiskHistoryEntry(
            package_id=package.package_id,
            step_id="explore-01",
            layer="implementation",
            step_class=StepClass.EXPLORE,
            posture=PostureProfile.P1_LIGHT,
            predicted_risk=22,
            actual_outcome="success",
            dominant_risks=[RiskType.CONTEXT_ROT],
        ),
    )

    prompt = build_risk_assessment_prompt(package, tmp_path, "section-03")

    assert "Section spec" in prompt
    assert "Spec body" in prompt
    assert "Proposal excerpt" in prompt
    assert "Alignment details" in prompt
    assert "Problem frame details" in prompt
    assert "Microstrategy" in prompt
    assert "tool-registry.json" in prompt
    assert "LOOP_DETECTED" in prompt


def test_build_optimization_prompt_includes_assessment_and_parameters(
    tmp_path: Path,
) -> None:
    package = _package()
    assessment = _assessment()
    _write_artifacts(tmp_path)

    prompt = build_optimization_prompt(
        assessment,
        package,
        {"step_thresholds": {"explore": 60, "edit": 45}},
        tmp_path,
    )

    assert "ROAL Execution Optimization" in prompt
    assert '"assessment_id": "assessment-1"' in prompt
    assert '"package_id": "pkg-implementation-section-03"' in prompt
    assert '"edit": 45' in prompt
    assert "tool-registry.json" in prompt


def test_parse_risk_assessment_with_valid_json() -> None:
    assessment = parse_risk_assessment(json.dumps(serialize_assessment(_assessment())))

    assert assessment == _assessment()


def test_parse_risk_assessment_with_code_fenced_json() -> None:
    response = "```json\n" + json.dumps(serialize_assessment(_assessment())) + "\n```"

    assessment = parse_risk_assessment(response)

    assert assessment == _assessment()


def test_parse_risk_assessment_with_invalid_json_returns_none() -> None:
    assert parse_risk_assessment("not valid json") is None


def test_parse_risk_plan_with_valid_json() -> None:
    plan = parse_risk_plan(json.dumps(serialize_plan(_valid_plan())))

    assert plan == _valid_plan()


def test_parse_risk_plan_with_code_fenced_json() -> None:
    response = "```json\n" + json.dumps(serialize_plan(_valid_plan())) + "\n```"

    plan = parse_risk_plan(response)

    assert plan == _valid_plan()


def test_parse_risk_plan_with_invalid_json_returns_none() -> None:
    assert parse_risk_plan("not valid json") is None


def test_run_lightweight_risk_check_with_mocked_dispatch(tmp_path: Path) -> None:
    package = _package()
    _write_artifacts(tmp_path)

    def _dispatch(*args, **kwargs) -> str:  # noqa: ANN002, ANN003
        assert kwargs["agent_file"] == "risk-assessor.md"
        return json.dumps(serialize_assessment(_assessment()))

    plan = run_lightweight_risk_check(
        tmp_path,
        "section-03",
        "implementation",
        package,
        _dispatch,
    )

    assert plan.accepted_frontier == ["explore-01"]
    assert plan.deferred_steps == []
    assert plan.step_decisions[0].posture == PostureProfile.P1_LIGHT


def test_run_risk_loop_single_iteration_when_plan_passes(tmp_path: Path) -> None:
    package = _package()
    _write_artifacts(tmp_path)
    calls: list[str] = []

    def _dispatch(*args, **kwargs) -> str:  # noqa: ANN002, ANN003
        calls.append(kwargs["agent_file"])
        if kwargs["agent_file"] == "risk-assessor.md":
            return json.dumps(serialize_assessment(_assessment()))
        return json.dumps(serialize_plan(_valid_plan()))

    plan = run_risk_loop(
        tmp_path,
        "section-03",
        "implementation",
        package,
        _dispatch,
    )

    assert calls == ["risk-assessor.md", "execution-optimizer.md"]
    assert plan.accepted_frontier == ["explore-01"]
    assert plan.deferred_steps == []


def test_run_risk_loop_returns_mechanically_enforced_plan(tmp_path: Path) -> None:
    package = _package()
    _write_artifacts(tmp_path)
    calls: list[str] = []

    def _dispatch(*args, **kwargs) -> str:  # noqa: ANN002, ANN003
        calls.append(kwargs["agent_file"])
        if kwargs["agent_file"] == "risk-assessor.md":
            return json.dumps(serialize_assessment(_assessment()))
        return json.dumps(serialize_plan(_invalid_threshold_plan()))

    plan = run_risk_loop(
        tmp_path,
        "section-03",
        "implementation",
        package,
        _dispatch,
    )

    assert calls == ["risk-assessor.md", "execution-optimizer.md"]
    assert plan.accepted_frontier == []
    assert plan.deferred_steps == ["explore-01"]
    assert plan.step_decisions[0].decision == StepDecision.REJECT_DEFER


def test_run_risk_loop_retries_when_plan_fails_validation(tmp_path: Path) -> None:
    package = _package()
    _write_artifacts(tmp_path)
    optimizer_calls = 0
    calls: list[str] = []

    def _dispatch(*args, **kwargs) -> str:  # noqa: ANN002, ANN003
        nonlocal optimizer_calls
        calls.append(kwargs["agent_file"])
        if kwargs["agent_file"] == "risk-assessor.md":
            return json.dumps(serialize_assessment(_assessment()))
        optimizer_calls += 1
        if optimizer_calls == 1:
            return json.dumps(serialize_plan(_invalid_frontier_plan()))
        return json.dumps(serialize_plan(_valid_plan()))

    plan = run_risk_loop(
        tmp_path,
        "section-03",
        "implementation",
        package,
        _dispatch,
        max_iterations=3,
    )

    assert calls == [
        "risk-assessor.md",
        "execution-optimizer.md",
        "risk-assessor.md",
        "execution-optimizer.md",
    ]
    assert plan.accepted_frontier == ["explore-01"]
    assert plan.deferred_steps == []


def _package() -> RiskPackage:
    return RiskPackage(
        package_id="pkg-implementation-section-03",
        layer="implementation",
        scope="section-03",
        origin_problem_id="problem-03",
        origin_source="proposal",
        steps=[
            PackageStep(
                step_id="explore-01",
                step_class=StepClass.EXPLORE,
                summary="Refresh understanding",
            )
        ],
    )


def _assessment() -> RiskAssessment:
    return RiskAssessment(
        assessment_id="assessment-1",
        layer="implementation",
        package_id="pkg-implementation-section-03",
        assessment_scope="section-03",
        understanding_inventory=UnderstandingInventory(
            confirmed=["proposal excerpt reviewed"],
            assumed=[],
            missing=[],
            stale=[],
        ),
        package_raw_risk=25,
        assessment_confidence=0.8,
        dominant_risks=[RiskType.CONTEXT_ROT],
        step_assessments=[
            StepAssessment(
                step_id="explore-01",
                step_class=StepClass.EXPLORE,
                summary="Refresh understanding",
                prerequisites=[],
                risk_vector=RiskVector(context_rot=1),
                modifiers=RiskModifiers(confidence=0.8),
                raw_risk=25,
                dominant_risks=[RiskType.CONTEXT_ROT],
            )
        ],
        frontier_candidates=["explore-01"],
        reopen_recommendations=[],
        notes=["low risk"],
    )


def _valid_plan() -> RiskPlan:
    return RiskPlan(
        plan_id="plan-1",
        assessment_id="assessment-1",
        package_id="pkg-implementation-section-03",
        layer="implementation",
        step_decisions=[
            StepMitigation(
                step_id="explore-01",
                decision=StepDecision.ACCEPT,
                posture=PostureProfile.P1_LIGHT,
                mitigations=["refresh section context"],
                residual_risk=25,
                reason="below threshold",
            )
        ],
        accepted_frontier=["explore-01"],
        deferred_steps=[],
        reopen_steps=[],
    )


def _invalid_threshold_plan() -> RiskPlan:
    return RiskPlan(
        plan_id="plan-1",
        assessment_id="assessment-1",
        package_id="pkg-implementation-section-03",
        layer="implementation",
        step_decisions=[
            StepMitigation(
                step_id="explore-01",
                decision=StepDecision.ACCEPT,
                posture=PostureProfile.P3_GUARDED,
                mitigations=["heavy guardrails"],
                residual_risk=75,
                reason="still risky",
            )
        ],
        accepted_frontier=["explore-01"],
        deferred_steps=[],
        reopen_steps=[],
    )


def _invalid_frontier_plan() -> RiskPlan:
    return RiskPlan(
        plan_id="plan-1",
        assessment_id="assessment-1",
        package_id="pkg-implementation-section-03",
        layer="implementation",
        step_decisions=[
            StepMitigation(
                step_id="explore-01",
                decision=StepDecision.ACCEPT,
                posture=PostureProfile.P1_LIGHT,
                mitigations=["refresh section context"],
                residual_risk=25,
                reason="below threshold",
                route_to="coordination",
            )
        ],
        accepted_frontier=["explore-01"],
        deferred_steps=[],
        reopen_steps=[],
    )


def _write_artifacts(planspace: Path) -> None:
    artifacts = planspace / "artifacts"
    sections = artifacts / "sections"
    proposals = artifacts / "proposals"
    readiness = artifacts / "readiness"
    signals = artifacts / "signals"
    notes = artifacts / "notes"
    sections.mkdir(parents=True, exist_ok=True)
    proposals.mkdir(parents=True, exist_ok=True)
    readiness.mkdir(parents=True, exist_ok=True)
    signals.mkdir(parents=True, exist_ok=True)
    notes.mkdir(parents=True, exist_ok=True)

    (sections / "section-03.md").write_text("Spec body\n", encoding="utf-8")
    (sections / "section-03-proposal-excerpt.md").write_text(
        "Proposal excerpt details\n",
        encoding="utf-8",
    )
    (sections / "section-03-alignment-excerpt.md").write_text(
        "Alignment details\n",
        encoding="utf-8",
    )
    (sections / "section-03-problem-frame.md").write_text(
        "Problem frame details\n",
        encoding="utf-8",
    )
    (proposals / "section-03-microstrategy.md").write_text(
        "- Refresh understanding\n- Verify final state\n",
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
            }
        ),
        encoding="utf-8",
    )
    (readiness / "section-03-execution-ready.json").write_text(
        json.dumps({"ready": True, "blockers": [], "rationale": "ready"}),
        encoding="utf-8",
    )
    (artifacts / "tool-registry.json").write_text(
        json.dumps({"tools": ["pytest"]}),
        encoding="utf-8",
    )
    (artifacts / "codemap.md").write_text("Codemap body\n", encoding="utf-8")
    (signals / "section-03-monitor.json").write_text(
        json.dumps({"signal": "LOOP_DETECTED"}),
        encoding="utf-8",
    )
    (notes / "section-03-consequence.md").write_text(
        "Consequence note\n",
        encoding="utf-8",
    )
