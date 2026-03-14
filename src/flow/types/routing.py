"""Task queue submission helper.

Provides the :class:`Task` dataclass and ``submit_task()`` for inserting
tasks into the SQLite task queue.  Task type *resolution* (mapping
qualified names to agent files + models) is handled by
``taskrouter.registry``.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from flow.service.task_db_client import task_db

if TYPE_CHECKING:
    from flow.types.schema import TaskSpec


# ---------------------------------------------------------------------------
# Task dataclass — maps 1:1 to the tasks table columns
# ---------------------------------------------------------------------------

@dataclass
class Task:
    """A task to be submitted to the queue.

    Fields map 1:1 to the ``tasks`` table columns (excluding
    auto-generated ``id``, ``status``, and timestamp columns).
    """

    task_type: str
    submitted_by: str
    problem_id: str | None = None
    concern_scope: str | None = None
    payload_path: str | None = None
    priority: str = "normal"
    depends_on: int | None = None
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


def submit_task(db_path: Path, task: Task) -> int:
    """Submit a task to the queue. Returns the task ID.

    This is a Python-native alternative to shelling out to
    ``db.sh submit-task``. Uses the same SQLite schema.

    ``freshness_token`` (P4): lightweight hash of alignment artifacts
    at submission time.  The dispatcher compares this against the
    current hash before dispatch and rejects stale tasks.
    """
    with task_db(db_path) as conn:
        cur = conn.cursor()
        cur.execute(
            """INSERT INTO tasks(submitted_by, task_type, problem_id, concern_scope,
               payload_path, priority, depends_on,
               instance_id, flow_id, chain_id, declared_by_task_id,
               trigger_gate_id, flow_context_path, continuation_path,
               result_manifest_path, freshness_token)
               VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                task.submitted_by,
                task.task_type,
                task.problem_id,
                task.concern_scope,
                task.payload_path,
                task.priority,
                str(task.depends_on) if task.depends_on is not None else None,
                task.instance_id,
                task.flow_id,
                task.chain_id,
                task.declared_by_task_id,
                task.trigger_gate_id,
                task.flow_context_path,
                task.continuation_path,
                task.result_manifest_path,
                task.freshness_token,
            ),
        )
        conn.commit()
        task_id = cur.lastrowid
    return task_id
