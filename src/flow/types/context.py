"""Typed domain objects for flow context."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path


def new_instance_id() -> str:
    return f"inst_{uuid.uuid4()}"


def new_flow_id() -> str:
    return f"flow_{uuid.uuid4()}"


def new_chain_id() -> str:
    return f"chain_{uuid.uuid4()}"


def new_gate_id() -> str:
    return f"gate_{uuid.uuid4()}"


class TaskStatus(str, Enum):
    """Status of a task in the task DB lifecycle."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETE = "complete"
    FAILED = "failed"
    CANCELLED = "cancelled"

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True)
class FlowEnvelope:
    """Common metadata for submitting tasks into a flow.

    Bundles the 7 parameters shared by submit_chain and submit_fanout.
    """

    db_path: Path
    submitted_by: str
    flow_id: str | None = None
    declared_by_task_id: int | None = None
    origin_refs: list[str] = field(default_factory=list)
    planspace: Path | None = None
    freshness_token: str | None = None


@dataclass
class FlowTask:
    """Identity and lineage for a task within a flow."""

    task_id: int
    instance_id: str
    flow_id: str
    chain_id: str
    task_type: str
    declared_by_task_id: int | None = None
    trigger_gate_id: str | None = None


@dataclass
class FlowContext:
    """Typed flow context carried through task dispatch pipelines.

    Replaces the raw ``dict`` returned by ``build_flow_context`` and
    written by ``write_flow_context``.
    """

    task: FlowTask
    origin_refs: list[str] = field(default_factory=list)
    previous_result_manifest: str | None = None
    gate_aggregate_manifest: str | None = None
    continuation_path: str | None = None
    result_manifest_path: str | None = None


def flow_context_to_dict(ctx: FlowContext) -> dict:
    """Serialize a :class:`FlowContext` to the JSON-compatible dict format."""
    return {
        "task": {
            "task_id": ctx.task.task_id,
            "instance_id": ctx.task.instance_id,
            "flow_id": ctx.task.flow_id,
            "chain_id": ctx.task.chain_id,
            "task_type": ctx.task.task_type,
            "declared_by_task_id": ctx.task.declared_by_task_id,
            "trigger_gate_id": ctx.task.trigger_gate_id,
        },
        "origin_refs": ctx.origin_refs,
        "previous_result_manifest": ctx.previous_result_manifest,
        "gate_aggregate_manifest": ctx.gate_aggregate_manifest,
        "continuation_path": ctx.continuation_path,
        "result_manifest_path": ctx.result_manifest_path,
    }


def flow_context_from_dict(data: dict) -> FlowContext:
    """Deserialize a :class:`FlowContext` from the JSON-compatible dict format."""
    task_data = data.get("task", {})
    return FlowContext(
        task=FlowTask(
            task_id=task_data.get("task_id", 0),
            instance_id=task_data.get("instance_id", ""),
            flow_id=task_data.get("flow_id", ""),
            chain_id=task_data.get("chain_id", ""),
            task_type=task_data.get("task_type", ""),
            declared_by_task_id=task_data.get("declared_by_task_id"),
            trigger_gate_id=task_data.get("trigger_gate_id"),
        ),
        origin_refs=data.get("origin_refs", []),
        previous_result_manifest=data.get("previous_result_manifest"),
        gate_aggregate_manifest=data.get("gate_aggregate_manifest"),
        continuation_path=data.get("continuation_path"),
        result_manifest_path=data.get("result_manifest_path"),
    )
