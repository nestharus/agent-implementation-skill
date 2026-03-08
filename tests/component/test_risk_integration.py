"""Component tests for ROAL pipeline integrations."""

from __future__ import annotations

import json
from pathlib import Path

from lib.core.artifact_io import read_json, write_json
from lib.pipelines.implementation_pass import _append_risk_history, _run_risk_review
from lib.pipelines.proposal_pass import _risk_check_proposal
from lib.repositories.strategic_state import build_strategic_state
from lib.risk.history import read_history
from lib.risk.serialization import serialize_assessment, serialize_plan
from lib.risk.types import (
    PostureProfile,
    RiskAssessment,
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
from section_loop.types import Section


def test_run_risk_review_with_mocked_dispatch_returns_plan(
    planspace: Path,
) -> None:
    _write_risk_inputs(planspace, "01", triage_confidence="medium")
    section = Section(
        number="01",
        path=planspace / "artifacts" / "sections" / "section-01.md",
        related_files=["src/app.py", "src/utils.py"],
    )

    def _dispatch(*args, **kwargs) -> str:  # noqa: ANN002, ANN003
        if kwargs["agent_file"] == "risk-assessor.md":
            return json.dumps(serialize_assessment(_assessment()))
        return json.dumps(serialize_plan(_plan()))

    plan = _run_risk_review(planspace, "01", section, _dispatch)

    assert plan is not None
    assert plan.accepted_frontier == ["explore-01", "edit-02", "verify-03"]


def test_run_risk_review_returns_none_when_engagement_skips(
    planspace: Path,
) -> None:
    _write_risk_inputs(planspace, "01", triage_confidence="high", simple=True)
    section = Section(
        number="01",
        path=planspace / "artifacts" / "sections" / "section-01.md",
        related_files=["src/app.py"],
    )

    plan = _run_risk_review(planspace, "01", section, lambda *args, **kwargs: "")

    assert plan is None


def test_run_risk_review_failure_blocks_fail_closed(
    planspace: Path,
    monkeypatch,
) -> None:
    _write_risk_inputs(planspace, "01", triage_confidence="medium")
    section = Section(
        number="01",
        path=planspace / "artifacts" / "sections" / "section-01.md",
        related_files=["src/app.py", "src/utils.py"],
    )
    monkeypatch.setattr(
        "lib.pipelines.implementation_pass.build_package_from_proposal",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("boom")),
    )

    plan = _run_risk_review(planspace, "01", section, lambda *args, **kwargs: "")

    assert plan is not None
    assert plan.accepted_frontier == []
    blocker = read_json(planspace / "artifacts" / "signals" / "section-01-blocker.json")
    assert blocker == {
        "state": "needs_parent",
        "blocker_type": "risk_review_failure",
        "source": "roal",
        "section": "01",
        "scope": "section-01",
        "reason": "boom",
        "detail": "boom",
        "why_blocked": "ROAL review failed; fail-closed implementation skip engaged",
        "needs": "Repair risk review inputs or rerun ROAL successfully",
    }


def test_risk_check_proposal_returns_advisory_summary(planspace: Path) -> None:
    _write_risk_inputs(planspace, "01", triage_confidence="medium")

    def _dispatch(*args, **kwargs) -> str:  # noqa: ANN002, ANN003
        return json.dumps(serialize_assessment(_proposal_assessment()))

    summary = _risk_check_proposal(planspace, "01", _dispatch)

    assert summary == {
        "risk_mode": "full",
        "dominant_risks": ["silent_drift"],
        "dominant_risk_severities": {"silent_drift": 3},
        "package_raw_risk": 72,
        "recommendation": "recommend additional exploration",
    }


def test_risk_check_proposal_writes_advisory_artifact_and_blocker(
    planspace: Path,
) -> None:
    _write_risk_inputs(planspace, "01", triage_confidence="medium")

    def _dispatch(*args, **kwargs) -> str:  # noqa: ANN002, ANN003
        return json.dumps(serialize_assessment(_proposal_assessment()))

    summary = _risk_check_proposal(planspace, "01", _dispatch)

    advisory_path = (
        planspace
        / "artifacts"
        / "inputs"
        / "section-01"
        / "section-01-proposal-risk-advisory.json"
    )
    advisory = read_json(advisory_path)
    blocker = read_json(
        planspace / "artifacts" / "signals" / "section-01-proposal-risk-blocker.json",
    )
    roal_index = read_json(
        planspace
        / "artifacts"
        / "inputs"
        / "section-01"
        / "section-01-roal-input-index.json",
    )

    assert advisory == summary
    assert roal_index == [
        {
            "kind": "proposal_advisory",
            "path": str(advisory_path),
            "produced_by": "proposal_pass",
        },
    ]
    assert blocker == {
        "state": "needs_parent",
        "blocker_type": "proposal_risk_advisory",
        "source": "roal",
        "section": "01",
        "scope": "section-01-proposal",
        "detail": (
            "ROAL recommends additional exploration before implementation due to "
            "high-risk proposal findings (silent_drift=3)"
        ),
        "why_blocked": (
            "ROAL recommends additional exploration before implementation due to "
            "high-risk proposal findings (silent_drift=3)"
        ),
        "needs": "Additional exploration before implementation",
        "dominant_risks": ["silent_drift"],
        "dominant_risk_severities": {"silent_drift": 3},
        "risk_summary_path": str(advisory_path.resolve()),
    }


def test_risk_check_proposal_remains_advisory_without_blocking_finalization(
    planspace: Path,
) -> None:
    _write_risk_inputs(planspace, "01", triage_confidence="medium")

    def _dispatch(*args, **kwargs) -> str:  # noqa: ANN002, ANN003
        return json.dumps(serialize_assessment(_proposal_assessment()))

    summary = _risk_check_proposal(planspace, "01", _dispatch)

    assert summary["recommendation"] == "recommend additional exploration"
    assert (planspace / "artifacts" / "signals" / "section-01-blocker.json").exists() is False


def test_build_strategic_state_includes_risk_fields_when_artifacts_exist(
    planspace: Path,
) -> None:
    decisions_dir = planspace / "artifacts" / "decisions"
    risk_dir = planspace / "artifacts" / "risk"
    write_json(
        risk_dir / "section-01-risk-assessment.json",
        serialize_assessment(_assessment()),
    )
    write_json(
        risk_dir / "section-01-risk-plan.json",
        serialize_plan(_plan(posture=PostureProfile.P3_GUARDED)),
    )
    write_json(
        risk_dir / "section-02-risk-plan.json",
        serialize_plan(_blocked_plan()),
    )
    write_json(
        risk_dir / "section-02-risk-assessment.json",
        serialize_assessment(_proposal_assessment(scope="section-02")),
    )

    snapshot = build_strategic_state(
        decisions_dir,
        {
            "01": {"aligned": True, "problems": None},
            "02": {"aligned": False, "problems": "Risk gate blocked"},
        },
        planspace,
    )

    assert snapshot["risk_posture"] == {
        "01": "P3",
        "02": "P4",
    }
    assert snapshot["dominant_risks_by_section"] == {
        "01": ["context_rot"],
        "02": ["silent_drift"],
    }
    assert snapshot["blocked_by_risk"] == ["02"]


def test_build_strategic_state_uses_empty_risk_fields_without_artifacts(
    planspace: Path,
) -> None:
    decisions_dir = planspace / "artifacts" / "decisions"

    snapshot = build_strategic_state(
        decisions_dir,
        {"01": {"aligned": True, "problems": None}},
        planspace,
    )

    assert snapshot["risk_posture"] == {}
    assert snapshot["dominant_risks_by_section"] == {}
    assert snapshot["blocked_by_risk"] == []


def test_append_risk_history_records_deferred_reopened_and_failure(
    planspace: Path,
) -> None:
    _write_risk_inputs(planspace, "01", triage_confidence="medium")
    section = Section(
        number="01",
        path=planspace / "artifacts" / "sections" / "section-01.md",
        related_files=["src/app.py", "src/utils.py"],
    )

    def _dispatch(*args, **kwargs) -> str:  # noqa: ANN002, ANN003
        if kwargs["agent_file"] == "risk-assessor.md":
            return json.dumps(serialize_assessment(_assessment()))
        return json.dumps(
            serialize_plan(
                RiskPlan(
                    plan_id="plan-mixed",
                    assessment_id="assessment-section-01",
                    package_id="pkg-implementation-section-01",
                    layer="implementation",
                    step_decisions=[
                        StepMitigation(
                            step_id="explore-01",
                            decision=StepDecision.ACCEPT,
                            posture=PostureProfile.P2_STANDARD,
                            mitigations=["refresh context"],
                            residual_risk=25,
                            reason="below threshold",
                        ),
                        StepMitigation(
                            step_id="edit-02",
                            decision=StepDecision.REJECT_DEFER,
                            posture=PostureProfile.P3_GUARDED,
                            wait_for=["explore-01 output"],
                            residual_risk=30,
                            reason="defer until exploration output lands",
                        ),
                        StepMitigation(
                            step_id="verify-03",
                            decision=StepDecision.REJECT_REOPEN,
                            posture=PostureProfile.P4_REOPEN,
                            residual_risk=70,
                            reason="needs higher-level coordination",
                            route_to="coordination",
                        ),
                    ],
                    accepted_frontier=["explore-01"],
                    deferred_steps=["edit-02"],
                    reopen_steps=["verify-03"],
                    expected_reassessment_inputs=["modified-file-manifest"],
                )
            )
        )

    plan = _run_risk_review(planspace, "01", section, _dispatch)
    assert plan is not None

    _append_risk_history(
        planspace,
        "01",
        plan,
        None,
        implementation_failed=True,
    )
    history = read_history(planspace / "artifacts" / "risk" / "risk-history.jsonl")

    assert {entry.step_id: entry.actual_outcome for entry in history} == {
        "explore-01": "failure",
        "edit-02": "deferred",
        "verify-03": "reopened",
    }


def _write_risk_inputs(
    planspace: Path,
    sec_num: str,
    *,
    triage_confidence: str,
    simple: bool = False,
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

    (sections / f"section-{sec_num}.md").write_text("Spec body\n", encoding="utf-8")
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
    if simple:
        microstrategy = "- Refresh understanding\n"
    else:
        microstrategy = (
            "- Refresh understanding\n"
            "- Implement approved change\n"
            "- Verify results\n"
        )
    (proposals / f"section-{sec_num}-microstrategy.md").write_text(
        microstrategy,
        encoding="utf-8",
    )
    write_json(
        proposals / f"section-{sec_num}-proposal-state.json",
        {
            "resolved_anchors": ["anchor"],
            "unresolved_anchors": [],
            "resolved_contracts": ["Contract"],
            "unresolved_contracts": [],
            "research_questions": [],
            "blocking_research_questions": [],
            "user_root_questions": [],
            "new_section_candidates": [],
            "shared_seam_candidates": [],
            "execution_ready": True,
            "readiness_rationale": "ready",
        },
    )
    write_json(
        readiness / f"section-{sec_num}-execution-ready.json",
        {"ready": True, "blockers": [], "rationale": "ready"},
    )
    write_json(
        signals / f"intent-triage-{sec_num}.json",
        {
            "intent_mode": "lightweight",
            "confidence": triage_confidence,
            "risk_mode": (
                "skip"
                if simple and triage_confidence == "high"
                else "full"
            ),
            "risk_budget_hint": {
                "high": 0,
                "medium": 2,
                "low": 4,
            }[triage_confidence],
        },
    )
    write_json(artifacts / "tool-registry.json", {"tools": ["pytest"]})
    (artifacts / "codemap.md").write_text("Codemap\n", encoding="utf-8")


def _assessment(scope: str = "section-01") -> RiskAssessment:
    return RiskAssessment(
        assessment_id=f"assessment-{scope}",
        layer="implementation",
        package_id=f"pkg-implementation-{scope}",
        assessment_scope=scope,
        understanding_inventory=UnderstandingInventory(
            confirmed=["proposal reviewed"],
            assumed=[],
            missing=[],
            stale=[],
        ),
        package_raw_risk=35,
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
            ),
            StepAssessment(
                step_id="edit-02",
                step_class=StepClass.EDIT,
                summary="Implement approved change",
                prerequisites=["explore-01"],
                risk_vector=RiskVector(context_rot=1),
                modifiers=RiskModifiers(confidence=0.8, blast_radius=2),
                raw_risk=30,
                dominant_risks=[RiskType.CONTEXT_ROT],
            ),
            StepAssessment(
                step_id="verify-03",
                step_class=StepClass.VERIFY,
                summary="Verify results",
                prerequisites=["edit-02"],
                risk_vector=RiskVector(context_rot=1),
                modifiers=RiskModifiers(confidence=0.8),
                raw_risk=20,
                dominant_risks=[RiskType.CONTEXT_ROT],
            ),
        ],
        frontier_candidates=["explore-01", "edit-02", "verify-03"],
    )


def _proposal_assessment(scope: str = "section-01-proposal") -> RiskAssessment:
    return RiskAssessment(
        assessment_id=f"assessment-{scope}",
        layer="proposal",
        package_id=f"pkg-proposal-{scope}",
        assessment_scope=scope,
        understanding_inventory=UnderstandingInventory(
            confirmed=["proposal reviewed"],
            assumed=[],
            missing=[],
            stale=[],
        ),
        package_raw_risk=72,
        assessment_confidence=0.7,
        dominant_risks=[RiskType.SILENT_DRIFT],
        step_assessments=[
            StepAssessment(
                step_id="explore-01",
                step_class=StepClass.EXPLORE,
                summary="Refresh understanding",
                prerequisites=[],
                risk_vector=RiskVector(silent_drift=3),
                modifiers=RiskModifiers(confidence=0.7),
                raw_risk=72,
                dominant_risks=[RiskType.SILENT_DRIFT],
            ),
        ],
        frontier_candidates=["explore-01"],
    )


def _plan(
    posture: PostureProfile = PostureProfile.P2_STANDARD,
) -> RiskPlan:
    return RiskPlan(
        plan_id="plan-01",
        assessment_id="assessment-section-01",
        package_id="pkg-implementation-section-01",
        layer="implementation",
        step_decisions=[
            StepMitigation(
                step_id="explore-01",
                decision=StepDecision.ACCEPT,
                posture=posture,
                mitigations=["refresh context"],
                residual_risk=25,
                reason="below threshold",
            ),
            StepMitigation(
                step_id="edit-02",
                decision=StepDecision.ACCEPT,
                posture=posture,
                mitigations=["bounded edit"],
                residual_risk=30,
                reason="below threshold",
            ),
            StepMitigation(
                step_id="verify-03",
                decision=StepDecision.ACCEPT,
                posture=posture,
                mitigations=["run checks"],
                residual_risk=20,
                reason="below threshold",
            ),
        ],
        accepted_frontier=["explore-01", "edit-02", "verify-03"],
        deferred_steps=[],
        reopen_steps=[],
    )


def _blocked_plan() -> RiskPlan:
    return RiskPlan(
        plan_id="plan-02",
        assessment_id="assessment-section-02",
        package_id="pkg-implementation-section-02",
        layer="implementation",
        step_decisions=[
            StepMitigation(
                step_id="edit-01",
                decision=StepDecision.REJECT_REOPEN,
                posture=PostureProfile.P4_REOPEN,
                mitigations=[],
                residual_risk=90,
                reason="reopen required",
                route_to="parent",
            ),
        ],
        accepted_frontier=[],
        deferred_steps=[],
        reopen_steps=["edit-01"],
    )
