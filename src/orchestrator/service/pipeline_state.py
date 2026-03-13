"""Pipeline state queries and pause handling."""

from __future__ import annotations

import sys
from pathlib import Path

from containers import Services
from signals.service.database_client import DatabaseClient
from signals.service.mailbox_service import MailboxService
from orchestrator.types import ControlSignal

_PAUSE_POLL_TIMEOUT_SECONDS = 5


def check_pipeline_state(planspace: Path, *, db_sh: Path) -> str:
    """Return the latest pipeline-state lifecycle value."""
    line = DatabaseClient.for_planspace(planspace, db_sh).query(
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
) -> None:
    """Block while the pipeline is paused, buffering non-control messages."""
    if check_pipeline_state(planspace, db_sh=db_sh) != "paused":
        return
    log = Services.logger().log
    mailbox = MailboxService.for_planspace(
        planspace,
        db_sh=db_sh,
        agent_name=agent_name,
    )
    log("Pipeline paused — waiting for resume")
    mailbox.send(parent, "status:paused")
    buffered: list[str] = []
    while check_pipeline_state(planspace, db_sh=db_sh) == "paused":
        msg = mailbox.recv(timeout=_PAUSE_POLL_TIMEOUT_SECONDS)
        if msg == "TIMEOUT":
            continue
        if msg.startswith("abort"):
            log("Received abort while paused — shutting down")
            mailbox.send(parent, "fail:aborted")
            mailbox.cleanup()
            sys.exit(0)
        if msg.startswith(ControlSignal.ALIGNMENT_CHANGED):
            log("Alignment changed while paused — invalidating excerpts")
            Services.change_tracker().invalidate_excerpts(planspace)
            Services.change_tracker().set_flag(planspace)
            continue
        buffered.append(msg)
    for msg in buffered:
        mailbox.send(agent_name, msg)
    log("Pipeline resumed")
    mailbox.send(parent, "status:resumed")


def pause_for_parent(
    planspace: Path,
    parent: str,
    signal: str,
    *,
    db_sh: Path,
    agent_name: str,
) -> str:
    """Send a pause signal to the parent and wait for the next response."""
    log = Services.logger().log
    mailbox = MailboxService.for_planspace(
        planspace,
        db_sh=db_sh,
        agent_name=agent_name,
    )
    mailbox.send(parent, signal)
    while True:
        msg = mailbox.recv(timeout=0)
        if msg.startswith("abort"):
            log("Received abort — shutting down")
            mailbox.send(parent, "fail:aborted")
            mailbox.cleanup()
            sys.exit(0)
        if msg.startswith(ControlSignal.ALIGNMENT_CHANGED):
            log("Alignment changed during pause — invalidating excerpts")
            Services.change_tracker().invalidate_excerpts(planspace)
            Services.change_tracker().set_flag(planspace)
            continue
        return msg
