"""DatabaseClient: thin wrapper around ``db.sh`` subprocess calls."""

from __future__ import annotations

import subprocess
from pathlib import Path


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
        """Receive one mailbox message, optionally timing out."""
        return self.run("recv", name, str(timeout), check=check)

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
