"""Component tests for ROAL pipeline integrations."""

from __future__ import annotations

import json
from pathlib import Path

from lib.core.artifact_io import write_json
from lib.pipelines.implementation_pass import _run_risk_review
from lib.pipelines.proposal_pass import _risk_check_proposal
from lib.repositories.strategic_state import build_strategic_state
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


def test_run_risk_review_failure_falls_back_gracefully(
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

    assert plan is None


def test_risk_check_proposal_returns_advisory_summary(planspace: Path) -> None:
    _write_risk_inputs(planspace, "01", triage_confidence="medium")

    def _dispatch(*args, **kwargs) -> str:  # noqa: ANN002, ANN003
        return json.dumps(serialize_assessment(_proposal_assessment()))

    summary = _risk_check_proposal(planspace, "01", _dispatch)

    assert summary == {
        "risk_mode": "full",
        "dominant_risks": ["silent_drift"],
        "recommendation": "recommend additional exploration",
    }


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
        {"intent_mode": "lightweight", "confidence": triage_confidence},
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
