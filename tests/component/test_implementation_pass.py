from pathlib import Path
from typing import Callable

import pytest

from signals.repository.artifact_io import read_json, write_json
from implementation.engine.implementation_phase import (
    ImplementationPassExit,
    ImplementationPassRestart,
    run_implementation_pass,
)
from implementation.repository.roal_index import read_roal_input_index
from implementation.service.risk_history import append_risk_history
from risk.repository.history import read_history
from risk.repository.serialization import serialize_assessment, serialize_package
from risk.types import (
    PackageStep,
    PostureProfile,
    RiskAssessment,
    RiskPackage,
    RiskModifiers,
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

def _make_section(planspace: Path, number: str) -> Section:
    path = planspace / "artifacts" / "sections" / f"section-{number}.md"
    path.write_text(f"# Section {number}\n", encoding="utf-8")
    return Section(number=number, path=path, related_files=["src/app.py"])

def _plan(
    *,
    accepted_frontier: list[str],
    deferred_steps: list[str] | None = None,
    reopen_steps: list[str] | None = None,
    step_decisions: list[StepMitigation] | None = None,
) -> RiskPlan:
    return RiskPlan(
        plan_id="plan-01",
        assessment_id="assessment-01",
        package_id="pkg-implementation-section-01",
        layer="implementation",
        step_decisions=step_decisions or [],
        accepted_frontier=accepted_frontier,
        deferred_steps=deferred_steps or [],
        reopen_steps=reopen_steps or [],
        expected_reassessment_inputs=["modified-file-manifest", "alignment-check-result"],
    )

def _write_risk_context(planspace: Path, sec_num: str) -> None:
    risk_dir = planspace / "artifacts" / "risk"
    risk_dir.mkdir(parents=True, exist_ok=True)
    package = serialize_package(
        RiskPackage(
            package_id=f"pkg-implementation-section-{sec_num}",
            layer="implementation",
            scope=f"section-{sec_num}",
            origin_problem_id=f"problem-{sec_num}",
            origin_source="proposal",
            steps=[
                PackageStep(
                    step_id="edit-01",
                    assessment_class=StepClass.EDIT,
                    summary="Apply the change",
                ),
                PackageStep(
                    step_id="verify-02",
                    assessment_class=StepClass.VERIFY,
                    summary="Verify the change",
                    prerequisites=["edit-01"],
                ),
                PackageStep(
                    step_id="coord-03",
                    assessment_class=StepClass.COORDINATE,
                    summary="Coordinate fallout",
                    prerequisites=["verify-02"],
                ),
            ],
        ),
    )
    assessment = serialize_assessment(
        RiskAssessment(
            assessment_id=f"assessment-{sec_num}",
            layer="implementation",
            package_id=f"pkg-implementation-section-{sec_num}",
            assessment_scope=f"section-{sec_num}",
            understanding_inventory=UnderstandingInventory(
                confirmed=["proposal reviewed"],
                assumed=[],
                missing=[],
                stale=[],
            ),
            package_raw_risk=55,
            assessment_confidence=0.8,
            dominant_risks=[RiskType.BRUTE_FORCE_REGRESSION],
            step_assessments=[
                StepAssessment(
                    step_id="edit-01",
                    assessment_class=StepClass.EDIT,
                    summary="Apply the change",
                    prerequisites=[],
                    risk_vector=RiskVector(brute_force_regression=2),
                    modifiers=RiskModifiers(blast_radius=1, confidence=0.8),
                    raw_risk=35,
                    dominant_risks=[RiskType.BRUTE_FORCE_REGRESSION],
                ),
                StepAssessment(
                    step_id="verify-02",
                    assessment_class=StepClass.VERIFY,
                    summary="Verify the change",
                    prerequisites=["edit-01"],
                    risk_vector=RiskVector(context_rot=2),
                    modifiers=RiskModifiers(blast_radius=1, confidence=0.8),
                    raw_risk=40,
                    dominant_risks=[RiskType.CONTEXT_ROT],
                ),
                StepAssessment(
                    step_id="coord-03",
                    assessment_class=StepClass.COORDINATE,
                    summary="Coordinate fallout",
                    prerequisites=["verify-02"],
                    risk_vector=RiskVector(cross_section_incoherence=3),
                    modifiers=RiskModifiers(blast_radius=2, confidence=0.7),
                    raw_risk=70,
                    dominant_risks=[RiskType.CROSS_SECTION_INCOHERENCE],
                ),
            ],
            frontier_candidates=["edit-01", "verify-02", "coord-03"],
            reopen_recommendations=[],
            notes=[],
        ),
    )
    write_json(
        risk_dir / f"section-{sec_num}-risk-package.json",
        package,
    )
    write_json(
        risk_dir / f"section-{sec_num}-risk-assessment.json",
        assessment,
    )

def _patch_implementation_pass_basics(
    monkeypatch: pytest.MonkeyPatch,
    *,
    risk_plan: RiskPlan | None,
    run_section_fn: Callable[..., list[str] | None],
    reassess_fn: Callable[..., RiskPlan | None] | None = None,
    append_history_fn: Callable[..., None] | None = None,
    alignment_checks: list[bool] | None = None,
) -> None:
    if alignment_checks is None:
        monkeypatch.setattr(
            "implementation.engine.implementation_phase._check_and_clear_alignment_changed",
            lambda *args: False,
        )
    else:
        remaining = list(alignment_checks)

        def _check_alignment(*_args) -> bool:
            if remaining:
                return remaining.pop(0)
            return False

        monkeypatch.setattr(
            "implementation.engine.implementation_phase._check_and_clear_alignment_changed",
            _check_alignment,
        )
    monkeypatch.setattr(
        "implementation.engine.implementation_phase.resolve_readiness",
        lambda *_args, **_kwargs: {"ready": True},
    )
    monkeypatch.setattr(
        "implementation.engine.implementation_phase._run_risk_review",
        lambda *_args, **_kwargs: risk_plan,
    )
    monkeypatch.setattr(
        "implementation.engine.implementation_phase.run_section",
        run_section_fn,
    )
    monkeypatch.setattr(
        "implementation.engine.implementation_phase.subprocess.run",
        lambda *args, **kwargs: None,
    )
    if reassess_fn is not None:
        monkeypatch.setattr(
            "implementation.engine.implementation_phase._maybe_reassess_deferred_steps",
            reassess_fn,
        )
    if append_history_fn is not None:
        monkeypatch.setattr(
            "implementation.engine.implementation_phase.append_risk_history",
            append_history_fn,
        )

def test_run_implementation_pass_records_results_and_hashes(
    planspace: Path, codespace: Path, monkeypatch: pytest.MonkeyPatch,
    capturing_communicator, noop_pipeline_control) -> None:
    section = _make_section(planspace, "01")

    noop_pipeline_control.section_inputs_hash = lambda *args: "hash-123"
    monkeypatch.setattr(
        "implementation.engine.implementation_phase._check_and_clear_alignment_changed",
        lambda *args: False,
    )
    monkeypatch.setattr(
        "implementation.engine.implementation_phase.resolve_readiness",
        lambda *_args, **_kwargs: {"ready": True},
    )
    monkeypatch.setattr(
        "implementation.engine.implementation_phase._run_risk_review",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        "implementation.engine.implementation_phase.run_section",
        lambda *args, **kwargs: ["src/app.py"],
    )
    monkeypatch.setattr(
        "implementation.engine.implementation_phase.subprocess.run",
        lambda *args, **kwargs: None,
    )

    results = run_implementation_pass(
        {"01": ProposalPassResult(section_number="01", execution_ready=True)},
        {"01": section},
        planspace,
        codespace,
        "parent",
    )

    assert results["01"].modified_files == ["src/app.py"]
    assert capturing_communicator.messages == ["done:01:1 files modified"]
    assert (planspace / "artifacts" / "section-inputs-hashes" / "01.hash").read_text(
        encoding="utf-8",
    ) == "hash-123"
    assert (planspace / "artifacts" / "phase2-inputs-hashes" / "01.hash").read_text(
        encoding="utf-8",
    ) == "hash-123"

def test_run_implementation_pass_writes_accepted_steps_artifact(
    planspace: Path, codespace: Path, monkeypatch: pytest.MonkeyPatch,
    noop_communicator, noop_pipeline_control) -> None:
    section = _make_section(planspace, "01")
    plan = _plan(
        accepted_frontier=["explore-01", "edit-02"],
        step_decisions=[
            StepMitigation(
                step_id="explore-01",
                decision=StepDecision.ACCEPT,
                posture=PostureProfile.P1_LIGHT,
                mitigations=["refresh context"],
                residual_risk=15,
            ),
            StepMitigation(
                step_id="edit-02",
                decision=StepDecision.ACCEPT,
                posture=PostureProfile.P2_STANDARD,
                mitigations=["alignment check after edit", "monitor on multi-file work"],
                residual_risk=25,
                dispatch_shape={"chain": ["implementation-strategist", "alignment-judge"]},
            ),
        ],
    )
    monkeypatch.setattr("implementation.engine.implementation_phase._check_and_clear_alignment_changed", lambda *args: False)
    monkeypatch.setattr("implementation.engine.implementation_phase.resolve_readiness", lambda *_args, **_kwargs: {"ready": True})
    monkeypatch.setattr("implementation.engine.implementation_phase._run_risk_review", lambda *_args, **_kwargs: plan)
    monkeypatch.setattr("implementation.engine.implementation_phase.run_section", lambda *args, **kwargs: ["src/app.py"])
    monkeypatch.setattr("implementation.engine.implementation_phase.subprocess.run", lambda *args, **kwargs: None)
    monkeypatch.setattr("implementation.engine.implementation_phase.append_risk_history", lambda *args, **kwargs: None)

    run_implementation_pass(
        {"01": ProposalPassResult(section_number="01", execution_ready=True)},
        {"01": section},
        planspace,
        codespace,
        "parent",
    )

    accepted_path = (
        planspace
        / "artifacts"
        / "inputs"
        / "section-01"
        / "section-01-risk-accepted-steps.json"
    )
    accepted = read_json(accepted_path)
    assert accepted is not None
    assert accepted["accepted_steps"] == ["explore-01", "edit-02"]
    assert accepted["posture"] == "P2"
    assert accepted["mitigations"] == [
        "refresh context",
        "alignment check after edit",
        "monitor on multi-file work",
    ]
    assert accepted["dispatch_shape"] == {
        "edit-02": {
            "chain": ["implementation-strategist", "alignment-judge"],
        }
    }
    assert accepted["dispatch_shapes"] == accepted["dispatch_shape"]
    ref_path = accepted_path.with_suffix(".ref")
    assert ref_path.read_text(encoding="utf-8").strip() == str(accepted_path.resolve())
    assert read_roal_input_index(planspace, "01") == [
        {
            "kind": "accepted_frontier",
            "path": str(accepted_path),
            "produced_by": "implementation_pass",
        },
    ]

def test_run_implementation_pass_writes_deferred_steps_artifact(
    planspace: Path, codespace: Path, monkeypatch: pytest.MonkeyPatch,
    noop_communicator, noop_pipeline_control) -> None:
    section = _make_section(planspace, "01")
    plan = _plan(
        accepted_frontier=["edit-02"],
        deferred_steps=["verify-03"],
        step_decisions=[
            StepMitigation(
                step_id="edit-02",
                decision=StepDecision.ACCEPT,
                posture=PostureProfile.P2_STANDARD,
                mitigations=["bounded edit"],
            ),
            StepMitigation(
                step_id="verify-03",
                decision=StepDecision.REJECT_DEFER,
                posture=PostureProfile.P3_GUARDED,
                wait_for=["edit-02 output", "alignment-check-result"],
                reason="verify after edit artifacts land",
            ),
        ],
    )
    monkeypatch.setattr("implementation.engine.implementation_phase._check_and_clear_alignment_changed", lambda *args: False)
    monkeypatch.setattr("implementation.engine.implementation_phase.resolve_readiness", lambda *_args, **_kwargs: {"ready": True})
    monkeypatch.setattr("implementation.engine.implementation_phase._run_risk_review", lambda *_args, **_kwargs: plan)
    monkeypatch.setattr("implementation.engine.implementation_phase.run_section", lambda *args, **kwargs: ["src/app.py"])
    monkeypatch.setattr("implementation.engine.implementation_phase.subprocess.run", lambda *args, **kwargs: None)
    monkeypatch.setattr("implementation.engine.implementation_phase.append_risk_history", lambda *args, **kwargs: None)

    run_implementation_pass(
        {"01": ProposalPassResult(section_number="01", execution_ready=True)},
        {"01": section},
        planspace,
        codespace,
        "parent",
    )

    deferred = read_json(
        planspace
        / "artifacts"
        / "inputs"
        / "section-01"
        / "section-01-risk-deferred.json",
    )
    assert deferred == {
        "deferred_steps": ["verify-03"],
        "wait_for": ["edit-02 output", "alignment-check-result"],
        "reassessment_inputs": [
            "modified-file-manifest",
            "alignment-check-result",
        ],
    }
    assert read_roal_input_index(planspace, "01") == [
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
        {
            "kind": "deferred",
            "path": str(
                planspace
                / "artifacts"
                / "inputs"
                / "section-01"
                / "section-01-risk-deferred.json"
            ),
            "produced_by": "implementation_pass",
        },
    ]

def test_run_implementation_pass_writes_reopen_blocker_and_skips(
    planspace: Path, codespace: Path, monkeypatch: pytest.MonkeyPatch,
    noop_communicator, noop_pipeline_control) -> None:
    section = _make_section(planspace, "01")
    run_calls: list[str] = []
    plan = _plan(
        accepted_frontier=[],
        reopen_steps=["coordinate-04"],
        step_decisions=[
            StepMitigation(
                step_id="coordinate-04",
                decision=StepDecision.REJECT_REOPEN,
                posture=PostureProfile.P4_REOPEN,
                reason="cross-section incoherence requires reconciliation before local execution",
                route_to="coordination",
            ),
        ],
    )
    monkeypatch.setattr("implementation.engine.implementation_phase._check_and_clear_alignment_changed", lambda *args: False)
    monkeypatch.setattr("implementation.engine.implementation_phase.resolve_readiness", lambda *_args, **_kwargs: {"ready": True})
    monkeypatch.setattr("implementation.engine.implementation_phase._run_risk_review", lambda *_args, **_kwargs: plan)
    monkeypatch.setattr(
        "implementation.engine.implementation_phase.run_section",
        lambda *args, **kwargs: run_calls.append("run") or ["src/app.py"],
    )
    monkeypatch.setattr("implementation.engine.implementation_phase.subprocess.run", lambda *args, **kwargs: None)

    results = run_implementation_pass(
        {"01": ProposalPassResult(section_number="01", execution_ready=True)},
        {"01": section},
        planspace,
        codespace,
        "parent",
    )

    blocker = read_json(planspace / "artifacts" / "signals" / "section-01-blocker.json")
    assert read_roal_input_index(planspace, "01") == [
        {
            "kind": "reopen",
            "path": str(
                planspace
                / "artifacts"
                / "signals"
                / "section-01-blocker.json"
            ),
            "produced_by": "implementation_pass",
        },
    ]
    assert blocker == {
        "state": "needs_parent",
        "blocker_type": "risk_reopen",
        "source": "roal",
        "section": "01",
        "scope": "section-01",
        "steps": ["coordinate-04"],
        "route_to": "coordination",
        "reason": "cross-section incoherence requires reconciliation before local execution",
        "detail": "cross-section incoherence requires reconciliation before local execution",
        "why_blocked": "cross-section incoherence requires reconciliation before local execution",
        "needs": "Resolve reopened ROAL steps before continuing local execution",
    }
    assert results == {}
    assert run_calls == []

def test_run_implementation_pass_fail_closed_on_roal_failure(
    planspace: Path, codespace: Path, monkeypatch: pytest.MonkeyPatch,
    noop_communicator, noop_pipeline_control) -> None:
    section = _make_section(planspace, "01")
    run_calls: list[str] = []
    monkeypatch.setattr("implementation.engine.implementation_phase._check_and_clear_alignment_changed", lambda *args: False)
    monkeypatch.setattr("implementation.engine.implementation_phase.resolve_readiness", lambda *_args, **_kwargs: {"ready": True})
    monkeypatch.setattr(
        "implementation.engine.implementation_phase._run_risk_review",
        lambda *_args, **_kwargs: _plan(accepted_frontier=[]),
    )
    monkeypatch.setattr(
        "implementation.engine.implementation_phase.run_section",
        lambda *args, **kwargs: run_calls.append("run") or ["src/app.py"],
    )
    monkeypatch.setattr("implementation.engine.implementation_phase.subprocess.run", lambda *args, **kwargs: None)

    results = run_implementation_pass(
        {"01": ProposalPassResult(section_number="01", execution_ready=True)},
        {"01": section},
        planspace,
        codespace,
        "parent",
    )

    assert results == {}
    assert run_calls == []

def test_run_implementation_pass_skip_mode_proceeds_without_risk_artifacts(
    planspace: Path, codespace: Path, monkeypatch: pytest.MonkeyPatch,
    noop_communicator, noop_pipeline_control) -> None:
    section = _make_section(planspace, "01")
    monkeypatch.setattr("implementation.engine.implementation_phase._check_and_clear_alignment_changed", lambda *args: False)
    monkeypatch.setattr("implementation.engine.implementation_phase.resolve_readiness", lambda *_args, **_kwargs: {"ready": True})
    monkeypatch.setattr("implementation.engine.implementation_phase._run_risk_review", lambda *_args, **_kwargs: None)
    monkeypatch.setattr("implementation.engine.implementation_phase.run_section", lambda *args, **kwargs: ["src/app.py"])
    monkeypatch.setattr("implementation.engine.implementation_phase.subprocess.run", lambda *args, **kwargs: None)

    results = run_implementation_pass(
        {"01": ProposalPassResult(section_number="01", execution_ready=True)},
        {"01": section},
        planspace,
        codespace,
        "parent",
    )

    assert results["01"].modified_files == ["src/app.py"]
    assert read_roal_input_index(planspace, "01") == []
    assert not (planspace / "artifacts" / "signals" / "section-01-blocker.json").exists()

def test_run_implementation_pass_restarts_on_alignment_change(
    planspace: Path, codespace: Path, monkeypatch: pytest.MonkeyPatch,
    noop_communicator, capturing_pipeline_control) -> None:
    section = _make_section(planspace, "01")
    capturing_pipeline_control._alignment_changed_return = True
    monkeypatch.setattr(
        "implementation.engine.implementation_phase._check_and_clear_alignment_changed",
        lambda *args: True,
    )

    with pytest.raises(ImplementationPassRestart):
        run_implementation_pass(
            {"01": ProposalPassResult(section_number="01", execution_ready=True)},
            {"01": section},
            planspace,
            codespace,
            "parent",
        )

def testappend_risk_history_records_enriched_outcomes(planspace: Path) -> None:
    _write_risk_context(planspace, "01")
    plan = RiskPlan(
        plan_id="plan-01",
        assessment_id="assessment-01",
        package_id="pkg-implementation-section-01",
        layer="implementation",
        step_decisions=[
            StepMitigation(
                step_id="edit-01",
                decision=StepDecision.ACCEPT,
                posture=PostureProfile.P2_STANDARD,
                residual_risk=35,
                reason="safe enough",
            ),
            StepMitigation(
                step_id="verify-02",
                decision=StepDecision.REJECT_DEFER,
                posture=PostureProfile.P3_GUARDED,
                residual_risk=40,
                reason="wait for implementation artifacts",
                wait_for=["modified-file-manifest"],
            ),
            StepMitigation(
                step_id="coord-03",
                decision=StepDecision.REJECT_REOPEN,
                posture=PostureProfile.P4_REOPEN,
                residual_risk=70,
                reason="needs parent coordination",
                route_to="coordination",
            ),
        ],
        accepted_frontier=["edit-01"],
        deferred_steps=["verify-02"],
        reopen_steps=["coord-03"],
        expected_reassessment_inputs=["alignment-check-result"],
    )

    append_risk_history(
        planspace,
        "01",
        plan,
        None,
        implementation_failed=True,
    )

    history = read_history(planspace / "artifacts" / "risk" / "risk-history.jsonl")

    assert {entry.step_id: entry.actual_outcome for entry in history} == {
        "edit-01": "failure",
        "verify-02": "deferred",
        "coord-03": "reopened",
    }

def test_run_implementation_pass_dispatches_reassessed_frontier_slice(
    planspace: Path, codespace: Path, monkeypatch: pytest.MonkeyPatch,
    noop_communicator, noop_pipeline_control) -> None:
    section = _make_section(planspace, "01")
    initial_plan = _plan(
        accepted_frontier=["edit-01"],
        deferred_steps=["verify-02"],
        step_decisions=[
            StepMitigation(
                step_id="edit-01",
                decision=StepDecision.ACCEPT,
                posture=PostureProfile.P2_STANDARD,
                residual_risk=30,
            ),
            StepMitigation(
                step_id="verify-02",
                decision=StepDecision.REJECT_DEFER,
                posture=PostureProfile.P3_GUARDED,
                residual_risk=55,
                wait_for=["modified-file-manifest", "alignment-check-result"],
            ),
        ],
    )
    reassessed_plan = _plan(
        accepted_frontier=["verify-02"],
        step_decisions=[
            StepMitigation(
                step_id="verify-02",
                decision=StepDecision.ACCEPT,
                posture=PostureProfile.P2_STANDARD,
                residual_risk=35,
            ),
        ],
    )
    run_calls: list[list[str]] = []
    reassess_calls: list[str] = []
    history_calls: list[tuple[str, list[str] | None, bool]] = []

    def _run_section(*_args, **_kwargs) -> list[str]:
        files = (
            ["src/app.py"]
            if not run_calls
            else ["tests/app_test.py"]
        )
        run_calls.append(files)
        return files

    def _reassess(*_args, **_kwargs) -> RiskPlan | None:
        reassess_calls.append("reassess")
        return reassessed_plan if len(reassess_calls) == 1 else None

    def _append_history(_planspace, _sec_num, plan, modified, *, implementation_failed=False) -> None:
        history_calls.append(
            (plan.plan_id, list(modified) if modified is not None else None, implementation_failed),
        )

    _patch_implementation_pass_basics(
        monkeypatch,
        risk_plan=initial_plan,
        run_section_fn=_run_section,
        reassess_fn=_reassess,
        append_history_fn=_append_history,
    )

    results = run_implementation_pass(
        {"01": ProposalPassResult(section_number="01", execution_ready=True)},
        {"01": section},
        planspace,
        codespace,
        "parent",
    )

    assert reassess_calls == ["reassess"]
    assert run_calls == [["src/app.py"], ["tests/app_test.py"]]
    assert history_calls == [
        ("plan-01", ["src/app.py"], False),
        ("plan-01", ["tests/app_test.py"], False),
    ]
    assert results["01"].aligned is True
    assert results["01"].modified_files == ["src/app.py", "tests/app_test.py"]
    assert read_roal_input_index(planspace, "01") == [
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

def test_run_implementation_pass_bounds_frontier_iterations(
    planspace: Path, codespace: Path, monkeypatch: pytest.MonkeyPatch,
    noop_communicator, noop_pipeline_control) -> None:
    section = _make_section(planspace, "01")
    initial_plan = _plan(
        accepted_frontier=["edit-01"],
        deferred_steps=["verify-02"],
        step_decisions=[
            StepMitigation(step_id="edit-01", decision=StepDecision.ACCEPT),
            StepMitigation(step_id="verify-02", decision=StepDecision.REJECT_DEFER),
        ],
    )
    frontier_plans = [
        _plan(
            accepted_frontier=["verify-02"],
            deferred_steps=["coord-03"],
            step_decisions=[
                StepMitigation(step_id="verify-02", decision=StepDecision.ACCEPT),
                StepMitigation(step_id="coord-03", decision=StepDecision.REJECT_DEFER),
            ],
        ),
        _plan(
            accepted_frontier=["coord-03"],
            deferred_steps=["audit-04"],
            step_decisions=[
                StepMitigation(step_id="coord-03", decision=StepDecision.ACCEPT),
                StepMitigation(step_id="audit-04", decision=StepDecision.REJECT_DEFER),
            ],
        ),
        _plan(
            accepted_frontier=["audit-04"],
            deferred_steps=["wrap-05"],
            step_decisions=[
                StepMitigation(step_id="audit-04", decision=StepDecision.ACCEPT),
                StepMitigation(step_id="wrap-05", decision=StepDecision.REJECT_DEFER),
            ],
        ),
        _plan(
            accepted_frontier=["wrap-05"],
            deferred_steps=["tail-06"],
            step_decisions=[
                StepMitigation(step_id="wrap-05", decision=StepDecision.ACCEPT),
                StepMitigation(step_id="tail-06", decision=StepDecision.REJECT_DEFER),
            ],
        ),
    ]
    run_calls: list[list[str]] = []
    history_calls: list[list[str] | None] = []
    reassess_count = 0

    def _run_section(*_args, **_kwargs) -> list[str]:
        files = [f"src/step-{len(run_calls) + 1}.py"]
        run_calls.append(files)
        return files

    def _reassess(*_args, **_kwargs) -> RiskPlan | None:
        nonlocal reassess_count
        plan = frontier_plans[reassess_count]
        reassess_count += 1
        return plan

    def _append_history(_planspace, _sec_num, _plan, modified, *, implementation_failed=False) -> None:
        assert implementation_failed is False
        history_calls.append(list(modified) if modified is not None else None)

    _patch_implementation_pass_basics(
        monkeypatch,
        risk_plan=initial_plan,
        run_section_fn=_run_section,
        reassess_fn=_reassess,
        append_history_fn=_append_history,
    )

    results = run_implementation_pass(
        {"01": ProposalPassResult(section_number="01", execution_ready=True)},
        {"01": section},
        planspace,
        codespace,
        "parent",
    )

    assert reassess_count == 3
    assert run_calls == [
        ["src/step-1.py"],
        ["src/step-2.py"],
        ["src/step-3.py"],
        ["src/step-4.py"],
    ]
    assert history_calls == [
        ["src/step-1.py"],
        ["src/step-2.py"],
        ["src/step-3.py"],
        ["src/step-4.py"],
    ]
    assert results["01"].aligned is False
    assert results["01"].problems == (
        "ROAL deferred steps remain after bounded frontier execution: wrap-05"
    )
    assert results["01"].modified_files == [
        "src/step-1.py",
        "src/step-2.py",
        "src/step-3.py",
        "src/step-4.py",
    ]

def test_run_implementation_pass_stops_when_reassessment_accepts_nothing(
    planspace: Path, codespace: Path, monkeypatch: pytest.MonkeyPatch,
    noop_communicator, noop_pipeline_control) -> None:
    section = _make_section(planspace, "01")
    initial_plan = _plan(
        accepted_frontier=["edit-01"],
        deferred_steps=["verify-02"],
        step_decisions=[
            StepMitigation(step_id="edit-01", decision=StepDecision.ACCEPT),
            StepMitigation(step_id="verify-02", decision=StepDecision.REJECT_DEFER),
        ],
    )
    deferred_only_plan = _plan(
        accepted_frontier=[],
        deferred_steps=["verify-02"],
        step_decisions=[
            StepMitigation(step_id="verify-02", decision=StepDecision.REJECT_DEFER),
        ],
    )
    run_calls: list[list[str]] = []

    def _run_section(*_args, **_kwargs) -> list[str]:
        files = ["src/app.py"]
        run_calls.append(files)
        return files

    _patch_implementation_pass_basics(
        monkeypatch,
        risk_plan=initial_plan,
        run_section_fn=_run_section,
        reassess_fn=lambda *_args, **_kwargs: deferred_only_plan,
        append_history_fn=lambda *args, **kwargs: None,
    )

    results = run_implementation_pass(
        {"01": ProposalPassResult(section_number="01", execution_ready=True)},
        {"01": section},
        planspace,
        codespace,
        "parent",
    )

    assert run_calls == [["src/app.py"]]
    assert results["01"].aligned is False
    assert results["01"].problems == "ROAL deferred steps remain: verify-02"

def test_run_implementation_pass_stops_on_reopen_outcome(
    planspace: Path, codespace: Path, monkeypatch: pytest.MonkeyPatch,
    noop_communicator, noop_pipeline_control) -> None:
    section = _make_section(planspace, "01")
    initial_plan = _plan(
        accepted_frontier=["edit-01"],
        deferred_steps=["verify-02"],
        step_decisions=[
            StepMitigation(step_id="edit-01", decision=StepDecision.ACCEPT),
            StepMitigation(step_id="verify-02", decision=StepDecision.REJECT_DEFER),
        ],
    )
    reopened_plan = _plan(
        accepted_frontier=["verify-02"],
        reopen_steps=["coord-03"],
        step_decisions=[
            StepMitigation(step_id="verify-02", decision=StepDecision.ACCEPT),
            StepMitigation(
                step_id="coord-03",
                decision=StepDecision.REJECT_REOPEN,
                reason="needs parent coordination",
                route_to="coordination",
            ),
        ],
    )
    run_calls: list[list[str]] = []

    def _run_section(*_args, **_kwargs) -> list[str]:
        files = [f"src/step-{len(run_calls) + 1}.py"]
        run_calls.append(files)
        return files

    _patch_implementation_pass_basics(
        monkeypatch,
        risk_plan=initial_plan,
        run_section_fn=_run_section,
        reassess_fn=lambda *_args, **_kwargs: reopened_plan,
        append_history_fn=lambda *args, **kwargs: None,
    )

    results = run_implementation_pass(
        {"01": ProposalPassResult(section_number="01", execution_ready=True)},
        {"01": section},
        planspace,
        codespace,
        "parent",
    )

    assert run_calls == [["src/step-1.py"], ["src/step-2.py"]]
    assert results["01"].aligned is False
    assert results["01"].problems == "needs parent coordination"
    assert read_json(planspace / "artifacts" / "signals" / "section-01-blocker.json") == {
        "state": "needs_parent",
        "blocker_type": "risk_reopen",
        "source": "roal",
        "section": "01",
        "scope": "section-01",
        "steps": ["coord-03"],
        "route_to": "coordination",
        "reason": "needs parent coordination",
        "detail": "needs parent coordination",
        "why_blocked": "needs parent coordination",
        "needs": "Resolve reopened ROAL steps before continuing local execution",
    }

def test_run_implementation_pass_marks_frontier_failure_in_section_result(
    planspace: Path, codespace: Path, monkeypatch: pytest.MonkeyPatch,
    noop_communicator, noop_pipeline_control) -> None:
    section = _make_section(planspace, "01")
    initial_plan = _plan(
        accepted_frontier=["edit-01"],
        deferred_steps=["verify-02"],
        step_decisions=[
            StepMitigation(step_id="edit-01", decision=StepDecision.ACCEPT),
            StepMitigation(step_id="verify-02", decision=StepDecision.REJECT_DEFER),
        ],
    )
    frontier_plan = _plan(
        accepted_frontier=["verify-02"],
        step_decisions=[
            StepMitigation(step_id="verify-02", decision=StepDecision.ACCEPT),
        ],
    )
    history_calls: list[tuple[list[str] | None, bool]] = []
    run_results = iter([["src/app.py"], None])

    def _run_section(*_args, **_kwargs) -> list[str] | None:
        return next(run_results)

    def _append_history(_planspace, _sec_num, _plan, modified, *, implementation_failed=False) -> None:
        history_calls.append(
            (list(modified) if modified is not None else None, implementation_failed),
        )

    _patch_implementation_pass_basics(
        monkeypatch,
        risk_plan=initial_plan,
        run_section_fn=_run_section,
        reassess_fn=lambda *_args, **_kwargs: frontier_plan,
        append_history_fn=_append_history,
    )

    results = run_implementation_pass(
        {"01": ProposalPassResult(section_number="01", execution_ready=True)},
        {"01": section},
        planspace,
        codespace,
        "parent",
    )

    assert history_calls == [
        (["src/app.py"], False),
        (None, True),
    ]
    assert results["01"].aligned is False
    assert results["01"].problems == "deferred frontier execution failed"
    assert results["01"].modified_files == ["src/app.py"]

def test_run_implementation_pass_restarts_on_alignment_change_during_frontier_execution(
    planspace: Path, codespace: Path, monkeypatch: pytest.MonkeyPatch,
    noop_communicator, noop_pipeline_control) -> None:
    section = _make_section(planspace, "01")
    initial_plan = _plan(
        accepted_frontier=["edit-01"],
        deferred_steps=["verify-02"],
        step_decisions=[
            StepMitigation(step_id="edit-01", decision=StepDecision.ACCEPT),
            StepMitigation(step_id="verify-02", decision=StepDecision.REJECT_DEFER),
        ],
    )
    frontier_plan = _plan(
        accepted_frontier=["verify-02"],
        step_decisions=[
            StepMitigation(step_id="verify-02", decision=StepDecision.ACCEPT),
        ],
    )
    run_calls: list[list[str]] = []

    def _run_section(*_args, **_kwargs) -> list[str]:
        files = [f"src/step-{len(run_calls) + 1}.py"]
        run_calls.append(files)
        return files

    _patch_implementation_pass_basics(
        monkeypatch,
        risk_plan=initial_plan,
        run_section_fn=_run_section,
        reassess_fn=lambda *_args, **_kwargs: frontier_plan,
        append_history_fn=lambda *args, **kwargs: None,
        alignment_checks=[False, True],
    )

    with pytest.raises(ImplementationPassRestart):
        run_implementation_pass(
            {"01": ProposalPassResult(section_number="01", execution_ready=True)},
            {"01": section},
            planspace,
            codespace,
            "parent",
        )

    assert run_calls == [["src/step-1.py"], ["src/step-2.py"]]

def test_run_implementation_pass_exits_when_parent_aborts(
    planspace: Path, codespace: Path,
    capturing_communicator, capturing_pipeline_control) -> None:
    section = _make_section(planspace, "01")

    capturing_pipeline_control._pending_return = True

    with pytest.raises(ImplementationPassExit):
        run_implementation_pass(
            {"01": ProposalPassResult(section_number="01", execution_ready=True)},
            {"01": section},
            planspace,
            codespace,
            "parent",
        )

    assert capturing_communicator.messages == ["fail:aborted"]

def test_run_implementation_pass_invokes_roal_when_section_is_ready(
    planspace: Path,
    codespace: Path,
    monkeypatch: pytest.MonkeyPatch,
    noop_communicator, noop_pipeline_control) -> None:
    section = _make_section(planspace, "01")
    risk_plans: list[tuple[str, str]] = []

    monkeypatch.setattr(
        "implementation.engine.implementation_phase._check_and_clear_alignment_changed",
        lambda *args: False,
    )
    monkeypatch.setattr(
        "implementation.engine.implementation_phase.resolve_readiness",
        lambda *_args, **_kwargs: {"ready": True},
    )
    monkeypatch.setattr(
        "implementation.engine.implementation_phase._run_risk_review",
        lambda planspace_arg, sec_num, section_arg, _dispatch: (
            risk_plans.append((sec_num, section_arg.number)) or None
        ),
    )
    monkeypatch.setattr(
        "implementation.engine.implementation_phase.run_section",
        lambda *args, **kwargs: ["src/app.py"],
    )
    monkeypatch.setattr(
        "implementation.engine.implementation_phase.subprocess.run",
        lambda *args, **kwargs: None,
    )

    results = run_implementation_pass(
        {"01": ProposalPassResult(section_number="01", execution_ready=True)},
        {"01": section},
        planspace,
        codespace,
        "parent",
    )

    assert results["01"].modified_files == ["src/app.py"]
    assert risk_plans == [("01", "01")]
