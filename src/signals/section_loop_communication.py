"""Compatibility facade for shared section-loop communication helpers."""

from signals.communication import (
    AGENT_NAME,
    DB_PATH,
    DB_SH,
    WORKFLOW_HOME,
    _db_client,
    _log_artifact,
    _mailbox,
    _record_traceability,
    _summary_tag,
    log,
    mailbox_cleanup,
    mailbox_drain,
    mailbox_recv,
    mailbox_register,
    mailbox_send,
)

__all__ = [
    "AGENT_NAME",
    "DB_PATH",
    "DB_SH",
    "WORKFLOW_HOME",
    "_db_client",
    "_log_artifact",
    "_mailbox",
    "_record_traceability",
    "_summary_tag",
    "log",
    "mailbox_cleanup",
    "mailbox_drain",
    "mailbox_recv",
    "mailbox_register",
    "mailbox_send",
]
