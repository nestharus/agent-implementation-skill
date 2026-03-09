"""Shared communication constants and helpers for section-loop."""

from __future__ import annotations

import os
from pathlib import Path

from lib.core.database_client import DatabaseClient
from lib.dispatch.mailbox_service import MailboxService, summary_tag
from lib.core.path_registry import PathRegistry

WORKFLOW_HOME = Path(
    os.environ.get(
        "WORKFLOW_HOME",
        Path(__file__).resolve().parent.parent.parent.parent,
    ),
)
DB_SH = WORKFLOW_HOME / "scripts" / "db.sh"
DB_PATH = Path("run.db")
AGENT_NAME = "section-loop"


def log(msg: str) -> None:
    """Print a timestamped log message to stdout."""
    print(f"[section-loop] {msg}", flush=True)


def _db_client(planspace: Path) -> DatabaseClient:
    return DatabaseClient(DB_SH, PathRegistry(planspace).run_db())


def _mailbox(planspace: Path) -> MailboxService:
    return MailboxService(_db_client(planspace), AGENT_NAME, log)


def _summary_tag(message: str) -> str:
    """Backward-compatible wrapper around extracted mailbox logic."""
    return summary_tag(message)


def mailbox_send(planspace: Path, target: str, message: str) -> None:
    """Send a message to a target mailbox."""
    _mailbox(planspace).send(target, message)


def mailbox_recv(planspace: Path, timeout: int = 0) -> str:
    """Block until a message arrives in our mailbox. Returns message text."""
    return _mailbox(planspace).recv(timeout=timeout)


def mailbox_drain(planspace: Path) -> list[str]:
    """Read all pending messages without blocking."""
    return _mailbox(planspace).drain()


def mailbox_register(planspace: Path) -> None:
    """Register this agent for receiving messages."""
    _mailbox(planspace).register()


def mailbox_cleanup(planspace: Path) -> None:
    """Clean up and unregister this agent."""
    _mailbox(planspace).cleanup()


def _log_artifact(planspace: Path, name: str) -> None:
    """Log an artifact lifecycle event to the database."""
    _db_client(planspace).log_event(
        "lifecycle",
        f"artifact:{name}",
        "created",
        agent=AGENT_NAME,
        check=False,
    )


def _record_traceability(
    planspace: Path,
    section: str,
    artifact: str,
    source: str,
    detail: str = "",
) -> None:
    """Append a traceability entry to artifacts/traceability.json."""
    from lib.core.artifact_io import read_json, write_json

    trace_path = PathRegistry(planspace).traceability()
    data = read_json(trace_path)
    entries: list[dict] = data if isinstance(data, list) else []
    entries.append({
        "section": section,
        "artifact": artifact,
        "source": source,
        "detail": detail,
    })
    write_json(trace_path, entries)
