"""DatabaseClient: thin wrapper around ``db.sh`` subprocess calls.

Most operations delegate to ``db.sh`` via subprocess.  ``recv`` is
implemented in pure Python to avoid the performance penalty of
spawning a new ``python3`` interpreter on every 0.5 s poll iteration.
"""

from __future__ import annotations

import sqlite3
import subprocess
import time
from pathlib import Path

_POLL_INTERVAL = 0.5
_SQLITE_TIMEOUT = 5.0
_SQLITE_BUSY_TIMEOUT_MS = 5000


def _connect(db_path: Path) -> sqlite3.Connection:
    """Open a WAL-mode SQLite connection with busy timeout."""
    conn = sqlite3.connect(str(db_path), timeout=_SQLITE_TIMEOUT)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(f"PRAGMA busy_timeout={_SQLITE_BUSY_TIMEOUT_MS}")
    return conn


class DatabaseClient:
    """Execute ``db.sh`` commands against a specific database path."""

    def __init__(self, db_sh: Path, db_path: Path) -> None:
        self._db_sh = db_sh
        self._db_path = db_path

    @classmethod
    def for_planspace(cls, planspace: Path, db_sh: Path) -> DatabaseClient:
        """Create a client wired to the run database for *planspace*."""
        from orchestrator.path_registry import PathRegistry

        return cls(db_sh, PathRegistry(planspace).run_db())

    def run(
        self,
        command: str,
        *args: str,
        check: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        """Run a ``db.sh`` command and return the raw process result."""
        return subprocess.run(  # noqa: S603
            ["bash", str(self._db_sh), command, str(self._db_path), *args],
            capture_output=True,
            text=True,
            check=check,
        )

    def execute(self, command: str, *args: str, check: bool = True) -> str:
        """Run a ``db.sh`` command and return stripped stdout."""
        return self.run(command, *args, check=check).stdout.strip()

    def send(
        self,
        target: str,
        message: str,
        *,
        sender: str | None = None,
        check: bool = True,
    ) -> str:
        """Send a mailbox message."""
        args = [target]
        if sender is not None:
            args.extend(["--from", sender])
        args.append(message)
        return self.execute("send", *args, check=check)

    def recv(
        self,
        name: str,
        *,
        timeout: int = 0,
        check: bool = False,
    ) -> subprocess.CompletedProcess[str]:
        """Receive one mailbox message, blocking until one arrives.

        Uses a single persistent SQLite connection with an in-process
        poll loop instead of spawning ``db.sh recv`` (which forks a new
        ``python3`` process on every 0.5 s iteration).

        Returns a ``CompletedProcess``-compatible object so that
        ``MailboxService.recv`` doesn't need changes.
        """
        conn = _connect(self._db_path)
        try:
            self._update_status(conn, name, "waiting")
            elapsed = 0.0
            timeout_secs = float(timeout)
            while True:
                body = self._try_claim(conn, name)
                if body is not None:
                    self._update_status(conn, name, "running")
                    return subprocess.CompletedProcess(
                        args=[], returncode=0, stdout=body + "\n", stderr="",
                    )
                if timeout > 0 and elapsed >= timeout_secs:
                    self._update_status(conn, name, "running")
                    return subprocess.CompletedProcess(
                        args=[], returncode=1, stdout="TIMEOUT\n", stderr="",
                    )
                time.sleep(_POLL_INTERVAL)
                elapsed += _POLL_INTERVAL
        finally:
            conn.close()

    @staticmethod
    def _try_claim(conn: sqlite3.Connection, name: str) -> str | None:
        """Atomically claim the oldest unclaimed message, or return None."""
        cur = conn.cursor()
        while True:
            cur.execute("BEGIN IMMEDIATE")
            cur.execute(
                "SELECT id, body FROM messages "
                "WHERE target=? AND claimed=0 "
                "ORDER BY id ASC LIMIT 1",
                (name,),
            )
            row = cur.fetchone()
            if not row:
                conn.execute("COMMIT")
                return None
            msg_id, body = row
            cur.execute(
                "UPDATE messages "
                "SET claimed=1, claimed_by=?, "
                "    claimed_at=strftime('%Y-%m-%dT%H:%M:%f','now') "
                "WHERE id=? AND claimed=0",
                (name, msg_id),
            )
            if cur.rowcount == 0:
                conn.execute("COMMIT")
                continue  # another process claimed it, retry
            conn.execute("COMMIT")
            return body

    @staticmethod
    def _update_status(
        conn: sqlite3.Connection, name: str, new_status: str,
    ) -> None:
        """Update the agent status row (mirrors ``db.sh _update_status``)."""
        cur = conn.cursor()
        cur.execute(
            "SELECT pid FROM agents WHERE name=? ORDER BY id DESC LIMIT 1",
            (name,),
        )
        row = cur.fetchone()
        if row:
            cur.execute("INSERT INTO id_seq DEFAULT VALUES")
            nid = cur.lastrowid
            cur.execute(
                "INSERT INTO agents(id, name, pid, status) VALUES(?, ?, ?, ?)",
                (nid, name, row[0], new_status),
            )
            conn.commit()

    def drain(self, name: str, *, check: bool = True) -> str:
        """Drain all pending mailbox messages for *name*."""
        return self.execute("drain", name, check=check)

    def register(
        self,
        name: str,
        *,
        pid: int | None = None,
        check: bool = True,
    ) -> str:
        """Register an agent mailbox."""
        args = [name]
        if pid is not None:
            args.append(str(pid))
        return self.execute("register", *args, check=check)

    def unregister(self, name: str, *, check: bool = True) -> str:
        """Mark an agent as exited."""
        return self.execute("unregister", name, check=check)

    def cleanup(
        self,
        name: str | None = None,
        *,
        check: bool = True,
    ) -> str:
        """Mark one agent, or all agents, as cleaned."""
        args = [name] if name else []
        return self.execute("cleanup", *args, check=check)

    def log_event(
        self,
        kind: str,
        tag: str = "",
        body: str = "",
        *,
        agent: str | None = None,
        check: bool = True,
    ) -> str:
        """Record an event row."""
        args = [kind]
        if tag:
            args.append(tag)
            args.append(body)
        elif body:
            args.extend(["", body])
        if agent:
            args.extend(["--agent", agent])
        return self.execute("log", *args, check=check)

    def query(
        self,
        kind: str,
        *,
        tag: str | None = None,
        agent: str | None = None,
        since: str | None = None,
        limit: int | str | None = None,
        check: bool = True,
    ) -> str:
        """Query event rows by kind with optional filters."""
        args = [kind]
        if tag:
            args.extend(["--tag", tag])
        if agent:
            args.extend(["--agent", agent])
        if since:
            args.extend(["--since", since])
        if limit is not None:
            args.extend(["--limit", str(limit)])
        return self.execute("query", *args, check=check)
