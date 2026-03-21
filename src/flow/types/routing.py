"""Task queue submission helper.

Provides the :class:`Task` dataclass and ``request_task()`` for inserting
tasks into the SQLite task queue. Task type resolution is handled by
``taskrouter.registry``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from flow.service.task_db_client import (
    request_task as _db_request_task,
    update_task_flow_paths as _db_update_task_flow_paths,
)

if TYPE_CHECKING:
    from flow.types.schema import TaskSpec


# ---------------------------------------------------------------------------
# Task dataclass — maps to requestable task fields
# ---------------------------------------------------------------------------

@dataclass
class Task:
    """A task to be submitted to the queue.

    Fields map to task-request fields plus flow metadata.
    """

    task_type: str
    submitted_by: str
    problem_id: str | None = None
    concern_scope: str | None = None
    payload_path: str | None = None
    priority: str = "normal"
    depends_on_tasks: list[int] = field(default_factory=list)
    instance_id: str | None = None
    flow_id: str | None = None
    chain_id: str | None = None
    declared_by_task_id: int | None = None
    trigger_gate_id: str | None = None
    flow_context_path: str | None = None
    continuation_path: str | None = None
    result_manifest_path: str | None = None
    freshness_token: str | None = None

    @classmethod
    def from_spec(cls, spec: TaskSpec, submitted_by: str, **kwargs) -> Task:
        """Create a Task from a declaration-time TaskSpec."""
        return cls(
            task_type=spec.task_type,
            submitted_by=submitted_by,
            problem_id=spec.problem_id or None,
            concern_scope=spec.concern_scope or None,
            payload_path=spec.payload_path or None,
            priority=spec.priority,
            **kwargs,
        )


def request_task(
    db_path: Path,
    task: Task,
    *,
    dedupe_key: str | tuple[str, str] | None = None,
    subscriber_scope: str | None = None,
) -> int:
    """Request a task reservation and return the task ID."""
    return _db_request_task(
        db_path,
        task,
        dedupe_key=dedupe_key,
        depends_on_tasks=task.depends_on_tasks,
        subscriber_scope=subscriber_scope,
    )


def update_task_flow_paths(
    db_path: Path,
    task_id: int,
    flow_context_path: str,
    continuation_path: str,
    result_manifest_path: str,
) -> None:
    """Update a task's flow-related paths after submission."""
    _db_update_task_flow_paths(
        db_path, task_id, flow_context_path, continuation_path, result_manifest_path,
    )
