from __future__ import annotations

import json

import pytest

from flow.exceptions import FlowCorruptionError
from containers import Services
from flow.repository.flow_context_store import (
    FlowContextStore,
    continuation_relpath,
    dispatch_prompt_relpath,
    flow_context_relpath,
    gate_aggregate_relpath,
    result_manifest_relpath,
    write_dispatch_prompt,
)
from flow.types.context import FlowTask
from orchestrator.path_registry import PathRegistry


def _make_store() -> FlowContextStore:
    return FlowContextStore(Services.artifact_io())


def test_relpath_helpers_match_existing_layout() -> None:
    assert flow_context_relpath(7) == "artifacts/flows/task-7-context.json"
    assert continuation_relpath(7) == "artifacts/flows/task-7-continuation.json"
    assert result_manifest_relpath(7) == "artifacts/flows/task-7-result.json"
    assert dispatch_prompt_relpath(7) == "artifacts/flows/task-7-dispatch.md"
    assert gate_aggregate_relpath("gate_1") == "artifacts/flows/gate_1-aggregate.json"


def test_write_flow_context_writes_expected_json(tmp_path) -> None:
    planspace = tmp_path / "planspace"
    planspace.mkdir()
    PathRegistry(planspace).ensure_artifacts_tree()

    _make_store().write_flow_context(
        planspace=planspace,
        task=FlowTask(
            task_id=11,
            instance_id="inst_1",
            flow_id="flow_1",
            chain_id="chain_1",
            task_type="staleness.alignment_check",
            declared_by_task_id=10,
            trigger_gate_id=None,
        ),
        origin_refs=["ref-1"],
        previous_task_id=10,
    )

    ctx = json.loads(
        (planspace / flow_context_relpath(11)).read_text(encoding="utf-8")
    )
    assert ctx["task"]["task_id"] == 11
    assert ctx["origin_refs"] == ["ref-1"]
    assert ctx["previous_result_manifest"] == result_manifest_relpath(10)


def test_build_flow_context_raises_on_missing_file(tmp_path) -> None:
    with pytest.raises(FlowCorruptionError, match="missing"):
        _make_store().build_flow_context(
            tmp_path,
            flow_context_path=flow_context_relpath(11),
        )


def test_build_flow_context_enriches_gate_aggregate(tmp_path) -> None:
    planspace = tmp_path / "planspace"
    planspace.mkdir()
    PathRegistry(planspace).ensure_artifacts_tree()
    gate_id = "gate_1"
    (planspace / gate_aggregate_relpath(gate_id)).write_text("{}", encoding="utf-8")
    (planspace / flow_context_relpath(11)).write_text(
        json.dumps(
            {
                "task": {"task_id": 11, "trigger_gate_id": gate_id},
                "gate_aggregate_manifest": None,
            }
        ),
        encoding="utf-8",
    )

    ctx = _make_store().build_flow_context(
        planspace,
        flow_context_path=flow_context_relpath(11),
        trigger_gate_id=gate_id,
    )

    assert ctx is not None
    assert ctx.gate_aggregate_manifest == gate_aggregate_relpath(gate_id)


def test_write_dispatch_prompt_wraps_original_without_mutation(tmp_path) -> None:
    planspace = tmp_path / "planspace"
    planspace.mkdir()
    PathRegistry(planspace).ensure_artifacts_tree()
    original = tmp_path / "prompt.md"
    original.write_text("# Original\n", encoding="utf-8")

    wrapped = write_dispatch_prompt(
        planspace,
        11,
        original,
        flow_context_path=flow_context_relpath(11),
        continuation_path=continuation_relpath(11),
    )

    content = wrapped.read_text(encoding="utf-8")
    assert f"Planspace root (write all artifacts here): {planspace}" in content
    assert f"Read your flow context from: {planspace / 'artifacts/flows/task-11-context.json'}" in content
    assert f"Write any follow-up task declarations to: {planspace / 'artifacts/flows/task-11-continuation.json'}" in content
    assert "# Original" in content
