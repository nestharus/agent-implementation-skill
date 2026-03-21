"""Component tests for structured agent signal readers."""

from __future__ import annotations

import json

from src.signals.repository.signal_reader import read_agent_signal, read_signal_tuple
from signals.types import AgentSignal


def test_read_agent_signal_returns_parsed_object(tmp_path) -> None:
    signal_path = tmp_path / "signal.json"
    signal_path.write_text(json.dumps({"state": "NEED_DECISION", "detail": "help"}))

    result = read_agent_signal(signal_path)

    assert isinstance(result, AgentSignal)
    assert result.state == "NEED_DECISION"
    assert result.detail == "help"


def test_read_agent_signal_returns_none_for_missing_file(tmp_path) -> None:
    assert read_agent_signal(tmp_path / "nope.json") is None


def test_read_agent_signal_renames_non_object_json(tmp_path) -> None:
    signal_path = tmp_path / "signal.json"
    signal_path.write_text(json.dumps(["bad"]), encoding="utf-8")

    result = read_agent_signal(signal_path)

    assert result is None
    assert not signal_path.exists()
    assert (tmp_path / "signal.malformed.json").exists()


def test_read_signal_tuple_enriches_detail_from_structured_fields(tmp_path) -> None:
    signal_path = tmp_path / "signal.json"
    signal_path.write_text(json.dumps({
        "state": "need_decision",
        "detail": "blocked",
        "needs": "design decision",
        "assumptions_refused": "will not guess",
        "suggested_escalation_target": "parent-agent",
    }), encoding="utf-8")

    signal_type, detail = read_signal_tuple(signal_path)

    assert signal_type == "need_decision"
    assert "blocked" in detail
    assert "Needs: design decision" in detail
    assert "Refused assumptions: will not guess" in detail
    assert "Escalation target: parent-agent" in detail


def test_read_signal_tuple_fails_closed_for_unknown_state(tmp_path) -> None:
    signal_path = tmp_path / "signal.json"
    signal_path.write_text(json.dumps({
        "state": "unexpected_state",
        "detail": "raw detail",
    }), encoding="utf-8")

    signal_type, detail = read_signal_tuple(signal_path)

    assert signal_type == "need_decision"
    assert "Unknown signal state" in detail
    assert "raw detail" in detail


def test_read_signal_tuple_fails_closed_for_malformed_json(tmp_path) -> None:
    signal_path = tmp_path / "signal.json"
    signal_path.write_text("{not json", encoding="utf-8")

    signal_type, detail = read_signal_tuple(signal_path)

    assert signal_type == "need_decision"
    assert "Malformed signal JSON" in detail
    assert not signal_path.exists()
    assert (tmp_path / "signal.malformed.json").exists()
