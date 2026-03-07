from __future__ import annotations

from pathlib import Path

import pytest

from src.scripts.lib.tasks.task_db_client import db_cmd


def test_db_cmd_init_and_log_round_trip(tmp_path: Path) -> None:
    db_path = tmp_path / "run.db"

    assert db_cmd(str(db_path), "init") == f"initialized:{db_path}"

    logged = db_cmd(
        str(db_path),
        "log",
        "summary",
        "dispatch:01",
        "task dispatched",
        "--agent",
        "task-dispatcher",
    )
    assert logged.startswith("logged:")

    rows = db_cmd(
        str(db_path),
        "query",
        "summary",
        "--tag",
        "dispatch:01",
        "--agent",
        "task-dispatcher",
    ).splitlines()
    assert len(rows) == 1
    assert "task dispatched" in rows[0]


def test_db_cmd_raises_on_failed_command(tmp_path: Path) -> None:
    db_path = tmp_path / "run.db"
    db_cmd(str(db_path), "init")

    with pytest.raises(RuntimeError, match="db.sh missing-command failed"):
        db_cmd(str(db_path), "missing-command")
