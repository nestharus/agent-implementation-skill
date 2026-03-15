"""Component tests for shared task-ingestion helpers."""

from __future__ import annotations

import json

from flow.types.schema import ChainAction, FlowDeclaration, TaskSpec
from src.flow.service.flow_signal_parser import (
    FlowSignalParser,
    extract_legacy_tasks,
    find_first_section_scope,
)
from containers import Services


def _make_parser() -> FlowSignalParser:
    return FlowSignalParser(
        logger=Services.logger(),
        artifact_io=Services.artifact_io(),
    )


def parse_signal_file(signal_path):
    return _make_parser().parse_signal_file(signal_path)


def ingest_task_requests(signal_path):
    return _make_parser().ingest_task_requests(signal_path)


def test_parse_signal_file_consumes_legacy_signal(tmp_path) -> None:
    signal_path = tmp_path / "task.json"
    signal_path.write_text(json.dumps({"task_type": "staleness.alignment_check"}), encoding="utf-8")

    decl = parse_signal_file(signal_path)

    assert decl is not None
    assert decl.version == 1
    assert not signal_path.exists()


def test_parse_signal_file_renames_malformed_input(tmp_path) -> None:
    signal_path = tmp_path / "task.json"
    signal_path.write_text("{bad json", encoding="utf-8")

    decl = parse_signal_file(signal_path)

    assert decl is None
    assert not signal_path.exists()
    assert signal_path.with_suffix(".malformed.json").exists()


def test_extract_legacy_tasks_flattens_chain_steps() -> None:
    decl = FlowDeclaration(
        version=1,
        actions=[
            ChainAction(
                steps=[
                    TaskSpec(task_type="staleness.alignment_check", concern_scope="section-03"),
                    TaskSpec(task_type="signals.impact_analysis", payload_path="artifacts/x.md"),
                ],
            ),
        ],
    )

    tasks = extract_legacy_tasks(decl)

    assert tasks == [
        {"task_type": "staleness.alignment_check", "concern_scope": "section-03"},
        {"task_type": "signals.impact_analysis", "payload_path": "artifacts/x.md"},
    ]


def test_find_first_section_scope_returns_first_matching_section() -> None:
    steps = [
        TaskSpec(task_type="a", concern_scope="payments"),
        TaskSpec(task_type="b", concern_scope="section-07"),
        TaskSpec(task_type="c", concern_scope="section-09"),
    ]

    assert find_first_section_scope(steps) == "07"


def test_ingest_task_requests_skips_v2_declarations(tmp_path) -> None:
    signal_path = tmp_path / "task.json"
    signal_path.write_text(
        json.dumps(
            {
                "version": 2,
                "actions": [
                    {
                        "kind": "chain",
                        "steps": [
                            {
                                "task_type": "staleness.alignment_check",
                                "payload_path": "artifacts/tasks/t1.md",
                            },
                        ],
                    },
                ],
            },
        ),
        encoding="utf-8",
    )
    tasks = ingest_task_requests(signal_path)

    assert tasks == []
