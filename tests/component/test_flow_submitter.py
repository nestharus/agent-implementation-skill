from __future__ import annotations

import sqlite3
import subprocess
from pathlib import Path

from _paths import DB_SH
from flow_schema import BranchSpec, GateSpec, TaskSpec
from src.scripts.lib.flow_submitter import (
    new_chain_id,
    new_flow_id,
    new_gate_id,
    new_instance_id,
    submit_chain,
    submit_fanout,
)


def _init_db(db_path: Path) -> None:
    subprocess.run(
        ["bash", str(DB_SH), "init", str(db_path)],
        check=True,
        capture_output=True,
        text=True,
    )


def _query_all(db_path: Path, sql: str) -> list[sqlite3.Row]:
    conn = sqlite3.connect(str(db_path), timeout=5.0)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute(sql)
    rows = cur.fetchall()
    conn.close()
    return rows


def test_id_helpers_allocate_expected_prefixes() -> None:
    assert new_instance_id().startswith("inst_")
    assert new_flow_id().startswith("flow_")
    assert new_chain_id().startswith("chain_")
    assert new_gate_id().startswith("gate_")


def test_submit_chain_writes_db_and_flow_context(tmp_path) -> None:
    db_path = tmp_path / "test.db"
    planspace = tmp_path / "planspace"
    planspace.mkdir()
    _init_db(db_path)

    ids = submit_chain(
        db_path,
        "tester",
        [
            TaskSpec(task_type="alignment_check"),
            TaskSpec(task_type="impact_analysis"),
        ],
        planspace=planspace,
    )

    assert len(ids) == 2
    rows = _query_all(db_path, "SELECT * FROM tasks ORDER BY id")
    assert rows[0]["depends_on"] is None
    assert rows[1]["depends_on"] == str(ids[0])
    assert (planspace / f"artifacts/flows/task-{ids[0]}-context.json").exists()
    assert (planspace / f"artifacts/flows/task-{ids[1]}-context.json").exists()


def test_submit_fanout_creates_gate_and_members(tmp_path) -> None:
    db_path = tmp_path / "test.db"
    planspace = tmp_path / "planspace"
    planspace.mkdir()
    _init_db(db_path)

    gate_id = submit_fanout(
        db_path,
        "tester",
        [
            BranchSpec(label="a", steps=[TaskSpec(task_type="alignment_check")]),
            BranchSpec(label="b", steps=[TaskSpec(task_type="impact_analysis")]),
        ],
        flow_id="flow_test",
        gate=GateSpec(
            mode="all",
            failure_policy="include",
            synthesis=TaskSpec(task_type="synthesis"),
        ),
        planspace=planspace,
    )

    assert gate_id is not None
    gates = _query_all(db_path, "SELECT * FROM gates")
    members = _query_all(db_path, "SELECT * FROM gate_members ORDER BY slot_label")
    assert len(gates) == 1
    assert gates[0]["gate_id"] == gate_id
    assert len(members) == 2
    assert [member["slot_label"] for member in members] == ["a", "b"]
