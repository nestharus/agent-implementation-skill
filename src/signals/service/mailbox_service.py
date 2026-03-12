"""MailboxService: mailbox operations extracted from section-loop communication."""

from __future__ import annotations

import re
from collections.abc import Callable
from pathlib import Path

from signals.service.database_client import DatabaseClient

_SUMMARY_PREFIXES = (
    "summary:",
    "done:",
    "complete",
    "status:",
    "fail:",
    "pause:",
)


def summary_tag(message: str) -> str:
    """Extract the structured summary tag for a mailbox message."""
    parts = message.split(":")
    if message.startswith("summary:") and len(parts) >= 3:
        return f"{parts[1]}:{parts[2]}"
    if message.startswith("status:") and len(parts) >= 3:
        return f"{parts[1]}:{parts[2]}"
    if message.startswith(("done:", "fail:")) and len(parts) >= 2:
        return f"{parts[0]}:{parts[1]}"
    if message.startswith("pause:") and len(parts) >= 3:
        return f"{parts[1]}:{parts[2]}"
    if message == "complete":
        return "complete"
    return parts[0]


class MailboxService:
    """Mailbox behavior for one agent name."""

    def __init__(
        self,
        db: DatabaseClient,
        agent_name: str,
        logger: Callable[[str], None] | None = None,
    ) -> None:
        self._db = db
        self._agent_name = agent_name
        self._logger = logger

    @classmethod
    def for_planspace(
        cls,
        planspace: Path,
        *,
        db_sh: Path,
        agent_name: str,
        logger: Callable[[str], None] | None = None,
    ) -> MailboxService:
        """Create a mailbox wired to the run database for *planspace*."""
        db = DatabaseClient.for_planspace(planspace, db_sh)
        return cls(db, agent_name, logger=logger)

    def send(self, target: str, message: str) -> None:
        """Send a message and emit summary events for monitored prefixes."""
        self._db.send(target, message, sender=self._agent_name)
        self._log(f"  mail → {target}: {message[:80]}")
        for prefix in _SUMMARY_PREFIXES:
            if message.startswith(prefix):
                self._db.log_event(
                    "summary",
                    summary_tag(message),
                    message,
                    agent=self._agent_name,
                    check=False,
                )
                break

    def recv(self, timeout: int = 0) -> str:
        """Block until a message arrives, returning ``TIMEOUT`` on timeout."""
        self._log(f"  mail ← waiting (timeout={timeout})...")
        result = self._db.recv(self._agent_name, timeout=timeout, check=False)
        message = result.stdout.strip()
        if result.returncode != 0 or message == "TIMEOUT":
            return "TIMEOUT"
        self._log(f"  mail ← received: {message[:80]}")
        return message

    def drain(self) -> list[str]:
        """Read all pending messages without blocking."""
        drained = self._db.drain(self._agent_name, check=False)
        messages: list[str] = []
        for chunk in re.split(r"\n---\n", drained):
            chunk = chunk.strip()
            if chunk:
                messages.append(chunk)
        return messages

    def register(self) -> None:
        """Register the mailbox."""
        self._db.register(self._agent_name)

    def cleanup(self) -> None:
        """Clean up and unregister the mailbox."""
        self._db.cleanup(self._agent_name, check=False)
        self._db.unregister(self._agent_name, check=False)

    def _log(self, message: str) -> None:
        if self._logger is not None:
            self._logger(message)
