"""Component tests for shared dispatch helpers."""

from __future__ import annotations

import json

from src.dispatch.helpers.utils import (
    check_agent_signals,
    summarize_output,
    write_model_choice_signal,
)


def test_summarize_output_prefers_summary_line() -> None:
    output = "# Heading\nSummary: Important result\nDetails"

    assert summarize_output(output) == "Important result"


def test_summarize_output_falls_back_and_truncates() -> None:
    summary = summarize_output("# H\n---\n" + ("x" * 300), max_len=50)

    assert len(summary) == 50
    assert summary == "x" * 50


def test_write_model_choice_signal_writes_structured_artifact(planspace: Path) -> None:
    write_model_choice_signal(
        planspace,
        "04",
        "alignment",
        "gpt-high",
        "default policy",
        escalated_from="glm",
    )

    signal_path = (
        planspace / "artifacts" / "signals" / "model-choice-04-alignment.json"
    )
    data = json.loads(signal_path.read_text(encoding="utf-8"))

    assert data == {
        "section": "04",
        "step": "alignment",
        "model": "gpt-high",
        "reason": "default policy",
        "escalated_from": "glm",
    }


def test_check_agent_signals_reads_structured_signal_file(tmp_path) -> None:
    signal_path = tmp_path / "signal.json"
    signal_path.write_text(
        json.dumps({"state": "dependency", "detail": "wait for section 02"}),
        encoding="utf-8",
    )

    signal, detail = check_agent_signals("ignored output", signal_path=signal_path)

    assert signal == "dependency"
    assert "section 02" in detail


def test_check_agent_signals_returns_none_when_signal_missing(tmp_path) -> None:
    result = check_agent_signals("ignored", signal_path=tmp_path / "missing.json")
    assert result.signal_type is None
    assert result.detail == ""
