"""Task-dispatcher notification and observability helpers."""

from __future__ import annotations

import subprocess
from pathlib import Path

from containers import Services
from orchestrator.path_registry import PathRegistry
from flow.service.task_db_client import DB_SH, db_cmd, task_db

DISPATCHER_NAME = "task-dispatcher"
_NOTIFY_SUBPROCESS_TIMEOUT_SECONDS = 10


def notify_task_result(
    db_path: str,
    submitted_by: str,
    task_id: str,
    task_type: str,
    status: str,
    detail: str,
) -> None:
    """Send a mailbox notification to the task submitter."""
    body = f"task:{status}:{task_id}:{task_type}:{detail}"
    try:
        db_cmd(db_path, "send", submitted_by, "--from", DISPATCHER_NAME, body)
    except RuntimeError:
        # Non-critical — submitter may not have a mailbox.
        pass


def record_task_routing(
    planspace: Path,
    task_id: str,
    task_type: str,
    agent_file: str,
    model: str,
    *,
    db_path: str | Path | None = None,
) -> None:
    """Update the task row with the resolved agent file and model."""
    del task_type
    resolved_db_path = (
        Path(db_path) if db_path is not None else PathRegistry(planspace).run_db()
    )
    with task_db(resolved_db_path) as conn:
        conn.execute(
            "UPDATE tasks SET agent_file=?, model=? WHERE id=?",
            (agent_file, model, int(task_id)),
        )
        conn.commit()


def record_qa_intercept(
    planspace: Path,
    task_id: str,
    task_type: str,
    rejection_reason: str | None,
    *,
    db_path: str | Path | None = None,
    reason_code: str | None = None,
) -> None:
    """Record a QA intercept event for observability.

    PAT-0014: degraded advisory outcomes are logged distinctly from
    genuine approval.  When *reason_code* is set and *rejection_reason*
    is None, the verdict is ``degraded`` (not ``passed``).
    """
    del task_type
    resolved_db_path = (
        str(db_path) if db_path is not None else str(PathRegistry(planspace).run_db())
    )
    if rejection_reason:
        verdict = "rejected"
    elif reason_code:
        verdict = "degraded"
    else:
        verdict = "passed"
    body = f"qa:{verdict}:{task_id}"
    if rejection_reason:
        body += f":{rejection_reason}"
    elif reason_code:
        body += f":{reason_code}"
    try:
        subprocess.run(  # noqa: S603, S607
            [
                "bash",
                str(DB_SH),
                "log",
                resolved_db_path,
                "lifecycle",
                f"qa-intercept:{task_id}",
                body,
                "--agent",
                DISPATCHER_NAME,
            ],
            capture_output=True,
            text=True,
            timeout=_NOTIFY_SUBPROCESS_TIMEOUT_SECONDS,
        )
    except Exception as exc:  # noqa: BLE001
        # Non-critical — logging failure must not block dispatch.
        Services.logger().log(
            f"QA intercept logging failed ({exc}) — failing open",
        )
        pass
