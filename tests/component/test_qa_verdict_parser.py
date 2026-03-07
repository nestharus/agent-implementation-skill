"""Component tests for qa_verdict_parser."""

from __future__ import annotations

import json

from src.scripts.lib.qa_verdict_parser import parse_qa_verdict


def test_parse_qa_verdict_accepts_pass_json() -> None:
    verdict, rationale, violations = parse_qa_verdict(
        '{"verdict": "PASS", "rationale": "All good"}',
    )

    assert verdict == "PASS"
    assert rationale == "All good"
    assert violations == []


def test_parse_qa_verdict_accepts_reject_json() -> None:
    verdict, rationale, violations = parse_qa_verdict(json.dumps({
        "verdict": "REJECT",
        "rationale": "Scope violation",
        "violations": ["v1", "v2"],
    }))

    assert verdict == "REJECT"
    assert rationale == "Scope violation"
    assert violations == ["v1", "v2"]


def test_parse_qa_verdict_extracts_json_from_code_fence() -> None:
    verdict, rationale, violations = parse_qa_verdict(
        'Here is my verdict:\n```json\n{"verdict": "PASS", "rationale": "OK"}\n```',
    )

    assert verdict == "PASS"
    assert rationale == "OK"
    assert violations == []


def test_parse_qa_verdict_defaults_to_pass_for_unknown_verdict() -> None:
    verdict, rationale, violations = parse_qa_verdict(
        '{"verdict": "MAYBE", "rationale": "dunno"}',
    )

    assert verdict == "PASS"
    assert "Unknown verdict" in rationale
    assert violations == []


def test_parse_qa_verdict_defaults_to_pass_for_garbage() -> None:
    verdict, rationale, violations = parse_qa_verdict("not json")

    assert verdict == "PASS"
    assert "could not be parsed" in rationale
    assert violations == []
