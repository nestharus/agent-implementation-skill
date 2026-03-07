"""Component tests for MailboxService using the real SQLite-backed mailbox."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from _paths import DB_SH
from src.scripts.lib.database_client import DatabaseClient
from src.scripts.lib.mailbox_service import MailboxService, summary_tag


def _mailbox(tmp_path: Path, agent_name: str = "section-loop") -> tuple[MailboxService, DatabaseClient, Path]:
    db_path = tmp_path / "run.db"
    client = DatabaseClient(DB_SH, db_path)
    client.execute("init")
    return MailboxService(client, agent_name), client, db_path


def test_summary_tag_matches_existing_message_conventions() -> None:
    assert summary_tag("summary:proposal-align:03:PROBLEMS") == "proposal-align:03"
    assert summary_tag("status:coordination:round-2") == "coordination:round-2"
    assert summary_tag("done:03:5 files modified") == "done:03"
    assert summary_tag("pause:underspec:03:detail") == "underspec:03"


def test_send_and_drain_round_trip(tmp_path: Path) -> None:
    mailbox, _, _ = _mailbox(tmp_path)
    mailbox.register()

    mailbox.send("section-loop", "test message one")
    mailbox.send("section-loop", "test message two")

    assert mailbox.drain() == ["test message one", "test message two"]


def test_summary_messages_log_summary_events(tmp_path: Path) -> None:
    mailbox, client, _ = _mailbox(tmp_path)
    mailbox.register()

    mailbox.send("section-loop", "status:coordination:round-2")

    rows = client.query(
        "summary",
        tag="coordination:round-2",
        agent="section-loop",
        check=False,
    )
    assert "status:coordination:round-2" in rows


def test_recv_timeout_returns_timeout(tmp_path: Path) -> None:
    mailbox, _, _ = _mailbox(tmp_path)
    mailbox.register()

    assert mailbox.recv(timeout=1) == "TIMEOUT"


def test_cleanup_preserves_existing_status_order(tmp_path: Path) -> None:
    mailbox, _, db_path = _mailbox(tmp_path)
    mailbox.register()

    mailbox.cleanup()

    conn = sqlite3.connect(db_path)
    rows = conn.execute(
        "SELECT status FROM agents WHERE name = ? ORDER BY id ASC",
        ("section-loop",),
    ).fetchall()
    conn.close()

    assert [row[0] for row in rows] == ["running", "cleaned", "exited"]
