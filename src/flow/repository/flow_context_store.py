"""Flow context helpers shared by task submission and reconciliation."""

from __future__ import annotations

from pathlib import Path

from containers import Services
from orchestrator.path_registry import PathRegistry
from flow.exceptions import FlowCorruptionError
from flow.types.context import (
    FlowContext,
    FlowTask,
    flow_context_from_dict,
    flow_context_to_dict,
)


def flow_context_relpath(task_id: int) -> str:
    return f"artifacts/flows/task-{task_id}-context.json"


def continuation_relpath(task_id: int) -> str:
    return f"artifacts/flows/task-{task_id}-continuation.json"


def result_manifest_relpath(task_id: int) -> str:
    return f"artifacts/flows/task-{task_id}-result.json"


def dispatch_prompt_relpath(task_id: int) -> str:
    return f"artifacts/flows/task-{task_id}-dispatch.md"


def gate_aggregate_relpath(gate_id: str) -> str:
    return f"artifacts/flows/{gate_id}-aggregate.json"


def read_flow_json(path: Path) -> tuple[str, dict | list | None]:
    """Read a flow artifact JSON file with fail-closed semantics."""
    if not path.exists():
        return ("missing", None)

    data = Services.artifact_io().read_json(path)
    if data is None:
        print(
            f"[FLOW][WARN] Malformed JSON in {path} "
            f"— renaming to .malformed.json",
        )
        return ("malformed", None)

    return ("ok", data)


def build_flow_context(
    planspace: Path,
    task_id: int,
    flow_context_path: str | None = None,
    continuation_path: str | None = None,
    trigger_gate_id: str | None = None,
) -> FlowContext | None:
    """Read and return the flow context for a task, enriched for dispatch."""
    if not flow_context_path:
        return None

    ctx_file = planspace / flow_context_path
    status, raw = read_flow_json(ctx_file)

    if status == "missing":
        raise FlowCorruptionError(
            f"flow context declared but file missing: {ctx_file}"
        )

    if status == "malformed":
        raise FlowCorruptionError(
            f"flow context declared but file corrupt: {ctx_file}"
        )

    context = flow_context_from_dict(raw)

    gate_id = trigger_gate_id or context.task.trigger_gate_id
    if gate_id and not context.gate_aggregate_manifest:
        agg_relpath = gate_aggregate_relpath(gate_id)
        agg_file = planspace / agg_relpath
        if agg_file.exists():
            context.gate_aggregate_manifest = agg_relpath

    if continuation_path and not context.continuation_path:
        context.continuation_path = continuation_path

    return context


def write_dispatch_prompt(
    planspace: Path,
    task_id: int,
    original_prompt_path: Path,
    flow_context_path: str,
    continuation_path: str | None = None,
) -> Path:
    """Create a wrapper prompt that includes flow context for dispatch."""
    flows_dir = PathRegistry(planspace).flows_dir()
    flows_dir.mkdir(parents=True, exist_ok=True)

    original_content = ""
    if original_prompt_path.exists():
        original_content = original_prompt_path.read_text(encoding="utf-8")

    header_lines = [
        "<flow-context>",
        f"Read your flow context from: {flow_context_path}",
    ]
    if continuation_path:
        header_lines.append(
            f"Write any follow-up task declarations to: {continuation_path}"
        )
    header_lines.append("</flow-context>")
    header_lines.append("")

    wrapper_content = "\n".join(header_lines) + "\n" + original_content

    dispatch_path = PathRegistry(planspace).flow_dispatch_prompt(task_id)
    dispatch_path.write_text(wrapper_content, encoding="utf-8")

    return dispatch_path


def write_flow_context(
    planspace: Path,
    task: FlowTask,
    origin_refs: list[str],
    previous_task_id: int | None,
) -> None:
    """Write a flow context JSON file for a task."""
    flows_dir = PathRegistry(planspace).flows_dir()
    flows_dir.mkdir(parents=True, exist_ok=True)

    previous_result = None
    if previous_task_id is not None:
        previous_result = result_manifest_relpath(previous_task_id)

    context = FlowContext(
        task=task,
        origin_refs=origin_refs or [],
        previous_result_manifest=previous_result,
        continuation_path=continuation_relpath(task.task_id),
        result_manifest_path=result_manifest_relpath(task.task_id),
    )

    context_path = PathRegistry(planspace).flow_context(task.task_id)
    Services.artifact_io().write_json(context_path, flow_context_to_dict(context))
