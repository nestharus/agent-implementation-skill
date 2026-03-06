"""Tests for the artifact and signal extractor."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from log_extract.extractors.artifacts import iter_events


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def _collect(artifacts_dir: Path) -> list:
    return list(iter_events(artifacts_dir))


def _make_file(path: Path, content: str = "") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


# ------------------------------------------------------------------
# Fixtures
# ------------------------------------------------------------------

@pytest.fixture()
def artifacts_dir(tmp_path: Path) -> Path:
    """Build a tiny artifacts tree for testing.

    Layout::

        artifacts/
            readme.txt              (plain artifact)
            build.meta.json         (meta with returncode/timed_out)
            traceability.json       (2 entries)
            signals/
                good.json           (valid signal)
                bad.json            (malformed JSON)
    """
    root = tmp_path / "artifacts"
    root.mkdir()

    # Plain artifact
    _make_file(root / "readme.txt", "hello world")

    # Meta JSON
    _make_file(
        root / "build.meta.json",
        json.dumps({"returncode": 0, "timed_out": False, "duration": 1.5}),
    )

    # Traceability JSON with 2 entries
    _make_file(
        root / "traceability.json",
        json.dumps([
            {
                "section": "section-01",
                "artifact": "auth.py",
                "source": "spec",
                "detail": "authentication module",
            },
            {
                "section": "section-02",
                "artifact": "db.py",
                "source": "spec",
                "detail": "database layer",
            },
        ]),
    )

    # Signals
    signals = root / "signals"
    signals.mkdir()
    _make_file(
        signals / "good.json",
        json.dumps({"status": "ok", "count": 42}),
    )
    _make_file(signals / "bad.json", "{broken json!!!")

    return root


# ------------------------------------------------------------------
# Tests
# ------------------------------------------------------------------

class TestRegularArtifact:
    def test_plain_file_emits_artifact_event(self, artifacts_dir: Path) -> None:
        events = _collect(artifacts_dir)
        artifact_events = [e for e in events if e.source == "artifact" and "readme.txt" in e.detail]
        assert len(artifact_events) == 1
        ev = artifact_events[0]
        assert ev.kind == "artifact"
        assert "bytes" in ev.detail
        assert ev.ts  # non-empty timestamp
        assert ev.ts_ms > 0


class TestMetaJson:
    def test_meta_json_includes_returncode_and_timed_out(self, artifacts_dir: Path) -> None:
        events = _collect(artifacts_dir)
        meta_events = [e for e in events if "build.meta.json" in e.detail]
        assert len(meta_events) == 1
        ev = meta_events[0]
        assert "returncode=0" in ev.detail
        assert "timed_out=False" in ev.detail


class TestTraceability:
    def test_traceability_emits_file_plus_entries(self, artifacts_dir: Path) -> None:
        events = _collect(artifacts_dir)
        trace_events = [
            e for e in events
            if e.source == "artifact" and (
                "traceability.json" in e.detail
                or "section=" in e.detail
                or "artifact=" in e.detail
            )
        ]
        # 1 file event + 2 per-entry events = 3
        assert len(trace_events) == 3

    def test_traceability_entries_have_section(self, artifacts_dir: Path) -> None:
        events = _collect(artifacts_dir)
        entry_events = [
            e for e in events
            if e.source == "artifact" and "artifact=" in e.detail
        ]
        sections = {e.section for e in entry_events}
        assert "01" in sections
        assert "02" in sections


class TestSignals:
    def test_signal_events_emitted(self, artifacts_dir: Path) -> None:
        events = _collect(artifacts_dir)
        signal_events = [e for e in events if e.source == "signal"]
        # good.json + bad.json = 2 signal events
        assert len(signal_events) == 2

    def test_good_signal_has_detail(self, artifacts_dir: Path) -> None:
        events = _collect(artifacts_dir)
        good = [e for e in events if e.source == "signal" and "good" in e.detail]
        assert len(good) == 1
        assert "status=ok" in good[0].detail

    def test_signals_not_double_counted(self, artifacts_dir: Path) -> None:
        """Signal files must NOT also appear as artifact events."""
        events = _collect(artifacts_dir)
        artifact_details = [e.detail for e in events if e.source == "artifact"]
        for detail in artifact_details:
            assert "signals" not in detail or "signals/" not in detail.split(" ")[0], (
                f"signal file leaked into artifact events: {detail}"
            )


class TestBrokenJson:
    def test_broken_json_warns_and_emits_file_event(
        self, artifacts_dir: Path, capsys: pytest.CaptureFixture[str],
    ) -> None:
        events = _collect(artifacts_dir)
        # The broken signal file still produces a signal event
        bad_signals = [e for e in events if e.source == "signal" and "bad" in e.detail]
        assert len(bad_signals) == 1
        # Warning was printed to stderr
        captured = capsys.readouterr()
        assert "WARNING" in captured.err
        assert "bad.json" in captured.err


class TestMissingDirectory:
    def test_missing_dir_yields_no_events(self, tmp_path: Path) -> None:
        events = list(iter_events(tmp_path / "nonexistent"))
        assert events == []

    def test_empty_dir_yields_no_events(self, tmp_path: Path) -> None:
        empty = tmp_path / "empty_artifacts"
        empty.mkdir()
        events = list(iter_events(empty))
        assert events == []
