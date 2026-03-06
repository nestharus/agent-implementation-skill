"""Tests for the Gemini CLI session log extractor."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from log_extract.extractors.gemini import iter_events, iter_session_candidates


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------


def _make_file(path: Path, content: str = "") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _make_gemini_home(tmp_path: Path, name: str = "gemini") -> Path:
    home = tmp_path / name
    home.mkdir()
    return home


def _history_dir(home: Path, project: str = "my-project") -> Path:
    d = home / "history" / project
    d.mkdir(parents=True, exist_ok=True)
    return d


# ------------------------------------------------------------------
# Empty / missing directory (the normal case)
# ------------------------------------------------------------------


class TestEmptyAndMissing:
    def test_missing_home_yields_no_events(self, tmp_path: Path) -> None:
        missing = tmp_path / "nonexistent"
        events = list(iter_events([missing]))
        assert events == []

    def test_missing_home_yields_no_candidates(self, tmp_path: Path) -> None:
        missing = tmp_path / "nonexistent"
        candidates = list(iter_session_candidates([missing]))
        assert candidates == []

    def test_empty_home_yields_no_events(self, tmp_path: Path) -> None:
        home = _make_gemini_home(tmp_path)
        events = list(iter_events([home]))
        assert events == []

    def test_empty_history_dir_yields_no_events(self, tmp_path: Path) -> None:
        home = _make_gemini_home(tmp_path)
        _history_dir(home)  # creates history/my-project/ but no files
        events = list(iter_events([home]))
        assert events == []

    def test_empty_list_yields_no_events(self) -> None:
        events = list(iter_events([]))
        assert events == []


# ------------------------------------------------------------------
# JSON file with timestamp + role/content -> events
# ------------------------------------------------------------------


class TestJsonWithMessages:
    @pytest.fixture()
    def home_with_messages(self, tmp_path: Path) -> Path:
        home = _make_gemini_home(tmp_path)
        hdir = _history_dir(home)
        records = [
            {
                "role": "user",
                "content": "Explain quicksort",
                "timestamp": "2026-02-15T10:00:00Z",
            },
            {
                "role": "model",
                "content": "Quicksort is a divide-and-conquer algorithm...",
                "timestamp": "2026-02-15T10:00:05Z",
            },
        ]
        _make_file(hdir / "session1.json", json.dumps(records))
        return home

    def test_produces_events(self, home_with_messages: Path) -> None:
        events = list(iter_events([home_with_messages]))
        assert len(events) == 2

    def test_user_event_is_message(self, home_with_messages: Path) -> None:
        events = list(iter_events([home_with_messages]))
        user_events = [e for e in events if e.kind == "message"]
        assert len(user_events) == 1
        assert "quicksort" in user_events[0].detail.lower()

    def test_model_event_is_response(self, home_with_messages: Path) -> None:
        events = list(iter_events([home_with_messages]))
        model_events = [e for e in events if e.kind == "response"]
        assert len(model_events) == 1
        assert "divide-and-conquer" in model_events[0].detail.lower()

    def test_source_and_backend_correct(self, home_with_messages: Path) -> None:
        events = list(iter_events([home_with_messages]))
        for ev in events:
            assert ev.source == "gemini"
            assert ev.backend == "gemini"

    def test_timestamps_parsed(self, home_with_messages: Path) -> None:
        events = list(iter_events([home_with_messages]))
        for ev in events:
            assert ev.ts  # non-empty
            assert ev.ts_ms > 0


class TestJsonSingleObject:
    """A JSON file containing a single dict record rather than an array."""

    def test_single_object_produces_event(self, tmp_path: Path) -> None:
        home = _make_gemini_home(tmp_path)
        hdir = _history_dir(home)
        record = {
            "role": "user",
            "text": "hello",
            "timestamp": 1700000000,
        }
        _make_file(hdir / "single.json", json.dumps(record))
        events = list(iter_events([home]))
        assert len(events) == 1
        assert events[0].kind == "message"


class TestJsonlFile:
    """JSONL files should be parsed line-by-line."""

    def test_jsonl_produces_events(self, tmp_path: Path) -> None:
        home = _make_gemini_home(tmp_path)
        hdir = _history_dir(home)
        lines = [
            json.dumps({"role": "user", "content": "line one", "ts": 1700000000}),
            json.dumps({"role": "model", "content": "line two", "ts": 1700000001}),
        ]
        _make_file(hdir / "chat.jsonl", "\n".join(lines))
        events = list(iter_events([home]))
        assert len(events) == 2


# ------------------------------------------------------------------
# Session candidates
# ------------------------------------------------------------------


class TestSessionCandidates:
    def test_candidate_from_json_with_id(self, tmp_path: Path) -> None:
        home = _make_gemini_home(tmp_path)
        hdir = _history_dir(home)
        records = [
            {
                "id": "sess-abc123",
                "role": "user",
                "content": "What is 2+2?",
                "timestamp": "2026-02-15T10:00:00Z",
            },
            {
                "role": "model",
                "content": "4",
                "timestamp": "2026-02-15T10:00:01Z",
            },
        ]
        _make_file(hdir / "session.json", json.dumps(records))
        candidates = list(iter_session_candidates([home]))
        assert len(candidates) == 1
        c = candidates[0]
        assert c.session_id == "sess-abc123"
        assert c.backend == "gemini"
        assert c.source_family == "gemini"
        assert c.ts_ms > 0
        assert c.prompt_signature  # non-empty because user text exists

    def test_no_candidate_without_id(self, tmp_path: Path) -> None:
        """Records without any id field should not produce candidates."""
        home = _make_gemini_home(tmp_path)
        hdir = _history_dir(home)
        records = [
            {"role": "user", "content": "hello", "timestamp": 1700000000},
        ]
        _make_file(hdir / "noid.json", json.dumps(records))
        candidates = list(iter_session_candidates([home]))
        assert candidates == []

    def test_no_candidate_without_timestamp(self, tmp_path: Path) -> None:
        """Records with an id but no timestamp should not produce candidates."""
        home = _make_gemini_home(tmp_path)
        hdir = _history_dir(home)
        records = [
            {"id": "sess-no-ts", "role": "user", "content": "hello"},
        ]
        _make_file(hdir / "nots.json", json.dumps(records))
        candidates = list(iter_session_candidates([home]))
        assert candidates == []

    def test_empty_dir_yields_no_candidates(self, tmp_path: Path) -> None:
        home = _make_gemini_home(tmp_path)
        _history_dir(home)
        candidates = list(iter_session_candidates([home]))
        assert candidates == []


# ------------------------------------------------------------------
# Malformed JSON: warn but never crash
# ------------------------------------------------------------------


class TestMalformedJson:
    def test_malformed_json_warns_and_yields_nothing(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str],
    ) -> None:
        home = _make_gemini_home(tmp_path)
        hdir = _history_dir(home)
        _make_file(hdir / "broken.json", "{{{not valid json!!!")
        events = list(iter_events([home]))
        assert events == []
        captured = capsys.readouterr()
        assert "WARNING" in captured.err
        assert "malformed" in captured.err.lower()

    def test_malformed_jsonl_warns_per_line(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str],
    ) -> None:
        home = _make_gemini_home(tmp_path)
        hdir = _history_dir(home)
        content = (
            json.dumps({"role": "user", "content": "ok", "timestamp": 1700000000})
            + "\n"
            + "{bad line}\n"
        )
        _make_file(hdir / "mixed.jsonl", content)
        events = list(iter_events([home]))
        # First line is valid -> 1 event; second line is bad -> warning
        assert len(events) == 1
        captured = capsys.readouterr()
        assert "WARNING" in captured.err

    def test_malformed_json_candidates_yields_nothing(
        self, tmp_path: Path,
    ) -> None:
        home = _make_gemini_home(tmp_path)
        hdir = _history_dir(home)
        _make_file(hdir / "broken.json", "not json at all")
        candidates = list(iter_session_candidates([home]))
        assert candidates == []


# ------------------------------------------------------------------
# Robustness: extractor never raises exceptions
# ------------------------------------------------------------------


class TestNeverRaises:
    """The Gemini extractor must absorb all errors and never propagate
    exceptions to the caller."""

    def test_iter_events_never_raises_on_missing(self) -> None:
        # Deeply nested nonexistent path
        try:
            list(iter_events([Path("/nonexistent/deep/path/gemini")]))
        except Exception:
            pytest.fail("iter_events raised an exception on missing path")

    def test_iter_session_candidates_never_raises_on_missing(self) -> None:
        try:
            list(iter_session_candidates([Path("/nonexistent/deep/path/gemini")]))
        except Exception:
            pytest.fail("iter_session_candidates raised on missing path")

    def test_iter_events_never_raises_on_empty_list(self) -> None:
        try:
            list(iter_events([]))
        except Exception:
            pytest.fail("iter_events raised on empty list")

    def test_iter_session_candidates_never_raises_on_empty_list(self) -> None:
        try:
            list(iter_session_candidates([]))
        except Exception:
            pytest.fail("iter_session_candidates raised on empty list")

    def test_non_json_files_ignored(self, tmp_path: Path) -> None:
        """Files with non-JSON extensions in history/ are silently skipped."""
        home = _make_gemini_home(tmp_path)
        hdir = _history_dir(home)
        _make_file(hdir / "notes.txt", "just some text")
        _make_file(hdir / "data.csv", "a,b,c\n1,2,3")
        try:
            events = list(iter_events([home]))
        except Exception:
            pytest.fail("iter_events raised on non-JSON files")
        assert events == []


# ------------------------------------------------------------------
# Gemini API-style parts structure
# ------------------------------------------------------------------


class TestGeminiApiParts:
    """Gemini API records use a 'parts' field with text sub-objects."""

    def test_parts_structure_extracted(self, tmp_path: Path) -> None:
        home = _make_gemini_home(tmp_path)
        hdir = _history_dir(home)
        record = {
            "role": "model",
            "parts": [{"text": "The answer is 42"}],
            "timestamp": "2026-02-15T12:00:00Z",
        }
        _make_file(hdir / "parts.json", json.dumps(record))
        events = list(iter_events([home]))
        assert len(events) == 1
        assert "42" in events[0].detail
        assert events[0].kind == "response"


# ------------------------------------------------------------------
# Multiple homes
# ------------------------------------------------------------------


class TestMultipleHomes:
    def test_events_from_multiple_homes(self, tmp_path: Path) -> None:
        home1 = _make_gemini_home(tmp_path, "gemini1")
        home2 = _make_gemini_home(tmp_path, "gemini2")
        hdir1 = _history_dir(home1)
        hdir2 = _history_dir(home2)
        _make_file(
            hdir1 / "s1.json",
            json.dumps({"role": "user", "content": "home1", "timestamp": 1700000000}),
        )
        _make_file(
            hdir2 / "s2.json",
            json.dumps({"role": "user", "content": "home2", "timestamp": 1700000001}),
        )
        events = list(iter_events([home1, home2]))
        assert len(events) == 2
        details = {e.detail for e in events}
        assert "home1" in details
        assert "home2" in details
