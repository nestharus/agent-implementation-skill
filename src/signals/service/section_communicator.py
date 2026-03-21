"""Shared communication constants and helpers for section-loop."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from signals.service.database_client import DatabaseClient
from signals.service.mailbox_service import MailboxService
from signals.service.mailbox_service import summary_tag
from orchestrator.path_registry import PathRegistry

if TYPE_CHECKING:
    from containers import ConfigService


AGENT_NAME = "section-loop"


class SectionCommunicator:
    """Communication helpers for section-loop with injected config."""

    def __init__(self, config: ConfigService) -> None:
        self._config = config

    def _mailbox(self, planspace: Path) -> MailboxService:
        return MailboxService.for_planspace(
            planspace,
            db_sh=self._config.db_sh,
            agent_name=self._config.agent_name,
        )

    def mailbox_send(self, planspace: Path, target: str, message: str) -> None:
        """Send a message to a target mailbox."""
        self._mailbox(planspace).send(target, message)

    def mailbox_recv(self, planspace: Path, timeout: int = 0) -> str:
        """Block until a message arrives in our mailbox. Returns message text."""
        return self._mailbox(planspace).recv(timeout=timeout)

    def mailbox_drain(self, planspace: Path) -> list[str]:
        """Read all pending messages without blocking."""
        return self._mailbox(planspace).drain()

    def mailbox_register(self, planspace: Path) -> None:
        """Register this agent for receiving messages."""
        self._mailbox(planspace).register()

    def mailbox_cleanup(self, planspace: Path) -> None:
        """Clean up and unregister this agent."""
        self._mailbox(planspace).cleanup()

    def log_artifact(self, planspace: Path, name: str) -> None:
        """Log an artifact lifecycle event to the database."""
        DatabaseClient.for_planspace(planspace, self._config.db_sh).log_event(
            "lifecycle",
            f"artifact:{name}",
            "created",
            agent=self._config.agent_name,
            check=False,
        )

    def log_summary(self, planspace: Path, message: str) -> None:
        """Record a structured summary event without parent mailbox routing."""
        DatabaseClient.for_planspace(planspace, self._config.db_sh).log_event(
            "summary",
            summary_tag(message),
            message,
            agent=self._config.agent_name,
            check=False,
        )


# ── Pure functions (no Services dependency) ───────────────────────────

def log(msg: str) -> None:
    """Print a timestamped log message to stdout."""
    print(f"[{AGENT_NAME}] {msg}", flush=True)


def _record_traceability(
    planspace: Path,
    section: str,
    artifact: str,
    source: str,
    detail: str = "",
) -> None:
    """Append a traceability entry to artifacts/traceability.json."""
    from signals.repository.artifact_io import read_json, write_json
    from proposal.repository.state import State as ProposalStateRepo
    from containers import Services

    paths = PathRegistry(planspace)
    trace_path = paths.traceability()
    data = read_json(trace_path)
    entries: list[dict] = data if isinstance(data, list) else []

    # Inherit governance identity from proposal-state if available
    governance: dict = {}
    state_path = paths.proposal_state(section)
    if state_path.exists():
        ps = ProposalStateRepo(artifact_io=Services.artifact_io()).load_proposal_state(state_path)
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


# ── Backward-compat wrappers (called by containers.py) ───────────────

def _mailbox(planspace: Path) -> MailboxService:
    from containers import Services
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
