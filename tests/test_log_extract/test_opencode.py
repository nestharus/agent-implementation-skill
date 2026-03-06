"""Tests for log_extract.extractors.opencode against a temporary SQLite fixture."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from log_extract.extractors.opencode import iter_events, iter_session_candidates

# ------------------------------------------------------------------
# Schema + seed data
# ------------------------------------------------------------------

_SCHEMA = """\
CREATE TABLE session (
    id TEXT PRIMARY KEY,
    project_id TEXT,
    parent_id TEXT,
    slug TEXT,
    directory TEXT,
    title TEXT,
    version TEXT,
    time_created TEXT,
    time_updated TEXT
);
CREATE TABLE message (
    id TEXT PRIMARY KEY,
    session_id TEXT,
    time_created TEXT,
    time_updated TEXT,
    data TEXT
);
CREATE TABLE part (
    id TEXT PRIMARY KEY,
    message_id TEXT,
    session_id TEXT,
    time_created TEXT,
    time_updated TEXT,
    data TEXT
);
"""

_USER_MSG_DATA = json.dumps({
    "role": "user",
    "time": {"created": 1770325360000, "completed": 1770325360500},
    "parentID": "",
    "modelID": "",
    "providerID": "",
    "mode": "build",
    "agent": "build",
    "cost": 0,
    "tokens": {"input": 0, "output": 0, "reasoning": 0, "cache": {"read": 0, "write": 0}},
    "finish": "",
})

_ASSISTANT_MSG_DATA = json.dumps({
    "role": "assistant",
    "time": {"created": 1770325366978, "completed": 1770325373166},
    "parentID": "msg_user_1",
    "modelID": "zai-glm-4.7",
    "providerID": "cerebras",
    "mode": "build",
    "agent": "build",
    "cost": 0,
    "tokens": {"input": 291, "output": 572, "reasoning": 496, "cache": {"read": 21120, "write": 0}},
    "finish": "stop",
})

_STEP_FINISH_PART_DATA = json.dumps({
    "type": "step-finish",
    "reason": "tool-calls",
    "snapshot": "",
    "cost": 0,
    "tokens": {"input": 1373, "output": 2065, "reasoning": 446, "cache": {"read": 41088, "write": 0}},
})

_USER_TEXT_PART_DATA = json.dumps({
    "type": "text",
    "text": "Implement the authentication module for the project",
})

_SEED = """\
INSERT INTO session (id, project_id, parent_id, slug, directory, title, version, time_created, time_updated)
VALUES ('sess_001', 'proj_001', NULL, 'my-session', '/home/user/myproject', 'My Session', '1.0', '2026-02-06T12:00:00Z', '2026-02-06T13:00:00Z');

INSERT INTO message (id, session_id, time_created, time_updated, data)
VALUES ('msg_user_1', 'sess_001', '2026-02-06T12:01:00Z', '2026-02-06T12:01:00Z', '{user_msg_data}');

INSERT INTO message (id, session_id, time_created, time_updated, data)
VALUES ('msg_asst_1', 'sess_001', '2026-02-06T12:02:00Z', '2026-02-06T12:02:30Z', '{assistant_msg_data}');

INSERT INTO part (id, message_id, session_id, time_created, time_updated, data)
VALUES ('part_text_1', 'msg_user_1', 'sess_001', '2026-02-06T12:01:00Z', '2026-02-06T12:01:00Z', '{user_text_part_data}');

INSERT INTO part (id, message_id, session_id, time_created, time_updated, data)
VALUES ('part_step_1', 'msg_asst_1', 'sess_001', '2026-02-06T12:02:35Z', '2026-02-06T12:02:35Z', '{step_finish_part_data}');
"""


def _build_seed() -> str:
    """Interpolate JSON blobs into the seed SQL, escaping single quotes."""
    return _SEED.format(
        user_msg_data=_USER_MSG_DATA.replace("'", "''"),
        assistant_msg_data=_ASSISTANT_MSG_DATA.replace("'", "''"),
        user_text_part_data=_USER_TEXT_PART_DATA.replace("'", "''"),
        step_finish_part_data=_STEP_FINISH_PART_DATA.replace("'", "''"),
    )


# ------------------------------------------------------------------
# Fixtures
# ------------------------------------------------------------------


@pytest.fixture()
def opencode_home(tmp_path: Path) -> Path:
    """Create a tiny opencode.db in a temporary directory and return the home path."""
    db_path = tmp_path / "opencode.db"
    con = sqlite3.connect(str(db_path))
    con.executescript(_SCHEMA)
    con.executescript(_build_seed())
    con.close()
    return tmp_path


@pytest.fixture()
def homes_list(opencode_home: Path) -> list[Path]:
    return [opencode_home]


# ------------------------------------------------------------------
# Tests: iter_events
# ------------------------------------------------------------------


class TestIterEvents:
    def test_message_events_emitted(self, homes_list: list[Path]) -> None:
        events = list(iter_events(homes_list))
        msg_events = [e for e in events if e.kind == "message"]
        assert len(msg_events) == 1
        assert msg_events[0].source == "opencode"
        assert msg_events[0].session_id == "sess_001"

    def test_response_event_from_assistant(self, homes_list: list[Path]) -> None:
        events = list(iter_events(homes_list))
        resp_events = [e for e in events if e.kind == "response"]
        assert len(resp_events) == 1
        ev = resp_events[0]
        assert ev.model == "zai-glm-4.7"
        assert ev.backend == "opencode"
        assert "zai-glm-4.7" in ev.detail

    def test_step_finish_part_emits_lifecycle(self, homes_list: list[Path]) -> None:
        events = list(iter_events(homes_list))
        lifecycle = [e for e in events if e.kind == "lifecycle" and "step-finish" in e.detail]
        assert len(lifecycle) == 1
        assert "tool-calls" in lifecycle[0].detail

    def test_all_sources_are_opencode(self, homes_list: list[Path]) -> None:
        for ev in iter_events(homes_list):
            assert ev.source == "opencode"

    def test_all_backends_are_opencode(self, homes_list: list[Path]) -> None:
        for ev in iter_events(homes_list):
            assert ev.backend == "opencode"

    def test_timestamp_from_data_json(self, homes_list: list[Path]) -> None:
        """User message has data.time.created = 1770325360000 (Unix ms).
        This should be used as the primary timestamp."""
        events = list(iter_events(homes_list))
        user_ev = [e for e in events if e.kind == "message"][0]
        assert user_ev.ts_ms == 1770325360000

    def test_assistant_timestamp_from_data_json(self, homes_list: list[Path]) -> None:
        """Assistant message has data.time.created = 1770325366978."""
        events = list(iter_events(homes_list))
        asst_ev = [e for e in events if e.kind == "response"][0]
        assert asst_ev.ts_ms == 1770325366978


# ------------------------------------------------------------------
# Tests: iter_session_candidates
# ------------------------------------------------------------------


class TestIterSessionCandidates:
    def test_session_candidate_created(self, homes_list: list[Path]) -> None:
        candidates = list(iter_session_candidates(homes_list))
        assert len(candidates) == 1

    def test_session_candidate_fields(self, homes_list: list[Path]) -> None:
        candidate = list(iter_session_candidates(homes_list))[0]
        assert candidate.session_id == "sess_001"
        assert candidate.backend == "opencode"
        assert candidate.source_family == "opencode"
        assert candidate.cwd == "/home/user/myproject"

    def test_model_from_assistant_message(self, homes_list: list[Path]) -> None:
        candidate = list(iter_session_candidates(homes_list))[0]
        assert candidate.model == "zai-glm-4.7"

    def test_prompt_signature_from_first_user_message(self, homes_list: list[Path]) -> None:
        candidate = list(iter_session_candidates(homes_list))[0]
        assert candidate.prompt_signature != ""

    def test_earliest_timestamp_used(self, homes_list: list[Path]) -> None:
        """Session candidate should use the earliest message timestamp."""
        candidate = list(iter_session_candidates(homes_list))[0]
        # User message is at 1770325360000, assistant at 1770325366978
        assert candidate.ts_ms == 1770325360000


# ------------------------------------------------------------------
# Tests: broken JSON handling
# ------------------------------------------------------------------


class TestBrokenJson:
    def test_broken_json_warns_but_continues(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str],
    ) -> None:
        """A message with broken JSON in data should warn and be skipped."""
        db_path = tmp_path / "opencode.db"
        con = sqlite3.connect(str(db_path))
        con.executescript(_SCHEMA)
        con.execute(
            "INSERT INTO session (id, directory, time_created)"
            " VALUES ('sess_bad', '/tmp', '2026-02-06T12:00:00Z')"
        )
        con.execute(
            "INSERT INTO message (id, session_id, time_created, data)"
            " VALUES ('msg_broken', 'sess_bad', '2026-02-06T12:01:00Z', '{not valid json!!!')"
        )
        # Also insert one good message to verify we still get events
        con.execute(
            "INSERT INTO message (id, session_id, time_created, data)"
            " VALUES ('msg_good', 'sess_bad', '2026-02-06T12:02:00Z', ?)",
            (_ASSISTANT_MSG_DATA,),
        )
        con.commit()
        con.close()

        events = list(iter_events([tmp_path]))
        captured = capsys.readouterr()

        # Warning was emitted
        assert "WARNING" in captured.err
        assert "malformed JSON" in captured.err

        # The good message was still processed
        assert len(events) >= 1
        resp_events = [e for e in events if e.kind == "response"]
        assert len(resp_events) == 1


# ------------------------------------------------------------------
# Tests: missing DB file
# ------------------------------------------------------------------


class TestMissingDbFile:
    def test_missing_db_yields_no_events(self, tmp_path: Path) -> None:
        nonexistent = tmp_path / "no_such_home"
        events = list(iter_events([nonexistent]))
        assert events == []

    def test_missing_db_yields_no_session_candidates(self, tmp_path: Path) -> None:
        nonexistent = tmp_path / "no_such_home"
        candidates = list(iter_session_candidates([nonexistent]))
        assert candidates == []

    def test_empty_homes_list(self) -> None:
        events = list(iter_events([]))
        assert events == []
        candidates = list(iter_session_candidates([]))
        assert candidates == []


# ------------------------------------------------------------------
# Tests: tool call / tool result parts
# ------------------------------------------------------------------


class TestToolParts:
    def test_tool_call_part(self, tmp_path: Path) -> None:
        db_path = tmp_path / "opencode.db"
        con = sqlite3.connect(str(db_path))
        con.executescript(_SCHEMA)
        con.execute(
            "INSERT INTO session (id, directory, time_created)"
            " VALUES ('sess_tools', '/tmp', '2026-02-06T12:00:00Z')"
        )
        con.execute(
            "INSERT INTO message (id, session_id, time_created, data)"
            " VALUES ('msg_t1', 'sess_tools', '2026-02-06T12:01:00Z', ?)",
            (_ASSISTANT_MSG_DATA,),
        )
        tool_call_data = json.dumps({
            "type": "tool-call",
            "name": "read_file",
            "input": {"path": "/tmp/foo.txt"},
        })
        con.execute(
            "INSERT INTO part (id, message_id, session_id, time_created, data)"
            " VALUES ('part_tc', 'msg_t1', 'sess_tools', '2026-02-06T12:01:05Z', ?)",
            (tool_call_data,),
        )
        tool_result_data = json.dumps({
            "type": "tool-result",
            "output": "file contents here",
        })
        con.execute(
            "INSERT INTO part (id, message_id, session_id, time_created, data)"
            " VALUES ('part_tr', 'msg_t1', 'sess_tools', '2026-02-06T12:01:06Z', ?)",
            (tool_result_data,),
        )
        con.commit()
        con.close()

        events = list(iter_events([tmp_path]))
        tool_calls = [e for e in events if e.kind == "tool_call"]
        tool_results = [e for e in events if e.kind == "tool_result"]
        assert len(tool_calls) == 1
        assert "read_file" in tool_calls[0].detail
        assert len(tool_results) == 1


# ------------------------------------------------------------------
# Tests: fallback to column timestamp
# ------------------------------------------------------------------


class TestTimestampFallback:
    def test_column_timestamp_when_data_has_no_time(self, tmp_path: Path) -> None:
        """When data JSON has no time block, fall back to SQL time_created column."""
        db_path = tmp_path / "opencode.db"
        con = sqlite3.connect(str(db_path))
        con.executescript(_SCHEMA)
        con.execute(
            "INSERT INTO session (id, directory, time_created)"
            " VALUES ('sess_fb', '/tmp', '2026-02-06T12:00:00Z')"
        )
        data_no_time = json.dumps({
            "role": "assistant",
            "modelID": "test-model",
            "finish": "stop",
        })
        con.execute(
            "INSERT INTO message (id, session_id, time_created, data)"
            " VALUES ('msg_fb', 'sess_fb', '2026-02-06T14:30:00Z', ?)",
            (data_no_time,),
        )
        con.commit()
        con.close()

        events = list(iter_events([tmp_path]))
        resp = [e for e in events if e.kind == "response"]
        assert len(resp) == 1
        # Should have used the column timestamp
        assert "2026-02-06" in resp[0].ts


# ------------------------------------------------------------------
# Tests: missing tables
# ------------------------------------------------------------------


class TestMissingTables:
    def test_empty_database_no_crash(self, tmp_path: Path) -> None:
        """A database with no tables should yield nothing, not crash."""
        db_path = tmp_path / "opencode.db"
        con = sqlite3.connect(str(db_path))
        con.close()

        events = list(iter_events([tmp_path]))
        assert events == []
        candidates = list(iter_session_candidates([tmp_path]))
        assert candidates == []
