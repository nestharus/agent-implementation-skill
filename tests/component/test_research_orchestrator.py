from __future__ import annotations

from pathlib import Path

from src.scripts.lib.core.artifact_io import write_json
from src.scripts.lib.research.orchestrator import (
    is_research_complete,
    validate_research_plan,
    write_research_status,
)


def test_validate_research_plan_accepts_valid_structure(tmp_path: Path) -> None:
    plan_path = tmp_path / "research-plan.json"
    write_json(plan_path, {
        "section": "03",
        "tickets": [{"ticket_id": "T-01"}],
        "flow": {"parallel_groups": [["T-01"]]},
    })

    assert validate_research_plan(plan_path) == {
        "section": "03",
        "tickets": [{"ticket_id": "T-01"}],
        "flow": {"parallel_groups": [["T-01"]]},
    }


def test_validate_research_plan_rejects_malformed_payloads(tmp_path: Path) -> None:
    wrong_type = tmp_path / "wrong-type.json"
    missing_keys = tmp_path / "missing-keys.json"
    tickets_not_list = tmp_path / "tickets-not-list.json"

    write_json(wrong_type, [{"section": "03"}])
    write_json(missing_keys, {"section": "03", "tickets": []})
    write_json(tickets_not_list, {"section": "03", "tickets": {}, "flow": {}})

    assert validate_research_plan(wrong_type) is None
    assert validate_research_plan(missing_keys) is None
    assert validate_research_plan(tickets_not_list) is None


def test_write_research_status_writes_status_artifact(tmp_path: Path) -> None:
    status_path = write_research_status(
        "03",
        tmp_path / "planspace",
        "planned",
        detail="awaiting planner",
    )

    assert status_path == (
        tmp_path
        / "planspace"
        / "artifacts"
        / "research"
        / "sections"
        / "section-03"
        / "research-status.json"
    )
    assert status_path.read_text(encoding="utf-8") == (
        '{\n'
        '  "section": "03",\n'
        '  "status": "planned",\n'
        '  "detail": "awaiting planner"\n'
        '}\n'
    )


def test_is_research_complete_only_for_terminal_states(tmp_path: Path) -> None:
    planspace = tmp_path / "planspace"
    assert is_research_complete("03", planspace) is False

    status_path = (
        planspace
        / "artifacts"
        / "research"
        / "sections"
        / "section-03"
        / "research-status.json"
    )
    write_json(status_path, {"section": "03", "status": "planned", "detail": ""})
    assert is_research_complete("03", planspace) is False

    for terminal in ("synthesized", "verified", "failed"):
        write_json(status_path, {"section": "03", "status": terminal, "detail": ""})
        assert is_research_complete("03", planspace) is True
