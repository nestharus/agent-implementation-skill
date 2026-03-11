"""Pipeline state queries and pause handling."""

from __future__ import annotations

import sys
from collections.abc import Callable
from pathlib import Path

from staleness.alignment_change_tracker import invalidate_excerpts, set_flag
from signals.database_client import DatabaseClient
from signals.mailbox_service import MailboxService
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


def check_pipeline_state(planspace: Path, *, db_sh: Path) -> str:
    """Return the latest pipeline-state lifecycle value."""
    line = _database_client(planspace, db_sh).query(
        "lifecycle",
        tag="pipeline-state",
        limit=1,
        check=False,
    ).strip()
    if line:
        parts = line.split("|")
        if len(parts) >= 5 and parts[4]:
            return parts[4]
    return "running"


def wait_if_paused(
    planspace: Path,
    parent: str,
    *,
    db_sh: Path,
    agent_name: str,
    logger: Callable[[str], None],
) -> None:
    """Block while the pipeline is paused, buffering non-control messages."""
    if check_pipeline_state(planspace, db_sh=db_sh) != "paused":
        return
    mailbox = _mailbox(
        planspace,
        db_sh=db_sh,
        agent_name=agent_name,
        logger=logger,
    )
    logger("Pipeline paused — waiting for resume")
    mailbox.send(parent, "status:paused")
    buffered: list[str] = []
    while check_pipeline_state(planspace, db_sh=db_sh) == "paused":
        msg = mailbox.recv(timeout=5)
        if msg == "TIMEOUT":
            continue
        if msg.startswith("abort"):
            logger("Received abort while paused — shutting down")
            mailbox.send(parent, "fail:aborted")
            mailbox.cleanup()
            sys.exit(0)
        if msg.startswith("alignment_changed"):
            logger("Alignment changed while paused — invalidating excerpts")
            invalidate_excerpts(planspace)
            set_flag(planspace, db_sh=db_sh, agent_name=agent_name)
            continue
        buffered.append(msg)
    for msg in buffered:
        mailbox.send(agent_name, msg)
    logger("Pipeline resumed")
    mailbox.send(parent, "status:resumed")


def pause_for_parent(
    planspace: Path,
    parent: str,
    signal: str,
    *,
    db_sh: Path,
    agent_name: str,
    logger: Callable[[str], None],
) -> str:
    """Send a pause signal to the parent and wait for the next response."""
    mailbox = _mailbox(
        planspace,
        db_sh=db_sh,
        agent_name=agent_name,
        logger=logger,
    )
    mailbox.send(parent, signal)
    while True:
        msg = mailbox.recv(timeout=0)
        if msg.startswith("abort"):
            logger("Received abort — shutting down")
            mailbox.send(parent, "fail:aborted")
            mailbox.cleanup()
            sys.exit(0)
        if msg.startswith("alignment_changed"):
            logger("Alignment changed during pause — invalidating excerpts")
            invalidate_excerpts(planspace)
            set_flag(planspace, db_sh=db_sh, agent_name=agent_name)
            continue
        return msg
