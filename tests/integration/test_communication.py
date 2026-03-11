"""Integration tests for communication module.

Tests pure-logic helpers and file-I/O traceability.
Mailbox functions use real db.sh (SQLite).
"""

import json
from pathlib import Path

from signals.service.communication import (
    _record_traceability,
    _summary_tag,
    mailbox_drain,
    mailbox_recv,
    mailbox_register,
    mailbox_send,
)


class TestSummaryTag:
    def test_summary_prefix(self) -> None:
        assert _summary_tag("summary:proposal-align:03:PROBLEMS") == "proposal-align:03"

    def test_status_prefix(self) -> None:
        assert _summary_tag("status:coordination:round-2") == "coordination:round-2"

    def test_done_prefix(self) -> None:
        assert _summary_tag("done:03:5 files modified") == "done:03"

    def test_fail_prefix(self) -> None:
        assert _summary_tag("fail:03:error") == "fail:03"

    def test_complete(self) -> None:
        assert _summary_tag("complete") == "complete"

    def test_pause_prefix(self) -> None:
        assert _summary_tag("pause:underspec:03:detail") == "underspec:03"

    def test_unknown_prefix(self) -> None:
        assert _summary_tag("foobar:stuff") == "foobar"


class TestRecordTraceability:
    def test_creates_traceability_file(self, planspace: Path) -> None:
        _record_traceability(
            planspace, "01", "section-01-todos.md",
            "related files TODO extraction",
            "in-code microstrategies",
        )
        trace = planspace / "artifacts" / "traceability.json"
        assert trace.exists()
        entries = json.loads(trace.read_text())
        assert len(entries) == 1
        assert entries[0]["section"] == "01"
        assert entries[0]["artifact"] == "section-01-todos.md"
        assert entries[0]["source"] == "related files TODO extraction"

    def test_appends_to_existing(self, planspace: Path) -> None:
        _record_traceability(planspace, "01", "artifact-a", "source-a")
        _record_traceability(planspace, "02", "artifact-b", "source-b")
        entries = json.loads(
            (planspace / "artifacts" / "traceability.json").read_text(),
        )
        assert len(entries) == 2
        assert entries[0]["section"] == "01"
        assert entries[1]["section"] == "02"

    def test_handles_corrupt_existing_file(self, planspace: Path) -> None:
        trace = planspace / "artifacts" / "traceability.json"
        trace.write_text("not valid json {{{")
        _record_traceability(planspace, "03", "artifact-c", "source-c")
        entries = json.loads(trace.read_text())
        assert len(entries) == 1
        assert entries[0]["section"] == "03"


class TestMailboxIntegration:
    """Tests mailbox operations using real db.sh + SQLite."""

    def test_send_and_drain(self, planspace: Path) -> None:
        # Register a test recipient
        mailbox_register(planspace)
        mailbox_send(planspace, "section-loop", "test message one")
        mailbox_send(planspace, "section-loop", "test message two")
        msgs = mailbox_drain(planspace)
        assert len(msgs) == 2
        assert "test message one" in msgs[0]
        assert "test message two" in msgs[1]

    def test_drain_empty(self, planspace: Path) -> None:
        mailbox_register(planspace)
        msgs = mailbox_drain(planspace)
        assert msgs == []

    def test_recv_timeout(self, planspace: Path) -> None:
        mailbox_register(planspace)
        msg = mailbox_recv(planspace, timeout=1)
        assert msg == "TIMEOUT"

    def test_recv_gets_message(self, planspace: Path) -> None:
        mailbox_register(planspace)
        mailbox_send(planspace, "section-loop", "hello from test")
        msg = mailbox_recv(planspace, timeout=2)
        assert "hello from test" in msg
