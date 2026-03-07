"""Tests for contracts and helpers — Packet 0."""

import os
import tempfile
from pathlib import Path

from log_extract.models import (
    CorrelationLink,
    DispatchCandidate,
    SessionCandidate,
    TimelineEvent,
)
from log_extract.utils import (
    infer_section,
    load_model_backend_map,
    parse_timestamp,
    prompt_signature,
    summarize_text,
)


class TestParseTimestamp:
    def test_iso_with_z(self):
        ts, ms = parse_timestamp("2026-01-15T10:30:00.123Z")
        assert ts.endswith("Z")
        assert "2026-01-15" in ts
        assert isinstance(ms, int)
        assert ms > 0

    def test_iso_with_offset(self):
        ts, ms = parse_timestamp("2026-01-15T10:30:00+05:00")
        assert ts.endswith("Z")

    def test_naive_iso(self):
        ts, ms = parse_timestamp("2026-01-15T10:30:00")
        assert ts.endswith("Z")

    def test_unix_seconds(self):
        ts, ms = parse_timestamp(1700000000)
        assert ts.endswith("Z")
        assert ms == 1700000000000

    def test_unix_milliseconds(self):
        ts, ms = parse_timestamp(1700000000000)
        assert ts.endswith("Z")
        assert ms == 1700000000000

    def test_unix_string(self):
        ts, ms = parse_timestamp("1700000000")
        assert ms == 1700000000000

    def test_empty_raises(self):
        try:
            parse_timestamp("")
            assert False
        except ValueError:
            pass


class TestPromptSignature:
    def test_stable_across_whitespace(self):
        sig1 = prompt_signature("hello   world")
        sig2 = prompt_signature("hello world")
        assert sig1 == sig2

    def test_stable_across_leading_trailing(self):
        sig1 = prompt_signature("  hello world  ")
        sig2 = prompt_signature("hello world")
        assert sig1 == sig2

    def test_hex_digest(self):
        sig = prompt_signature("test")
        assert len(sig) == 64
        assert all(c in "0123456789abcdef" for c in sig)


class TestInferSection:
    def test_from_agent_name(self):
        assert infer_section("solver-03") == "03"

    def test_from_path(self):
        assert infer_section("section-05-output.md") == "05"

    def test_from_tag(self):
        assert infer_section("proposal-align:03") == "03"

    def test_no_match(self):
        assert infer_section("no section here") == ""

    def test_empty(self):
        assert infer_section("", "") == ""

    def test_multiple_candidates(self):
        # First match wins
        result = infer_section("section-03", "section-05")
        assert result == "03"


class TestSummarizeText:
    def test_short_text(self):
        assert summarize_text("hello") == "hello"

    def test_long_text_truncated(self):
        result = summarize_text("x" * 200)
        assert len(result) <= 160
        assert result.endswith("...")

    def test_whitespace_collapsed(self):
        result = summarize_text("hello\n  world\t foo")
        assert result == "hello world foo"


class TestLoadModelBackendMap:
    def test_parses_toml_configs(self):
        with tempfile.TemporaryDirectory() as td:
            models_dir = Path(td) / ".agents" / "models"
            models_dir.mkdir(parents=True)
            (models_dir / "claude-opus.toml").write_text(
                'command = "env -u CLAUDECODE claude2"\n'
                'args = ["-p", "--model", "opus"]\n'
            )
            (models_dir / "glm.toml").write_text(
                'command = "opencode"\n'
                'args = ["run"]\n'
                'prompt_mode = "arg"\n'
            )
            result = load_model_backend_map(Path(td) / "planspace")
            assert "claude-opus" in result
            assert result["claude-opus"] == ("claude2", "claude")
            assert "glm" in result
            assert result["glm"] == ("opencode", "opencode")

    def test_missing_models_dir(self):
        with tempfile.TemporaryDirectory() as td:
            result = load_model_backend_map(Path(td))
            assert result == {}


class TestDataclasses:
    def test_timeline_event_defaults(self):
        ev = TimelineEvent(ts="t", ts_ms=0, source="run.db", kind="lifecycle", detail="d")
        assert ev.agent == ""
        assert ev.raw == {}

    def test_dispatch_candidate_defaults(self):
        dc = DispatchCandidate(dispatch_id="d", ts="t", ts_ms=0, backend="b", source_family="f")
        assert dc.model == ""

    def test_session_candidate_defaults(self):
        sc = SessionCandidate(session_id="s", ts="t", ts_ms=0, backend="b", source_family="f")
        assert sc.model == ""

    def test_correlation_link(self):
        cl = CorrelationLink(session_id="s", dispatch_id="d", score=90, reasons=["match"])
        assert cl.score == 90
