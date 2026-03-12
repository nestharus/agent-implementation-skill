"""Task DB helpers: db.sh command wrapper and direct SQLite connection."""

from __future__ import annotations

import sqlite3
import subprocess
from contextlib import contextmanager
from collections.abc import Generator
from pathlib import Path

DB_SH = Path(__file__).resolve().parent.parent.parent / "scripts" / "db.sh"


def db_cmd(db_path: str, command: str, *args: str) -> str:
    """Run a ``db.sh`` command, returning stripped stdout."""
    result = subprocess.run(  # noqa: S603, S607
        ["bash", str(DB_SH), command, db_path, *args],
        capture_output=True,
        text=True,
        timeout=30,
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
    conn = sqlite3.connect(str(db_path), timeout=5.0)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    try:
        yield conn
    finally:
        conn.close()
