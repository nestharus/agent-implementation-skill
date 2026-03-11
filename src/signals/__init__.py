"""Signals system: JSON I/O, logging, mailbox communication, database access.

Public API (import from submodules):
    artifact_io: read_json, read_json_or_default, rename_malformed, write_json
    database_client: DatabaseClient
    mailbox_service: MailboxService
    section_loop_communication: AGENT_NAME, DB_SH, log, mailbox_send
    signal_reader: read_agent_signal, read_signal_tuple
"""
