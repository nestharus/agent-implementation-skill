from pathlib import Path

import pytest

from lib.core.artifact_io import read_json, write_json
from lib.pipelines.implementation_pass import (
    ImplementationPassExit,
    ImplementationPassRestart,
    _append_risk_history,
    run_implementation_pass,
)
from lib.risk.history import read_history
from lib.risk.serialization import serialize_assessment, serialize_package
from lib.risk.types import (
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
from section_loop.types import ProposalPassResult, Section


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
                    step_class=StepClass.EDIT,
                    summary="Apply the change",
                ),
                PackageStep(
                    step_id="verify-02",
                    step_class=StepClass.VERIFY,
                    summary="Verify the change",
                    prerequisites=["edit-01"],
                ),
                PackageStep(
                    step_id="coord-03",
                    step_class=StepClass.COORDINATE,
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
                    step_class=StepClass.EDIT,
                    summary="Apply the change",
                    prerequisites=[],
                    risk_vector=RiskVector(brute_force_regression=2),
                    modifiers=RiskModifiers(blast_radius=1, confidence=0.8),
                    raw_risk=35,
                    dominant_risks=[RiskType.BRUTE_FORCE_REGRESSION],
                ),
                StepAssessment(
                    step_id="verify-02",
                    step_class=StepClass.VERIFY,
                    summary="Verify the change",
                    prerequisites=["edit-01"],
                    risk_vector=RiskVector(context_rot=2),
                    modifiers=RiskModifiers(blast_radius=1, confidence=0.8),
                    raw_risk=40,
                    dominant_risks=[RiskType.CONTEXT_ROT],
                ),
                StepAssessment(
                    step_id="coord-03",
                    step_class=StepClass.COORDINATE,
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


def test_run_implementation_pass_records_results_and_hashes(
    planspace: Path, codespace: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    section = _make_section(planspace, "01")
    messages: list[str] = []

    monkeypatch.setattr(
        "lib.pipelines.implementation_pass.handle_pending_messages",
        lambda *args: False,
    )
    monkeypatch.setattr(
        "lib.pipelines.implementation_pass.alignment_changed_pending",
        lambda *args: False,
    )
    monkeypatch.setattr(
        "lib.pipelines.implementation_pass._check_and_clear_alignment_changed",
        lambda *args: False,
    )
    monkeypatch.setattr(
        "lib.pipelines.implementation_pass.resolve_readiness",
        lambda *_args, **_kwargs: {"ready": True},
    )
    monkeypatch.setattr(
        "lib.pipelines.implementation_pass._run_risk_review",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        "lib.pipelines.implementation_pass.run_section",
        lambda *args, **kwargs: ["src/app.py"],
    )
    monkeypatch.setattr(
        "lib.pipelines.implementation_pass._section_inputs_hash",
        lambda *args: "hash-123",
    )
    monkeypatch.setattr(
        "lib.pipelines.implementation_pass.mailbox_send",
        lambda _planspace, _parent, message: messages.append(message),
    )
    monkeypatch.setattr(
        "lib.pipelines.implementation_pass.subprocess.run",
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
    assert messages == ["done:01:1 files modified"]
    assert (planspace / "artifacts" / "section-inputs-hashes" / "01.hash").read_text(
        encoding="utf-8",
    ) == "hash-123"
    assert (planspace / "artifacts" / "phase2-inputs-hashes" / "01.hash").read_text(
        encoding="utf-8",
    ) == "hash-123"


def test_run_implementation_pass_writes_accepted_steps_artifact(
    planspace: Path, codespace: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
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

    monkeypatch.setattr("lib.pipelines.implementation_pass.handle_pending_messages", lambda *args: False)
    monkeypatch.setattr("lib.pipelines.implementation_pass.alignment_changed_pending", lambda *args: False)
    monkeypatch.setattr("lib.pipelines.implementation_pass._check_and_clear_alignment_changed", lambda *args: False)
    monkeypatch.setattr("lib.pipelines.implementation_pass.resolve_readiness", lambda *_args, **_kwargs: {"ready": True})
    monkeypatch.setattr("lib.pipelines.implementation_pass._run_risk_review", lambda *_args, **_kwargs: plan)
    monkeypatch.setattr("lib.pipelines.implementation_pass.run_section", lambda *args, **kwargs: ["src/app.py"])
    monkeypatch.setattr("lib.pipelines.implementation_pass._section_inputs_hash", lambda *args: "hash-123")
    monkeypatch.setattr("lib.pipelines.implementation_pass.mailbox_send", lambda *_args, **_kwargs: None)
    monkeypatch.setattr("lib.pipelines.implementation_pass.subprocess.run", lambda *args, **kwargs: None)
    monkeypatch.setattr("lib.pipelines.implementation_pass._append_risk_history", lambda *args, **kwargs: None)

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


def test_run_implementation_pass_writes_deferred_steps_artifact(
    planspace: Path, codespace: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
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

    monkeypatch.setattr("lib.pipelines.implementation_pass.handle_pending_messages", lambda *args: False)
    monkeypatch.setattr("lib.pipelines.implementation_pass.alignment_changed_pending", lambda *args: False)
    monkeypatch.setattr("lib.pipelines.implementation_pass._check_and_clear_alignment_changed", lambda *args: False)
    monkeypatch.setattr("lib.pipelines.implementation_pass.resolve_readiness", lambda *_args, **_kwargs: {"ready": True})
    monkeypatch.setattr("lib.pipelines.implementation_pass._run_risk_review", lambda *_args, **_kwargs: plan)
    monkeypatch.setattr("lib.pipelines.implementation_pass.run_section", lambda *args, **kwargs: ["src/app.py"])
    monkeypatch.setattr("lib.pipelines.implementation_pass._section_inputs_hash", lambda *args: "hash-123")
    monkeypatch.setattr("lib.pipelines.implementation_pass.mailbox_send", lambda *_args, **_kwargs: None)
    monkeypatch.setattr("lib.pipelines.implementation_pass.subprocess.run", lambda *args, **kwargs: None)
    monkeypatch.setattr("lib.pipelines.implementation_pass._append_risk_history", lambda *args, **kwargs: None)

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


def test_run_implementation_pass_writes_reopen_blocker_and_skips(
    planspace: Path, codespace: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
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

    monkeypatch.setattr("lib.pipelines.implementation_pass.handle_pending_messages", lambda *args: False)
    monkeypatch.setattr("lib.pipelines.implementation_pass.alignment_changed_pending", lambda *args: False)
    monkeypatch.setattr("lib.pipelines.implementation_pass._check_and_clear_alignment_changed", lambda *args: False)
    monkeypatch.setattr("lib.pipelines.implementation_pass.resolve_readiness", lambda *_args, **_kwargs: {"ready": True})
    monkeypatch.setattr("lib.pipelines.implementation_pass._run_risk_review", lambda *_args, **_kwargs: plan)
    monkeypatch.setattr(
        "lib.pipelines.implementation_pass.run_section",
        lambda *args, **kwargs: run_calls.append("run") or ["src/app.py"],
    )
    monkeypatch.setattr("lib.pipelines.implementation_pass.mailbox_send", lambda *_args, **_kwargs: None)
    monkeypatch.setattr("lib.pipelines.implementation_pass.subprocess.run", lambda *args, **kwargs: None)

    results = run_implementation_pass(
        {"01": ProposalPassResult(section_number="01", execution_ready=True)},
        {"01": section},
        planspace,
        codespace,
        "parent",
    )

    blocker = read_json(planspace / "artifacts" / "signals" / "section-01-blocker.json")
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
) -> None:
    section = _make_section(planspace, "01")
    run_calls: list[str] = []

    monkeypatch.setattr("lib.pipelines.implementation_pass.handle_pending_messages", lambda *args: False)
    monkeypatch.setattr("lib.pipelines.implementation_pass.alignment_changed_pending", lambda *args: False)
    monkeypatch.setattr("lib.pipelines.implementation_pass._check_and_clear_alignment_changed", lambda *args: False)
    monkeypatch.setattr("lib.pipelines.implementation_pass.resolve_readiness", lambda *_args, **_kwargs: {"ready": True})
    monkeypatch.setattr(
        "lib.pipelines.implementation_pass._run_risk_review",
        lambda *_args, **_kwargs: _plan(accepted_frontier=[]),
    )
    monkeypatch.setattr(
        "lib.pipelines.implementation_pass.run_section",
        lambda *args, **kwargs: run_calls.append("run") or ["src/app.py"],
    )
    monkeypatch.setattr("lib.pipelines.implementation_pass.mailbox_send", lambda *_args, **_kwargs: None)
    monkeypatch.setattr("lib.pipelines.implementation_pass.subprocess.run", lambda *args, **kwargs: None)

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
) -> None:
    section = _make_section(planspace, "01")

    monkeypatch.setattr("lib.pipelines.implementation_pass.handle_pending_messages", lambda *args: False)
    monkeypatch.setattr("lib.pipelines.implementation_pass.alignment_changed_pending", lambda *args: False)
    monkeypatch.setattr("lib.pipelines.implementation_pass._check_and_clear_alignment_changed", lambda *args: False)
    monkeypatch.setattr("lib.pipelines.implementation_pass.resolve_readiness", lambda *_args, **_kwargs: {"ready": True})
    monkeypatch.setattr("lib.pipelines.implementation_pass._run_risk_review", lambda *_args, **_kwargs: None)
    monkeypatch.setattr("lib.pipelines.implementation_pass.run_section", lambda *args, **kwargs: ["src/app.py"])
    monkeypatch.setattr("lib.pipelines.implementation_pass._section_inputs_hash", lambda *args: "hash-123")
    monkeypatch.setattr("lib.pipelines.implementation_pass.mailbox_send", lambda *_args, **_kwargs: None)
    monkeypatch.setattr("lib.pipelines.implementation_pass.subprocess.run", lambda *args, **kwargs: None)

    results = run_implementation_pass(
        {"01": ProposalPassResult(section_number="01", execution_ready=True)},
        {"01": section},
        planspace,
        codespace,
        "parent",
    )

    assert results["01"].modified_files == ["src/app.py"]
    assert not (planspace / "artifacts" / "inputs" / "section-01").exists()
    assert not (planspace / "artifacts" / "signals" / "section-01-blocker.json").exists()


def test_run_implementation_pass_restarts_on_alignment_change(
    planspace: Path, codespace: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    section = _make_section(planspace, "01")

    monkeypatch.setattr(
        "lib.pipelines.implementation_pass.handle_pending_messages",
        lambda *args: False,
    )
    monkeypatch.setattr(
        "lib.pipelines.implementation_pass.alignment_changed_pending",
        lambda *args: True,
    )
    monkeypatch.setattr(
        "lib.pipelines.implementation_pass._check_and_clear_alignment_changed",
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


def test_append_risk_history_records_enriched_outcomes(planspace: Path) -> None:
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

    _append_risk_history(
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


def test_run_implementation_pass_triggers_one_shot_reassessment(
    planspace: Path, codespace: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    section = _make_section(planspace, "01")
    package = RiskPackage(
        package_id="pkg-implementation-section-01",
        layer="implementation",
        scope="section-01",
        origin_problem_id="problem-01",
        origin_source="proposal",
        steps=[
            PackageStep(
                step_id="edit-01",
                step_class=StepClass.EDIT,
                summary="Apply the change",
            ),
            PackageStep(
                step_id="verify-02",
                step_class=StepClass.VERIFY,
                summary="Verify the change",
                prerequisites=["edit-01"],
            ),
        ],
    )
    initial_plan = RiskPlan(
        plan_id="plan-initial",
        assessment_id="assessment-initial",
        package_id=package.package_id,
        layer="implementation",
        step_decisions=[
            StepMitigation(
                step_id="edit-01",
                decision=StepDecision.ACCEPT,
                posture=PostureProfile.P2_STANDARD,
                residual_risk=30,
                reason="safe edit",
            ),
            StepMitigation(
                step_id="verify-02",
                decision=StepDecision.REJECT_DEFER,
                posture=PostureProfile.P3_GUARDED,
                residual_risk=55,
                reason="wait for implementation outputs",
                wait_for=["modified-file-manifest", "alignment-check-result"],
            ),
        ],
        accepted_frontier=["edit-01"],
        deferred_steps=["verify-02"],
        reopen_steps=[],
        expected_reassessment_inputs=[
            "modified-file-manifest",
            "alignment-check-result",
        ],
    )
    reassessed_plan = RiskPlan(
        plan_id="plan-reassessed",
        assessment_id="assessment-reassessed",
        package_id=package.package_id,
        layer="implementation",
        step_decisions=[
            StepMitigation(
                step_id="verify-02",
                decision=StepDecision.ACCEPT,
                posture=PostureProfile.P2_STANDARD,
                residual_risk=35,
                reason="outputs are now available",
            ),
        ],
        accepted_frontier=["verify-02"],
        deferred_steps=[],
        reopen_steps=[],
        expected_reassessment_inputs=[],
    )
    reassessment_calls: list[list[str]] = []

    monkeypatch.setattr("lib.pipelines.implementation_pass.handle_pending_messages", lambda *args: False)
    monkeypatch.setattr("lib.pipelines.implementation_pass.alignment_changed_pending", lambda *args: False)
    monkeypatch.setattr("lib.pipelines.implementation_pass._check_and_clear_alignment_changed", lambda *args: False)
    monkeypatch.setattr("lib.pipelines.implementation_pass.resolve_readiness", lambda *_args, **_kwargs: {"ready": True})
    monkeypatch.setattr("lib.pipelines.implementation_pass._run_risk_review", lambda *_args, **_kwargs: initial_plan)
    monkeypatch.setattr("lib.pipelines.implementation_pass._append_risk_history", lambda *args, **kwargs: None)
    monkeypatch.setattr("lib.pipelines.implementation_pass.read_package", lambda *_args, **_kwargs: package)
    monkeypatch.setattr(
        "lib.pipelines.implementation_pass.run_section",
        lambda *args, **kwargs: _write_alignment_output(planspace) or ["src/app.py"],
    )
    monkeypatch.setattr("lib.pipelines.implementation_pass._section_inputs_hash", lambda *args: "hash-123")
    monkeypatch.setattr("lib.pipelines.implementation_pass.mailbox_send", lambda *_args, **_kwargs: None)
    monkeypatch.setattr("lib.pipelines.implementation_pass.subprocess.run", lambda *args, **kwargs: None)

    def _reassess(*args, **_kwargs) -> RiskPlan:
        reassessment_calls.append([step.step_id for step in args[3].steps])
        return reassessed_plan

    monkeypatch.setattr("lib.pipelines.implementation_pass.run_risk_loop", _reassess)

    run_implementation_pass(
        {"01": ProposalPassResult(section_number="01", execution_ready=True)},
        {"01": section},
        planspace,
        codespace,
        "parent",
    )

    accepted = read_json(
        planspace
        / "artifacts"
        / "inputs"
        / "section-01"
        / "section-01-risk-accepted-steps.json",
    )
    deferred = read_json(
        planspace
        / "artifacts"
        / "inputs"
        / "section-01"
        / "section-01-risk-deferred.json",
    )

    assert reassessment_calls == [["verify-02"]]
    assert accepted["accepted_steps"] == ["verify-02"]
    assert deferred["deferred_steps"] == []


def _write_alignment_output(planspace: Path) -> None:
    (planspace / "artifacts" / "impl-align-01-output.md").write_text(
        '{"frame_ok": true, "aligned": true, "problems": []}',
        encoding="utf-8",
    )


def test_run_implementation_pass_exits_when_parent_aborts(
    planspace: Path, codespace: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    section = _make_section(planspace, "01")
    messages: list[str] = []

    monkeypatch.setattr(
        "lib.pipelines.implementation_pass.handle_pending_messages",
        lambda *args: True,
    )
    monkeypatch.setattr(
        "lib.pipelines.implementation_pass.mailbox_send",
        lambda _planspace, _parent, message: messages.append(message),
    )

    with pytest.raises(ImplementationPassExit):
        run_implementation_pass(
            {"01": ProposalPassResult(section_number="01", execution_ready=True)},
            {"01": section},
            planspace,
            codespace,
            "parent",
        )

    assert messages == ["fail:aborted"]


def test_run_implementation_pass_invokes_roal_when_section_is_ready(
    planspace: Path,
    codespace: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    section = _make_section(planspace, "01")
    risk_plans: list[tuple[str, str]] = []

    monkeypatch.setattr(
        "lib.pipelines.implementation_pass.handle_pending_messages",
        lambda *args: False,
    )
    monkeypatch.setattr(
        "lib.pipelines.implementation_pass.alignment_changed_pending",
        lambda *args: False,
    )
    monkeypatch.setattr(
        "lib.pipelines.implementation_pass._check_and_clear_alignment_changed",
        lambda *args: False,
    )
    monkeypatch.setattr(
        "lib.pipelines.implementation_pass.resolve_readiness",
        lambda *_args, **_kwargs: {"ready": True},
    )
    monkeypatch.setattr(
        "lib.pipelines.implementation_pass._run_risk_review",
        lambda planspace_arg, sec_num, section_arg, _dispatch: (
            risk_plans.append((sec_num, section_arg.number)) or None
        ),
    )
    monkeypatch.setattr(
        "lib.pipelines.implementation_pass.run_section",
        lambda *args, **kwargs: ["src/app.py"],
    )
    monkeypatch.setattr(
        "lib.pipelines.implementation_pass._section_inputs_hash",
        lambda *args: "hash-123",
    )
    monkeypatch.setattr(
        "lib.pipelines.implementation_pass.mailbox_send",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        "lib.pipelines.implementation_pass.subprocess.run",
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
