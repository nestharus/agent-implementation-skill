"""Mailbox polling for section-loop control messages."""

from __future__ import annotations

import sys
from collections.abc import Callable
from pathlib import Path

from staleness.service.change_tracker import invalidate_excerpts, set_flag
from signals.service.database_client import DatabaseClient
from signals.service.mailbox_service import MailboxService
from orchestrator.path_registry import PathRegistry


def _database_client(planspace: Path, db_sh: Path) -> DatabaseClient:
    return DatabaseClient(db_sh, PathRegistry(planspace).run_db())


def _mailbox(
    planspace: Path,
    *,
    db_sh: Path,
    agent_name: str,
    logger: Callable[[str], None] | None,
) -> MailboxService:
    return MailboxService(
        _database_client(planspace, db_sh),
        agent_name,
        logger=logger,
    )


def poll_control_messages(
    planspace: Path,
    parent: str,
    current_section: str | None = None,
    *,
    db_sh: Path,
    agent_name: str,
    logger: Callable[[str], None],
) -> str | None:
    """Drain and process abort/alignment_changed control messages."""
    mailbox = _mailbox(
        planspace,
        db_sh=db_sh,
        agent_name=agent_name,
        logger=logger,
    )
    messages = mailbox.drain()
    alignment_changed = False
    for msg in messages:
        if msg.startswith("abort"):
            if current_section:
                mailbox.send(parent, f"fail:{current_section}:aborted")
            else:
                mailbox.send(parent, "fail:aborted")
            logger("Received abort — shutting down")
            mailbox.cleanup()
            sys.exit(0)
        if msg.startswith("alignment_changed"):
            logger("Alignment changed — invalidating excerpts and setting flag")
            invalidate_excerpts(planspace)
            set_flag(planspace, db_sh=db_sh, agent_name=agent_name)
            alignment_changed = True
            continue
        mailbox.send(agent_name, msg)
    if alignment_changed:
        return "alignment_changed"
    return None


def check_for_messages(
    planspace: Path,
    *,
    db_sh: Path,
    agent_name: str,
    logger: Callable[[str], None] | None = None,
) -> list[str]:
    """Drain all currently pending mailbox messages."""
    return _mailbox(
        planspace,
        db_sh=db_sh,
        agent_name=agent_name,
        logger=logger,
    ).drain()


def handle_pending_messages(
    planspace: Path,
    queue: list[str],
    completed: set[str],
    *,
    db_sh: Path,
    agent_name: str,
    logger: Callable[[str], None],
) -> bool:
    """Process pending mailbox messages. Returns True on abort."""
    del queue, completed
    for msg in check_for_messages(
        planspace,
        db_sh=db_sh,
        agent_name=agent_name,
        logger=logger,
    ):
        if msg.startswith("abort"):
            return True
        if msg.startswith("alignment_changed"):
            logger("Alignment changed — invalidating excerpts and setting flag")
            invalidate_excerpts(planspace)
            set_flag(planspace, db_sh=db_sh, agent_name=agent_name)
    return False
