"""Component tests for structured agent signal readers."""

from __future__ import annotations

import json

from src.scripts.lib.signal_reader import read_agent_signal, read_signal_tuple


def test_read_agent_signal_returns_parsed_object(tmp_path) -> None:
    signal_path = tmp_path / "signal.json"
    signal_path.write_text(json.dumps({"state": "NEEDS_PARENT", "detail": "help"}))

    result = read_agent_signal(signal_path, expected_fields=["state"])

    assert result == {"state": "NEEDS_PARENT", "detail": "help"}


def test_read_agent_signal_returns_none_for_missing_expected_field(tmp_path) -> None:
    signal_path = tmp_path / "signal.json"
    signal_path.write_text(json.dumps({"state": "NEEDS_PARENT"}))

    assert read_agent_signal(signal_path, expected_fields=["detail"]) is None


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
        "state": "needs_parent",
        "detail": "blocked",
        "needs": "design decision",
        "assumptions_refused": "will not guess",
        "suggested_escalation_target": "parent-agent",
    }), encoding="utf-8")

    signal_type, detail = read_signal_tuple(signal_path)

    assert signal_type == "needs_parent"
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

    assert signal_type == "needs_parent"
    assert "Unknown signal state" in detail
    assert "raw detail" in detail


def test_read_signal_tuple_fails_closed_for_malformed_json(tmp_path) -> None:
    signal_path = tmp_path / "signal.json"
    signal_path.write_text("{not json", encoding="utf-8")

    signal_type, detail = read_signal_tuple(signal_path)

    assert signal_type == "needs_parent"
    assert "Malformed signal JSON" in detail
    assert not signal_path.exists()
    assert (tmp_path / "signal.malformed.json").exists()
