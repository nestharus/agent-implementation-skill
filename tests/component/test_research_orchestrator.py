from __future__ import annotations

from pathlib import Path

from src.scripts.lib.core.artifact_io import write_json
from src.scripts.lib.research.orchestrator import (
    compute_trigger_hash,
    is_research_complete,
    is_research_complete_for_trigger,
    load_research_status,
    validate_research_plan,
    write_research_status,
)


def test_compute_trigger_hash_is_order_insensitive() -> None:
    assert compute_trigger_hash(["b", "a"]) == compute_trigger_hash(["a", "b"])


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
    assert wrong_type.with_suffix(".malformed.json").exists()
    assert missing_keys.with_suffix(".malformed.json").exists()
    assert tickets_not_list.with_suffix(".malformed.json").exists()


def test_write_research_status_writes_status_artifact(tmp_path: Path) -> None:
    status_path = write_research_status(
        "03",
        tmp_path / "planspace",
        "planned",
        detail="awaiting planner",
        trigger_hash="hash-03",
        cycle_id="cycle-03",
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
        '  "detail": "awaiting planner",\n'
        '  "trigger_hash": "hash-03",\n'
        '  "cycle_id": "cycle-03"\n'
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
    write_json(
        status_path,
        {
            "section": "03",
            "status": "planned",
            "detail": "",
            "trigger_hash": "hash-03",
            "cycle_id": "cycle-03",
        },
    )
    assert is_research_complete("03", planspace) is False
    assert is_research_complete_for_trigger("03", planspace, "other-hash") is False

    for terminal in ("synthesized", "verified", "failed"):
        write_json(
            status_path,
            {
                "section": "03",
                "status": terminal,
                "detail": "",
                "trigger_hash": "hash-03",
                "cycle_id": "cycle-03",
            },
        )
        assert is_research_complete("03", planspace) is True
        assert is_research_complete_for_trigger("03", planspace, "hash-03") is True


def test_load_research_status_preserves_schema_mismatches(tmp_path: Path) -> None:
    planspace = tmp_path / "planspace"
    status_path = (
        planspace
        / "artifacts"
        / "research"
        / "sections"
        / "section-03"
        / "research-status.json"
    )
    write_json(status_path, {"status": "planned"})

    assert load_research_status("03", planspace) is None
    assert status_path.with_suffix(".malformed.json").exists()
