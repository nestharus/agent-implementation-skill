"""Tests for the Claude Code session log extractor."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from log_extract.extractors.claude import iter_events, iter_session_candidates


# ------------------------------------------------------------------
# Fixture helpers
# ------------------------------------------------------------------

def _write_jsonl(path: Path, records: list[dict | str]) -> None:
    """Write records as JSONL.  Strings are written raw (for malformed-line tests)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for rec in records:
            if isinstance(rec, str):
                fh.write(rec + "\n")
            else:
                fh.write(json.dumps(rec) + "\n")


def _make_claude_home(tmp_path: Path) -> Path:
    """Return a claude_home root with ``projects/fake-project/`` ready."""
    home = tmp_path / "claude_home"
    (home / "projects" / "fake-project").mkdir(parents=True)
    return home


# ------------------------------------------------------------------
# Sample records
# ------------------------------------------------------------------

_QUEUE_RECORD = {
    "type": "queue-operation",
    "operation": "enqueue",
    "timestamp": "2026-02-23T09:04:47.564Z",
    "sessionId": "sess-001",
    "content": "Implement the frobnicator module with full test coverage",
}

_USER_RECORD = {
    "type": "user",
    "cwd": "/home/dev/myproject",
    "sessionId": "sess-001",
    "version": "2.1.50",
    "timestamp": "2026-02-23T09:05:10.000Z",
    "message": {
        "role": "user",
        "content": "Please also add type hints to all public functions",
    },
}

_ASSISTANT_RECORD_WITH_TOOLS = {
    "type": "assistant",
    "timestamp": "2026-02-23T09:05:35.200Z",
    "sessionId": "sess-001",
    "costUSD": 0.042,
    "durationMs": 8200,
    "message": {
        "role": "assistant",
        "content": [
            {"type": "text", "text": "I will start by reading the existing code."},
            {"type": "tool_use", "id": "tu-1", "name": "Read", "input": {"file_path": "/src/main.py"}},
            {"type": "tool_result", "tool_use_id": "tu-1", "content": "def main(): pass"},
        ],
    },
}

_ASSISTANT_RECORD_PLAIN = {
    "type": "assistant",
    "timestamp": "2026-02-23T09:06:00.000Z",
    "sessionId": "sess-001",
    "message": {
        "role": "assistant",
        "content": "Done. All functions now have type hints.",
    },
}


# ------------------------------------------------------------------
# Tests
# ------------------------------------------------------------------


class TestIterEvents:
    """iter_events: event generation from JSONL records."""

    def test_basic_event_types(self, tmp_path: Path) -> None:
        """Queue, user, and assistant records produce the expected event kinds."""
        home = _make_claude_home(tmp_path)
        _write_jsonl(
            home / "projects" / "fake-project" / "test-session.jsonl",
            [_QUEUE_RECORD, _USER_RECORD, _ASSISTANT_RECORD_WITH_TOOLS, _ASSISTANT_RECORD_PLAIN],
        )

        events = list(iter_events([home]))

        kinds = [e.kind for e in events]
        # queue-operation -> dispatch
        # user -> message
        # assistant with tool_use + tool_result -> response + tool_call + tool_result
        # assistant plain -> response
        assert kinds == ["dispatch", "message", "response", "tool_call", "tool_result", "response"]

    def test_source_and_backend(self, tmp_path: Path) -> None:
        """All events carry source='claude' and backend='claude2'."""
        home = _make_claude_home(tmp_path)
        _write_jsonl(
            home / "projects" / "fake-project" / "s.jsonl",
            [_QUEUE_RECORD, _USER_RECORD],
        )

        events = list(iter_events([home]))
        assert all(e.source == "claude" for e in events)
        assert all(e.backend == "claude2" for e in events)

    def test_tool_use_detail(self, tmp_path: Path) -> None:
        """tool_call events include the tool name in their detail."""
        home = _make_claude_home(tmp_path)
        _write_jsonl(
            home / "projects" / "fake-project" / "s.jsonl",
            [_ASSISTANT_RECORD_WITH_TOOLS],
        )

        events = list(iter_events([home]))
        tool_calls = [e for e in events if e.kind == "tool_call"]
        assert len(tool_calls) == 1
        assert "Read" in tool_calls[0].detail

    def test_tool_result_detail(self, tmp_path: Path) -> None:
        """tool_result events include the tool_use_id in their detail."""
        home = _make_claude_home(tmp_path)
        _write_jsonl(
            home / "projects" / "fake-project" / "s.jsonl",
            [_ASSISTANT_RECORD_WITH_TOOLS],
        )

        events = list(iter_events([home]))
        tool_results = [e for e in events if e.kind == "tool_result"]
        assert len(tool_results) == 1
        assert "tu-1" in tool_results[0].detail

    def test_session_id_from_record(self, tmp_path: Path) -> None:
        """Session ID is taken from the record's sessionId field."""
        home = _make_claude_home(tmp_path)
        _write_jsonl(
            home / "projects" / "fake-project" / "whatever.jsonl",
            [_QUEUE_RECORD],
        )

        events = list(iter_events([home]))
        assert events[0].session_id == "sess-001"

    def test_session_id_falls_back_to_filename(self, tmp_path: Path) -> None:
        """When sessionId is missing, use the file stem."""
        home = _make_claude_home(tmp_path)
        record = {
            "type": "queue-operation",
            "operation": "enqueue",
            "timestamp": "2026-02-23T09:00:00.000Z",
            "content": "hello",
        }
        _write_jsonl(
            home / "projects" / "fake-project" / "fallback-id.jsonl",
            [record],
        )

        events = list(iter_events([home]))
        assert events[0].session_id == "fallback-id"

    def test_dispatch_detail_summarized(self, tmp_path: Path) -> None:
        """Dispatch detail is a summarized version of the prompt content."""
        home = _make_claude_home(tmp_path)
        long_content = "x" * 300
        record = {
            "type": "queue-operation",
            "operation": "enqueue",
            "timestamp": "2026-01-01T00:00:00Z",
            "content": long_content,
        }
        _write_jsonl(
            home / "projects" / "p" / "s.jsonl",
            [record],
        )

        events = list(iter_events([home]))
        assert len(events[0].detail) == 300  # full content, no truncation


class TestIterSessionCandidates:
    """iter_session_candidates: session metadata extraction."""

    def test_basic_candidate(self, tmp_path: Path) -> None:
        """A session candidate is created from a valid JSONL file."""
        home = _make_claude_home(tmp_path)
        _write_jsonl(
            home / "projects" / "fake-project" / "test-session.jsonl",
            [_QUEUE_RECORD, _USER_RECORD, _ASSISTANT_RECORD_PLAIN],
        )

        candidates = list(iter_session_candidates([home]))
        assert len(candidates) == 1

        c = candidates[0]
        assert c.session_id == "sess-001"
        assert c.backend == "claude2"
        assert c.source_family == "claude"
        assert c.cwd == "/home/dev/myproject"
        assert c.prompt_signature != ""  # hashed first prompt

    def test_earliest_timestamp_used(self, tmp_path: Path) -> None:
        """The candidate timestamp is the earliest across all records."""
        home = _make_claude_home(tmp_path)
        early = {
            "type": "queue-operation",
            "operation": "enqueue",
            "timestamp": "2026-01-01T00:00:00Z",
            "content": "early",
        }
        late = {
            "type": "queue-operation",
            "operation": "enqueue",
            "timestamp": "2026-12-31T23:59:59Z",
            "content": "late",
        }
        _write_jsonl(
            home / "projects" / "p" / "s.jsonl",
            [late, early],  # intentionally reversed
        )

        candidates = list(iter_session_candidates([home]))
        assert len(candidates) == 1
        # The candidate should use the earlier timestamp
        assert "2026-01-01" in candidates[0].ts

    def test_no_valid_timestamp_skips(self, tmp_path: Path) -> None:
        """A file with no valid timestamps produces no candidate."""
        home = _make_claude_home(tmp_path)
        record = {"type": "user", "message": {"role": "user", "content": "hi"}}
        _write_jsonl(
            home / "projects" / "p" / "s.jsonl",
            [record],
        )

        candidates = list(iter_session_candidates([home]))
        assert len(candidates) == 0


class TestMalformedInput:
    """Robustness against bad data."""

    def test_truncated_line_warns_and_continues(self, tmp_path: Path, capsys) -> None:
        """A truncated JSON line is skipped with a warning; valid lines still parse."""
        home = _make_claude_home(tmp_path)
        _write_jsonl(
            home / "projects" / "fake-project" / "s.jsonl",
            [
                '{"type": "queue-operation", "operation": "enqueue", "timestamp": "2026-01-01T00:00:00Z", "content": "ok"}',
                '{"type": "broken',  # truncated
                json.dumps(_USER_RECORD),
            ],
        )

        events = list(iter_events([home]))
        # Should get events from line 1 and line 3, skipping line 2
        assert len(events) >= 2

        captured = capsys.readouterr()
        assert "WARNING" in captured.err
        assert "malformed" in captured.err.lower()

    def test_truncated_line_does_not_kill_session_candidate(self, tmp_path: Path) -> None:
        """Session candidate is still produced despite a bad line."""
        home = _make_claude_home(tmp_path)
        _write_jsonl(
            home / "projects" / "p" / "s.jsonl",
            [
                '{"totally broken',
                json.dumps(_QUEUE_RECORD),
            ],
        )

        candidates = list(iter_session_candidates([home]))
        assert len(candidates) == 1

    def test_empty_lines_ignored(self, tmp_path: Path) -> None:
        """Blank lines in the JSONL file are silently skipped."""
        home = _make_claude_home(tmp_path)
        _write_jsonl(
            home / "projects" / "p" / "s.jsonl",
            [
                json.dumps(_QUEUE_RECORD),
                "",
                "",
                json.dumps(_USER_RECORD),
            ],
        )

        events = list(iter_events([home]))
        assert len(events) == 2


class TestEmptyAndMissing:
    """Edge cases for directory structure."""

    def test_empty_directory_produces_no_events(self, tmp_path: Path) -> None:
        """A claude_home with no projects/ subdirectory yields nothing."""
        home = tmp_path / "empty_home"
        home.mkdir()

        events = list(iter_events([home]))
        assert events == []

    def test_missing_directory_produces_no_events(self, tmp_path: Path) -> None:
        """A non-existent path yields nothing (not an error)."""
        fake = tmp_path / "does_not_exist"

        events = list(iter_events([fake]))
        assert events == []

        candidates = list(iter_session_candidates([fake]))
        assert candidates == []

    def test_empty_projects_dir(self, tmp_path: Path) -> None:
        """A claude_home with an empty projects/ directory yields nothing."""
        home = tmp_path / "home"
        (home / "projects").mkdir(parents=True)

        events = list(iter_events([home]))
        assert events == []

    def test_multiple_claude_homes(self, tmp_path: Path) -> None:
        """Events are aggregated across multiple claude_home directories."""
        home1 = tmp_path / "home1"
        home2 = tmp_path / "home2"
        for h in (home1, home2):
            (h / "projects" / "proj").mkdir(parents=True)

        rec1 = {
            "type": "queue-operation",
            "operation": "enqueue",
            "timestamp": "2026-01-01T00:00:00Z",
            "sessionId": "s1",
            "content": "from home1",
        }
        rec2 = {
            "type": "queue-operation",
            "operation": "enqueue",
            "timestamp": "2026-01-02T00:00:00Z",
            "sessionId": "s2",
            "content": "from home2",
        }
        _write_jsonl(home1 / "projects" / "proj" / "a.jsonl", [rec1])
        _write_jsonl(home2 / "projects" / "proj" / "b.jsonl", [rec2])

        events = list(iter_events([home1, home2]))
        assert len(events) == 2
        session_ids = {e.session_id for e in events}
        assert session_ids == {"s1", "s2"}
