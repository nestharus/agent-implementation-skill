"""Shared communication constants and helpers for section-loop."""

from __future__ import annotations

import json
import os
from pathlib import Path

from .database_client import DatabaseClient
from .mailbox_service import MailboxService, summary_tag
from .path_registry import PathRegistry

WORKFLOW_HOME = Path(
    os.environ.get(
        "WORKFLOW_HOME",
        Path(__file__).resolve().parent.parent.parent,
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
    trace_path = PathRegistry(planspace).traceability()
    entries: list[dict] = []
    if trace_path.exists():
        try:
            entries = json.loads(trace_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, ValueError) as exc:
            import time

            corrupt_name = f"traceability.corrupt-{int(time.time())}.json"
            corrupt_path = trace_path.parent / corrupt_name
            try:
                trace_path.rename(corrupt_path)
            except OSError:
                pass
            log(
                f"traceability.json malformed ({exc}) — "
                f"preserved as {corrupt_name}, starting fresh",
            )
            entries = []
    entries.append({
        "section": section,
        "artifact": artifact,
        "source": source,
        "detail": detail,
    })
    trace_path.parent.mkdir(parents=True, exist_ok=True)
    trace_path.write_text(
        json.dumps(entries, indent=2) + "\n",
        encoding="utf-8",
    )
