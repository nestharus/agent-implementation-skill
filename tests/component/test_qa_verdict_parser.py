"""Component tests for qa_verdict_parser."""

from __future__ import annotations

import json

from src.qa.helpers.qa_verdict import parse_qa_verdict


def test_parse_qa_verdict_accepts_pass_json() -> None:
    result = parse_qa_verdict(
        '{"verdict": "PASS", "rationale": "All good"}',
    )

    assert result.verdict == "PASS"
    assert result.rationale == "All good"
    assert result.violations == []


def test_parse_qa_verdict_accepts_reject_json() -> None:
    result = parse_qa_verdict(json.dumps({
        "verdict": "REJECT",
        "rationale": "Scope violation",
        "violations": ["v1", "v2"],
    }))

    assert result.verdict == "REJECT"
    assert result.rationale == "Scope violation"
    assert result.violations == ["v1", "v2"]


def test_parse_qa_verdict_extracts_json_from_code_fence() -> None:
    result = parse_qa_verdict(
        'Here is my verdict:\n```json\n{"verdict": "PASS", "rationale": "OK"}\n```',
    )

    assert result.verdict == "PASS"
    assert result.rationale == "OK"
    assert result.violations == []


def test_parse_qa_verdict_degrades_for_unknown_verdict() -> None:
    """PAT-0014: unknown verdict maps to DEGRADED, not PASS."""
    result = parse_qa_verdict(
        '{"verdict": "MAYBE", "rationale": "dunno"}',
    )

    assert result.verdict == "DEGRADED"
    assert "Unknown verdict" in result.rationale
    assert result.violations == []


def test_parse_qa_verdict_degrades_for_garbage() -> None:
    """PAT-0014: unparseable output maps to DEGRADED, not PASS."""
    result = parse_qa_verdict("not json")

    assert result.verdict == "DEGRADED"
    assert "could not be parsed" in result.rationale
    assert result.violations == []
