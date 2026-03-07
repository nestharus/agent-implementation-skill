from __future__ import annotations

import json
import sqlite3
import subprocess
from pathlib import Path

from _paths import DB_SH
from flow_schema import TaskSpec
from src.scripts.lib.flow_reconciler import (
    build_gate_aggregate_manifest,
    build_result_manifest,
    reconcile_task_completion,
)
from src.scripts.lib.flow_submitter import submit_chain


def _init_db(db_path: Path) -> None:
    subprocess.run(
        ["bash", str(DB_SH), "init", str(db_path)],
        check=True,
        capture_output=True,
        text=True,
    )


def _query_task(db_path: Path, task_id: int) -> dict:
    conn = sqlite3.connect(str(db_path), timeout=5.0)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("SELECT * FROM tasks WHERE id=?", (task_id,))
    row = cur.fetchone()
    conn.close()
    return dict(row)


def _update_task_status(db_path: Path, task_id: int, status: str) -> None:
    conn = sqlite3.connect(str(db_path), timeout=5.0)
    conn.execute(
        "UPDATE tasks SET status=?, completed_at=datetime('now') WHERE id=?",
        (status, task_id),
    )
    conn.commit()
    conn.close()


def test_manifest_builders_keep_existing_shape() -> None:
    result = build_result_manifest(
        task_id=1,
        instance_id="inst_1",
        flow_id="flow_1",
        chain_id="chain_1",
        task_type="alignment_check",
        status="complete",
        output_path="out.md",
        error=None,
    )
    aggregate = build_gate_aggregate_manifest(
        gate_id="gate_1",
        flow_id="flow_1",
        mode="all",
        failure_policy="include",
        origin_refs=["ref-1"],
        members=[{"chain_id": "chain_1"}],
    )

    assert result["task_id"] == 1
    assert result["status"] == "complete"
    assert aggregate["gate_id"] == "gate_1"
    assert aggregate["members"] == [{"chain_id": "chain_1"}]


def test_reconcile_task_completion_writes_result_manifest(tmp_path) -> None:
    db_path = tmp_path / "test.db"
    planspace = tmp_path / "planspace"
    planspace.mkdir()
    _init_db(db_path)

    [task_id] = submit_chain(
        db_path,
        "tester",
        [TaskSpec(task_type="alignment_check")],
        planspace=planspace,
    )
    _update_task_status(db_path, task_id, "complete")

    reconcile_task_completion(
        db_path,
        planspace,
        task_id,
        "complete",
        "artifacts/out.md",
    )

    task = _query_task(db_path, task_id)
    manifest = json.loads(
        (planspace / task["result_manifest_path"]).read_text(encoding="utf-8")
    )
    assert manifest["task_id"] == task_id
    assert manifest["status"] == "complete"


def test_reconcile_task_completion_extends_chain_from_continuation(tmp_path) -> None:
    db_path = tmp_path / "test.db"
    planspace = tmp_path / "planspace"
    planspace.mkdir()
    _init_db(db_path)

    [task_id] = submit_chain(
        db_path,
        "tester",
        [TaskSpec(task_type="alignment_check")],
        planspace=planspace,
    )
    continuation_path = planspace / f"artifacts/flows/task-{task_id}-continuation.json"
    continuation_path.write_text(
        json.dumps(
            {
                "version": 2,
                "actions": [
                    {
                        "kind": "chain",
                        "steps": [{"task_type": "impact_analysis"}],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    _update_task_status(db_path, task_id, "complete")

    reconcile_task_completion(db_path, planspace, task_id, "complete", None)

    conn = sqlite3.connect(str(db_path), timeout=5.0)
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT * FROM tasks ORDER BY id").fetchall()
    conn.close()
    assert len(rows) == 2
    assert rows[1]["depends_on"] == str(task_id)
