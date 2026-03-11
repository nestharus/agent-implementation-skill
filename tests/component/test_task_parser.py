"""Component tests for task_parser."""

from __future__ import annotations

from src.flow.helpers.task_parser import parse_task_output


def test_parse_task_output_returns_none_for_no_runnable_tasks() -> None:
    assert parse_task_output("NO_RUNNABLE_TASKS") is None


def test_parse_task_output_parses_all_supported_fields() -> None:
    output = (
        "id=17 | type=alignment_check | by=section-loop | prio=high"
        " | scope=section-03 | payload=artifacts/tasks/t17.md"
        " | flow_context=artifacts/flows/f17.json | continuation=artifacts/flows/c17.md"
        " | trigger_gate=g-1 | freshness=abc123"
    )

    assert parse_task_output(output) == {
        "id": "17",
        "type": "alignment_check",
        "by": "section-loop",
        "prio": "high",
        "scope": "section-03",
        "payload": "artifacts/tasks/t17.md",
        "flow_context": "artifacts/flows/f17.json",
        "continuation": "artifacts/flows/c17.md",
        "trigger_gate": "g-1",
        "freshness": "abc123",
    }


def test_parse_task_output_ignores_segments_without_equals() -> None:
    output = "id=9 | type=task | garbage segment | by=tester"

    assert parse_task_output(output) == {
        "id": "9",
        "type": "task",
        "by": "tester",
    }


def test_parse_task_output_returns_none_when_id_missing() -> None:
    assert parse_task_output("type=task | by=tester") is None


def test_parse_task_output_preserves_additional_equals_in_values() -> None:
    output = "id=5 | payload=artifacts/tasks/x=1.md | type=alignment_check"

    assert parse_task_output(output) == {
        "id": "5",
        "payload": "artifacts/tasks/x=1.md",
        "type": "alignment_check",
    }
