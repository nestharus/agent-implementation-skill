from __future__ import annotations

from pathlib import Path

from signals.repository.artifact_io import write_json
from src.intent.service.surfaces import (
    load_implementation_feedback_surfaces,
)


def test_load_implementation_feedback_surfaces_returns_none_when_missing(
    tmp_path: Path,
) -> None:
    assert load_implementation_feedback_surfaces("01", tmp_path) is None


def test_load_implementation_feedback_surfaces_reads_valid_payload(
    tmp_path: Path,
) -> None:
    feedback_path = (
        tmp_path / "artifacts" / "signals" / "impl-feedback-surfaces-01.json"
    )
    payload = {
        "problem_surfaces": [
            {
                "kind": "gap",
                "axis_id": "A2",
                "title": "Constraint discovered during implementation",
                "description": "Observed latency cap not captured in problem",
                "evidence": "Profiling run exceeded expected threshold",
            },
        ],
        "philosophy_surfaces": [],
    }
    write_json(feedback_path, payload)

    assert load_implementation_feedback_surfaces("01", tmp_path) == payload


def test_load_implementation_feedback_surfaces_returns_none_for_malformed_json(
    tmp_path: Path,
) -> None:
    feedback_path = (
        tmp_path / "artifacts" / "signals" / "impl-feedback-surfaces-01.json"
    )
    feedback_path.parent.mkdir(parents=True, exist_ok=True)
    feedback_path.write_text("{not-json", encoding="utf-8")

    assert load_implementation_feedback_surfaces("01", tmp_path) is None
    assert not feedback_path.exists()
    assert feedback_path.with_suffix(".malformed.json").exists()
