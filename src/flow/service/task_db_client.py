"""Task DB helpers: db.sh command wrapper and direct SQLite connection."""

from __future__ import annotations

import sqlite3
import subprocess
from contextlib import contextmanager
from collections.abc import Generator
from pathlib import Path

DB_SH = Path(__file__).resolve().parent.parent.parent / "scripts" / "db.sh"

_DB_COMMAND_TIMEOUT_SECONDS = 30
_SQLITE_CONNECT_TIMEOUT_SECONDS = 5.0
_SQLITE_BUSY_TIMEOUT_MS = 5000


def db_cmd(db_path: str, command: str, *args: str) -> str:
    """Run a ``db.sh`` command, returning stripped stdout."""
    result = subprocess.run(  # noqa: S603, S607
        ["bash", str(DB_SH), command, db_path, *args],
        capture_output=True,
        text=True,
        timeout=_DB_COMMAND_TIMEOUT_SECONDS,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"db.sh {command} failed (rc={result.returncode}): "
            f"{result.stderr.strip()}"
        )
    return result.stdout.strip()


@contextmanager
def task_db(db_path: str | Path) -> Generator[sqlite3.Connection]:
    """Open a WAL-mode SQLite connection with standard pragmas.

    Usage::

        with task_db(db_path) as conn:
            conn.execute("SELECT ...")
    """
    conn = sqlite3.connect(str(db_path), timeout=_SQLITE_CONNECT_TIMEOUT_SECONDS)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(f"PRAGMA busy_timeout={_SQLITE_BUSY_TIMEOUT_MS}")
    try:
        yield conn
    finally:
        conn.close()
