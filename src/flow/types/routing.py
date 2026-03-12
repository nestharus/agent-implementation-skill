"""Task queue submission helper.

Provides ``submit_task()`` for inserting tasks into the SQLite task queue.
Task type *resolution* (mapping qualified names to agent files + models)
is handled by ``taskrouter.registry``.
"""

from __future__ import annotations

from pathlib import Path

from flow.service.task_db_client import task_db


def submit_task(
    db_path: Path,
    submitted_by: str,
    task_type: str,
    *,
    problem_id: str | None = None,
    concern_scope: str | None = None,
    payload_path: str | None = None,
    priority: str = "normal",
    depends_on: int | None = None,
    instance_id: str | None = None,
    flow_id: str | None = None,
    chain_id: str | None = None,
    declared_by_task_id: int | None = None,
    trigger_gate_id: str | None = None,
    flow_context_path: str | None = None,
    continuation_path: str | None = None,
    result_manifest_path: str | None = None,
    freshness_token: str | None = None,
) -> int:
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
                submitted_by,
                task_type,
                problem_id,
                concern_scope,
                payload_path,
                priority,
                str(depends_on) if depends_on is not None else None,
                instance_id,
                flow_id,
                chain_id,
                declared_by_task_id,
                trigger_gate_id,
                flow_context_path,
                continuation_path,
                result_manifest_path,
                freshness_token,
            ),
        )
        conn.commit()
        task_id = cur.lastrowid
    return task_id
