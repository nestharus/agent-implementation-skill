"""Component tests for DatabaseClient against the real ``db.sh`` interface."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from _paths import DB_SH
from src.scripts.lib.database_client import DatabaseClient


def _init_client(tmp_path: Path) -> tuple[DatabaseClient, Path]:
    db_path = tmp_path / "run.db"
    client = DatabaseClient(DB_SH, db_path)
    result = client.execute("init")
    assert result == f"initialized:{db_path}"
    return client, db_path


def test_log_event_and_query_round_trip(tmp_path: Path) -> None:
    client, _ = _init_client(tmp_path)

    logged = client.log_event(
        "summary",
        "dispatch:01",
        "impl-01 dispatched",
        agent="section-loop",
    )

    assert logged.startswith("logged:")
    rows = client.query(
        "summary",
        tag="dispatch:01",
        agent="section-loop",
    ).splitlines()
    assert len(rows) == 1
    assert "dispatch:01" in rows[0]
    assert "impl-01 dispatched" in rows[0]


def test_recv_timeout_returns_process_result_without_raising(
    tmp_path: Path,
) -> None:
    client, _ = _init_client(tmp_path)
    client.register("worker-01")

    result = client.recv("worker-01", timeout=1, check=False)

    assert result.returncode != 0
    assert result.stdout.strip() == "TIMEOUT"


def test_register_cleanup_and_unregister_append_status_rows(
    tmp_path: Path,
) -> None:
    client, db_path = _init_client(tmp_path)

    client.register("worker-02")
    client.cleanup("worker-02", check=False)
    client.unregister("worker-02", check=False)

    conn = sqlite3.connect(db_path)
    rows = conn.execute(
        "SELECT status FROM agents WHERE name = ? ORDER BY id ASC",
        ("worker-02",),
    ).fetchall()
    conn.close()

    assert [row[0] for row in rows] == ["running", "cleaned", "exited"]
