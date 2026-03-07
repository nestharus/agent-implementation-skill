"""Tests for formatters — Packet 9."""

import csv
import io
import json

from log_extract.formatters import format_csv, format_jsonl, format_text
from log_extract.models import TimelineEvent


def _ev(**kw):
    defaults = dict(
        ts="2026-01-01T00:00:01.000Z", ts_ms=1000,
        source="run.db", kind="lifecycle", detail="test event",
    )
    defaults.update(kw)
    return TimelineEvent(**defaults)


class TestJsonl:
    def test_valid_json(self):
        lines = list(format_jsonl([_ev()]))
        assert len(lines) == 1
        obj = json.loads(lines[0])
        assert obj["ts"] == "2026-01-01T00:00:01.000Z"
        assert obj["source"] == "run.db"
        assert obj["kind"] == "lifecycle"
        assert obj["detail"] == "test event"

    def test_excludes_ts_ms(self):
        lines = list(format_jsonl([_ev()]))
        obj = json.loads(lines[0])
        assert "ts_ms" not in obj

    def test_omits_empty_fields(self):
        lines = list(format_jsonl([_ev()]))
        obj = json.loads(lines[0])
        assert "agent" not in obj
        assert "session_id" not in obj

    def test_includes_nonempty_fields(self):
        lines = list(format_jsonl([_ev(agent="solver-03", section="03")]))
        obj = json.loads(lines[0])
        assert obj["agent"] == "solver-03"
        assert obj["section"] == "03"


class TestCsv:
    def test_header_row(self):
        lines = list(format_csv([_ev()]))
        assert len(lines) == 2  # header + 1 data row
        assert "ts" in lines[0]
        assert "source" in lines[0]

    def test_valid_csv(self):
        lines = list(format_csv([_ev(agent="solver-03")]))
        reader = csv.reader(io.StringIO("\n".join(lines)))
        rows = list(reader)
        assert len(rows) == 2
        header = rows[0]
        data = rows[1]
        assert header[0] == "ts"
        assert data[header.index("agent")] == "solver-03"


class TestText:
    def test_contains_timestamp(self):
        lines = list(format_text([_ev()], use_color=False))
        assert "2026-01-01T00:00:01.000Z" in lines[0]

    def test_contains_detail(self):
        lines = list(format_text([_ev()], use_color=False))
        assert "test event" in lines[0]

    def test_color_output(self):
        lines = list(format_text([_ev()], use_color=True))
        # Should contain ANSI escape codes
        assert "\033[" in lines[0]

    def test_no_color(self):
        lines = list(format_text([_ev()], use_color=False))
        assert "\033[" not in lines[0]

    def test_alignment_without_color(self):
        events = [
            _ev(kind="lifecycle", source="run.db"),
            _ev(kind="signal", source="signal"),
        ]
        lines = list(format_text(events, use_color=False))
        # Both lines should be consistently structured
        assert len(lines) == 2
