"""Pipeline state queries and pause handling."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from containers import ChangeTrackerService, LogService

from signals.service.database_client import DatabaseClient
from signals.service.mailbox_service import MailboxService
from orchestrator.types import ControlSignal, PipelineAbortError

_PAUSE_POLL_TIMEOUT_SECONDS = 5
_DB_BODY_COLUMN_INDEX = 4
_DB_MIN_COLUMNS = 5
_PIPELINE_STATE_PAUSED = "paused"


class PipelineState:
    def __init__(
        self,
        logger: LogService,
        change_tracker: ChangeTrackerService,
    ) -> None:
        self._logger = logger
        self._change_tracker = change_tracker

    def _handle_control_msg(
        self,
        msg: str,
        mailbox: MailboxService,
        planspace: Path,
        parent: str,
    ) -> str | None:
        """Handle abort/alignment_changed control messages.

        Returns ``None`` if the message was handled (control signal),
        or the original ``msg`` if it should be processed by the caller.
        """
        log = self._logger.log
        if msg.startswith(ControlSignal.ABORT):
            log("Received abort — shutting down")
            mailbox.send(parent, "fail:aborted")
            mailbox.cleanup()
            raise PipelineAbortError("abort received")
        if msg.startswith(ControlSignal.ALIGNMENT_CHANGED):
            log("Alignment changed — invalidating excerpts")
            self._change_tracker.invalidate_excerpts(planspace)
            self._change_tracker.set_flag(planspace)
            return None
        return msg

    def wait_if_paused(
        self,
        planspace: Path,
        parent: str,
        *,
        db_sh: Path,
        agent_name: str,
    ) -> None:
        """Block while the pipeline is paused, buffering non-control messages."""
        if check_pipeline_state(planspace, db_sh=db_sh) != _PIPELINE_STATE_PAUSED:
            return
        log = self._logger.log
        mailbox = MailboxService.for_planspace(
            planspace,
            db_sh=db_sh,
            agent_name=agent_name,
        )
        log("Pipeline paused — waiting for resume")
        mailbox.send(parent, "status:paused")
        buffered: list[str] = []
        while check_pipeline_state(planspace, db_sh=db_sh) == _PIPELINE_STATE_PAUSED:
            msg = mailbox.recv(timeout=_PAUSE_POLL_TIMEOUT_SECONDS)
            if msg == "TIMEOUT":
                continue
            result = self._handle_control_msg(msg, mailbox, planspace, parent)
            if result is None:
                continue
            buffered.append(result)
        for msg in buffered:
            mailbox.send(agent_name, msg)
        log("Pipeline resumed")
        mailbox.send(parent, "status:resumed")

    def pause_for_parent(
        self,
        planspace: Path,
        parent: str,
        signal: str,
        *,
        db_sh: Path,
        agent_name: str,
    ) -> str:
        """Send a pause signal to the parent and wait for the next response."""
        log = self._logger.log
        mailbox = MailboxService.for_planspace(
            planspace,
            db_sh=db_sh,
            agent_name=agent_name,
        )
        mailbox.send(parent, signal)
        while True:
            msg = mailbox.recv(timeout=0)
            result = self._handle_control_msg(msg, mailbox, planspace, parent)
            if result is None:
                continue
            return result


# Pure function — no Services usage

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
        if len(parts) >= _DB_MIN_COLUMNS and parts[_DB_BODY_COLUMN_INDEX]:
            return parts[_DB_BODY_COLUMN_INDEX]
    return "running"


# Backward-compat wrappers

def _get_pipeline_state() -> PipelineState:
    from containers import Services
    return PipelineState(
        logger=Services.logger(),
        change_tracker=Services.change_tracker(),
    )


def _handle_control_msg(
    msg: str,
    mailbox: MailboxService,
    planspace: Path,
    parent: str,
) -> str | None:
    return _get_pipeline_state()._handle_control_msg(msg, mailbox, planspace, parent)


def wait_if_paused(
    planspace: Path,
    parent: str,
    *,
    db_sh: Path,
    agent_name: str,
) -> None:
    return _get_pipeline_state().wait_if_paused(
        planspace, parent, db_sh=db_sh, agent_name=agent_name,
    )


def pause_for_parent(
    planspace: Path,
    parent: str,
    signal: str,
    *,
    db_sh: Path,
    agent_name: str,
) -> str:
    return _get_pipeline_state().pause_for_parent(
        planspace, parent, signal, db_sh=db_sh, agent_name=agent_name,
    )
