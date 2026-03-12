"""Component tests for ROAL loop orchestration."""

from __future__ import annotations

import json
from pathlib import Path

from dependency_injector import providers

from containers import PromptGuard, Services
from risk.engine import risk_assessor as risk_loop
from risk.prompt import writers as risk_prompt_writers
from risk.repository.history import append_history_entry
from risk.engine.risk_assessor import (
    run_lightweight_risk_check,
    run_risk_loop,
)
from risk.prompt.writers import (
    _collect_roal_evidence,
    write_optimization_prompt,
    write_risk_assessment_prompt,
)
from risk.service.response_parser import (
    parse_risk_assessment,
    parse_risk_plan,
)
from risk.repository.serialization import load_risk_artifact, serialize_assessment, serialize_plan
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


def test_write_risk_assessment_prompt_includes_expected_context(tmp_path: Path) -> None:
    package = _package()
    _write_artifacts(tmp_path)
    append_history_entry(
        tmp_path / "artifacts" / "risk" / "risk-history.jsonl",
        RiskHistoryEntry(
            package_id=package.package_id,
            step_id="explore-01",
            layer="implementation",
            assessment_class=StepClass.EXPLORE,
            posture=PostureProfile.P1_LIGHT,
            predicted_risk=22,
            actual_outcome="success",
            dominant_risks=[RiskType.CONTEXT_ROT],
        ),
    )

    prompt = write_risk_assessment_prompt(package, tmp_path, "section-03")

    assert "Section spec" in prompt
    assert "`" + str(tmp_path / "artifacts" / "sections" / "section-03.md") + "`" in prompt
    assert "Proposal excerpt" in prompt
    assert "## Artifact Paths" in prompt
    assert "Codemap corrections (authoritative overrides)" in prompt
    assert "Monitor signals directory" in prompt
    assert "tool-registry.json" in prompt
    assert "Risk package" in prompt
    assert "`" + str(tmp_path / "artifacts" / "risk" / "section-03-risk-package.json") + "`" in prompt
    assert "Incoming consequence notes" in prompt
    assert "Outgoing consequence notes" in prompt
    assert "from-12-to-03.md" in prompt
    assert "from-03-to-07.md" in prompt
    assert "Impact artifacts" in prompt
    assert "Spec body" not in prompt
    assert "Alignment details" not in prompt
    assert "Problem frame details" not in prompt
    assert "LOOP_DETECTED" not in prompt
    assert '"package_id": "pkg-implementation-section-03"' not in prompt


def test_write_optimization_prompt_includes_assessment_and_parameters(
    tmp_path: Path,
) -> None:
    package = _package()
    assessment = _assessment()
    _write_artifacts(tmp_path)

    prompt = write_optimization_prompt(
        assessment,
        package,
        {"class_thresholds": {"explore": 60, "edit": 45}},
        tmp_path,
        "section-03",
    )

    assert "ROAL Execution Optimization" in prompt
    assert "`" + str(tmp_path / "artifacts" / "risk" / "section-03-risk-assessment.json") + "`" in prompt
    assert "`" + str(tmp_path / "artifacts" / "risk" / "section-03-risk-package.json") + "`" in prompt
    assert "Risk parameters" in prompt
    assert "`" + str(tmp_path / "artifacts" / "risk" / "risk-parameters.json") + "`" in prompt
    assert "tool-registry.json" in prompt
    assert '"edit": 45' not in prompt
    assert '"assessment_id": "assessment-1"' not in prompt
    assert '"package_id": "pkg-implementation-section-03"' not in prompt


def test_write_optimization_prompt_marks_lightweight_single_pass_mode(
    tmp_path: Path,
) -> None:
    _write_artifacts(tmp_path)

    prompt = write_optimization_prompt(
        _assessment(),
        _package(),
        {"class_thresholds": {"explore": 60}},
        tmp_path,
        "section-03",
        lightweight=True,
    )

    assert "## Lightweight Mode" in prompt
    assert "single-pass lightweight risk check" in prompt
    assert "No iteration, repeated reassessment, or horizon refinement is available." in prompt
    assert "standard structured risk plan JSON" in prompt


def test_prompt_builders_do_not_use_inline_json_blocks(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _write_artifacts(tmp_path)
    monkeypatch.setattr(
        risk_prompt_writers,
        "_inline_json_block",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("_inline_json_block should not be used by prompt builders")
        ),
    )

    write_risk_assessment_prompt(_package(), tmp_path, "section-03")
    write_optimization_prompt(
        _assessment(),
        _package(),
        {"class_thresholds": {"explore": 60}},
        tmp_path,
        "section-03",
    )


def test_collect_roal_evidence_includes_reassessment_artifacts(tmp_path: Path) -> None:
    _write_artifacts(tmp_path)
    artifacts = tmp_path / "artifacts"
    input_dir = artifacts / "inputs" / "section-03"
    input_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = input_dir / "section-03-modified-file-manifest.json"
    manifest_path.write_text(json.dumps({"modified_files": ["src/app.py"]}), encoding="utf-8")
    accepted_path = input_dir / "section-03-risk-accepted-steps.json"
    accepted_path.write_text(json.dumps({"accepted_steps": ["edit-02"]}), encoding="utf-8")
    deferred_path = input_dir / "section-03-risk-deferred.json"
    deferred_path.write_text(json.dumps({"deferred_steps": ["verify-03"]}), encoding="utf-8")
    align_path = artifacts / "impl-align-03-output.md"
    align_path.write_text("Aligned\n", encoding="utf-8")
    recon_dir = artifacts / "reconciliation"
    recon_dir.mkdir(parents=True, exist_ok=True)
    recon_path = recon_dir / "section-03-reconciliation.md"
    recon_path.write_text("Reconciled\n", encoding="utf-8")

    evidence = _collect_roal_evidence(risk_loop.PathRegistry(tmp_path), "section-03", "03")

    assert evidence == [
        ("Modified-file manifest", manifest_path),
        ("Alignment check result", align_path),
        ("Reconciliation result", recon_path),
        ("Previous risk artifact", accepted_path),
        ("Previous risk artifact", deferred_path),
    ]


def test_collect_roal_evidence_returns_empty_when_no_artifacts_exist(tmp_path: Path) -> None:
    evidence = _collect_roal_evidence(risk_loop.PathRegistry(tmp_path), "section-03", "03")

    assert evidence == []


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
    calls: list[str] = []

    def _dispatch(*args, **kwargs) -> str:  # noqa: ANN002, ANN003
        calls.append(kwargs["agent_file"])
        if kwargs["agent_file"] == "risk-assessor.md":
            return json.dumps(serialize_assessment(_assessment()))
        return json.dumps(serialize_plan(_valid_plan()))

    plan = run_lightweight_risk_check(
        tmp_path,
        "section-03",
        "implementation",
        package,
        _dispatch,
    )

    assert calls == ["risk-assessor.md", "execution-optimizer.md"]
    assert plan.accepted_frontier == ["explore-01"]
    assert plan.deferred_steps == []
    assert plan.step_decisions[0].posture == PostureProfile.P1_LIGHT


def test_run_lightweight_risk_check_fails_closed_when_optimizer_parse_fails(
    tmp_path: Path,
) -> None:
    package = _package()
    _write_artifacts(tmp_path)
    calls: list[str] = []

    def _dispatch(*args, **kwargs) -> str:  # noqa: ANN002, ANN003
        calls.append(kwargs["agent_file"])
        if kwargs["agent_file"] == "risk-assessor.md":
            return json.dumps(serialize_assessment(_assessment()))
        return "not valid json"

    plan = run_lightweight_risk_check(
        tmp_path,
        "section-03",
        "implementation",
        package,
        _dispatch,
    )

    assert calls == ["risk-assessor.md", "execution-optimizer.md"]
    assert plan.accepted_frontier == []
    assert plan.deferred_steps == ["explore-01"]
    assert plan.reopen_steps == []
    assert plan.step_decisions[0].decision == StepDecision.REJECT_DEFER
    assert plan.step_decisions[0].posture == PostureProfile.P4_REOPEN


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


def test_run_risk_loop_applies_history_adjustment_to_assessment(tmp_path: Path) -> None:
    package = _package()
    _write_artifacts(tmp_path)
    append_history_entry(
        tmp_path / "artifacts" / "risk" / "risk-history.jsonl",
        RiskHistoryEntry(
            package_id="pkg-prev",
            step_id="explore-01",
            layer="implementation",
            assessment_class=StepClass.EXPLORE,
            posture=PostureProfile.P3_GUARDED,
            predicted_risk=5,
            actual_outcome="failure",
            dominant_risks=[RiskType.CONTEXT_ROT],
            blast_radius_band=0,
        ),
    )

    def _dispatch(*args, **kwargs) -> str:  # noqa: ANN002, ANN003
        if kwargs["agent_file"] == "risk-assessor.md":
            return json.dumps(serialize_assessment(_assessment()))
        return json.dumps(serialize_plan(_valid_plan()))

    run_risk_loop(
        tmp_path,
        "section-03",
        "implementation",
        package,
        _dispatch,
    )

    assessment_payload = load_risk_artifact(
        tmp_path / "artifacts" / "risk" / "section-03-risk-assessment.json",
    )

    assert assessment_payload is not None
    assert assessment_payload["package_raw_risk"] > 25


def test_run_risk_loop_applies_posture_hysteresis_after_plan(tmp_path: Path) -> None:
    package = _package()
    _write_artifacts(tmp_path)
    append_history_entry(
        tmp_path / "artifacts" / "risk" / "risk-history.jsonl",
        RiskHistoryEntry(
            package_id="pkg-prev",
            step_id="explore-01",
            layer="implementation",
            assessment_class=StepClass.EXPLORE,
            posture=PostureProfile.P0_DIRECT,
            predicted_risk=20,
            actual_outcome="success",
            dominant_risks=[RiskType.CONTEXT_ROT],
            blast_radius_band=0,
        ),
    )
    assessment = _assessment()
    assessment.package_raw_risk = 85
    assessment.step_assessments[0].raw_risk = 85
    plan = _valid_plan()
    plan.step_decisions[0].posture = PostureProfile.P4_REOPEN
    plan.step_decisions[0].residual_risk = 85

    def _dispatch(*args, **kwargs) -> str:  # noqa: ANN002, ANN003
        if kwargs["agent_file"] == "risk-assessor.md":
            return json.dumps(serialize_assessment(assessment))
        return json.dumps(serialize_plan(plan))

    result = run_risk_loop(
        tmp_path,
        "section-03",
        "implementation",
        package,
        _dispatch,
    )

    assert result.step_decisions[0].posture == PostureProfile.P1_LIGHT


def test_run_risk_loop_calls_prompt_guard_validation(
    tmp_path: Path,
) -> None:
    package = _package()
    _write_artifacts(tmp_path)
    prompt_calls: list[Path] = []

    class _CaptureGuard(PromptGuard):
        def write_validated(self, content, path):
            prompt_calls.append(path)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
            return True

        def validate_dynamic(self, content):
            return []

    Services.prompt_guard.override(providers.Object(_CaptureGuard()))

    def _dispatch(*args, **kwargs) -> str:  # noqa: ANN002, ANN003
        if kwargs["agent_file"] == "risk-assessor.md":
            return json.dumps(serialize_assessment(_assessment()))
        return json.dumps(serialize_plan(_valid_plan()))

    try:
        run_risk_loop(
            tmp_path,
            "section-03",
            "implementation",
            package,
            _dispatch,
        )

        assert prompt_calls == [
            tmp_path / "artifacts" / "risk" / "section-03-risk-assessment-prompt.md",
            tmp_path / "artifacts" / "risk" / "section-03-risk-plan-prompt.md",
        ]
    finally:
        Services.prompt_guard.reset_override()


def test_run_risk_loop_falls_back_when_prompt_guard_fails(
    tmp_path: Path,
) -> None:
    package = _package()
    _write_artifacts(tmp_path)

    class _FailGuard(PromptGuard):
        def write_validated(self, content, path):
            return False

        def validate_dynamic(self, content):
            return []

    Services.prompt_guard.override(providers.Object(_FailGuard()))

    def _dispatch(*args, **kwargs) -> str:  # noqa: ANN002, ANN003
        raise AssertionError("dispatch should not be called when prompt validation fails")

    try:
        plan = run_risk_loop(
            tmp_path,
            "section-03",
            "implementation",
            package,
            _dispatch,
        )

        assert plan.step_decisions[0].posture == PostureProfile.P4_REOPEN
        assert plan.step_decisions[0].decision == StepDecision.REJECT_REOPEN
        assert plan.reopen_steps == ["explore-01"]
    finally:
        Services.prompt_guard.reset_override()


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
                assessment_class=StepClass.EXPLORE,
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
                assessment_class=StepClass.EXPLORE,
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
    inputs = artifacts / "inputs"
    sections.mkdir(parents=True, exist_ok=True)
    proposals.mkdir(parents=True, exist_ok=True)
    readiness.mkdir(parents=True, exist_ok=True)
    signals.mkdir(parents=True, exist_ok=True)
    notes.mkdir(parents=True, exist_ok=True)
    inputs.mkdir(parents=True, exist_ok=True)
    coordination = artifacts / "coordination"
    risk = artifacts / "risk"
    reconciliation = artifacts / "reconciliation"
    coordination.mkdir(parents=True, exist_ok=True)
    risk.mkdir(parents=True, exist_ok=True)
    reconciliation.mkdir(parents=True, exist_ok=True)

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
                "problem_ids": [],
                "pattern_ids": [],
                "profile_id": "",
                "pattern_deviations": [],
                "governance_questions": [],
                "constraint_ids": [],
                "governance_candidate_refs": [],
                "design_decision_refs": [],
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
    (signals / "codemap-corrections.json").write_text(
        json.dumps({"section-03": {"source": "override"}}),
        encoding="utf-8",
    )
    (signals / "section-03-monitor.json").write_text(
        json.dumps({"signal": "LOOP_DETECTED"}),
        encoding="utf-8",
    )
    (risk / "risk-parameters.json").write_text(
        json.dumps({"class_thresholds": {"explore": 60, "edit": 45}}),
        encoding="utf-8",
    )
    (notes / "from-12-to-03.md").write_text(
        "Incoming consequence note\n",
        encoding="utf-8",
    )
    (notes / "from-03-to-07.md").write_text(
        "Outgoing consequence note\n",
        encoding="utf-8",
    )
    (coordination / "section-03-impact.md").write_text(
        "Impact artifact\n",
        encoding="utf-8",
    )
