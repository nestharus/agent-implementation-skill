from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from src.orchestrator.path_registry import PathRegistry
from src.signals.repository.artifact_io import write_json
from containers import Services
from src.orchestrator.repository.decisions import Decision, Decisions
from src.orchestrator.engine.strategic_state_builder import StrategicStateBuilder

_artifact_io = Services.artifact_io()
_decisions = Decisions(artifact_io=_artifact_io)
_builder = StrategicStateBuilder(artifact_io=_artifact_io)


def record_decision(decisions_dir, decision):
    _decisions.record_decision(decisions_dir, decision)


def build_strategic_state(decisions_dir, section_results, planspace):
    return _builder.build_strategic_state(decisions_dir, section_results, planspace)


def test_build_strategic_state_writes_snapshot_and_derives_fields(
    tmp_path: Path,
) -> None:
    planspace = tmp_path / "planspace"
    planspace.mkdir()
    PathRegistry(planspace).ensure_artifacts_tree()
    decisions_dir = planspace / "artifacts" / "decisions"

    record_decision(
        decisions_dir,
        Decision(
            id="d-global-001",
            scope="global",
            section=None,
            problem_id=None,
            parent_problem_id=None,
            concern_scope="coordination",
            proposal_summary="Global coordination",
            alignment_to_parent=None,
            status="decided",
            new_child_problems=["p-global-child"],
        ),
    )
    record_decision(
        decisions_dir,
        Decision(
            id="d-03-001",
            scope="section",
            section="03",
            problem_id=None,
            parent_problem_id=None,
            concern_scope="local",
            proposal_summary="Local decision",
            alignment_to_parent=None,
            status="partial",
        ),
    )
    write_json(
        planspace / "artifacts" / "signals" / "section-03-blocker.json",
        {
            "state": "needs_parent",
            "problem_id": "block-03",
            "detail": "Blocked on parent decision",
        },
    )

    snapshot = build_strategic_state(
        decisions_dir,
        {
            "01": {"aligned": True, "problems": None},
            "02": SimpleNamespace(aligned=False, problems="Need follow-up"),
            "03": {"aligned": False, "problems": "ignored because blocked"},
        },
        planspace,
    )

    saved = json.loads(
        (planspace / "artifacts" / "strategic-state.json").read_text(
            encoding="utf-8"
        )
    )

    assert saved == snapshot
    assert snapshot["completed_sections"] == ["01"]
    assert snapshot["in_progress"] == "02"
    assert snapshot["blocked"] == {
        "03": {
            "problem_id": "block-03",
            "reason": "Blocked on parent decision",
        }
    }
    assert snapshot["risk_posture"] == {}
    assert snapshot["dominant_risks_by_section"] == {}
    assert snapshot["blocked_by_risk"] == []
    assert snapshot["research_questions"] == []
    assert snapshot["key_decisions"] == ["d-global-001"]
    assert snapshot["coordination_rounds"] == 1
    assert snapshot["next_action"] == "resolve blocker for section 03"
    assert {
        "id": "p-02",
        "scope": "section-02",
        "summary": "Need follow-up",
    } in snapshot["open_problems"]
    assert {
        "id": "p-global-child",
        "scope": "global",
        "summary": "child problem from d-global-001",
    } in snapshot["open_problems"]


def test_build_strategic_state_fail_closed_on_malformed_blocker(
    tmp_path: Path,
) -> None:
    planspace = tmp_path / "planspace"
    planspace.mkdir()
    PathRegistry(planspace).ensure_artifacts_tree()
    decisions_dir = planspace / "artifacts" / "decisions"
    blocker_path = planspace / "artifacts" / "signals" / "section-04-blocker.json"
    blocker_path.write_text("{not-json", encoding="utf-8")

    snapshot = build_strategic_state(
        decisions_dir,
        {"04": {"aligned": False, "problems": "Needs parent"}},
        planspace,
    )

    assert snapshot["blocked"] == {
        "04": {
            "problem_id": "",
            "reason": "blocker signal malformed",
        }
    }
    assert snapshot["risk_posture"] == {}
    assert snapshot["dominant_risks_by_section"] == {}
    assert snapshot["blocked_by_risk"] == []
    assert snapshot["research_questions"] == []
    assert snapshot["in_progress"] is None
    assert snapshot["next_action"] == "resolve blocker for section 04"
    assert not blocker_path.exists()
    assert blocker_path.with_suffix(".malformed.json").exists()


def test_build_strategic_state_includes_research_question_artifacts(
    tmp_path: Path,
) -> None:
    planspace = tmp_path / "planspace"
    planspace.mkdir()
    PathRegistry(planspace).ensure_artifacts_tree()
    decisions_dir = planspace / "artifacts" / "decisions"
    open_problems_dir = planspace / "artifacts" / "open-problems"

    write_json(
        open_problems_dir / "section-02-research-questions.json",
        {
            "section": "02",
            "research_questions": [
                "How should retries behave when quota is exhausted?",
                "Can the cache be warmed asynchronously?",
            ],
            "source": "proposal-state",
        },
    )

    snapshot = build_strategic_state(
        decisions_dir,
        {"02": {"aligned": False, "problems": "Needs follow-up"}},
        planspace,
    )

    assert snapshot["research_questions"] == [
        {
            "section": "02",
            "research_questions": [
                "How should retries behave when quota is exhausted?",
                "Can the cache be warmed asynchronously?",
            ],
            "source": "proposal-state",
        }
    ]
