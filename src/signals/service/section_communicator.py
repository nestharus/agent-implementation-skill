"""Shared communication constants and helpers for section-loop."""

from __future__ import annotations

from pathlib import Path

from containers import Services
from signals.service.database_client import DatabaseClient
from signals.service.mailbox_service import MailboxService
from orchestrator.path_registry import PathRegistry


AGENT_NAME = "section-loop"


def log(msg: str) -> None:
    """Print a timestamped log message to stdout."""
    print(f"[{AGENT_NAME}] {msg}", flush=True)


def _mailbox(planspace: Path) -> MailboxService:
    cfg = Services.config()
    return MailboxService.for_planspace(
        planspace, db_sh=cfg.db_sh, agent_name=cfg.agent_name,
    )


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
    cfg = Services.config()
    DatabaseClient.for_planspace(planspace, cfg.db_sh).log_event(
        "lifecycle",
        f"artifact:{name}",
        "created",
        agent=cfg.agent_name,
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
    from signals.repository.artifact_io import read_json, write_json
    from proposal.repository.state import load_proposal_state

    paths = PathRegistry(planspace)
    trace_path = paths.traceability()
    data = read_json(trace_path)
    entries: list[dict] = data if isinstance(data, list) else []

    # Inherit governance identity from proposal-state if available
    governance: dict = {}
    state_path = paths.proposal_state(section)
    if state_path.exists():
        ps = load_proposal_state(state_path)
        governance = {
            "problem_ids": ps.problem_ids,
            "pattern_ids": ps.pattern_ids,
            "profile_id": ps.profile_id,
        }

    entry: dict = {
        "section": section,
        "artifact": artifact,
        "source": source,
        "detail": detail,
    }
    if governance:
        entry["governance"] = governance
    entries.append(entry)
    write_json(trace_path, entries)
