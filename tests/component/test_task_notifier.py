from __future__ import annotations

import sqlite3
from pathlib import Path

from src.flow.service.task_db_client import db_cmd
from src.flow.service.notifier import (
    notify_task_result,
    record_qa_intercept,
    record_task_routing,
)


def _init_planspace(tmp_path: Path) -> tuple[Path, str]:
    planspace = tmp_path / "planspace"
    planspace.mkdir()
    db_path = planspace / "run.db"
    db_cmd(str(db_path), "init")
    return planspace, str(db_path)


def test_notify_task_result_sends_mailbox_message(tmp_path: Path) -> None:
    _, db_path = _init_planspace(tmp_path)
    db_cmd(db_path, "register", "submitter")

    notify_task_result(
        db_path,
        "submitter",
        "17",
        "alignment_check",
        "failed",
        "bad prompt",
    )

    drained = db_cmd(db_path, "drain", "submitter")
    assert "task:failed:17:alignment_check:bad prompt" in drained


def test_record_task_routing_updates_task_row(tmp_path: Path) -> None:
    planspace, db_path = _init_planspace(tmp_path)
    task_id = db_cmd(
        db_path,
        "submit-task",
        "alignment_check",
        "--by",
        "submitter",
    ).split(":")[1]

    record_task_routing(
        planspace,
        task_id,
        "alignment_check",
        "alignment-judge.md",
        "test-model",
    )

    conn = sqlite3.connect(db_path)
    row = conn.execute(
        "SELECT agent_file, model FROM tasks WHERE id = ?",
        (int(task_id),),
    ).fetchone()
    conn.close()

    assert row == ("alignment-judge.md", "test-model")


def test_record_qa_intercept_logs_lifecycle_event(tmp_path: Path) -> None:
    planspace, db_path = _init_planspace(tmp_path)

    record_qa_intercept(planspace, "22", "alignment_check", "reason.md")

    rows = db_cmd(
        db_path,
        "query",
        "lifecycle",
        "--tag",
        "qa-intercept:22",
        "--agent",
        "task-dispatcher",
    ).splitlines()

    assert len(rows) == 1
    assert "qa:rejected:22:reason.md" in rows[0]
