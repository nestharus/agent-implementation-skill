from __future__ import annotations

import json
from pathlib import Path

from src.scripts.lib.decision_repository import (
    Decision,
    load_decisions,
    record_decision,
)


def test_record_decision_writes_json_and_prose(tmp_path: Path) -> None:
    decisions_dir = tmp_path / "artifacts" / "decisions"
    decision = Decision(
        id="d-01-001",
        scope="section",
        section="01",
        problem_id="p-01",
        parent_problem_id=None,
        concern_scope="alignment",
        proposal_summary="Use stricter validation",
        alignment_to_parent="Matches parent goal",
        status="decided",
        new_child_problems=["p-child-1"],
        why_unsolved="Needs follow-up",
        evidence=["artifact:a", "artifact:b"],
        next_action="Update proposal",
    )

    record_decision(decisions_dir, decision)

    json_payload = json.loads(
        (decisions_dir / "section-01.json").read_text(encoding="utf-8")
    )
    prose = (decisions_dir / "section-01.md").read_text(encoding="utf-8")

    assert json_payload[0]["id"] == "d-01-001"
    assert json_payload[0]["timestamp"]
    assert "## Decision d-01-001 (decided)" in prose
    assert "- **New child problems**:" in prose
    assert "- **Evidence**: `artifact:a`, `artifact:b`" in prose
    assert decision.timestamp == json_payload[0]["timestamp"]


def test_load_decisions_renames_non_list_json_and_reports_warning(
    tmp_path: Path,
) -> None:
    decisions_dir = tmp_path / "artifacts" / "decisions"
    decisions_dir.mkdir(parents=True)
    bad_path = decisions_dir / "section-02.json"
    bad_path.write_text('{"id": "not-a-list"}\n', encoding="utf-8")
    warnings: list[str] = []

    decisions = load_decisions(decisions_dir, section="02", warnings=warnings)

    assert decisions == []
    assert warnings == [
        f"Decision JSON at {bad_path} is not a list — renaming to .malformed.json"
    ]
    assert not bad_path.exists()
    assert (decisions_dir / "section-02.malformed.json").exists()


def test_load_decisions_filters_to_requested_section(tmp_path: Path) -> None:
    decisions_dir = tmp_path / "artifacts" / "decisions"
    record_decision(
        decisions_dir,
        Decision(
            id="d-01-001",
            scope="section",
            section="01",
            problem_id=None,
            parent_problem_id=None,
            concern_scope="one",
            proposal_summary="section one",
            alignment_to_parent=None,
            status="decided",
        ),
    )
    record_decision(
        decisions_dir,
        Decision(
            id="d-02-001",
            scope="section",
            section="02",
            problem_id=None,
            parent_problem_id=None,
            concern_scope="two",
            proposal_summary="section two",
            alignment_to_parent=None,
            status="partial",
        ),
    )

    decisions = load_decisions(decisions_dir, section="02")

    assert [decision.id for decision in decisions] == ["d-02-001"]
