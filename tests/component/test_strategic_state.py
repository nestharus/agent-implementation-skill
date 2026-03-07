from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from src.scripts.lib.artifact_io import write_json
from src.scripts.lib.decision_repository import Decision, record_decision
from src.scripts.lib.strategic_state import build_strategic_state


def test_build_strategic_state_writes_snapshot_and_derives_fields(
    tmp_path: Path,
) -> None:
    planspace = tmp_path / "planspace"
    decisions_dir = planspace / "artifacts" / "decisions"
    (planspace / "artifacts" / "signals").mkdir(parents=True)

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
    decisions_dir = planspace / "artifacts" / "decisions"
    blocker_path = planspace / "artifacts" / "signals" / "section-04-blocker.json"
    blocker_path.parent.mkdir(parents=True)
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
    assert snapshot["in_progress"] is None
    assert snapshot["next_action"] == "resolve blocker for section 04"
    assert not blocker_path.exists()
    assert blocker_path.with_suffix(".malformed.json").exists()
