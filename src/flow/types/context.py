"""Typed domain objects for flow context."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class FlowTask:
    """Identity and lineage for a task within a flow."""

    task_id: int
    instance_id: str
    flow_id: str
    chain_id: str
    task_type: str
    declared_by_task_id: int | None = None
    depends_on: int | None = None
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

    def to_dict(self) -> dict:
        """Serialize to the JSON-compatible dict format."""
        return {
            "task": {
                "task_id": self.task.task_id,
                "instance_id": self.task.instance_id,
                "flow_id": self.task.flow_id,
                "chain_id": self.task.chain_id,
                "task_type": self.task.task_type,
                "declared_by_task_id": self.task.declared_by_task_id,
                "depends_on": self.task.depends_on,
                "trigger_gate_id": self.task.trigger_gate_id,
            },
            "origin_refs": self.origin_refs,
            "previous_result_manifest": self.previous_result_manifest,
            "gate_aggregate_manifest": self.gate_aggregate_manifest,
            "continuation_path": self.continuation_path,
            "result_manifest_path": self.result_manifest_path,
        }

    @classmethod
    def from_dict(cls, data: dict) -> FlowContext:
        """Deserialize from the JSON-compatible dict format."""
        task_data = data.get("task", {})
        return cls(
            task=FlowTask(
                task_id=task_data.get("task_id", 0),
                instance_id=task_data.get("instance_id", ""),
                flow_id=task_data.get("flow_id", ""),
                chain_id=task_data.get("chain_id", ""),
                task_type=task_data.get("task_type", ""),
                declared_by_task_id=task_data.get("declared_by_task_id"),
                depends_on=task_data.get("depends_on"),
                trigger_gate_id=task_data.get("trigger_gate_id"),
            ),
            origin_refs=data.get("origin_refs", []),
            previous_result_manifest=data.get("previous_result_manifest"),
            gate_aggregate_manifest=data.get("gate_aggregate_manifest"),
            continuation_path=data.get("continuation_path"),
            result_manifest_path=data.get("result_manifest_path"),
        )
