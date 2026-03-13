"""Mailbox polling for section-loop control messages."""

from __future__ import annotations

import sys
from pathlib import Path

from containers import Services
from orchestrator.types import ControlSignal
from signals.service.mailbox_service import MailboxService


def poll_control_messages(
    planspace: Path,
    parent: str,
    current_section: str | None = None,
    *,
    db_sh: Path,
    agent_name: str,
) -> str | None:
    """Drain and process abort/alignment_changed control messages."""
    log = Services.logger().log
    mailbox = MailboxService.for_planspace(
        planspace,
        db_sh=db_sh,
        agent_name=agent_name,
    )
    messages = mailbox.drain()
    alignment_changed = False
    for msg in messages:
        if msg.startswith("abort"):
            if current_section:
                mailbox.send(parent, f"fail:{current_section}:aborted")
            else:
                mailbox.send(parent, "fail:aborted")
            log("Received abort — shutting down")
            mailbox.cleanup()
            sys.exit(0)
        if msg.startswith("alignment_changed"):
            log("Alignment changed — invalidating excerpts and setting flag")
            Services.change_tracker().invalidate_excerpts(planspace)
            Services.change_tracker().set_flag(planspace)
            alignment_changed = True
            continue
        mailbox.send(agent_name, msg)
    if alignment_changed:
        return ControlSignal.ALIGNMENT_CHANGED
    return None


def check_for_messages(
    planspace: Path,
    *,
    db_sh: Path,
    agent_name: str,
) -> list[str]:
    """Drain all currently pending mailbox messages."""
    return MailboxService.for_planspace(
        planspace,
        db_sh=db_sh,
        agent_name=agent_name,
    ).drain()


def handle_pending_messages(
    planspace: Path,
    queue: list[str],
    completed: set[str],
    *,
    db_sh: Path,
    agent_name: str,
) -> bool:
    """Process pending mailbox messages. Returns True on abort."""
    log = Services.logger().log
    del queue, completed
    for msg in check_for_messages(
        planspace,
        db_sh=db_sh,
        agent_name=agent_name,
    ):
        if msg.startswith("abort"):
            return True
        if msg.startswith("alignment_changed"):
            log("Alignment changed — invalidating excerpts and setting flag")
            Services.change_tracker().invalidate_excerpts(planspace)
            Services.change_tracker().set_flag(planspace)
    return False
