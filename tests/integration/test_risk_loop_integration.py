from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

from lib.core.artifact_io import read_json, write_json
from lib.pipelines.implementation_pass import (
    _append_risk_history,
    _read_roal_input_index,
    _run_risk_review,
    run_implementation_pass,
)
from lib.risk.history import append_history_entry, read_history
from lib.risk.loop import run_lightweight_risk_check, run_risk_loop
from lib.risk.package_builder import build_package_from_proposal
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
from section_loop.types import ProposalPassResult, Section


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
                step_class=step.step_class,
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
    package = build_package_from_proposal("section-01", planspace)
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

    result = _run_risk_review(planspace, "01", section, mock_dispatch)

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
    package = build_package_from_proposal("section-01", planspace)
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

    result = _run_risk_review(planspace, "01", section, mock_dispatch)

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
    package = build_package_from_proposal("section-01", planspace)
    mock_dispatch.return_value = "not valid json"

    result = _run_risk_review(planspace, "01", section, mock_dispatch)

    assert result is not None
    assert result.accepted_frontier == []
    assert result.reopen_steps == [package.steps[0].step_id]
    assert result.step_decisions[0].posture == PostureProfile.P4_REOPEN


def test_risk_loop_respects_threshold_enforcement(
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
    package = build_package_from_proposal("section-01", planspace)
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

    result = _run_risk_review(planspace, "01", section, mock_dispatch)

    assert result is not None
    assert result.accepted_frontier == []
    assert result.deferred_steps == [package.steps[0].step_id]
    assert result.step_decisions[0].decision == StepDecision.REJECT_DEFER


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
    first_package = build_package_from_proposal("section-01", planspace)
    second_package = build_package_from_proposal("section-02", planspace)
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

    first_result = _run_risk_review(planspace, "01", first_section, mock_dispatch)
    second_result = _run_risk_review(planspace, "02", second_section, mock_dispatch)
    assert first_result is not None
    assert second_result is not None

    _append_risk_history(planspace, "01", first_result, ["src/main.py"])
    _append_risk_history(planspace, "02", second_result, ["src/utils.py"])
    history = read_history(planspace / "artifacts" / "risk" / "risk-history.jsonl")

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
    package = build_package_from_proposal("section-01", planspace)
    assessment = _assessment_for(package, raw_risks=[20])
    mock_dispatch.return_value = json.dumps(serialize_assessment(assessment))

    plan = run_lightweight_risk_check(
        planspace,
        "section-01",
        "implementation",
        package,
        mock_dispatch,
    )

    assert plan.accepted_frontier == [package.steps[0].step_id]
    assert plan.deferred_steps == []
    assert [call.kwargs["agent_file"] for call in mock_dispatch.call_args_list] == [
        "risk-assessor.md",
    ]


def test_full_adaptive_cycle_relaxes_only_one_posture_level(
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
                step_class=StepClass.EXPLORE,
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
                step_class=package.steps[0].step_class,
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
                step_class=package.steps[0].step_class,
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
                step_class=package.steps[0].step_class,
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

    first_plan = run_risk_loop(
        planspace,
        "section-01",
        "implementation",
        package,
        _dispatch,
    )
    _append_risk_history(planspace, "01", first_plan, ["src/main.py"])
    second_plan = run_risk_loop(
        planspace,
        "section-01",
        "implementation",
        package,
        _dispatch,
    )

    assert first_plan.step_decisions[0].posture == PostureProfile.P3_GUARDED
    assert second_plan.step_decisions[0].posture == PostureProfile.P2_STANDARD


def test_reassessment_executes_newly_accepted_frontier_end_to_end(
    planspace: Path,
    codespace: Path,
    monkeypatch,
) -> None:
    _write_risk_inputs(
        planspace,
        "01",
        triage_confidence="medium",
        microstrategy_lines=["Apply the approved change", "Verify the change"],
    )
    section = _make_section(planspace, "01")
    package = build_package_from_proposal("section-01", planspace)
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

    monkeypatch.setattr("lib.pipelines.implementation_pass.handle_pending_messages", lambda *args: False)
    monkeypatch.setattr("lib.pipelines.implementation_pass.alignment_changed_pending", lambda *args: False)
    monkeypatch.setattr("lib.pipelines.implementation_pass._check_and_clear_alignment_changed", lambda *args: False)
    monkeypatch.setattr("lib.pipelines.implementation_pass.resolve_readiness", lambda *_args, **_kwargs: {"ready": True})
    monkeypatch.setattr("lib.pipelines.implementation_pass._run_risk_review", lambda *_args, **_kwargs: initial_plan)
    monkeypatch.setattr("lib.pipelines.implementation_pass._append_risk_history", lambda *args, **kwargs: None)
    monkeypatch.setattr("lib.pipelines.implementation_pass.read_package", lambda *_args, **_kwargs: package)
    monkeypatch.setattr("lib.pipelines.implementation_pass._section_inputs_hash", lambda *args: "hash-123")
    monkeypatch.setattr("lib.pipelines.implementation_pass.mailbox_send", lambda *_args, **_kwargs: None)
    monkeypatch.setattr("lib.pipelines.implementation_pass.subprocess.run", lambda *args, **kwargs: None)

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

    def _reassess(*args, **kwargs) -> RiskPlan:
        reassessment_packages.append([step.step_id for step in args[3].steps])
        return reassessed_plan

    monkeypatch.setattr("lib.pipelines.implementation_pass.run_section", _run_section)
    monkeypatch.setattr("lib.pipelines.implementation_pass.run_risk_loop", _reassess)

    results = run_implementation_pass(
        {"01": ProposalPassResult(section_number="01", execution_ready=True)},
        {"01": section},
        planspace,
        codespace,
        "parent",
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
