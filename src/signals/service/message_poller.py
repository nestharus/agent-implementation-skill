"""Mailbox polling for section-loop control messages."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from orchestrator.types import ControlSignal, PipelineAbortError
from signals.service.mailbox_service import MailboxService
from signals.service.section_communicator import send_to_parent

if TYPE_CHECKING:
    from containers import ChangeTrackerService, LogService


class MessagePoller:
    """Polls and processes mailbox control messages (abort, alignment_changed)."""

    def __init__(
        self,
        logger: LogService,
        change_tracker: ChangeTrackerService,
    ) -> None:
        self._logger = logger
        self._change_tracker = change_tracker

    def poll_control_messages(
        self,
        planspace: Path,
        current_section: str | None = None,
        *,
        db_sh: Path,
        agent_name: str,
    ) -> str | None:
        """Drain and process abort/alignment_changed control messages."""
        log = self._logger.log
        mailbox = MailboxService.for_planspace(
            planspace,
            db_sh=db_sh,
            agent_name=agent_name,
        )
        messages = mailbox.drain()
        alignment_changed = False
        for msg in messages:
            if msg.startswith(ControlSignal.ABORT):
                if current_section:
                    send_to_parent(planspace, f"fail:{current_section}:aborted")
                else:
                    send_to_parent(planspace, "fail:aborted")
                log("Received abort — shutting down")
                mailbox.cleanup()
                raise PipelineAbortError("abort received")
            if msg.startswith(ControlSignal.ALIGNMENT_CHANGED):
                log("Alignment changed — invalidating excerpts and setting flag")
                self._change_tracker.invalidate_excerpts(planspace)
                self._change_tracker.set_flag(planspace)
                alignment_changed = True
                continue
            mailbox.send(agent_name, msg)
        if alignment_changed:
            return ControlSignal.ALIGNMENT_CHANGED
        return None

    def handle_pending_messages(
        self,
        planspace: Path,
        *,
        db_sh: Path,
        agent_name: str,
    ) -> bool:
        """Process pending mailbox messages. Returns True on abort."""
        log = self._logger.log
        for msg in check_for_messages(
            planspace,
            db_sh=db_sh,
            agent_name=agent_name,
        ):
            if msg.startswith(ControlSignal.ABORT):
                return True
            if msg.startswith(ControlSignal.ALIGNMENT_CHANGED):
                log("Alignment changed — invalidating excerpts and setting flag")
                self._change_tracker.invalidate_excerpts(planspace)
                self._change_tracker.set_flag(planspace)
        return False


# ── Pure function (no Services dependency) ────────────────────────────

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


# ── Backward-compat wrappers (called by containers.py) ───────────────

def poll_control_messages(
    planspace: Path,
    current_section: str | None = None,
    *,
    db_sh: Path,
    agent_name: str,
) -> str | None:
    """Drain and process abort/alignment_changed control messages."""
    from containers import Services
    poller = MessagePoller(
        logger=Services.logger(),
        change_tracker=Services.change_tracker(),
    )
    return poller.poll_control_messages(
        planspace, current_section,
        db_sh=db_sh, agent_name=agent_name,
    )


def handle_pending_messages(
    planspace: Path,
    *,
    db_sh: Path,
    agent_name: str,
) -> bool:
    """Process pending mailbox messages. Returns True on abort."""
    from containers import Services
    poller = MessagePoller(
        logger=Services.logger(),
        change_tracker=Services.change_tracker(),
    )
    return poller.handle_pending_messages(
        planspace,
        db_sh=db_sh, agent_name=agent_name,
    )
