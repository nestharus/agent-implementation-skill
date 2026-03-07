"""Tests for the Codex session log extractor."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from log_extract.extractors.codex import iter_events, iter_session_candidates


# ------------------------------------------------------------------
# Fixtures
# ------------------------------------------------------------------

_SESSION_META = {
    "timestamp": "2026-03-01T10:00:00.000Z",
    "type": "session_meta",
    "payload": {
        "id": "019c668b-abcd-1234-5678-aabbccddeeff",
        "timestamp": "2026-03-01T10:00:00.000Z",
        "cwd": "/home/user/my-project",
        "originator": "codex_exec",
        "cli_version": "0.101.0",
        "source": "exec",
        "model_provider": "openai",
        "git": {
            "commit_hash": "abc123",
            "branch": "main",
            "repository_url": "https://github.com/example/repo",
        },
    },
}

_USER_MESSAGE = {
    "timestamp": "2026-03-01T10:00:01.000Z",
    "type": "response_item",
    "payload": {
        "type": "message",
        "role": "user",
        "content": [{"type": "input_text", "text": "Fix the login bug in auth.py"}],
    },
}

_ASSISTANT_MESSAGE = {
    "timestamp": "2026-03-01T10:00:05.000Z",
    "type": "response_item",
    "payload": {
        "type": "message",
        "role": "assistant",
        "content": [
            {"type": "input_text", "text": "I'll look at the auth.py file to fix the login issue."}
        ],
    },
}

_TASK_STARTED = {
    "timestamp": "2026-03-01T10:00:02.000Z",
    "type": "event_msg",
    "payload": {
        "type": "task_started",
        "turn_id": "turn-001",
        "model_context_window": 258400,
    },
}

_TASK_COMPLETED = {
    "timestamp": "2026-03-01T10:00:10.000Z",
    "type": "event_msg",
    "payload": {
        "type": "task_completed",
        "turn_id": "turn-001",
    },
}


def _write_rollout(tmp_path: Path, records: list[dict], filename: str | None = None) -> Path:
    """Write a JSONL rollout file in the expected directory structure."""
    sessions_dir = tmp_path / "sessions" / "2026" / "03" / "01"
    sessions_dir.mkdir(parents=True, exist_ok=True)
    name = filename or "rollout-2026-03-01T10-00-00-test-uuid.jsonl"
    rollout = sessions_dir / name
    lines = [json.dumps(r) for r in records]
    rollout.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return rollout


# ------------------------------------------------------------------
# iter_events tests
# ------------------------------------------------------------------


class TestIterEvents:
    def test_session_meta_emits_session_event(self, tmp_path: Path) -> None:
        _write_rollout(tmp_path, [_SESSION_META])
        events = list(iter_events([tmp_path]))

        assert len(events) == 1
        ev = events[0]
        assert ev.source == "codex"
        assert ev.kind == "session"
        assert ev.backend == "codex2"
        assert ev.session_id == "019c668b-abcd-1234-5678-aabbccddeeff"
        assert ev.ts_ms > 0

    def test_user_message_emits_message_event(self, tmp_path: Path) -> None:
        _write_rollout(tmp_path, [_USER_MESSAGE])
        events = list(iter_events([tmp_path]))

        assert len(events) == 1
        ev = events[0]
        assert ev.kind == "message"
        assert "login bug" in ev.detail

    def test_assistant_message_emits_response_event(self, tmp_path: Path) -> None:
        _write_rollout(tmp_path, [_ASSISTANT_MESSAGE])
        events = list(iter_events([tmp_path]))

        assert len(events) == 1
        ev = events[0]
        assert ev.kind == "response"
        assert "auth.py" in ev.detail

    def test_task_started_emits_task_event(self, tmp_path: Path) -> None:
        _write_rollout(tmp_path, [_TASK_STARTED])
        events = list(iter_events([tmp_path]))

        assert len(events) == 1
        ev = events[0]
        assert ev.kind == "task"
        assert ev.detail == "task started"

    def test_task_completed_emits_task_event(self, tmp_path: Path) -> None:
        _write_rollout(tmp_path, [_TASK_COMPLETED])
        events = list(iter_events([tmp_path]))

        assert len(events) == 1
        ev = events[0]
        assert ev.kind == "task"
        assert ev.detail == "task completed"

    def test_full_session_produces_all_events(self, tmp_path: Path) -> None:
        records = [
            _SESSION_META,
            _USER_MESSAGE,
            _TASK_STARTED,
            _ASSISTANT_MESSAGE,
            _TASK_COMPLETED,
        ]
        _write_rollout(tmp_path, records)
        events = list(iter_events([tmp_path]))

        assert len(events) == 5
        kinds = [e.kind for e in events]
        assert kinds == ["session", "message", "task", "response", "task"]

    def test_all_events_have_codex_source(self, tmp_path: Path) -> None:
        records = [_SESSION_META, _USER_MESSAGE, _ASSISTANT_MESSAGE]
        _write_rollout(tmp_path, records)
        events = list(iter_events([tmp_path]))

        for ev in events:
            assert ev.source == "codex"
            assert ev.backend == "codex2"

    def test_multiple_homes(self, tmp_path: Path) -> None:
        home_a = tmp_path / "home_a"
        home_b = tmp_path / "home_b"
        home_a.mkdir()
        home_b.mkdir()

        _write_rollout(home_a, [_SESSION_META])
        _write_rollout(home_b, [_USER_MESSAGE], filename="rollout-other.jsonl")

        events = list(iter_events([home_a, home_b]))
        assert len(events) == 2


# ------------------------------------------------------------------
# iter_session_candidates tests
# ------------------------------------------------------------------


class TestIterSessionCandidates:
    def test_session_candidate_from_meta(self, tmp_path: Path) -> None:
        records = [_SESSION_META, _USER_MESSAGE, _ASSISTANT_MESSAGE]
        _write_rollout(tmp_path, records)

        candidates = list(iter_session_candidates([tmp_path]))
        assert len(candidates) == 1

        sc = candidates[0]
        assert sc.session_id == "019c668b-abcd-1234-5678-aabbccddeeff"
        assert sc.backend == "codex2"
        assert sc.source_family == "codex"
        assert sc.cwd == "/home/user/my-project"
        assert sc.ts_ms > 0
        assert sc.prompt_signature  # non-empty hash

    def test_prompt_signature_from_first_user_message(self, tmp_path: Path) -> None:
        records = [_SESSION_META, _USER_MESSAGE, _ASSISTANT_MESSAGE]
        _write_rollout(tmp_path, records)

        candidates = list(iter_session_candidates([tmp_path]))
        sc = candidates[0]

        # The signature should be a sha256 hex digest
        assert len(sc.prompt_signature) == 64

    def test_no_meta_no_candidate(self, tmp_path: Path) -> None:
        """A file without session_meta should not produce a candidate."""
        _write_rollout(tmp_path, [_USER_MESSAGE, _ASSISTANT_MESSAGE])

        candidates = list(iter_session_candidates([tmp_path]))
        assert len(candidates) == 0

    def test_meta_without_user_message_still_produces_candidate(self, tmp_path: Path) -> None:
        """session_meta alone is enough; prompt_signature will be empty."""
        _write_rollout(tmp_path, [_SESSION_META])

        candidates = list(iter_session_candidates([tmp_path]))
        assert len(candidates) == 1
        assert candidates[0].prompt_signature == ""

    def test_cwd_from_payload(self, tmp_path: Path) -> None:
        _write_rollout(tmp_path, [_SESSION_META])
        candidates = list(iter_session_candidates([tmp_path]))
        assert candidates[0].cwd == "/home/user/my-project"


# ------------------------------------------------------------------
# Robustness tests
# ------------------------------------------------------------------


class TestRobustness:
    def test_missing_sessions_dir_yields_nothing(self, tmp_path: Path) -> None:
        """A codex_home without sessions/ should produce zero events, no error."""
        events = list(iter_events([tmp_path]))
        assert events == []

        candidates = list(iter_session_candidates([tmp_path]))
        assert candidates == []

    def test_nonexistent_home_yields_nothing(self, tmp_path: Path) -> None:
        fake = tmp_path / "does-not-exist"
        events = list(iter_events([fake]))
        assert events == []

    def test_truncated_line_skipped(self, tmp_path: Path) -> None:
        """A truncated JSON line should be warned and skipped."""
        sessions_dir = tmp_path / "sessions" / "2026" / "03" / "01"
        sessions_dir.mkdir(parents=True)
        rollout = sessions_dir / "rollout-truncated.jsonl"
        good_line = json.dumps(_SESSION_META)
        rollout.write_text(good_line + "\n" + '{"truncated": true, "bad\n', encoding="utf-8")

        events = list(iter_events([tmp_path]))
        # Should get the good line, skip the bad one
        assert len(events) == 1
        assert events[0].kind == "session"

    def test_missing_optional_fields_no_crash(self, tmp_path: Path) -> None:
        """Records with missing optional payload fields should not crash."""
        minimal_meta = {
            "timestamp": "2026-03-01T10:00:00.000Z",
            "type": "session_meta",
            "payload": {"id": "minimal-session"},
        }
        minimal_response = {
            "timestamp": "2026-03-01T10:00:01.000Z",
            "type": "response_item",
            "payload": {"role": "assistant"},
            # no content field at all
        }
        minimal_event = {
            "timestamp": "2026-03-01T10:00:02.000Z",
            "type": "event_msg",
            "payload": {"type": "task_started"},
        }
        _write_rollout(tmp_path, [minimal_meta, minimal_response, minimal_event])

        events = list(iter_events([tmp_path]))
        assert len(events) == 3

        candidates = list(iter_session_candidates([tmp_path]))
        assert len(candidates) == 1
        sc = candidates[0]
        assert sc.session_id == "minimal-session"
        assert sc.cwd == ""
        assert sc.prompt_signature == ""

    def test_empty_content_blocks(self, tmp_path: Path) -> None:
        """Response items with empty content blocks should produce empty detail."""
        record = {
            "timestamp": "2026-03-01T10:00:01.000Z",
            "type": "response_item",
            "payload": {
                "role": "user",
                "content": [],
            },
        }
        _write_rollout(tmp_path, [record])

        events = list(iter_events([tmp_path]))
        assert len(events) == 1
        assert events[0].detail == ""

    def test_unknown_event_msg_type_skipped(self, tmp_path: Path) -> None:
        """event_msg with a type other than task_started/task_completed is ignored."""
        record = {
            "timestamp": "2026-03-01T10:00:01.000Z",
            "type": "event_msg",
            "payload": {"type": "something_else"},
        }
        _write_rollout(tmp_path, [record])

        events = list(iter_events([tmp_path]))
        assert len(events) == 0

    def test_record_without_timestamp_skipped(self, tmp_path: Path) -> None:
        """Records missing the timestamp field should be skipped."""
        record = {
            "type": "session_meta",
            "payload": {"id": "no-ts"},
        }
        _write_rollout(tmp_path, [record])

        events = list(iter_events([tmp_path]))
        assert len(events) == 0

    def test_developer_role_emits_message(self, tmp_path: Path) -> None:
        """Developer role messages should emit kind='message' like user."""
        record = {
            "timestamp": "2026-03-01T10:00:01.000Z",
            "type": "response_item",
            "payload": {
                "role": "developer",
                "content": [{"type": "input_text", "text": "system instructions"}],
            },
        }
        _write_rollout(tmp_path, [record])

        events = list(iter_events([tmp_path]))
        assert len(events) == 1
        assert events[0].kind == "message"

    def test_empty_file_yields_nothing(self, tmp_path: Path) -> None:
        sessions_dir = tmp_path / "sessions" / "2026" / "03" / "01"
        sessions_dir.mkdir(parents=True)
        rollout = sessions_dir / "rollout-empty.jsonl"
        rollout.write_text("", encoding="utf-8")

        events = list(iter_events([tmp_path]))
        assert events == []
